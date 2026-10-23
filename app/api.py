from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.apk import apk_available
from app.bot import notify_owner
from app.config import settings
from app.database import get_db
from app.models import BlockedSender, Message, User, utcnow
from app.qr import qr_png
from app.security import client_ip, get_current_user, hash_ip
from app.services import (
    USERNAME_RE,
    RESERVED_USERNAMES,
    RateLimiter,
    block_sender,
    get_user_by_username,
    is_blocked,
    new_connect_token,
    unblock_sender,
)
from app.utils import describe_region, iso, parse_device

router = APIRouter(prefix="/api")
limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE, 60)

SESSION_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


# ======================================================================
#  PUBLIC: umumiy ma'lumot
# ======================================================================
@router.get("/app")
async def app_info():
    return {"name": settings.APP_NAME, "apk_available": apk_available()}


# ======================================================================
#  PUBLIC: mehmonlar uchun (ro'yxatdan o'tish shart emas)
# ======================================================================
class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    session_id: str = Field(pattern=SESSION_PATTERN)
    wants_reply: bool = False
    tz: str | None = Field(default=None, max_length=64)
    website: str | None = None  # honeypot: botlar to'ldiradi, odamlar ko'rmaydi


@router.get("/u/{username}")
async def public_profile(username: str, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_username(db, username)
    if not user:
        raise HTTPException(404, "Profil topilmadi")
    return {"username": user.username, "name": user.name, "avatar_url": user.avatar_url}


@router.post("/u/{username}/messages", status_code=201)
async def send_message(username: str, body: SendIn, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_username(db, username)
    if not user:
        raise HTTPException(404, "Profil topilmadi")

    ip_hash = hash_ip(client_ip(request))

    if not limiter.allow(f"{user.id}:{ip_hash}"):
        raise HTTPException(429, "Juda tez-tez yuboryapsiz. Bir ozdan keyin urinib ko'ring.")

    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Xabar bo'sh bo'lmasligi kerak")
    if len(text) > settings.MAX_MESSAGE_LENGTH:
        raise HTTPException(422, f"Xabar {settings.MAX_MESSAGE_LENGTH} belgidan oshmasligi kerak")

    # Honeypot yoki bloklangan yuboruvchi: "muvaffaqiyatli" deb javob beramiz (shadow-ban),
    # shunda yuboruvchi bloklanganini bilmaydi.
    if body.website or await is_blocked(db, user.id, body.session_id, ip_hash):
        return {"ok": True}

    msg = Message(
        user_id=user.id,
        text=text,
        session_id=body.session_id,
        ip_hash=ip_hash,
        wants_reply=True,  # javob tugmasi doim ishlaydi
        device=parse_device(request.headers.get("user-agent", "")),
        region=describe_region(body.tz, request.headers.get("cf-ipcountry")),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Telegram ulangan bo'lsa zudlik bilan yetkazamiz; aks holda kabinetda saqlanadi
    await notify_owner(db, user, msg)
    return {"ok": True}


@router.get("/u/{username}/replies")
async def my_thread(
    username: str,
    session_id: str = Query(..., pattern=SESSION_PATTERN),
    db: AsyncSession = Depends(get_db),
):
    """Mehmon o'zi yuborgan (javob kutilgan) xabarlar va ularga kelgan javoblar."""
    user = await get_user_by_username(db, username)
    if not user:
        raise HTTPException(404, "Profil topilmadi")
    res = await db.execute(
        select(Message)
        .where(Message.user_id == user.id, Message.session_id == session_id, Message.wants_reply.is_(True))
        .order_by(Message.created_at.desc())
        .limit(30)
    )
    return [
        {
            "id": m.id,
            "text": m.text,
            "created_at": iso(m.created_at),
            "reply_text": m.reply_text,
            "replied_at": iso(m.replied_at),
        }
        for m in res.scalars().all()
    ]


# ======================================================================
#  PRIVATE: shaxsiy kabinet (Google orqali kirgan foydalanuvchi)
# ======================================================================
@router.get("/me")
async def me(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    connected = user.telegram_chat_id is not None
    if not connected and not user.connect_token:
        user.connect_token = new_connect_token()
        await db.commit()

    total = (await db.execute(select(func.count(Message.id)).where(Message.user_id == user.id))).scalar_one()
    unread = (
        await db.execute(
            select(func.count(Message.id)).where(Message.user_id == user.id, Message.is_read.is_(False))
        )
    ).scalar_one()

    bot_username = request.app.state.bot_username
    connect_url = None if connected else f"https://t.me/{bot_username}?start=connect_{user.connect_token}"
    return {
        "name": user.name,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "username": user.username,
        "profile_url": f"{settings.BASE_URL}/u/{user.username}",
        "telegram_connected": connected,
        "connect_url": connect_url,
        "bot_username": bot_username,
        "bot_url": f"https://t.me/{bot_username}",
        "apk_available": apk_available(),
        "total": total,
        "unread": unread,
    }


@router.get("/me/qr.png")
async def my_qr(user: User = Depends(get_current_user)):
    """Shaxsiy havola uchun QR kod (PNG)."""
    url = f"{settings.BASE_URL}/u/{user.username}"
    return Response(content=qr_png(url), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


class UsernameIn(BaseModel):
    username: str


@router.patch("/me/username")
async def change_username(body: UsernameIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    new = body.username.strip().lower()
    if not USERNAME_RE.match(new):
        raise HTTPException(422, "Username 3-20 belgi: faqat a-z, 0-9 va _ bo'lishi mumkin")
    if new in RESERVED_USERNAMES:
        raise HTTPException(409, "Bu nom band qilingan")
    existing = await get_user_by_username(db, new)
    if existing and existing.id != user.id:
        raise HTTPException(409, "Bu username allaqachon band")
    user.username = new
    await db.commit()
    return {"username": new, "profile_url": f"{settings.BASE_URL}/u/{new}"}


@router.post("/me/telegram/disconnect")
async def telegram_disconnect(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user.telegram_chat_id = None
    user.connect_token = new_connect_token()
    await db.commit()
    return {"ok": True}


@router.get("/me/messages")
async def list_messages(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(limit).offset(offset)
    )
    messages = res.scalars().all()

    bl = (await db.execute(select(BlockedSender).where(BlockedSender.user_id == user.id))).scalars().all()
    blocked_sessions = {b.session_id for b in bl if b.session_id}
    blocked_ips = {b.ip_hash for b in bl if b.ip_hash}

    return [
        {
            "id": m.id,
            "text": m.text,
            "device": m.device,
            "region": m.region,
            "wants_reply": m.wants_reply,
            "is_read": m.is_read,
            "reply_text": m.reply_text,
            "replied_at": iso(m.replied_at),
            "created_at": iso(m.created_at),
            "blocked": (m.session_id in blocked_sessions) or (m.ip_hash in blocked_ips),
        }
        for m in messages
    ]


async def _own_message(db: AsyncSession, msg_id: int, user: User) -> Message:
    msg = (
        await db.execute(select(Message).where(Message.id == msg_id, Message.user_id == user.id))
    ).scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "Xabar topilmadi")
    return msg


@router.post("/me/messages/read-all")
async def read_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(update(Message).where(Message.user_id == user.id, Message.is_read.is_(False)).values(is_read=True))
    await db.commit()
    return {"ok": True}


@router.delete("/me/messages/{msg_id}")
async def delete_message(msg_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    msg = await _own_message(db, msg_id, user)
    await db.delete(msg)
    await db.commit()
    return {"ok": True}


@router.post("/me/messages/{msg_id}/block")
async def block_message_sender(msg_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    msg = await _own_message(db, msg_id, user)
    await block_sender(db, msg)
    await db.commit()
    return {"ok": True}


@router.post("/me/messages/{msg_id}/unblock")
async def unblock_message_sender(msg_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    msg = await _own_message(db, msg_id, user)
    await unblock_sender(db, msg)
    await db.commit()
    return {"ok": True}


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@router.post("/me/messages/{msg_id}/reply")
async def reply_from_site(
    msg_id: int, body: ReplyIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Saytdan javob yozish. Telegram ulanmagan bo'lsa ham ishlaydi; bot bilan bir xil maydonga yozadi."""
    msg = await _own_message(db, msg_id, user)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Javob bo'sh bo'lmasligi kerak")
    msg.reply_text = text[: settings.MAX_MESSAGE_LENGTH]
    msg.replied_at = utcnow()
    msg.is_read = True
    await db.commit()
    return {"ok": True}

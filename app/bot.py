import asyncio
import html
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.types import Message as TgMessage
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.apk import apk_available, apk_file, apk_link
from app.config import settings
from app.database import SessionLocal
from app.models import BlockedSender, Message, User, utcnow
from app.qr import qr_png
from app.services import block_sender, new_connect_token, unblock_sender
from app.storycard import render_story_card

log = logging.getLogger("anonqa.bot")

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Pastki menyu tugmalari (matnlari handlerlar bilan bir xil bo'lishi shart)
BTN_LINK = "🔗 Mening havolam"
BTN_INBOX = "📥 Oxirgi xabarlar"
BTN_STATS = "📊 Statistika"
BTN_APK = "📲 APK yuklab olish"
BTN_HELP = "ℹ️ Yordam"
BTN_CANCEL = "❌ Bekor qilish"
MENU_TEXTS = {BTN_LINK, BTN_INBOX, BTN_STATS, BTN_APK, BTN_HELP}


class ReplyFlow(StatesGroup):
    waiting_text = State()


# ============================================================ klaviaturalar
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LINK), KeyboardButton(text=BTN_INBOX)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_APK)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Menyudan tanlang",
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)


def build_keyboard(msg_id: int, blocked: bool = False, wants_reply: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="💬 Javob berish", callback_data=f"reply:{msg_id}")]]
    if blocked:
        rows.append([InlineKeyboardButton(text="✅ Bloklangan · qaytarish", callback_data=f"unblock:{msg_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Yuboruvchini bloklash", callback_data=f"block:{msg_id}")])
    rows.append([InlineKeyboardButton(text="🖼 Story uchun rasm", callback_data=f"card:{msg_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================ yordamchilar
def format_message(msg: Message) -> str:
    lines = [
        "📩 <b>Yangi anonim xabar</b>",
        "",
        html.escape(msg.text),
        "",
        "━━━━━━━━━━━━━━",
        f"📱 <b>Qurilma:</b> {html.escape(msg.device or '—')}",
        f"🌍 <b>Hudud:</b> {html.escape(msg.region or '—')}",
    ]
    lines += [
        "",
        "↩️ <i>Javob berish: «💬 Javob berish» tugmasini bosing yoki shu xabarga Reply qiling.</i>",
    ]
    return "\n".join(lines)


async def owner_of_chat(chat_id: int) -> User | None:
    async with SessionLocal() as db:
        return (await db.execute(select(User).where(User.telegram_chat_id == chat_id))).scalar_one_or_none()


async def require_owner(message: TgMessage) -> User | None:
    user = await owner_of_chat(message.chat.id)
    if not user:
        await message.answer(
            "🔒 Bu funksiya uchun avval akkauntingizni ulang.\n"
            f"Saytga kiring: {settings.BASE_URL} → «Telegram Botni ulash».",
            reply_markup=main_menu(),
        )
    return user


async def get_owned_message(db: AsyncSession, msg_id: int, chat_id: int) -> tuple[Message, User] | None:
    """Xabar aynan shu Telegram chatga ulangan foydalanuvchiga tegishli ekanini tekshiradi."""
    res = await db.execute(
        select(Message, User)
        .join(User, User.id == Message.user_id)
        .where(Message.id == msg_id, User.telegram_chat_id == chat_id)
    )
    row = res.first()
    return (row[0], row[1]) if row else None


async def notify_owner(db: AsyncSession, user: User, msg: Message) -> bool:
    """Xabarni profil egasining Telegramiga yuboradi. Muvaffaqiyatli bo'lsa True."""
    if not user.telegram_chat_id:
        return False
    try:
        sent = await bot.send_message(
            chat_id=user.telegram_chat_id,
            text=format_message(msg),
            reply_markup=build_keyboard(msg.id, wants_reply=msg.wants_reply),
        )
        msg.tg_message_id = sent.message_id
        await db.commit()
        return True
    except TelegramForbiddenError:
        # Foydalanuvchi botni bloklagan: ulanishni uzamiz, xabarlar kabinetda qoladi
        user.telegram_chat_id = None
        user.connect_token = new_connect_token()
        await db.commit()
        log.info("User %s botni bloklagan, ulanish uzildi", user.id)
    except Exception:
        log.exception("Telegramga yuborib bo'lmadi (user=%s)", user.id)
    return False


async def _refresh_keyboard(call: CallbackQuery, msg_id: int, blocked: bool, wants_reply: bool) -> None:
    try:
        await call.message.edit_reply_markup(reply_markup=build_keyboard(msg_id, blocked=blocked, wants_reply=wants_reply))
    except TelegramBadRequest:
        pass


async def send_link(message: TgMessage, user: User) -> None:
    url = f"{settings.BASE_URL}/u/{user.username}"
    caption = (
        "🔗 <b>Sizning anonim havolangiz</b>\n"
        f"{html.escape(url)}\n\n"
        "Uni Story, bio yoki guruhlarga qo'ying. Kim yozsa, xabar shu yerga tushadi.\n"
        "<i>Pastdagi QR kodni ham ko'rsating: ko'chadagi do'stlar telefonida skanerlaydi.</i>"
    )
    try:
        await message.answer_photo(BufferedInputFile(qr_png(url), filename="qr.png"), caption=caption)
    except Exception:
        log.exception("QR kod yuborilmadi")
        await message.answer(caption)


async def send_inbox(message: TgMessage, user: User) -> None:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(5)
            )
        ).scalars().all()
    if not rows:
        return await message.answer("📭 Hozircha xabar yo'q. Havolangizni ulashing, xabarlar shu yerga tushadi.")
    lines = ["📥 <b>Oxirgi xabarlar</b>"]
    for m in rows:
        dot = "🔴" if not m.is_read else "⚪"
        preview = html.escape(m.text[:120] + ("…" if len(m.text) > 120 else ""))
        if m.reply_text:
            tail = " · ✅ javob yuborilgan"
        elif m.wants_reply:
            tail = " · ↩️ javob kutilmoqda"
        else:
            tail = ""
        lines.append(f"\n{dot} {preview}{tail}")
    lines.append("\n<i>Javob berish uchun xabar bildirishnomasidagi «💬 Javob berish» tugmasini bosing.</i>")
    await message.answer("\n".join(lines))


async def send_stats(message: TgMessage, user: User) -> None:
    async with SessionLocal() as db:
        total = (await db.execute(select(func.count(Message.id)).where(Message.user_id == user.id))).scalar_one()
        unread = (
            await db.execute(
                select(func.count(Message.id)).where(Message.user_id == user.id, Message.is_read.is_(False))
            )
        ).scalar_one()
        replied = (
            await db.execute(
                select(func.count(Message.id)).where(Message.user_id == user.id, Message.reply_text.is_not(None))
            )
        ).scalar_one()
        blocked = (
            await db.execute(select(func.count(BlockedSender.id)).where(BlockedSender.user_id == user.id))
        ).scalar_one()
    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"📨 Jami xabarlar: <b>{total}</b>\n"
        f"🔴 O'qilmagan: <b>{unread}</b>\n"
        f"💬 Javob yuborilgan: <b>{replied}</b>\n"
        f"🚫 Bloklangan yuboruvchilar: <b>{blocked}</b>"
    )


async def send_apk(message: TgMessage) -> None:
    f = apk_file()
    if f:
        try:
            return await message.answer_document(
                FSInputFile(f),
                caption="📲 <b>Android ilova (APK)</b>\nYuklab oling va o'rnating. "
                "Telefon so'rasa, «Noma'lum manbalardan o'rnatish»ga ruxsat bering.",
            )
        except Exception:
            log.exception("APK fayl yuborilmadi, havola bo'lsa shuni yuboramiz")
    link = apk_link()
    if link:
        return await message.answer(f"📲 <b>Android ilova (APK)</b>\n{html.escape(link)}")
    await message.answer("📲 APK hozircha yuklanmagan. Keyinroq qayta urinib ko'ring.")


HELP_TEXT = (
    "<b>Yordam</b>\n"
    "• Saytdagi har bir anonim xabar shu yerga tushadi.\n"
    "• 💬 — javob yozish (yuboruvchi javob kutgan bo'lsa), 🚫 — bloklash, 🖼 — Story rasmi.\n"
    "• 🔗 Mening havolam — havola va QR kod.\n"
    "• 📥 Oxirgi xabarlar — so'nggi 5 ta xabar.\n"
    "• 📊 Statistika — raqamlar.\n"
    "• 📲 APK yuklab olish — Android ilova.\n"
    "• Xabarga <b>Reply</b> qilsangiz ham javob yuboriladi.\n"
    "• /unlink — Telegramni akkauntdan uzish."
)


# ============================================================ /start
@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: TgMessage, command: CommandObject):
    payload = command.args or ""
    if not payload.startswith("connect_"):
        return await start_plain(message)

    token = payload[len("connect_"):]
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.connect_token == token))).scalar_one_or_none()
        if not user:
            return await message.answer(
                "❌ Havola eskirgan yoki allaqachon ishlatilgan.\n"
                "Saytdagi shaxsiy kabinetingizdan «Telegram Botni ulash» tugmasini qayta bosing.",
                reply_markup=main_menu(),
            )

        # Bu Telegram chat boshqa akkauntga ulangan bo'lsa, uni ajratamiz
        others = (
            await db.execute(select(User).where(User.telegram_chat_id == message.chat.id, User.id != user.id))
        ).scalars().all()
        for o in others:
            o.telegram_chat_id = None
            o.connect_token = new_connect_token()
        await db.flush()

        user.telegram_chat_id = message.chat.id
        user.connect_token = None  # bir martalik
        await db.commit()

        unread = (
            await db.execute(
                select(func.count(Message.id)).where(Message.user_id == user.id, Message.is_read.is_(False))
            )
        ).scalar_one()

    text = (
        "🎉 <b>Tabriklaymiz, akkauntingiz ulandi!</b>\n"
        "Endi saytdan kelgan barcha anonim savollar shu yerga keladi.\n\n"
        "Pastdagi menyudan havolangizni oling, xabarlarni ko'ring yoki javob yozing."
    )
    if unread:
        text += f"\n\n📥 Kabinetingizda hali o'qilmagan <b>{unread}</b> ta xabar bor."
    await message.answer(text, reply_markup=main_menu())


@router.message(CommandStart())
async def start_plain(message: TgMessage):
    user = await owner_of_chat(message.chat.id)
    if user:
        await message.answer(
            f"👋 Salom, <b>{html.escape(user.name or user.username)}</b>!\n"
            "Akkauntingiz ulangan. Menyudan kerakli bo'limni tanlang.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer(
            "👋 Salom! Bu bot anonim savollarni sizga yetkazadi.\n\n"
            f"Avval {settings.BASE_URL} saytida Google orqali kiring va kabinetdagi "
            "«Telegram Botni ulash» tugmasini bosing.",
            reply_markup=main_menu(),
        )


# ============================================================ Javob berish (FSM: keyingi matn javob bo'ladi)
@router.callback_query(F.data.startswith("reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext):
    msg_id = int(call.data.split(":", 1)[1])
    async with SessionLocal() as db:
        owned = await get_owned_message(db, msg_id, call.message.chat.id)
    if not owned:
        return await call.answer("Xabar topilmadi", show_alert=True)
    msg, _ = owned

    await state.set_state(ReplyFlow.waiting_text)
    await state.update_data(msg_id=msg_id)
    await call.answer()
    hint = " Oldingi javobingiz yangilanadi." if msg.reply_text else ""
    await call.message.answer(
        f"✍️ Javobingizni yozing (ko'pi bilan {settings.MAX_MESSAGE_LENGTH} belgi).{hint}\n"
        "Yuboruvchi uni saytdagi sahifasida ko'radi.",
        reply_markup=cancel_menu(),
    )


@router.message(ReplyFlow.waiting_text, F.text)
async def on_reply_text(message: TgMessage, state: FSMContext):
    text = message.text.strip()
    if text == BTN_CANCEL or text.startswith("/") or text in MENU_TEXTS:
        await state.clear()
        return await message.answer("❌ Javob bekor qilindi.", reply_markup=main_menu())
    if not text:
        return await message.answer("Javob bo'sh bo'lmasligi kerak.")

    msg_id = (await state.get_data()).get("msg_id")
    async with SessionLocal() as db:
        owned = await get_owned_message(db, msg_id, message.chat.id) if msg_id else None
        if not owned:
            await state.clear()
            return await message.answer("ℹ️ Bu xabarga javob yozib bo'lmaydi.", reply_markup=main_menu())
        msg = owned[0]
        msg.reply_text = text[: settings.MAX_MESSAGE_LENGTH]
        msg.replied_at = utcnow()
        msg.is_read = True
        await db.commit()
    await state.clear()
    await message.answer(
        "✅ Javobingiz saytga yuborildi. Yuboruvchi uni sahifasida ko'radi.", reply_markup=main_menu()
    )


# ============================================================ Admin: hammaga xabar matni (menyudan oldin turadi)
def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admin_ids


class AdminFlow(StatesGroup):
    broadcast_text = State()


@router.message(AdminFlow.broadcast_text, F.text)
async def admin_broadcast_text(message: TgMessage, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    body = message.text.strip()
    if body.startswith("/") or body in MENU_TEXTS:
        await state.clear()
        return await message.answer("❌ Xabar yuborish bekor qilindi.", reply_markup=main_menu())
    if not body or len(body) > 3000:
        return await message.answer("Matn bo'sh bo'lmasligi va 3000 belgidan oshmasligi kerak.")

    await state.update_data(bc_text=body)
    async with SessionLocal() as db:
        n = await _count(db, User, User.telegram_chat_id.is_not(None))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Yuborish ({n} ta)", callback_data="adm:bc_send"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="adm:bc_cancel"),
    ]])
    await message.answer(
        f"📣 <b>Ko'rib chiqing</b>, xabar {n} ta ulangan foydalanuvchiga yuboriladi:\n\n{html.escape(body)}",
        reply_markup=kb,
    )


# ============================================================ menyu va buyruqlar
@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_cmd(message: TgMessage):
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("link"))
@router.message(F.text == BTN_LINK)
async def link_cmd(message: TgMessage):
    user = await require_owner(message)
    if user:
        await send_link(message, user)


@router.message(Command("inbox"))
@router.message(F.text == BTN_INBOX)
async def inbox_cmd(message: TgMessage):
    user = await require_owner(message)
    if user:
        await send_inbox(message, user)


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def stats_cmd(message: TgMessage):
    user = await require_owner(message)
    if user:
        await send_stats(message, user)


@router.message(Command("apk"))
@router.message(F.text == BTN_APK)
async def apk_cmd(message: TgMessage):
    await send_apk(message)


@router.message(Command("unlink"))
async def unlink_cmd(message: TgMessage):
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.telegram_chat_id == message.chat.id))).scalar_one_or_none()
        if not user:
            return await message.answer("Sizning Telegramingiz hech qaysi akkauntga ulanmagan.", reply_markup=main_menu())
        user.telegram_chat_id = None
        user.connect_token = new_connect_token()
        await db.commit()
    await message.answer(
        "🔌 Ulanish uzildi. Xabarlar endi faqat saytdagi kabinetda saqlanadi.", reply_markup=main_menu()
    )


# ============================================================ Reply -> saytga javob
@router.message(F.reply_to_message, F.text)
async def on_reply(message: TgMessage):
    reply_to = message.reply_to_message
    async with SessionLocal() as db:
        res = await db.execute(
            select(Message)
            .join(User, User.id == Message.user_id)
            .where(User.telegram_chat_id == message.chat.id, Message.tg_message_id == reply_to.message_id)
        )
        msg = res.scalar_one_or_none()
        if not msg:
            return await message.reply("ℹ️ Bu anonim xabar emas. Javob berish uchun 💬 tugmasidan foydalaning.")

        updated = msg.reply_text is not None
        msg.reply_text = message.text[: settings.MAX_MESSAGE_LENGTH]
        msg.replied_at = utcnow()
        msg.is_read = True
        await db.commit()

    await message.reply("✅ Javobingiz yangilandi." if updated else "✅ Javobingiz yuborildi. Yuboruvchi uni saytda ko'radi.")


# ============================================================ inline tugmalar
@router.callback_query(F.data.startswith("block:"))
async def cb_block(call: CallbackQuery):
    msg_id = int(call.data.split(":", 1)[1])
    async with SessionLocal() as db:
        owned = await get_owned_message(db, msg_id, call.message.chat.id)
        if not owned:
            return await call.answer("Xabar topilmadi", show_alert=True)
        msg = owned[0]
        await block_sender(db, msg)
        await db.commit()
    await _refresh_keyboard(call, msg_id, blocked=True, wants_reply=msg.wants_reply)
    await call.answer("Yuboruvchi bloklandi 🚫")


@router.callback_query(F.data.startswith("unblock:"))
async def cb_unblock(call: CallbackQuery):
    msg_id = int(call.data.split(":", 1)[1])
    async with SessionLocal() as db:
        owned = await get_owned_message(db, msg_id, call.message.chat.id)
        if not owned:
            return await call.answer("Xabar topilmadi", show_alert=True)
        msg = owned[0]
        await unblock_sender(db, msg)
        await db.commit()
    await _refresh_keyboard(call, msg_id, blocked=False, wants_reply=msg.wants_reply)
    await call.answer("Blokdan chiqarildi")


@router.callback_query(F.data.startswith("card:"))
async def cb_card(call: CallbackQuery):
    msg_id = int(call.data.split(":", 1)[1])
    async with SessionLocal() as db:
        owned = await get_owned_message(db, msg_id, call.message.chat.id)
    if not owned:
        return await call.answer("Xabar topilmadi", show_alert=True)
    msg, user = owned
    await call.answer("Rasm tayyorlanmoqda…")
    png = await asyncio.to_thread(render_story_card, msg.text, user.username)
    await call.message.answer_document(
        BufferedInputFile(png, filename=f"story-{msg.id}.png"),
        caption="🖼 Story tayyor! Yuklab oling va Instagram/Telegram Story ga joylang.",
    )


# ============================================================ Admin panel (faqat ADMIN_IDS)
def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Umumiy statistika", callback_data="adm:stats")],
            [InlineKeyboardButton(text="👥 Oxirgi foydalanuvchilar", callback_data="adm:users")],
            [InlineKeyboardButton(text="🩺 Tizim tekshiruvi", callback_data="adm:health")],
            [InlineKeyboardButton(text="📣 Hammaga xabar yuborish", callback_data="adm:broadcast")],
        ]
    )


async def _count(db: AsyncSession, model, *conds) -> int:
    stmt = select(func.count(model.id))
    if conds:
        stmt = stmt.where(*conds)
    return (await db.execute(stmt)).scalar_one()


@router.message(Command("admin"))
async def admin_cmd(message: TgMessage, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # adminlik yo'q: javob bermaymiz
    await state.clear()
    await message.answer("🛠 <b>Admin panel</b>\nKerakli bo'limni tanlang:", reply_markup=admin_kb())


@router.message(Command("cancel"))
async def cancel_cmd(message: TgMessage, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi.", reply_markup=main_menu())


async def _admin_stats(message: TgMessage) -> None:
    async with SessionLocal() as db:
        users = await _count(db, User)
        linked = await _count(db, User, User.telegram_chat_id.is_not(None))
        msgs = await _count(db, Message)
        day = await _count(db, Message, Message.created_at >= utcnow() - timedelta(hours=24))
        unread = await _count(db, Message, Message.is_read.is_(False))
        waiting = await _count(db, Message, Message.wants_reply.is_(True), Message.reply_text.is_(None))
        replied = await _count(db, Message, Message.reply_text.is_not(None))
        blocked = await _count(db, BlockedSender)
    await message.answer(
        "📊 <b>Umumiy statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🔗 Telegram ulangan: <b>{linked}</b>\n"
        f"📨 Jami xabarlar: <b>{msgs}</b>\n"
        f"🆕 So'nggi 24 soat: <b>{day}</b>\n"
        f"🔴 O'qilmagan: <b>{unread}</b>\n"
        f"↩️ Javob kutilayotgan: <b>{waiting}</b>\n"
        f"💬 Javob yuborilgan: <b>{replied}</b>\n"
        f"🚫 Bloklangan yuboruvchilar: <b>{blocked}</b>"
    )


async def _admin_users(message: TgMessage) -> None:
    async with SessionLocal() as db:
        users = (
            await db.execute(select(User).order_by(User.created_at.desc()).limit(10))
        ).scalars().all()
        lines = ["👥 <b>Oxirgi 10 foydalanuvchi</b>", "<i>Email ko'rsatilmaydi. Yuboruvchilar aniqlanmaydi.</i>", ""]
        for u in users:
            n = await _count(db, Message, Message.user_id == u.id)
            mark = "✅" if u.telegram_chat_id else "⚪"
            lines.append(f"{mark} @{html.escape(u.username)} · {n} xabar · {u.created_at:%Y-%m-%d}")
    if len(lines) == 3:
        lines.append("Hozircha foydalanuvchi yo'q.")
    await message.answer("\n".join(lines))


async def _admin_health(message: TgMessage) -> None:
    lines = ["🩺 <b>Tizim tekshiruvi</b>", ""]
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        lines.append("✅ Ma'lumotlar bazasi ishlayapti")
    except Exception as e:
        lines.append(f"❌ Ma'lumotlar bazasi xatosi: {html.escape(str(e)[:200])}")
    try:
        me = await bot.get_me()
        lines.append(f"✅ Bot ishlayapti: @{html.escape(me.username or '')}")
    except Exception as e:
        lines.append(f"❌ Bot javob bermayapti: {html.escape(str(e)[:200])}")
    lines.append("✅ APK tayyor" if apk_available() else "⚠️ APK sozlanmagan (APK_FILE yoki APK_URL)")
    if "localhost" in settings.BASE_URL:
        lines.append("⚠️ BASE_URL localhost: havola faqat sizning kompyuteringizda ishlaydi")
    else:
        lines.append(f"✅ Sayt manzili: {html.escape(settings.BASE_URL)}")
    lines.append(f"ℹ️ Bot rejimi: {settings.BOT_MODE}")
    lines.append(f"ℹ️ Admin soni: {len(settings.admin_ids)}")
    await message.answer("\n".join(lines))


async def _admin_broadcast_send(message: TgMessage, state: FSMContext) -> None:
    body = (await state.get_data()).get("bc_text")
    await state.clear()
    if not body:
        return await message.answer("Xabar topilmadi. Qaytadan boshlang: /admin")
    async with SessionLocal() as db:
        chats = (
            await db.execute(select(User.telegram_chat_id).where(User.telegram_chat_id.is_not(None)))
        ).scalars().all()
    ok = fail = 0
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, html.escape(body))
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # Telegram limitini oshmaslik uchun
    await message.answer(f"📣 Yuborish tugadi.\n✅ Yetdi: {ok}\n❌ Yetmadi: {fail}")


@router.callback_query(F.data.startswith("adm:"))
async def admin_callbacks(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Ruxsat yo'q", show_alert=True)
    action = call.data.split(":", 1)[1]
    await call.answer()
    if action == "stats":
        await _admin_stats(call.message)
    elif action == "users":
        await _admin_users(call.message)
    elif action == "health":
        await _admin_health(call.message)
    elif action == "broadcast":
        await state.set_state(AdminFlow.broadcast_text)
        await call.message.answer(
            "📣 Hammaga yuboriladigan matnni yozing (oddiy matn).\nBekor qilish: /cancel"
        )
    elif action == "bc_send":
        await _admin_broadcast_send(call.message, state)
    elif action == "bc_cancel":
        await state.clear()
        await call.message.answer("❌ Yuborish bekor qilindi.")


# ============================================================ noma'lum matn
@router.message(F.text)
async def fallback(message: TgMessage):
    user = await owner_of_chat(message.chat.id)
    if user:
        await message.answer("Menyudan tanlang yoki /help ni bosing.", reply_markup=main_menu())
    else:
        await message.answer(
            f"Avval {settings.BASE_URL} saytida ro'yxatdan o'ting va Telegramni ulang.",
            reply_markup=main_menu(),
        )


async def setup_bot_commands() -> None:
    """Telegram '/' menyusi."""
    await bot.set_my_commands(
        [
            BotCommand(command="link", description="Shaxsiy havolam va QR kod"),
            BotCommand(command="inbox", description="Oxirgi xabarlar"),
            BotCommand(command="stats", description="Statistika"),
            BotCommand(command="apk", description="Android APK yuklab olish"),
            BotCommand(command="help", description="Yordam"),
            BotCommand(command="unlink", description="Telegramni uzish"),
        ]
    )

import re
import secrets
import time
from collections import defaultdict, deque

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlockedSender, Message, User

RESERVED_USERNAMES = {"admin", "api", "auth", "u", "static", "telegram", "health", "login", "logout", "me", "root"}
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")


# ---------------------------------------------------------------- foydalanuvchi
def new_connect_token() -> str:
    # token_urlsafe: A-Z a-z 0-9 _ -  (Telegram start parametriga mos, <= 64 belgi)
    return secrets.token_urlsafe(16)


async def generate_unique_username(db: AsyncSession, email: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower())[:14]
    if len(base) < 3:
        base = "user"
    candidate = base
    while candidate in RESERVED_USERNAMES or await get_user_by_username(db, candidate):
        candidate = f"{base}{secrets.randbelow(9000) + 1000}"
    return candidate


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    res = await db.execute(select(User).where(User.username == username.lower()))
    return res.scalar_one_or_none()


# ---------------------------------------------------------------- bloklash
async def is_blocked(db: AsyncSession, user_id: int, session_id: str | None, ip_hash: str | None) -> bool:
    conds = []
    if session_id:
        conds.append(BlockedSender.session_id == session_id)
    if ip_hash:
        conds.append(BlockedSender.ip_hash == ip_hash)
    if not conds:
        return False
    res = await db.execute(select(BlockedSender.id).where(BlockedSender.user_id == user_id, or_(*conds)).limit(1))
    return res.first() is not None


async def block_sender(db: AsyncSession, msg: Message) -> None:
    if await is_blocked(db, msg.user_id, msg.session_id, msg.ip_hash):
        return
    db.add(BlockedSender(user_id=msg.user_id, session_id=msg.session_id, ip_hash=msg.ip_hash))


async def unblock_sender(db: AsyncSession, msg: Message) -> None:
    conds = []
    if msg.session_id:
        conds.append(BlockedSender.session_id == msg.session_id)
    if msg.ip_hash:
        conds.append(BlockedSender.ip_hash == msg.ip_hash)
    if not conds:
        return
    res = await db.execute(select(BlockedSender).where(BlockedSender.user_id == msg.user_id, or_(*conds)))
    for row in res.scalars().all():
        await db.delete(row)


# ---------------------------------------------------------------- rate limit
class RateLimiter:
    """Oddiy xotiradagi cheklovchi. Ko'p serverli muhitda Redis bilan almashtiring."""

    def __init__(self, limit: int, window: int = 60):
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if len(self.hits) > 20000:
            self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v and now - v[-1] <= self.window})
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

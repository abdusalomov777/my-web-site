from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC (SQLite va PostgreSQL da bir xil ishlashi uchun)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Telegramga ulash uchun bir martalik token (ulangandan keyin None bo'ladi)
    connect_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    blocked: Mapped[list["BlockedSender"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    text: Mapped[str] = mapped_column(Text)

    # Yuboruvchi identifikatorlari (faqat bloklash va javob uchun; egasiga HECH QACHON ko'rsatilmaydi)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    wants_reply: Mapped[bool] = mapped_column(Boolean, default=False)

    # Ishora (hint) ma'lumotlari
    device: Mapped[str] = mapped_column(String(80), default="")
    region: Mapped[str] = mapped_column(String(80), default="")

    # Telegramda yuborilgan xabar ID si (Reply ni topish uchun)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")


class BlockedSender(Base):
    __tablename__ = "blocked_senders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="blocked")

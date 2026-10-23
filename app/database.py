from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Jadvallarni yaratadi. Kattaroq loyihada Alembic migratsiyalaridan foydalaning."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

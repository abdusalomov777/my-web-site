import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.apk import apk_file, apk_link
from app.auth import router as auth_router
from app.bot import bot, dp, setup_bot_commands
from app.config import settings
from app.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anonqa")

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    me = await bot.get_me()
    app.state.bot_username = settings.BOT_USERNAME.lstrip("@") or me.username
    log.info("Bot: @%s | rejim: %s", app.state.bot_username, settings.BOT_MODE)

    try:
        await setup_bot_commands()
    except Exception:
        log.exception("Bot buyruqlari o'rnatilmadi")

    polling_task: asyncio.Task | None = None
    if settings.BOT_MODE == "webhook":
        await bot.set_webhook(
            url=f"{settings.BASE_URL}/telegram/webhook",
            secret_token=settings.WEBHOOK_SECRET,
            allowed_updates=dp.resolve_used_update_types(),
        )
    else:
        # Bitta jarayon, bitta event loop: FastAPI va bot birga ishlaydi
        await bot.delete_webhook(drop_pending_updates=False)
        polling_task = asyncio.create_task(
            dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
        )

    try:
        yield
    finally:
        if polling_task:
            with contextlib.suppress(Exception):
                await dp.stop_polling()
            polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await polling_task
        await bot.session.close()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# authlib Google OAuth "state" ni saqlashi uchun
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=settings.COOKIE_SECURE,
    max_age=3600,
)

app.include_router(auth_router)
app.include_router(api_router)


# ---------------------------------------------------------------- sahifalar
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(TEMPLATES / "index.html")


@app.get("/u/{username}", include_in_schema=False)
async def profile_page(username: str):
    return FileResponse(TEMPLATES / "profile.html")


@app.get("/download/apk", include_in_schema=False)
async def download_apk():
    f = apk_file()
    if f:
        return FileResponse(f, media_type="application/vnd.android.package-archive", filename=f.name)
    link = apk_link()
    if link:
        return RedirectResponse(link)
    raise HTTPException(404, "APK hozircha mavjud emas")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- Telegram webhook
@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request):
    if request.headers.get("x-telegram-bot-api-secret-token") != settings.WEBHOOK_SECRET:
        raise HTTPException(403, "Forbidden")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Anonim savol-javob"
    BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str
    COOKIE_SECURE: bool = False
    TRUST_PROXY: bool = False

    DATABASE_URL: str = "sqlite+aiosqlite:///./data.db"

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str

    BOT_TOKEN: str
    BOT_USERNAME: str = ""
    BOT_MODE: str = "polling"  # polling | webhook
    WEBHOOK_SECRET: str = "change-me-webhook-secret"

    # APK: ikkalasidan birini to'ldiring. Fayl bo'lsa, u ustun turadi.
    APK_FILE: str = ""  # serverdagi fayl, masalan /app/data/app.apk (botdan 50 MB gacha yuboriladi)
    APK_URL: str = ""  # yoki tashqi havola (GitHub Releases, Google Drive va h.k.)

    # Admin Telegram ID lari, vergul bilan: 6407109288,123456
    ADMIN_IDS: str = ""

    MAX_MESSAGE_LENGTH: int = 1000
    RATE_LIMIT_PER_MINUTE: int = 5
    JWT_EXPIRE_DAYS: int = 30
    STORY_FONT_PATH: str = ""

    @property
    def admin_ids(self) -> set[int]:
        return {int(x) for x in self.ADMIN_IDS.replace(" ", "").split(",") if x.isdigit()}


settings = Settings()

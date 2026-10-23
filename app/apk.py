"""APK yuklab olish: avval serverdagi fayl, bo'lmasa tashqi havola."""
from pathlib import Path

from app.config import settings


def apk_file() -> Path | None:
    if settings.APK_FILE:
        p = Path(settings.APK_FILE)
        if p.is_file():
            return p
    return None


def apk_link() -> str | None:
    return settings.APK_URL.strip() or None


def apk_available() -> bool:
    return apk_file() is not None or apk_link() is not None

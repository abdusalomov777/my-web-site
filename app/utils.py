import re
from datetime import datetime, timezone

_TZ_RE = re.compile(r"^[A-Za-z_]+(/[A-Za-z_\-+0-9]+){1,2}$")


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def parse_device(user_agent: str) -> str:
    """User-Agent dan qurilma va brauzer turini aniqlaydi (shaxsni oshkor qilmaydi)."""
    ua = (user_agent or "").lower()

    if "iphone" in ua:
        device = "iPhone"
    elif "ipad" in ua:
        device = "iPad"
    elif "android" in ua:
        device = "Android telefon" if "mobile" in ua else "Android planshet"
    elif "windows" in ua:
        device = "Windows kompyuter"
    elif "macintosh" in ua or "mac os x" in ua:
        device = "Mac kompyuter"
    elif "cros" in ua:
        device = "Chromebook"
    elif "linux" in ua:
        device = "Linux kompyuter"
    else:
        device = "Noma'lum qurilma"

    if "instagram" in ua:
        browser = "Instagram"
    elif "telegram" in ua:
        browser = "Telegram"
    elif "fban" in ua or "fbav" in ua:
        browser = "Facebook"
    elif "edg/" in ua or "edga/" in ua or "edgios/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "firefox" in ua or "fxios" in ua:
        browser = "Firefox"
    elif "chrome" in ua or "crios" in ua:
        browser = "Chrome"
    elif "safari" in ua:
        browser = "Safari"
    else:
        browser = ""

    return f"{device} ({browser})" if browser else device


def describe_region(timezone_name: str | None, country_header: str | None = None) -> str:
    """
    Taxminiy hudud. Aniq IP geolokatsiya ishlatilmaydi (maxfiylik uchun):
    - brauzer vaqt mintaqasi (masalan Asia/Tashkent)
    - Cloudflare orqali kelsa CF-IPCountry sarlavhasi
    """
    parts: list[str] = []
    if timezone_name and len(timezone_name) <= 64 and _TZ_RE.match(timezone_name):
        pieces = timezone_name.replace("_", " ").split("/")
        parts.append(f"{pieces[-1]} ({pieces[0]})")
    if country_header and country_header.upper() not in ("", "XX", "T1"):
        parts.append(country_header.upper()[:2])
    return ", ".join(parts) if parts else "Noma'lum hudud"

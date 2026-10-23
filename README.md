# Anonim Savol-Javob Platformasi (Web + Telegram Bot)

FastAPI + aiogram 3 + SQLAlchemy (async). Veb-sayt va bot **bitta jarayonda** ishlaydi.

## Nimalar bor
- Google orqali kirish, shaxsiy havola (`/u/username`) va QR kod
- Ismsiz xabar yuborish; xabar Telegram botga zudlik bilan tushadi
- **Ikki tomonlama javob**: botdagi «💬 Javob berish» tugmasi, Reply yoki saytdagi kabinet — javob ikkala joyda ham ko'rinadi
- Bot menyusi: 🔗 havola/QR, 📥 oxirgi xabarlar, 📊 statistika, 📲 APK, ℹ️ yordam
- APK yuklab olish: botdan va saytdan
- Bloklash, Story rasm, username o'zgartirish

## Struktura
```
anon-qa-v2/
├── app/
│   ├── main.py        # FastAPI, lifespan (bot polling/webhook), sahifalar, /download/apk
│   ├── auth.py        # Google OAuth2 + JWT cookie
│   ├── api.py         # Ochiq API (xabar yuborish, javoblar) va kabinet API
│   ├── bot.py         # aiogram: menyu, javob berish (FSM), havola, bloklash, story
│   ├── apk.py         # APK fayl yoki havola
│   ├── qr.py          # Havola uchun QR (PNG)
│   ├── models.py      # User, Message, BlockedSender
│   ├── database.py    # async engine/session
│   ├── services.py    # bloklash, username, rate limit
│   ├── security.py    # JWT, IP hash
│   ├── storycard.py   # Pillow bilan Story PNG
│   ├── utils.py       # User-Agent / hudud hint
│   └── config.py
├── templates/
│   ├── index.html     # kirish + shaxsiy kabinet
│   └── profile.html   # anonim savol yuborish sahifasi
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Lokal ishga tushirish
1. Python 3.11+ o'rnating, so'ng:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env
   ```
2. **Telegram bot**: @BotFather → `/newbot` → tokenni `.env` dagi `BOT_TOKEN` ga yozing.
3. **Google OAuth**: https://console.cloud.google.com/apis/credentials → *OAuth client ID* (Web application).
   Authorized redirect URI: `http://localhost:8000/auth/callback`.
4. `.env` da `SECRET_KEY` ni uzun tasodifiy qatorga almashtiring.
5. Ishga tushiring: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (faqat **1 worker**).
6. http://localhost:8000 → Google bilan kiring → «Telegram Botni ulash».

## Havolani localhost'dan chiqarish (haqiqiy manzil)
Havola `BASE_URL` dan olinadi. Uni o'zgartirsangiz, sayt, bot va QR hammasi yangi manzilga o'tadi.

**A. Tez test (bir necha daqiqa, uy kompyuteridan):** Cloudflare Tunnel
```bash
cloudflared tunnel --url http://localhost:8000
```
U sizga `https://xxxx.trycloudflare.com` beradi. Shuni `BASE_URL` ga yozing, Google redirect URI'ga `https://xxxx.trycloudflare.com/auth/callback` qo'shing, `BOT_MODE=webhook` qiling. Manzil har safar o'zgaradi, doimiy emas.

**B. Doimiy (tavsiya etiladi):** VPS (masalan Ubuntu) + domen + Docker + Nginx + Let's Encrypt (HTTPS).

**C. Tayyor platforma:** Render, Railway yoki Fly.io. Dockerfile bilan deploy qiling, `.env` o'zgaruvchilarini panelga kiriting, ma'lumot uchun doimiy disk (volume) ulang.

Production uchun `.env`:
```
BASE_URL=https://sizning-domen.uz
COOKIE_SECURE=true
TRUST_PROXY=true
BOT_MODE=webhook
APK_FILE=/app/data/app.apk
```

### Docker bilan
```bash
docker build -t anonqa .
docker run -d -p 8000:8000 --env-file .env -v anonqa_data:/app/data --name anonqa anonqa
# APK ni konteynerga joylash:
docker cp app.apk anonqa:/app/data/app.apk
```
Webhook rejimi Telegram uchun HTTPS talab qiladi. Polling rejimi HTTPS talab qilmaydi.

## APK
- **Variant 1**: APK faylni serverga qo'yasiz, `APK_FILE` ga yo'lini yozasiz. Bot faylni o'zi yuboradi (Telegram limiti 50 MB).
- **Variant 2**: APK ni GitHub Releases yoki Google Drive'ga yuklab, `APK_URL` ga havolasini yozasiz.
- Sayt: `/download/apk`. Bot: 📲 tugmasi yoki `/apk`.

## Production eslatmalar
- PostgreSQL: `pip install asyncpg`, `DATABASE_URL=postgresql+asyncpg://user:pass@host/db`. Katta o'zgarishlar uchun Alembic qo'shing.
- Rate limit hozir xotirada; bir nechta server bo'lsa Redis ishlating.
- Bot holati (javob kutish bosqichi) xotirada saqlanadi; server qayta ishga tushsa, «Javob berish» bosqichi bekor bo'ladi, qayta bosish kifoya.

## Maxfiylik haqida
- IP manzil bazada ochiq saqlanmaydi, faqat tuzlangan SHA-256 hash (bloklash uchun).
- `session_id` bloklash va javobni ko'rsatish uchun saqlanadi; profil egasiga ko'rsatilmaydi.
- Javoblarni yuboruvchi o'zi yozgan brauzerda ko'radi (`localStorage`). Brauzer ma'lumotini tozalasa, ro'yxat ko'rinmay qoladi.
- «Hudud» aniq joylashuv emas: brauzer vaqt mintaqasi va Cloudflare bo'lsa davlat kodi.
- Bloklangan yuboruvchi xabari «muvaffaqiyatli» deb ko'rinadi (shadow-ban), lekin egasiga yetmaydi.

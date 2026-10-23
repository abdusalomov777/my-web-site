FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY templates ./templates

# Ma'lumotlar bazasi va APK shu papkada saqlanadi (volume bilan ulang)
RUN mkdir -p /app/data
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/data.db \
    APK_FILE=/app/data/app.apk

EXPOSE 8000
# Faqat 1 worker: bot polling/webhook bitta jarayonda ishlashi kerak
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

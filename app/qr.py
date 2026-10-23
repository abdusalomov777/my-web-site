"""Shaxsiy havola uchun QR kod (segno: sof Python kutubxona)."""
import io

import segno


def qr_png(data: str) -> bytes:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=10, border=3, dark="#0b0a1a", light="#ffffff")
    return buf.getvalue()

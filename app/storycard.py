"""Instagram/Telegram Story (1080x1920) uchun gradient fonli PNG rasm yaratadi (Pillow)."""
import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import settings

W, H = 1080, 1920

_FONT_CANDIDATES = [
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = ([settings.STORY_FONT_PATH] if settings.STORY_FONT_PATH else []) + _FONT_CANDIDATES
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)  # Pillow >= 10.1


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _background() -> Image.Image:
    stops = [(24, 14, 56), (88, 28, 135), (190, 36, 120)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        c = _lerp(stops[0], stops[1], t * 2) if t < 0.5 else _lerp(stops[1], stops[2], (t - 0.5) * 2)
        d.line([(0, y), (W, y)], fill=c)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-200, 80, 560, 840), fill=(99, 102, 241, 150))
    gd.ellipse((600, 1100, 1300, 1800), fill=(236, 72, 153, 140))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    return Image.alpha_composite(img.convert("RGBA"), glow)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in (text.splitlines() or [""]):
        cur = ""
        for word in para.split(" "):
            test = f"{cur} {word}".strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
                continue
            if cur:
                lines.append(cur)
            while draw.textlength(word, font=font) > max_w and len(word) > 1:
                for i in range(len(word), 0, -1):
                    if draw.textlength(word[:i], font=font) <= max_w:
                        lines.append(word[:i])
                        word = word[i:]
                        break
            cur = word
        lines.append(cur)
    return lines


def render_story_card(text: str, username: str = "") -> bytes:
    canvas = _background()
    draw = ImageDraw.Draw(canvas)

    # shisha (glass) karta
    card = (80, 420, W - 80, 1400)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(card, radius=64, fill=(255, 255, 255, 40), outline=(255, 255, 255, 110), width=3)
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)

    # sarlavha
    title_font = _font(46)
    draw.text((W / 2, 300), "Menga anonim xabar yuborishdi", font=title_font, fill=(255, 255, 255, 235), anchor="mm")

    # matn: shrift kattaligini avtomatik moslash
    pad = 70
    max_w = (card[2] - card[0]) - pad * 2
    max_h = (card[3] - card[1]) - pad * 2
    size, lines, font = 78, [], None
    while size >= 34:
        font = _font(size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) * int(size * 1.35) <= max_h:
            break
        size -= 4
    line_h = int(size * 1.35)
    max_lines = max_h // line_h
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip()[:-1] + "…"

    block_h = len(lines) * line_h
    y = card[1] + ((card[3] - card[1]) - block_h) / 2
    for ln in lines:
        draw.text((W / 2, y), ln, font=font, fill=(255, 255, 255, 255), anchor="ma")
        y += line_h

    # pastki havola
    if username:
        pill_font = _font(44)
        label = f"Sen ham yoz: /u/{username}"
        tw = draw.textlength(label, font=pill_font)
        x0, x1 = W / 2 - tw / 2 - 48, W / 2 + tw / 2 + 48
        draw.rounded_rectangle((x0, 1530, x1, 1630), radius=50, fill=(255, 255, 255, 235))
        draw.text((W / 2, 1580), label, font=pill_font, fill=(88, 28, 135, 255), anchor="mm")

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()

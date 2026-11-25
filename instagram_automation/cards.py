import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

LABELS = {"economia": "ECONOMIA", "tecnologia": "TECNOLOGIA", "ciencia": "CIÊNCIA", "impacto_publico": "EM PAUTA", "geral": "DESTAQUE"}


def font(size, bold=False):
    windir = Path(os.environ.get("WINDIR", "C:/Windows"))
    paths = [windir / "Fonts" / ("arialbd.ttf" if bold else "arial.ttf"),
             Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]
    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def wrap(draw, text, face, width):
    lines = []
    current = ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=face) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = ""
            # Palavras/URLs muito longas não atravessam as margens.
            for char in word:
                if current and draw.textlength(current + char, font=face) > width:
                    lines.append(current)
                    current = ""
                current += char
    if current:
        lines.append(current)
    return lines


def render_card(article, headline, config, path, photo_path=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1080, 1350), "#06142b")
    draw = ImageDraw.Draw(image)
    for y in range(1350):
        color = (6 + int(y / 180), 18 + int(y / 95), 39 + int(y / 70))
        draw.line((0, y, 1080, y), fill=color)
    draw.ellipse((790, -170, 1300, 340), outline="#15334d", width=3)
    draw.ellipse((860, -100, 1230, 270), outline="#15334d", width=2)
    draw.ellipse((690, 650, 1240, 1200), outline="#132f49", width=2)
    draw.ellipse((755, 715, 1175, 1135), outline="#132f49", width=2)
    draw.ellipse((820, 780, 1110, 1070), outline="#132f49", width=2)
    draw.line((748, 932, 855, 817, 1002, 900, 1143, 751), fill="#15334d", width=2)
    for x, y in ((748, 932), (855, 817), (1002, 900), (1143, 751)):
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#08a5c5")
    for row in range(8):
        for column in range(7):
            x = 936 + column * 20
            y = 282 + row * 20
            draw.ellipse((x, y, x + 3, y + 3), fill="#173750")
    brand = str(config["project"].get("name", "Your Project"))[:32]
    draw.text((72, 55), brand, font=font(35, True), fill="#f5f7fa")

    kicker = str(config["project"].get("tagline", "YOUR CONTENT, IN CONTEXT"))[:48]
    draw.text((72, 112), kicker, font=font(19, True), fill="#69d9ef")
    label = LABELS.get(article["topic"], "DESTAQUE")
    draw.rounded_rectangle((72, 270, 72 + int(draw.textlength(label, font=font(22, True))) + 44, 324), radius=9, fill="#08c8e8")
    draw.text((94, 282), label, font=font(22, True), fill="#06142b")
    if photo_path:
        with Image.open(photo_path) as photo:
            photo = ImageOps.fit(photo.convert("RGB"), (936, 250), method=Image.Resampling.LANCZOS)
        image.paste(photo, (72, 350))
        y = 630
        max_height = 400
    else:
        y = 382
        max_height = 570
    for size in range(78, 31, -2):
        face = font(size, True)
        lines = wrap(draw, headline, face, 886)
        if len(lines) * (size + 18) <= max_height:
            break
    draw.rounded_rectangle((72, y, 82, min(y + len(lines) * (size + 18) - 12, 1000)), radius=5, fill="#08c8e8")
    for line in lines:
        draw.text((108, y), line, font=face, fill="#f5f7fa")
        y += size + 18
    draw.line((72, 1058, 1008, 1058), fill="#26465e", width=2)
    date = datetime.fromisoformat(article["published_at"]).astimezone(ZoneInfo(config["project"]["timezone"]))
    draw.text((72, 1083), "FONTE / REFERÊNCIA", font=font(17, True), fill="#69d9ef")
    draw.text((72, 1112), article["source"][:45], font=font(25, True), fill="#f5f7fa")
    draw.text((72, 1158), date.strftime("%d.%m.%Y  •  %H:%M"), font=font(21), fill="#bacbd5")
    draw.line((72, 1214, 1008, 1214), fill="#26465e", width=2)
    draw.text((72, 1248), config["project"]["handle"][:45], font=font(25, True), fill="white")
    draw.text((690, 1253), "CONTENT THAT CONNECTS.", font=font(17, True), fill="#bacbd5")
    image.save(path, "JPEG", quality=92, optimize=True)
    return path

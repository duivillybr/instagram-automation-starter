import asyncio
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageOps

from .cards import LABELS, font, wrap

log = logging.getLogger(__name__)


def download_feed_image(url, feed, destination):
    allowed = set(feed.get("media_domains", []))
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if urlsplit(url).scheme != "https" or not any(host == item or host.endswith("." + item) for item in allowed):
        raise ValueError("Imagem RSS fora dos domínios permitidos")
    try:
        with requests.get(url, headers={"User-Agent": "InstagramAutomationStarter/0.1"}, timeout=(10, 30), stream=True,
                           allow_redirects=False) as response:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("Imagem RSS redirecionou para um endereço não verificado")
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                raise ValueError("O link RSS não retornou uma imagem")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 12_000_000:
                    raise ValueError("Imagem RSS excede 12 MB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        with Image.open(destination) as image:
            if image.width * image.height > 30_000_000:
                raise ValueError("Imagem RSS excede a resolução permitida")
            image.verify()
        return destination
    except requests.RequestException:
        raise ValueError("Falha ao baixar imagem RSS") from None
    except (OSError, Image.DecompressionBombError):
        destination.unlink(missing_ok=True)
        raise ValueError("Arquivo de imagem RSS inválido") from None


def render_reel_video(frame_path, audio_path, video_path, music_path, background_music_volume=0.24):
    if not shutil.which("ffmpeg"):
        raise ValueError("Para criar Reels, instale ffmpeg no servidor")
    if not music_path.is_file() or music_path.stat().st_size < 1000:
        raise ValueError("Background music file not found; configure media.background_music_file")

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "30",
        "-i", str(frame_path), "-i", str(audio_path), "-stream_loop", "-1", "-i", str(music_path),
        "-filter_complex",
        f"[1:a]volume=1.0[voice];[2:a]volume={background_music_volume},afade=t=in:st=0:d=0.8[music];"
        "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.97[aout]",
        "-map", "0:v:0", "-map", "[aout]", "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x06142b,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-ar", "48000", "-shortest", "-movflags", "+faststart", str(video_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Falha ao montar o vídeo narrado com trilha") from None
    if not video_path.is_file() or video_path.stat().st_size < 10_000:
        raise ValueError("Vídeo gerado inválido")
    return video_path


def render_reel_frame(card_path, frame_path, article, headline, body, config, source_photo_path=None):
    """Renderiza uma arte editorial nativa 9:16 para ocupar a tela do Reel."""
    width, height = 1080, 1920
    canvas = Image.new("RGB", (width, height), "#06142b")
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        color = (6 + int(y / 220), 18 + int(y / 120), 39 + int(y / 85))
        draw.line((0, y, width, y), fill=color)

    # Grafismos sutis repetem a identidade das artes 4:5 sem criar barras vazias.
    draw.ellipse((900, -150, 1410, 360), outline="#15334d", width=3)
    draw.ellipse((970, -80, 1340, 290), outline="#15334d", width=2)
    draw.ellipse((700, 1160, 1260, 1720), outline="#132f49", width=2)
    draw.ellipse((770, 1230, 1190, 1650), outline="#132f49", width=2)
    for row in range(6):
        for column in range(6):
            x, y = 930 + column * 18, 250 + row * 18
            draw.ellipse((x, y, x + 3, y + 3), fill="#173750")

    project_name = str(config["project"].get("name", "Your Project"))[:32]
    draw.text((72, 165), project_name, font=font(38, True), fill="#f5f7fa")
    header_right = 1008
    kicker = str(config["project"].get("tagline", "YOUR CONTENT, IN CONTEXT"))[:48]
    kicker_font = font(19, True)
    draw.text((header_right - draw.textlength(kicker, font=kicker_font), 245),
              kicker, font=kicker_font, fill="#69d9ef")
    label = LABELS.get(article.get("topic"), "DESTAQUE")
    label_font = font(22, True)
    label_width = int(draw.textlength(label, font=label_font)) + 44
    label_left = header_right - label_width
    draw.rounded_rectangle((label_left, 280, header_right, 336), radius=9, fill="#08c8e8")
    draw.text((label_left + 22, 293), label, font=label_font, fill="#06142b")

    photo_path = Path(source_photo_path) if source_photo_path else Path(card_path).parent / "imagem-fonte.jpg"
    photo_top, photo_height = 420, 430
    if photo_path.is_file():
        with Image.open(photo_path) as source_photo:
            photo = ImageOps.fit(source_photo.convert("RGB"), (936, photo_height), method=Image.Resampling.LANCZOS)
        photo_layer = Image.new("RGB", photo.size, "#06142b")
        photo_layer.paste(photo)
        mask = Image.new("L", photo.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width - 1, photo.height - 1), radius=18, fill=255)
        canvas.paste(photo_layer, (72, photo_top), mask)
    else:
        draw.rounded_rectangle((72, photo_top, 1008, photo_top + photo_height), radius=18,
                               fill="#0b1c35", outline="#15334d", width=3)
        draw.ellipse((390, photo_top + 45, 690, photo_top + 345), outline="#08a5c5", width=3)
        draw.ellipse((450, photo_top + 105, 630, photo_top + 285), outline="#69d9ef", width=2)
        draw.text((356, photo_top + 370), "INFORMAÇÃO EM CONTEXTO",
                  font=font(24, True), fill="#69d9ef")

    title_top = 910
    for size in range(66, 39, -2):
        title_font = font(size, True)
        lines = wrap(draw, headline, title_font, 870)
        line_height = size + 15
        if len(lines) * line_height <= 440:
            break
    title_bottom = title_top + len(lines) * line_height - 10
    draw.rounded_rectangle((72, title_top, 83, min(title_bottom, 1450)), radius=5, fill="#08c8e8")
    y = title_top
    for line in lines:
        draw.text((108, y), line, font=title_font, fill="#f5f7fa")
        y += line_height

    summary_font = font(29)
    summary = " ".join(str(body).split())
    summary_lines = wrap(draw, summary, summary_font, 900)
    while len(summary_lines) > 3 and summary:
        summary = summary.rsplit(" ", 1)[0].rstrip(" ,;:")
        summary_lines = wrap(draw, summary + "…", summary_font, 900)
    summary_top = y + 26
    for line in summary_lines[:3]:
        draw.text((108, summary_top), line, font=summary_font, fill="#bacbd5")
        summary_top += 40

    draw.line((72, 1510, 1008, 1510), fill="#26465e", width=2)
    draw.text((72, 1540), "SOURCE / REFERENCE", font=font(17, True), fill="#69d9ef")
    draw.text((72, 1572), str(article.get("source", config["project"].get("name", "Your Project")))[:45],
              font=font(25, True), fill="#f5f7fa")
    try:
        date = datetime.fromisoformat(article["published_at"]).astimezone(ZoneInfo(config["project"]["timezone"]))
        date_label = date.strftime("%d.%m.%Y  •  %H:%M")
    except (KeyError, TypeError, ValueError):
        date_label = ""
    draw.text((72, 1620), date_label, font=font(21), fill="#bacbd5")
    draw.line((72, 1685, 1008, 1685), fill="#26465e", width=2)

    handle = config["project"]["handle"][:45]
    draw.text((72, 1745), handle, font=font(25, True), fill="white")
    badge_box = (480, 1728, 930, 1796)
    draw.rounded_rectangle(badge_box, radius=18, fill="#08c8e8")
    # Ícone de alto-falante desenhado para não depender de glifos externos.
    icon_draw = ImageDraw.Draw(canvas)
    icon_draw.polygon(((505, 1751), (517, 1751), (533, 1740), (533, 1784),
                       (517, 1773), (505, 1773)), fill="#06142b")
    icon_draw.arc((525, 1748, 553, 1776), start=-55, end=55, fill="#06142b", width=4)
    badge_text = "CONTENT WITH AUDIO"
    draw.text((562, 1748), badge_text, font=font(20, True), fill="#06142b")

    frame_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(frame_path, "JPEG", quality=92, optimize=True)
    return frame_path


def create_reel(card_path, article, headline, body, folder, config):
    if not shutil.which("ffmpeg"):
        raise ValueError("Para criar Reels, instale ffmpeg no servidor")
    try:
        import edge_tts
    except ImportError:
        raise ValueError("Para criar narração, instale as dependências edge-tts") from None

    folder.mkdir(parents=True, exist_ok=True)
    audio_path = folder / "narracao.mp3"
    frame_path = folder / "reel-frame.jpg"
    video_path = folder / "reel.mp4"
    music_path = Path(config["media"].get("background_music_file", "assets/audio/background-loop.wav"))
    if not music_path.is_absolute():
        music_path = config["project"]["root"] / music_path
    narration = f"{headline}. {body} Fonte: {article['source']}."
    narration = " ".join(narration.split())[:900]

    async def speak():
        await edge_tts.Communicate(narration, voice=config["media"]["voice"], rate="-5%").save(str(audio_path))

    try:
        asyncio.run(speak())
    except Exception as exc:
        raise ValueError(f"Não foi possível gerar a narração: {type(exc).__name__}") from None
    if not audio_path.is_file() or audio_path.stat().st_size < 500:
        raise ValueError("A narração veio vazia; vídeo não preparado")

    render_reel_frame(card_path, frame_path, article, headline, body, config, folder / "imagem-fonte.jpg")

    render_reel_video(frame_path, audio_path, video_path, music_path, config["media"]["background_music_volume"])
    log.info("Reel e narração preparados: %s", video_path.name)
    return video_path, audio_path

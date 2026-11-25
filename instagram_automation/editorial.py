"""Create original posts supplied by the account operator."""
import hashlib
import json

from .cards import font, wrap
from .db import now_iso

from PIL import Image, ImageDraw, ImageOps


def render_editorial(headline, config, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1080, 1350), "#0b1325")
    draw = ImageDraw.Draw(image)
    for radius in (210, 300, 390):
        draw.ellipse((920-radius, 115-radius, 920+radius, 115+radius), outline="#203d51", width=2)
    draw.rectangle((72, 76, 84, 160), fill="#59e0c0")
    project_name = str(config["project"].get("name", "Your Project"))[:28]
    draw.text((72, 85), project_name, font=font(38, True), fill="white")
    draw.rounded_rectangle((72, 276, 390, 332), radius=8, fill="#59e0c0")
    welcome = "bem-vindos" in headline.casefold()
    draw.text((94, 288), "WELCOME" if welcome else "FEATURED CONTENT", font=font(20, True), fill="#0b1325")
    for size in range(88, 31, -2):
        face = font(size, True)
        lines = wrap(draw, headline, face, 936)
        if len(lines) * (size + 18) <= 450:
            break
    y = 406
    for line in lines:
        draw.text((72, y), line, font=face, fill="#f5f7fa")
        y += size + 18
    draw.rectangle((72, 888, 178, 896), fill="#59e0c0")
    tagline = str(config["project"].get("tagline", "Your content, in context"))[:42]
    draw.text((72, 944), tagline, font=font(40, True), fill="#59e0c0")
    if welcome:
        draw.text((72, 1038), "Estamos apenas começando!", font=font(31), fill="#bacbd5")
    draw.line((72, 1203, 1008, 1203), fill="#385165", width=2)
    draw.text((72, 1252), config["project"]["handle"], font=font(28, True), fill="white")
    image.save(path, "JPEG", quality=92, optimize=True)
    return path


def create_editorial(db, config, headline, caption, image_source=None):
    headline, caption = headline.strip(), caption.strip()
    if not headline or len(headline) > 180:
        raise ValueError("A manchete deve ter de 1 a 180 caracteres")
    if not caption or len(caption) > 2200:
        raise ValueError("A legenda deve ter de 1 a 2.200 caracteres")
    digest = hashlib.sha256((headline + "\n" + caption).encode())
    custom_image = None
    if image_source is not None:
        digest.update(image_source.read_bytes())
        with Image.open(image_source) as original:
            custom_image = ImageOps.exif_transpose(original).convert("RGB")
        width, height = custom_image.size
        if width < 320 or not 0.8 <= width / height <= 1.91:
            raise ValueError("A imagem precisa ter largura mínima de 320 px e proporção entre 4:5 e 1,91:1")
        if width > 1440:
            custom_image.thumbnail((1440, 1800), Image.Resampling.LANCZOS)
    fingerprint = "editorial:" + digest.hexdigest()
    existing = db.conn.execute("SELECT id,status,publish_started_at FROM articles WHERE fingerprint=?", (fingerprint,)).fetchone()
    if existing and (existing["status"] != "failed" or existing["publish_started_at"]):
        return existing[0]
    date = now_iso()
    # Não fica elegível para envio até a criação dos arquivos terminar e haver aprovação.
    if existing:
        article_id = existing["id"]
    else:
        with db.conn:
            cursor = db.conn.execute("""INSERT INTO articles
            (fingerprint,title_key,source,url,title,summary,published_at,collected_at,
             score,topic,reason,status,kind,headline,body,caption)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                fingerprint, fingerprint, config["project"]["name"], "", headline, caption, date, date,
                100, "institucional", "Publicação própria fornecida pelo responsável", "failed", "editorial",
                headline, "", caption))
            article_id = cursor.lastrowid
    folder = config["project"]["output"] / f"post-{article_id:05d}"
    image = folder / "card.jpg"
    if custom_image is None:
        render_editorial(headline, config, image)
    else:
        folder.mkdir(parents=True, exist_ok=True)
        custom_image.save(image, "JPEG", quality=95, optimize=True)
    if image.stat().st_size > 8_000_000:
        raise ValueError("A imagem JPEG excede o tamanho máximo de 8 MB")
    (folder / "legenda.txt").write_text(caption, encoding="utf-8")
    (folder / "post.json").write_text(json.dumps({
        "id": article_id, "kind": "editorial", "author": config["project"]["name"],
        "headline": headline, "caption": caption, "created_at": date,
        "image_source": str(image_source) if image_source else None,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    db.update(article_id, status="ready", image_path=str(image.resolve()), image_url=None,
              container_id=None, publish_started_at=None, error=None)
    return article_id

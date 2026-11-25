import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .cards import render_card
from .config import required_env
from .db import now_iso
from .editor import caption_for, edit
from .media import create_reel, download_feed_image
from .feeds import collect, evaluate, event_identifier
from .providers import Instagram, PublishUncertain, upload_card, upload_media

log = logging.getLogger(__name__)


def recover(db):
    for article in db.rows("SELECT * FROM articles WHERE status='publishing'"):
        # Se houve intenção persistida de enviar media_publish, exige reconciliação.
        state = "uncertain" if article["publish_started_at"] else "approved"
        db.update(article["id"], status=state, error="Execução interrompida; verifique o container antes de continuar")


def expire(db, config, now):
    cutoff = (now - timedelta(hours=config["editorial"]["max_age_hours"])).isoformat()
    with db.conn:
        db.conn.execute("UPDATE articles SET status='expired', error='Notícia fora da janela editorial' WHERE kind='news' AND status IN ('pending','ready','approved') AND published_at < ?", (cutoff,))


def refilter(db, config):
    """Reavalia a fila e remove aprovações; não altera histórico nem rejeições manuais."""
    feeds = {feed["name"]: feed for feed in config["feeds"] if feed.get("enabled", True)}
    now = datetime.now(timezone.utc)
    count = 0
    for article in db.rows("SELECT * FROM articles WHERE kind='news' AND status IN ('pending','filtered','ready','approved','expired')"):
        source = article["source"]
        feed = feeds.get(source)
        if feed:
            score, topic, reason = evaluate(article["title"], article["summary"], datetime.fromisoformat(article["published_at"]), feed, config, now)
        else:
            score, topic, reason = 0, "geral", "Fonte desabilitada ou removida"
        # Não promove títulos marcados como duplicados pelo coletor.
        duplicate = article["reason"] == "Título semelhante a notícia já recebida"
        state = "pending" if score >= config["editorial"]["minimum_score"] and not duplicate else "filtered"
        db.update(article["id"], status=state, score=score, topic=topic, source=source,
                  reason=article["reason"] if duplicate else reason, error=None,
                  headline=None, body=None, caption=None, image_path=None,
                  image_url=None, container_id=None, publish_started_at=None,
                  video_path=None, audio_path=None, media_kind="image")
        count += 1
    return count


def prepare(db, config, limit):
    expire(db, config, datetime.now(timezone.utc))
    pending_news = db.conn.execute("SELECT COUNT(*) FROM articles WHERE kind='news' AND status='pending'").fetchone()[0]
    if not pending_news:
        return 0
    if not config["ai"].get("enabled", False):
        log.info("Curadoria por IA desativada; itens RSS não serão preparados")
        return 0
    active = db.conn.execute("SELECT COUNT(*) FROM articles WHERE status IN ('ready','approved','publishing','uncertain')").fetchone()[0]
    limit = max(0, min(limit, config["schedule"]["max_queue"] - active))
    articles = db.rows("SELECT * FROM articles WHERE kind='news' AND status='pending' ORDER BY score DESC,published_at DESC LIMIT ?", (limit,))
    prepared_news = db.conn.execute(
        "SELECT COUNT(*) FROM articles WHERE kind='news' AND status IN ('ready','approved','publishing','published')"
    ).fetchone()[0]
    prepared = 0
    for article in articles:
        try:
            if config["media"]["require_source_image"] and not article.get("source_image_url"):
                db.update(article["id"], status="filtered", reason="Fonte RSS não forneceu imagem original em domínio permitido")
                continue
            result = edit(article, config)
            if not result["publish"] or (config["ai"]["enabled"] and result["relevance"] < config["ai"]["minimum_relevance"]):
                db.update(article["id"], status="filtered", reason="IA: " + result["reason"])
                continue
            caption = caption_for(article, result, config)
            folder = config["project"]["output"] / f"post-{article['id']:05d}"
            media_mode = config["media"]["mode"]
            sequence = prepared_news + prepared + 1
            media_kind = ("reel" if sequence % config["media"]["reel_every"] == 0 else "image") if media_mode == "mixed" else media_mode
            photo = None
            if config["media"]["use_feed_images"] and article.get("source_image_url"):
                feed = next((item for item in config["feeds"] if item["name"] == article["source"]), {})
                try:
                    photo = download_feed_image(article["source_image_url"], feed, folder / "imagem-fonte.jpg")
                except ValueError as exc:
                    if config["media"]["require_source_image"]:
                        raise ValueError(f"Imagem original indisponível: {exc}") from None
                    log.warning("Imagem RSS da notícia %s ignorada: %s", article["id"], exc)
            if config["media"]["require_source_image"] and photo is None:
                raise ValueError("A notícia não tem foto original da fonte")
            image = render_card(article, result["headline"], config, folder / "card.jpg", photo)
            video = audio = None
            if media_kind == "reel":
                try:
                    video, audio = create_reel(image, article, result["headline"], result["body"],
                                               folder, config)
                except ValueError as exc:
                    if media_mode != "mixed":
                        raise
                    log.warning("Reel da notícia %s indisponível; seguirá como imagem: %s", article["id"], exc)
                    media_kind = "image"
            (folder / "legenda.txt").write_text(caption, encoding="utf-8")
            (folder / "content.json").write_text(json.dumps({
                "id": article["id"], "source": article["source"], "url": article["url"],
                "original_title": article["title"], "published_at": article["published_at"],
                "score": article["score"], "rule_reason": article["reason"],
                "ai_enabled": config["ai"]["enabled"], "editorial": result,
                "media_kind": media_kind, "has_source_image": bool(photo),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            db.update(article["id"], status="ready", headline=result["headline"], body=result["body"],
                      caption=caption, image_path=str(image.resolve()),
                      video_path=str(video.resolve()) if video else None,
                      audio_path=str(audio.resolve()) if audio else None,
                      media_kind=media_kind, error=None)
            prepared += 1
            log.info("Post %s preparado: %s", article["id"], article["title"])
        except (ValueError, OSError) as exc:
            # Sem fallback silencioso em falha de IA, sem gastar indefinidamente a API.
            db.update(article["id"], status="failed", error=str(exc))
            log.warning("Post %s não preparado: %s", article["id"], exc)
    return prepared


def within_window(config, now):
    local = now.astimezone(ZoneInfo(config["project"]["timezone"]))
    schedule = config["schedule"]
    start, end = schedule["start_hour"], schedule["end_hour"]
    in_window = start <= local.hour < end if start < end else local.hour >= start or local.hour < end
    return in_window


def allowed(db, config, now):
    schedule = config["schedule"]
    if not within_window(config, now):
        return False, "Fora do horário de publicação"
    if db.conn.execute("SELECT 1 FROM articles WHERE status IN ('uncertain','publishing') LIMIT 1").fetchone():
        return False, "Há publicação pendente de reconciliação"
    published = db.rows("SELECT posted_at FROM articles WHERE status='published' AND posted_at IS NOT NULL ORDER BY posted_at DESC")
    dates = [datetime.fromisoformat(item["posted_at"]) for item in published]
    if dates and now - dates[0] < timedelta(minutes=schedule["publish_minutes"]):
        return False, "Aguardando intervalo entre publicações"
    if schedule["daily_limit"] and sum(date > now - timedelta(hours=24) for date in dates) >= schedule["daily_limit"]:
        return False, "Limite de publicações nas últimas 24 horas atingido"
    return True, "Horário disponível"


def publish_next(db, config, now=None):
    now = now or datetime.now(timezone.utc)
    expire(db, config, now)
    ok, reason = allowed(db, config, now)
    if not ok:
        log.info(reason)
        return reason
    states = ("approved",) if config["publishing"]["require_approval"] else ("ready", "approved")
    placeholders = ",".join("?" for _ in states)
    articles = db.rows(f"SELECT * FROM articles WHERE status IN ({placeholders}) ORDER BY score DESC,published_at DESC", states)
    feeds = {feed["name"]: feed for feed in config["feeds"] if feed.get("enabled", True)}
    event_cutoff = now - timedelta(hours=config["editorial"]["event_cooldown_hours"])
    recent_events = db.rows("SELECT title FROM articles WHERE kind='news' AND status='published' AND posted_at >= ?",
                            (event_cutoff.isoformat(),))
    event_keys = {key for item in recent_events if (key := event_identifier(item["title"]))}
    eligible = []
    for candidate in articles:
        if candidate["kind"] == "editorial":
            # Comunicados próprios preservam a legenda do autor e sempre exigem aprovação.
            if candidate["status"] == "approved":
                eligible.append(candidate)
            continue
        feed = feeds.get(candidate["source"])
        score, topic, rule_reason = (evaluate(candidate["title"], candidate["summary"], datetime.fromisoformat(candidate["published_at"]), feed, config, now)
                                     if feed else (0, "geral", "Fonte desabilitada ou removida"))
        if score < config["editorial"]["minimum_score"]:
            db.update(candidate["id"], status="filtered", reason=rule_reason)
        else:
            if candidate["kind"] == "news" and config["media"]["require_source_image"]:
                card_path = Path(candidate["image_path"]) if candidate.get("image_path") else None
                source_photo = card_path.parent / "imagem-fonte.jpg" if card_path else None
                if not source_photo or not source_photo.is_file():
                    db.update(candidate["id"], status="failed", error="Foto original da notícia ausente no card")
                    continue
            event_key = event_identifier(candidate["title"]) or event_identifier(candidate["headline"] or "")
            if event_key and event_key in event_keys:
                continue
            eligible.append(candidate)
    articles = eligible
    if not articles:
        return "Nenhum post elegível. Prepare notícias e, se necessário, aprove-as."
    article = articles[0]
    if article["kind"] == "news" and not config["ai"].get("enabled", False):
        return "Post RSS bloqueado: ative a curadoria por IA antes de publicar itens do feed. Posts manuais aprovados continuam disponíveis."
    if config["publishing"]["dry_run"]:
        message = f"SIMULAÇÃO: post {article['id']} seria publicado; nenhum upload ou envio foi feito."
        log.info(message)
        return message
    # Preflight antes de alterar a fila ou acessar serviços pagos.
    required_env("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    media_kind = article.get("media_kind") or "image"
    if article["kind"] == "news" and config["media"]["mode"] == "reel":
        audio_path = Path(article["audio_path"]) if article.get("audio_path") else None
        if media_kind != "reel" or not audio_path or not audio_path.is_file() or audio_path.stat().st_size < 500:
            db.update(article["id"], status="failed", error="Notícia sem áudio válido; prepare novamente antes de publicar")
            return f"Post {article['id']}: falta áudio válido; prepare novamente."
    media_path = Path(article["video_path"]) if media_kind == "reel" and article.get("video_path") else Path(article["image_path"])
    if not media_path.is_file():
        raise ValueError("Mídia ausente; execute retry e prepare novamente")
    instagram = Instagram(config)
    db.update(article["id"], status="publishing", error=None)
    try:
        image_url = article["image_url"] or (upload_media(media_path, article["id"], media_kind)
                                               if media_kind == "reel" else upload_card(media_path, article["id"]))
        db.update(article["id"], image_url=image_url)
        if article["container_id"]:
            container_id = article["container_id"]
        elif media_kind == "reel":
            container_id = instagram.create(image_url, article["caption"], article["headline"], "reel")
        else:
            container_id = instagram.create(image_url, article["caption"], article["headline"])
        db.update(article["id"], container_id=container_id)
        state = instagram.wait_ready(container_id)
        if state == "PUBLISHED":
            db.update(article["id"], status="published", posted_at=now_iso(), error=None)
            return "Container já publicado; não foi enviado novamente."
        # Commit antes da chamada externa: um crash nunca causa reenvio silencioso.
        db.update(article["id"], publish_started_at=now_iso())
        media_id = instagram.publish(container_id)
        db.update(article["id"], status="published", media_id=media_id, posted_at=now_iso(), error=None)
        log.info("Post %s publicado. Media ID: %s", article["id"], media_id)
        return f"Post {article['id']} publicado. Media ID: {media_id}"
    except PublishUncertain as exc:
        db.update(article["id"], status="uncertain", error=str(exc))
        log.warning("Post %s com resultado incerto. Execute reconcile; não haverá reenvio automático.", article["id"])
        return "Publicação com resultado incerto. Execute reconcile."
    except (ValueError, OSError) as exc:
        # Antes de media_publish, repetir a preparação não duplica post no perfil.
        latest = db.get(article["id"])
        state = "uncertain" if latest["publish_started_at"] else "failed"
        db.update(article["id"], status=state, error=str(exc))
        log.warning("Falha ao publicar post %s: %s", article["id"], exc)
        return f"Post {article['id']}: {state}. Consulte show."


def reconcile(db, config, article_id):
    article = db.get(article_id)
    if article["status"] != "uncertain" or not article["container_id"]:
        raise ValueError("A notícia precisa estar em uncertain e ter container_id")
    state = Instagram(config).status(article["container_id"])
    if state == "PUBLISHED":
        db.update(article_id, status="published", posted_at=now_iso(), error=None)
        return "Publicação confirmada pela Meta; marcada como publicada."
    return f"Meta: {state}. Fila continua bloqueada. Confira o perfil; depois use resolve ID published ou resolve ID discard."


def tick(db, config):
    recover(db)
    now = datetime.now(timezone.utc)
    if not within_window(config, now):
        return "Fora do horário; coleta, IA e publicação suspensas"
    last = db.get_meta("last_collect")
    if not last or now - datetime.fromisoformat(last) >= timedelta(minutes=config["schedule"]["collect_minutes"]):
        collect(db, config)
    prepare(db, config, config["schedule"]["prepare_per_tick"])
    return publish_next(db, config)

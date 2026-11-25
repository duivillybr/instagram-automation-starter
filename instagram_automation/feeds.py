import calendar
import hashlib
import html
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from .db import now_iso

log = logging.getLogger(__name__)

class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class ImageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            src = dict(attrs).get("src")
            if src:
                self.links.append(src)


def clean(value):
    parser = TextOnly()
    parser.feed(str(value))
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def normalize(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"\w+", value))


def matches(text, term):
    return f" {normalize(term)} " in f" {normalize(text)} "


def canonical_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Link inválido")
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query)), ""))


def event_identifier(title):
    """Find recurring event codes such as COP31 or G20 in a headline."""
    match = re.search(r"\b(?:cop\s*-?\s*\d{1,3}|g\s*-?\s*(?:20|7|8))\b", str(title), re.IGNORECASE)
    return re.sub(r"\s+", "", normalize(match.group())) if match else None


def extract_image_url(entry, feed):
    allowed = set(feed.get("media_domains", []))
    if not allowed:
        return None
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        candidates.extend(item.get("url", "") for item in entry.get(key, []) if isinstance(item, dict))
    for item in entry.get("links", []):
        if item.get("rel") == "enclosure" and str(item.get("type", "")).startswith("image/"):
            candidates.append(item.get("href", ""))
    for item in entry.get("content", []):
        parser = ImageLinks()
        parser.feed(str(item.get("value", "")))
        candidates.extend(parser.links)
    parser = ImageLinks()
    parser.feed(str(entry.get("summary", "")))
    candidates.extend(parser.links)
    for candidate in candidates:
        try:
            parts = urlsplit(candidate)
            host = (parts.hostname or "").lower().rstrip(".")
            if (parts.scheme == "https" and not parts.username and not parts.password
                    and (host in allowed or any(host.endswith("." + domain) for domain in allowed))):
                return candidate
        except ValueError:
            continue
    return None


def evaluate(title, summary, published, feed, config, now):
    editorial = config["editorial"]
    text = f"{title} {summary}"
    age = now - published
    if age > timedelta(hours=editorial["max_age_hours"]) or age < -timedelta(minutes=15):
        return 0, "geral", "Data fora da janela editorial"
    blocked = [term for term in editorial["blocked_terms"] if matches(text, term)]
    if blocked:
        return 0, "geral", "Termo bloqueado: " + ", ".join(blocked)
    if editorial["required_terms"] and not any(matches(text, term) for term in editorial["required_terms"]):
        return 0, "geral", "Sem termo obrigatório"
    title_hits = {topic: [term for term in terms if matches(title, term)] for topic, terms in editorial["topics"].items()}
    topic_hits = {topic: [term for term in terms if matches(text, term)] for topic, terms in editorial["topics"].items()}
    topic = max(title_hits, key=lambda item: (len(title_hits[item]), len(topic_hits[item])), default="geral")
    hits = topic_hits.get(topic, [])
    if not title_hits.get(topic):
        return 0, "geral", "Manchete fora dos temas escolhidos"
    # A pontuação é uma heurística editorial, não uma medida de veracidade.
    region_group = feed.get("region_group")
    priority_region = feed.get("priority_region")
    configured_priorities = editorial.get("priority_regions", [])
    priority_region = next((region for region in configured_priorities
                            if normalize(region) == normalize(priority_region or feed.get("name", ""))),
                           priority_region)
    regional_bonus = 4 if priority_region in configured_priorities else 2 if region_group in ("Norte", "Nordeste") else 0
    score = (feed.get("weight", 1) + min(6, len(title_hits[topic]) * 3)
             + min(2, len(hits) - len(title_hits[topic]))
             + (1 if age <= timedelta(hours=6) else 0) + regional_bonus)
    reason = "Termos: " + ", ".join(hits)
    if priority_region in configured_priorities:
        reason += f"; prioridade regional {priority_region} (+{regional_bonus})"
    elif regional_bonus:
        reason += f"; prioridade regional {region_group} (+{regional_bonus})"
    return score, topic, reason


def ingest(db, entries, feed, config, now=None):
    now = now or datetime.now(timezone.utc)
    added = 0
    recent = db.rows("SELECT title_key FROM articles WHERE collected_at >= ? AND status != 'filtered'", ((now - timedelta(hours=config["editorial"]["max_age_hours"])).isoformat(),))
    keys = [item["title_key"] for item in recent]
    cooldown_cutoff = (now - timedelta(hours=config["editorial"]["event_cooldown_hours"])).isoformat()
    recent_events = db.rows("""SELECT title FROM articles WHERE kind='news' AND
        ((status='published' AND posted_at >= ?) OR
         (status IN ('pending','ready','approved','publishing','uncertain') AND published_at >= ?))""",
        (cooldown_cutoff, cooldown_cutoff))
    event_keys = {key for item in recent_events if (key := event_identifier(item["title"]))}
    for entry in entries:
        title = clean(entry.get("title", ""))[:500]
        summary = clean(entry.get("summary", ""))[:5000]
        date = entry.get("published_parsed") or entry.get("updated_parsed")
        if not title or not date:
            continue # Não inventa data de notícia sem data no feed.
        try:
            published = datetime.fromtimestamp(calendar.timegm(date), timezone.utc)
            url = canonical_url(entry.get("link", ""))
        except (ValueError, OverflowError, TypeError):
            continue
        host = urlsplit(url).hostname
        if not any(host == domain or host.endswith("." + domain) for domain in feed["domains"]):
            continue
        title_key = normalize(title)
        fingerprint = hashlib.sha256(url.encode()).hexdigest()
        if db.conn.execute("SELECT 1 FROM articles WHERE fingerprint=? OR title_key=?", (fingerprint, title_key)).fetchone():
            continue
        source = feed["name"]
        scoring_feed = feed
        score, topic, reason = evaluate(title, summary, published, scoring_feed, config, now)
        status = "pending" if score >= config["editorial"]["minimum_score"] else "filtered"
        event_key = event_identifier(title)
        if status == "pending" and event_key and event_key in event_keys:
            status, reason = "filtered", f"Evento {event_key.upper()} já coberto na janela de diversidade"
        if status == "pending" and any(SequenceMatcher(None, title_key, key).ratio() >= config["editorial"]["similarity_threshold"] for key in keys):
            status, reason = "filtered", "Título semelhante a notícia já recebida"
        image_url = extract_image_url(entry, feed)
        if status == "pending" and config["media"]["require_source_image"] and not image_url:
            status, reason = "filtered", "Fonte RSS não forneceu imagem original em domínio permitido"
        with db.conn:
            db.conn.execute("""INSERT INTO articles
                (fingerprint,title_key,source,url,title,summary,published_at,collected_at,score,topic,reason,status,source_image_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, title_key, source, url, title, summary,
                published.isoformat(), now.isoformat(), score, topic, reason, status, image_url))
        if status == "pending":
            keys.append(title_key)
            if event_key:
                event_keys.add(event_key)
        added += 1
    return added


def collect(db, config):
    count = 0
    for feed in config["feeds"]:
        if not feed.get("enabled", True):
            continue
        try:
            with requests.get(feed["url"], headers={"User-Agent": "InstagramAutomationStarter/0.1 RSS Reader"}, timeout=(10, 30), stream=True) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 5_000_000:
                        raise ValueError("Feed maior que 5 MB")
            parsed = feedparser.parse(bytes(content))
            if not parsed.entries:
                raise ValueError("Feed sem notícias ou inválido")
            added = ingest(db, parsed.entries[:200], feed, config)
            count += added
            db.set_meta("feed:" + feed["name"], now_iso())
            log.info("%s: %s novas entradas", feed["name"], added)
        except (requests.RequestException, ValueError):
            log.warning("Falha ao coletar %s; verifique conexão/endereço do RSS", feed["name"])
    db.set_meta("last_collect", now_iso())
    return count

import os
import re
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def load_config(path="config.toml"):
    path = Path(path).resolve()
    load_dotenv(path.parent / ".env", override=False)
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    config["root"] = path.parent
    ZoneInfo(config["project"]["timezone"])
    for key in ("database", "output"):
        config["project"][key] = path.parent / config["project"][key]
    schedule = config["schedule"]
    for key in ("collect_minutes", "publish_minutes", "poll_seconds", "max_queue", "prepare_per_tick"):
        if not isinstance(schedule[key], int) or isinstance(schedule[key], bool) or schedule[key] < 1:
            raise ValueError(f"schedule.{key} deve ser inteiro positivo")
    if not isinstance(schedule["daily_limit"], int) or isinstance(schedule["daily_limit"], bool) or schedule["daily_limit"] < 0:
        raise ValueError("schedule.daily_limit deve ser 0 (sem limite) ou inteiro positivo")
    for key in ("start_hour", "end_hour"):
        if not isinstance(schedule[key], int) or not 0 <= schedule[key] <= 23:
            raise ValueError(f"schedule.{key} deve estar entre 0 e 23")
    if schedule["start_hour"] == schedule["end_hour"]:
        raise ValueError("A janela de publicação deve ter início e fim diferentes")
    for key in ("dry_run", "require_approval"):
        if not isinstance(config["publishing"][key], bool):
            raise ValueError(f"publishing.{key} deve ser booleano")
    # Configurações antigas sem esse campo continuam na modalidade original.
    mode = config["publishing"].setdefault("login_mode", "instagram")
    if mode not in ("unified", "instagram"):
        raise ValueError('publishing.login_mode deve ser "unified" ou "instagram"')
    if not re.fullmatch(r"v\d+\.0", config["publishing"]["api_version"]):
        raise ValueError("api_version inválida; use por exemplo v27.0")
    editorial = config["editorial"]
    if editorial["max_age_hours"] <= 0 or not 0 <= editorial["similarity_threshold"] <= 1:
        raise ValueError("Idade máxima ou limiar de similaridade inválido")
    editorial.setdefault("event_cooldown_hours", 24)
    if not isinstance(editorial["event_cooldown_hours"], int) or isinstance(editorial["event_cooldown_hours"], bool) or not 1 <= editorial["event_cooldown_hours"] <= 72:
        raise ValueError("editorial.event_cooldown_hours deve ser inteiro entre 1 e 72")
    if len(editorial["hashtags"]) > 10 or any(not re.fullmatch(r"\w+", tag) for tag in editorial["hashtags"]):
        raise ValueError("Use até 10 hashtags, sem #, espaços ou pontuação")
    if not 0 <= config["ai"]["minimum_relevance"] <= 10:
        raise ValueError("ai.minimum_relevance deve estar entre 0 e 10")
    if not isinstance(config["ai"].get("enabled"), bool):
        raise ValueError("ai.enabled deve ser booleano")
    if config["ai"].get("provider", "openai") not in ("openai", "codex"):
        raise ValueError("ai.provider deve ser openai ou codex")
    media = config.setdefault("media", {"mode": "image", "use_feed_images": False,
                                        "require_source_image": False, "voice": "pt-BR-AntonioNeural"})
    media.setdefault("mode", "image")
    media.setdefault("use_feed_images", False)
    media.setdefault("require_source_image", False)
    media.setdefault("voice", "pt-BR-AntonioNeural")
    media.setdefault("background_music_volume", 0.24)
    if media["mode"] not in ("image", "reel", "mixed") or not isinstance(media["use_feed_images"], bool):
        raise ValueError('media.mode deve ser "image", "reel" ou "mixed" e use_feed_images deve ser booleano')
    if not isinstance(media["require_source_image"], bool):
        raise ValueError("media.require_source_image deve ser booleano")
    if media["require_source_image"] and not media["use_feed_images"]:
        raise ValueError("media.use_feed_images deve estar ativo quando require_source_image for verdadeiro")
    volume = media["background_music_volume"]
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 1:
        raise ValueError("media.background_music_volume deve estar entre 0.0 e 1.0")
    media.setdefault("reel_every", 3)
    if not isinstance(media["reel_every"], int) or isinstance(media["reel_every"], bool) or media["reel_every"] < 1:
        raise ValueError("media.reel_every deve ser inteiro positivo")
    if not re.fullmatch(r"pt-BR-[A-Za-z]+Neural", media["voice"]):
        raise ValueError("media.voice deve ser uma voz neural pt-BR compatível")
    return config


def required_env(*names):
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError("Preencha no .env: " + ", ".join(missing))
    return [os.environ[name] for name in names]

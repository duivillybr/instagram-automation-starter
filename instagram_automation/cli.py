import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .cards import render_card
from .config import load_config
from .db import Database, now_iso
from .editorial import create_editorial
from .feeds import collect
from .providers import Instagram
from .service import prepare, publish_next, reconcile, recover, refilter, tick, within_window


def parser():
    app = argparse.ArgumentParser(description="Instagram Automation Starter — content queue and publishing")
    app.add_argument("--config", default="config.toml", help="Arquivo TOML (padrão: config.toml)")
    commands = app.add_subparsers(dest="command", required=True)
    for name, help_text in (("collect", "Collect configured RSS feeds"), ("tick", "Run one scheduling cycle"),
                            ("run", "Iniciar agendador contínuo"), ("doctor", "Verificar configuração sem enviar dados"),
                            ("demo", "Generate a sample card without external services"),
                            ("instagram-info", "Consultar ID e usuário do Instagram usando o token do .env"),
                            ("refilter", "Reavaliar fila com filtros atuais e remover aprovações anteriores")):
        commands.add_parser(name, help=help_text)
    commands.choices["run"].add_argument("--exit-outside-window", action="store_true", help="Encerrar fora da janela; usado com timers da VM")
    item = commands.add_parser("prepare", help="Preparar cards e legendas")
    item.add_argument("--limit", type=int, default=3)
    item = commands.add_parser("create-post", help="Preparar uma publicação própria com legenda fornecida")
    item.add_argument("--headline", required=True)
    item.add_argument("--caption-file", type=Path, required=True)
    item.add_argument("--image", type=Path, help="Imagem própria; convertida para JPEG para a API")
    item = commands.add_parser("queue", help="Listar fila")
    item.add_argument("--status")
    item.add_argument("--limit", type=int, default=30)
    for name in ("show", "approve", "reject", "retry", "reconcile"):
        item = commands.add_parser(name)
        item.add_argument("id", type=int)
    item = commands.add_parser("resolve", help="Resolver manualmente resultado incerto após conferir perfil")
    item.add_argument("id", type=int)
    item.add_argument("outcome", choices=("published", "discard"))
    commands.add_parser("publish", help="Publicar próximo elegível, respeitando simulação, intervalo e limites")
    return app


def execute(args, config, db):
    command = args.command
    if command == "doctor":
        print(f"Instagram Automation | dry_run={config['publishing']['dry_run']} | approval_required={config['publishing']['require_approval']}")
        print("Modalidade Instagram: " + config["publishing"]["login_mode"])
        daily_limit = config["schedule"]["daily_limit"] or "sem limite diário"
        print(f"Publicação a cada {config['schedule']['publish_minutes']} min; {daily_limit} em 24h")
        print(f"Janela: {config['schedule']['start_hour']}h–{config['schedule']['end_hour']}h ({config['project']['timezone']})")
        print("IA: " + (config["ai"].get("provider", "openai") if config["ai"]["enabled"] else "desabilitada; curadoria por regras"))
        required = ["INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"]
        if config["ai"]["enabled"] and config["ai"].get("provider", "openai") == "openai":
            required.append("OPENAI_API_KEY")
        missing = [name for name in required if not os.getenv(name)]
        print("Credenciais ausentes: " + (", ".join(missing) or "nenhuma (validade ainda não verificada)"))
        print("As credenciais não foram enviadas nem validadas remotamente.")
    elif command == "collect":
        print(f"{collect(db, config)} novas entradas recebidas. Use queue para ver as selecionadas.")
    elif command == "refilter":
        print(f"{refilter(db, config)} entradas reavaliadas. Execute prepare e revise novamente.")
    elif command == "instagram-info":
        result = Instagram(config, require_user=False).account_info()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif command == "prepare":
        if args.limit < 1:
            raise ValueError("--limit deve ser positivo")
        print(f"{prepare(db, config, args.limit)} posts preparados.")
    elif command == "create-post":
        article_id = create_editorial(db, config, args.headline, args.caption_file.read_text(encoding="utf-8-sig"), args.image)
        print(f"Post próprio {article_id}. Use show para revisar e approve para aprovar.")
    elif command == "queue":
        query, params = "SELECT id,status,score,source,title FROM articles", []
        if args.status:
            query += " WHERE status=?"
            params.append(args.status)
        else:
            query += " WHERE status != 'filtered'"
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(args.limit, 500)))
        rows = db.rows(query, params)
        for row in rows:
            print(f"{row['id']:>4} | {row['status']:<10} | {row['score']:>2} | {row['source']} | {row['title']}")
        if not rows:
            print("Fila vazia.")
    elif command == "show":
        print(json.dumps(db.get(args.id), ensure_ascii=False, indent=2))
    elif command == "approve":
        if db.get(args.id)["status"] != "ready":
            raise ValueError("Somente posts ready podem ser aprovados")
        db.update(args.id, status="approved")
        print("Post aprovado. O envio seguirá o agendador e a configuração de simulação.")
    elif command == "reject":
        if db.get(args.id)["status"] not in ("pending", "ready", "approved", "failed"):
            raise ValueError("Este estado não permite rejeição")
        db.update(args.id, status="rejected")
        print("Post rejeitado.")
    elif command == "retry":
        article = db.get(args.id)
        if article["kind"] == "editorial":
            raise ValueError("Para posts próprios, revise os arquivos ou crie uma nova versão com create-post")
        if article["status"] not in ("failed", "ready", "approved"):
            raise ValueError("Retry permitido somente para failed, ready ou approved; uncertain exige reconcile")
        db.update(args.id, status="pending", image_url=None, container_id=None, publish_started_at=None, error=None)
        print("Post voltou a pending; execute prepare. A aprovação anterior foi removida.")
    elif command == "resolve":
        if db.get(args.id)["status"] != "uncertain":
            raise ValueError("Resolve é exclusivo para publicação com resultado uncertain")
        if args.outcome == "published":
            db.update(args.id, status="published", posted_at=now_iso(), error="Confirmado manualmente pelo operador")
        else:
            db.update(args.id, status="rejected", error="Descartado manualmente; sem reenvio")
        print("Resultado registrado. A fila pode continuar.")
    elif command == "reconcile":
        print(reconcile(db, config, args.id))
    elif command == "publish":
        print(publish_next(db, config))
    elif command == "tick":
        print(tick(db, config))
    elif command == "demo":
        article = {"topic": "product", "source": "SAMPLE CONTENT", "published_at": now_iso()}
        path = config["project"]["output"] / "demo" / "card.jpg"
        render_card(article, "Your content, in context", config, path)
        print(f"Demonstração visual (não entra na fila): {path}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args()
    db = None
    try:
        config = load_config(args.config)
        db = Database(config["project"]["database"])
        logfile = RotatingFileHandler(db.path.parent / "instagram_automation.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(logfile)
        if args.command == "run":
            logging.info("Agendador iniciado. Ctrl+C para encerrar. Configuração recarregada a cada ciclo.")
            while True:
                config = load_config(args.config)
                if args.exit_outside_window and not within_window(config, datetime.now(timezone.utc)):
                    logging.info("Fora da janela; processo encerrado até o próximo início programado.")
                    return
                if config["project"]["database"] != db.path:
                    raise ValueError("Para alterar o banco de dados, reinicie o agendador")
                try:
                    with db.lock():
                        logging.info(tick(db, config))
                except (ValueError, OSError, RuntimeError) as exc:
                    logging.error("Ciclo interrompido: %s", exc)
                time.sleep(config["schedule"]["poll_seconds"])
        else:
            with db.lock():
                recover(db)
                execute(args, config, db)
    except KeyboardInterrupt:
        print("\nAgendador encerrado.")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if db:
            db.close()

"""Curadoria via Codex CLI autenticado com a conta do proprietário."""
import json
import os
import subprocess
import tempfile
from pathlib import Path


def codex_edit(article, config, schema, instructions):
    # Não repassa credenciais do Instagram/Cloudinary/OpenAI ao subprocesso.
    allowed = ("PATH", "HOME", "CODEX_HOME", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    with tempfile.TemporaryDirectory(prefix="instagram-editor-") as folder:
        root = Path(folder)
        schema_path = root / "schema.json"
        output = root / "result.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        command = [config["ai"].get("codex_binary", "codex"), "exec", "--ignore-user-config",
                   "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                   "--color", "never", "--output-schema", str(schema_path),
                   "--output-last-message", str(output), "--cd", str(root)]
        if config["ai"].get("codex_model"):
            command += ["--model", config["ai"]["codex_model"]]
        command += ["-"]
        prompt = instructions + "\nNão use ferramentas, terminal ou rede. Apenas devolva o JSON editorial.\nDADOS RSS:\n"
        prompt += json.dumps({k: article[k] for k in ("title", "summary", "source", "published_at")}, ensure_ascii=False)
        try:
            result = subprocess.run(command, input=prompt, text=True, encoding="utf-8", capture_output=True,
                                    env=environment, cwd=root, timeout=300, check=False)
            if result.returncode != 0 or not output.exists():
                raise ValueError("Codex não concluiu a curadoria; verifique login e limites da conta")
            data = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            raise ValueError("Falha ao executar Codex ou resposta incompleta") from None
    if (type(data.get("publish")) is not bool or type(data.get("relevance")) is not int
            or not 0 <= data["relevance"] <= 10
            or any(not isinstance(data.get(key), str) for key in ("headline", "body", "reason"))):
        raise ValueError("Resposta editorial do Codex inválida")
    if data["publish"] and (not data["headline"].strip() or len(data["headline"]) > 130 or len(data["body"]) > 450):
        raise ValueError("Texto do Codex fora dos limites editoriais")
    return data

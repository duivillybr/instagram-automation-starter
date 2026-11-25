import hashlib
import os
import re
import time

import requests

from .config import required_env


class ProviderError(ValueError):
    pass


class PublishUncertain(ProviderError):
    pass


def upload_media(path, article_id, media_kind="image"):
    cloud, key, secret = required_env("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", cloud):
        raise ProviderError("CLOUDINARY_CLOUD_NAME inválido")
    params = {"public_id": f"instagram-automation/{article_id}-{path.stem}", "timestamp": str(int(time.time()))}
    raw = "&".join(f"{name}={value}" for name, value in sorted(params.items()))
    signature = hashlib.sha1((raw + secret).encode()).hexdigest()
    try:
        resource_type = "video" if media_kind == "reel" else "image"
        with path.open("rb") as stream:
            response = requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/{resource_type}/upload",
                                     data={**params, "api_key": key, "signature": signature},
                                     files={"file": (path.name, stream, "video/mp4" if resource_type == "video" else "image/jpeg")}, timeout=(10, 120))
        if not response.ok:
            raise ProviderError(f"Upload da mídia falhou: HTTP {response.status_code}")
        url = response.json().get("secure_url", "")
        if not url.startswith("https://"):
            raise ProviderError("Hospedagem não retornou URL pública HTTPS")
        return url
    except requests.RequestException:
        raise ProviderError("Falha de conexão com hospedagem de mídia") from None


def upload_card(path, article_id):
    return upload_media(path, article_id, "image")


class Instagram:
    def __init__(self, config, require_user=True):
        self.token, = required_env("INSTAGRAM_ACCESS_TOKEN")
        self.user_id = os.getenv("INSTAGRAM_USER_ID", "")
        if require_user and not self.user_id.isdigit():
            raise ProviderError("INSTAGRAM_USER_ID deve ser o ID numérico retornado pela API")
        self.login_mode = config["publishing"].get("login_mode", "instagram")
        hosts = {"unified": "graph.facebook.com", "instagram": "graph.instagram.com"}
        if self.login_mode not in hosts:
            raise ProviderError("Modalidade de login inválida")
        self.base = f"https://{hosts[self.login_mode]}/" + config["publishing"]["api_version"]
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def request(self, method, endpoint, **kwargs):
        # Não usa retries automáticos em POST nem inclui tokens nos logs/URLs.
        try:
            response = self.session.request(method, f"{self.base}/{endpoint}", timeout=(10, 40), **kwargs)
            if not response.ok:
                try:
                    error = response.json().get("error", {})
                    code, subcode = error.get("code", "?"), error.get("error_subcode", "?")
                except ValueError:
                    code, subcode = "?", "?"
                raise ProviderError(f"Meta HTTP {response.status_code}, código {code}/{subcode}. Verifique token, permissões e conta.")
            return response.json()
        except requests.RequestException:
            raise ProviderError("Falha de conexão com a Meta") from None
        except ValueError as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Resposta inválida da Meta") from None

    def account_info(self):
        if self.login_mode == "unified":
            # /me é o usuário de sistema nesta modalidade, não a conta do Instagram.
            result = self.request("GET", "me/assigned_instagram_accounts",
                                  params={"fields": "id,username", "limit": 100})
            accounts = result.get("data")
            if not isinstance(accounts, list):
                raise ProviderError("Meta não retornou a lista de contas atribuídas ao token")
            # URLs de paginação podem incluir tokens. Nunca as exibimos no terminal.
            return {"accounts": [{"id": item.get("id"), "username": item.get("username")}
                                  for item in accounts],
                    "has_more": bool(result.get("paging", {}).get("next"))}
        result = self.request("GET", "me", params={"fields": "user_id,username"})
        return {key: result[key] for key in ("id", "user_id", "username", "data") if key in result}

    def create(self, media_url, caption, headline, media_kind="image"):
        data = {"caption": caption}
        if media_kind == "reel":
            data.update({"media_type": "REELS", "video_url": media_url, "share_to_feed": "true"})
        else:
            data.update({"image_url": media_url, "alt_text": headline[:1000]})
        data = self.request("POST", f"{self.user_id}/media", data=data)
        if not data.get("id"):
            raise ProviderError("Meta não retornou container_id")
        return data["id"]

    def status(self, container_id):
        state = self.request("GET", container_id, params={"fields": "status_code,status"}).get("status_code")
        if state not in ("FINISHED", "PUBLISHED", "ERROR", "EXPIRED", "IN_PROGRESS"):
            raise ProviderError("Status de container ausente ou desconhecido")
        return state

    def wait_ready(self, container_id):
        for attempt in range(6):
            state = self.status(container_id)
            if state in ("FINISHED", "PUBLISHED"):
                return state
            if state in ("ERROR", "EXPIRED"):
                raise ProviderError(f"Container Meta em estado {state}")
            if attempt < 5:
                time.sleep(60)
        raise ProviderError("Container ainda processando; reenvio não executado")

    def publish(self, container_id):
        try:
            result = self.request("POST", f"{self.user_id}/media_publish", data={"creation_id": container_id})
            if not result.get("id"):
                raise ProviderError("Meta não retornou media_id")
            return result["id"]
        except ProviderError as exc:
            # Mesmo um erro HTTP pode acontecer depois de a postagem entrar no ar.
            raise PublishUncertain(str(exc)) from None

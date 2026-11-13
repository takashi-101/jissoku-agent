"""手元で動くLLM（Ollama）を呼ぶ。接続先は許可したホストに限り、外部のAIサービスは呼ばない。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

OLLAMA_URL = os.environ.get("JISSOKU_OLLAMA_URL", "http://127.0.0.1:11434")
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("JISSOKU_LLM_ALLOWED_HOSTS", "127.0.0.1,localhost,::1").split(",") if h.strip()}


class EndpointNotAllowed(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 転送先が外部でも追いかけない
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _endpoint(path: str) -> str:
    u = urlparse(OLLAMA_URL)
    if u.scheme not in ("http", "https") or u.hostname not in ALLOWED_HOSTS:
        raise EndpointNotAllowed(f"許可していない接続先です：{OLLAMA_URL}（許可：{sorted(ALLOWED_HOSTS)}）")
    return OLLAMA_URL.rstrip("/") + path


def chat(model: str, system: str, user: str, timeout: int = 300) -> str:
    body = json.dumps({
        "model": model, "stream": False, "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode("utf-8")
    # 接続先は _endpoint で許可ホスト・http(s)に限定している
    req = urllib.request.Request(_endpoint("/api/chat"), data=body, headers={"Content-Type": "application/json"})  # noqa: S310
    with _OPENER.open(req, timeout=timeout) as res:  # nosec B310
        return json.loads(res.read().decode("utf-8"))["message"]["content"]


def available(model: str | None) -> bool:
    if not model:
        return False
    try:
        with _OPENER.open(_endpoint("/api/tags"), timeout=3) as res:  # nosec B310
            names = [m.get("name", "") for m in json.loads(res.read().decode("utf-8")).get("models", [])]
        return any(n == model or n.split(":")[0] == model for n in names)
    except (urllib.error.URLError, OSError, ValueError, EndpointNotAllowed):
        return False

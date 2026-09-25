"""Minimal Ollama client, restricted to models that run on this machine.

Refuses to run:
- when the base URL is not loopback, a private address or host.docker.internal (unless llm_allow_remote);
- for Ollama cloud-offloaded models (":cloud"/"-cloud" names, or no local weights), since those send data off-machine.
The client never gets tools and never sees credentials. Its only output is JSON validated against a schema.
"""
import ipaddress
import json
from urllib.parse import urlparse

import httpx

from ..config import get_settings

LOCAL_HOSTNAMES = {"localhost", "host.docker.internal", "ollama"}


class LLMUnavailable(RuntimeError):
    pass


def host_is_local(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def looks_cloud(name: str) -> bool:
    n = name.lower()
    return n.endswith(":cloud") or n.endswith("-cloud") or ":cloud-" in n


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        if not s.llm_allow_remote and not host_is_local(self.base_url):
            raise LLMUnavailable(f"refusing non-local LLM endpoint {urlparse(self.base_url).hostname!r}")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout or s.llm_timeout_seconds, transport=transport,
                                 follow_redirects=False)

    def local_models(self) -> list[dict]:
        """Models with weights on this machine. Cloud-offloaded models are excluded."""
        try:
            r = self.http.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Ollama not reachable at {self.base_url}: {type(e).__name__}") from e
        out = []
        for m in r.json().get("models", []):
            name = m.get("name", "")
            details = m.get("details") or {}
            local = (not looks_cloud(name)) and (m.get("size") or 0) > 0 and bool(details.get("format"))
            if local or get_settings().llm_allow_remote:
                out.append({"name": name, "size_gb": round((m.get("size") or 0) / 1e9, 1),
                            "parameters": details.get("parameter_size"), "family": details.get("family"), "local": local})
        return out

    def ensure_local(self, model: str) -> None:
        if get_settings().llm_allow_remote:
            return
        if looks_cloud(model) or model not in {m["name"] for m in self.local_models()}:
            raise LLMUnavailable(f"model {model!r} is not a locally installed model")

    def chat_json(self, model: str, system: str, user: str, schema: dict, temperature: float = 0.2) -> dict:
        self.ensure_local(model)
        body = {"model": model, "stream": False, "think": False, "format": schema,
                "options": {"temperature": temperature, "num_ctx": 8192},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = self.http.post("/api/chat", json=body)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMUnavailable(f"LLM call failed: {type(e).__name__}: {str(e)[:200]}") from e

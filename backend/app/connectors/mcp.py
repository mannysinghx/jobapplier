"""Minimal MCP client (Streamable HTTP transport, JSON-RPC 2.0) for calling a provider's official tools.

Only tools/call is used, with fixed tool names chosen in code. Tool descriptions returned by the server are
never executed or fed to a model. Responses may be plain JSON or an SSE stream of `data:` lines.
"""
import itertools
import json

import httpx

from .base import ConnectorError

PROTOCOL_VERSION = "2025-06-18"


class MCPClient:
    def __init__(self, client: httpx.Client, endpoint: str):
        if not endpoint.startswith("https://"):
            raise ValueError("MCP endpoint must be https")
        self.client, self.endpoint = client, endpoint
        self.session_id: str | None = None
        self._ids = itertools.count(1)
        self._initialized = False

    def _post(self, payload: dict) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            r = self.client.post(self.endpoint, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise ConnectorError(f"network error: {type(e).__name__}") from e
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            raise ConnectorError("rate limited (429)", retry_after=float(ra) if ra and ra.isdigit() else 60.0)
        if r.status_code in (401, 403, 404):
            raise ConnectorError(f"HTTP {r.status_code} from MCP endpoint", permanent=True)
        if r.status_code >= 400:
            raise ConnectorError(f"HTTP {r.status_code} from MCP endpoint")
        if len(r.content) > 10 * 1024 * 1024:
            raise ConnectorError("response too large", permanent=True)
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid[:200]
        if "id" not in payload or not r.content:
            return None
        return self._parse(r)

    @staticmethod
    def _parse(r: httpx.Response) -> dict:
        text = r.text
        if "text/event-stream" in r.headers.get("content-type", ""):
            msgs = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
            if not msgs:
                raise ConnectorError("empty SSE response")
            text = msgs[-1]
        try:
            return json.loads(text)
        except ValueError as e:
            raise ConnectorError("invalid JSON-RPC response") from e

    def initialize(self) -> None:
        if self._initialized:
            return
        resp = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize",
                           "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": {"name": "jobApplier", "version": "0.1"}}})
        if not resp or "error" in resp:
            raise ConnectorError(f"MCP initialize failed: {(resp or {}).get('error')}")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.initialize()
        resp = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
        if not resp or "error" in resp:
            raise ConnectorError(f"MCP tools/call {name} failed: {str((resp or {}).get('error'))[:200]}")
        result = resp.get("result") or {}
        if result.get("isError"):
            msg = " ".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))
            raise ConnectorError(f"MCP tool error: {msg[:200]}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for c in result.get("content") or []:  # fall back to a JSON text block
            if isinstance(c, dict) and c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except (ValueError, KeyError):
                    continue
        raise ConnectorError("MCP tool returned no structured content")

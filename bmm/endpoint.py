"""Minimal OpenAI-compatible client built on the stdlib only.

Deliberately avoids the `openai` SDK / pydantic so the TUI installs cleanly on
Python 3.14 (no Rust wheels required). Talks to llama-swap on :9292.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import config


class EndpointError(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0
    raw: dict = field(default_factory=dict)


class Client:
    """Thin sync client: model discovery + chat completions (incl. tools)."""

    def __init__(self, endpoint: str | None = None, api_key: str | None = None,
                 timeout: int | None = None):
        self.endpoint = (endpoint or config.ENDPOINT).rstrip("/")
        self.api_key = api_key or config.API_KEY
        self.timeout = timeout or config.REQUEST_TIMEOUT

    # -- low level -----------------------------------------------------------
    def _request(self, path: str, payload: dict | None = None,
                 method: str = "POST", timeout: int | None = None) -> dict:
        url = f"{self.endpoint}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise EndpointError(f"HTTP {e.code} on {path}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise EndpointError(f"cannot reach {url}: {e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise EndpointError(f"bad JSON from {path}: {body[:300]}") from e

    # -- public --------------------------------------------------------------
    def models(self) -> list[str]:
        """Return the alias list llama-swap currently exposes."""
        try:
            data = self._request("/models", method="GET", timeout=15)
        except EndpointError:
            return []
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    def ping(self) -> bool:
        return bool(self.models()) or self._health()

    def _health(self) -> bool:
        try:
            self._request("/models", method="GET", timeout=8)
            return True
        except EndpointError:
            return False

    def chat(self, model: str, messages: list[dict], *,
             tools: list[dict] | None = None,
             temperature: float | None = None,
             max_tokens: int | None = None,
             extra: dict | None = None) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
            "max_tokens": config.DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if extra:
            payload.update(extra)

        t0 = time.time()
        data = self._request("/chat/completions", payload)
        dt = time.time() - t0

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        return ChatResult(
            text=(msg.get("content") or ""),
            tool_calls=msg.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason", ""),
            usage=data.get("usage", {}) or {},
            latency_s=dt,
            raw=data,
        )

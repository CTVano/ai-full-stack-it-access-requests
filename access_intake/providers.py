"""Model client for Databricks Model Serving (OpenAI-compatible chat completions).

The same ChatProvider serves both modes; only the transport differs:
  * databricks: HTTPS POST to {base_url}/chat/completions with Bearer API_KEY
  * mock:       FixtureTransport replays recorded response bodies from
                fixtures/responses/<sha256 of message text>.json (no network)
So retry/backoff, HTTP status handling and response parsing are exercised
identically by the mock tests and by live calls.

Fixture file format: one entry or a list of entries (successive calls).
  {"status": 200, "headers": {...}, "body": <chat completion object or raw string>}
  {"raise": "timeout" | "connection_reset"}
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import Settings
from .errors import (ExtractionFailure, InterruptedResponse, MalformedResponse, MockFixtureMissing,
                     ProviderRejected, ProviderTimeout, RateLimited, TransientProviderError)
from .prompt import prompt_sha256, source_message
from .redaction import redact, redact_headers
from .validation import load_schema

Transport = Callable[[str, dict, bytes, float], tuple[int, dict, bytes]]
MAX_BACKOFF_S = 30.0


@dataclass
class ChatResult:
    content: str
    finish_reason: str | None
    refusal: str | None
    model: str | None
    usage: dict | None
    raw: dict = field(repr=False)
    provider_attempts: int = 1


def message_key(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- transports
def urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (socket.timeout, TimeoutError):
        raise ProviderTimeout(f"no response within {timeout:g}s") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise ProviderTimeout(f"no response within {timeout:g}s") from None
        raise TransientProviderError(f"connection failed: {exc.reason}") from None
    except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionError) as exc:
        raise InterruptedResponse(f"connection dropped mid-response: {type(exc).__name__}", retryable=True) from None


class FixtureTransport:
    """Deterministic offline transport keyed by the SHA-256 of the source message text."""

    def __init__(self, fixtures_dir: Path):
        self.fixtures_dir = Path(fixtures_dir)
        self.calls: dict[str, int] = {}

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, dict, bytes]:
        request = json.loads(body)
        key = message_key(source_message(request["messages"]))
        path = self.fixtures_dir / f"{key}.json"
        if not path.is_file():
            raise MockFixtureMissing(f"no mock response recorded for this message (fixture {path.name})")
        entries = json.loads(path.read_text(encoding="utf-8"))
        entries = entries if isinstance(entries, list) else [entries]
        n = self.calls.get(key, 0)
        self.calls[key] = n + 1
        entry = entries[min(n, len(entries) - 1)]
        if entry.get("raise") == "timeout":
            raise ProviderTimeout(f"no response within {timeout:g}s (mock)")
        if entry.get("raise") == "connection_reset":
            raise InterruptedResponse("connection dropped mid-response (mock)", retryable=True)
        payload = entry.get("body", "")
        raw = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        return int(entry.get("status", 200)), dict(entry.get("headers", {})), raw


# ------------------------------------------------------------------ parsing
def parse_chat_response(raw: bytes) -> ChatResult:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InterruptedResponse("provider response body is not complete JSON", retryable=True) from None
    if not isinstance(data, dict):
        raise MalformedResponse("provider response is not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise MalformedResponse("provider response has no choices")
    choice = choices[0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):  # content blocks: keep text, ignore reasoning blocks
        content = "".join(b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type", "text") == "text")
    if content is not None and not isinstance(content, str):
        raise MalformedResponse(f"message content has unexpected type {type(content).__name__}")
    return ChatResult(content=content or "", finish_reason=choice.get("finish_reason"),
                      refusal=msg.get("refusal") or None, model=data.get("model"),
                      usage=data.get("usage"), raw=data)


def _retry_after(headers: dict) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                return None
    return None


def _snippet(raw: bytes) -> str:
    return redact(raw[:300].decode("utf-8", "replace")).replace("\n", " ")


# ----------------------------------------------------------------- provider
class ChatProvider:
    def __init__(self, settings: Settings, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random):
        self.settings = settings
        if transport is None:
            transport = FixtureTransport(settings.fixtures_dir) if settings.provider == "mock" else urllib_transport
        self.transport = transport
        self.sleep = sleep
        self.rng = rng

    @property
    def url(self) -> str:
        if self.settings.provider == "mock":
            return "mock://serving-endpoints/chat/completions"
        return f"{self.settings.base_url}/chat/completions"

    def request_body(self, messages: list[dict]) -> dict:
        s = self.settings
        body = {"model": s.model, "messages": messages, "temperature": s.temperature, "max_tokens": s.max_tokens}
        if s.response_format == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "access_request_extraction", "schema": load_schema(), "strict": True}}
        elif s.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.provider != "mock":
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def request_evidence(self, messages: list[dict]) -> dict:
        """Credential-redacted description of the exact request that would be sent."""
        return {"method": "POST", "url": self.url, "headers": redact_headers(self._headers()),
                "timeout_s": self.settings.timeout_s, "max_retries": self.settings.max_retries,
                "body": self.request_body(messages)}

    def complete(self, messages: list[dict]) -> ChatResult:
        self.settings.require_live()
        body = json.dumps(self.request_body(messages)).encode("utf-8")
        attempts = self.settings.max_retries + 1
        for n in range(1, attempts + 1):
            try:
                result = self._once(body)
                result.provider_attempts = n
                self._maybe_record(messages, result)
                return result
            except ExtractionFailure as exc:
                exc.provider_attempts = n
                if not exc.retryable or n == attempts:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else \
                    min(MAX_BACKOFF_S, 2 ** (n - 1)) * (0.5 + self.rng() / 2)
                self.sleep(min(delay, MAX_BACKOFF_S * 2))
        raise AssertionError("unreachable")

    def _once(self, body: bytes) -> ChatResult:
        status, headers, raw = self.transport(self.url, self._headers(), body, self.settings.timeout_s)
        if status == 429:
            raise RateLimited(f"HTTP 429: {_snippet(raw)}", status=429, retry_after=_retry_after(headers))
        if status == 408 or status == 504:
            raise ProviderTimeout(f"HTTP {status}: {_snippet(raw)}", status=status)
        if status >= 500:
            raise TransientProviderError(f"HTTP {status}: {_snippet(raw)}", status=status,
                                         retry_after=_retry_after(headers))
        if status >= 400:
            raise ProviderRejected(f"HTTP {status}: {_snippet(raw)}", status=status)
        return parse_chat_response(raw)

    def _maybe_record(self, messages: list[dict], result: ChatResult) -> None:
        s = self.settings
        if not (s.record_fixtures and s.provider != "mock"):
            return
        s.fixtures_dir.mkdir(parents=True, exist_ok=True)
        path = s.fixtures_dir / f"{message_key(source_message(messages))}.json"
        entry = {"recorded_from": f"{s.provider}:{s.model}", "reported_model": result.model,
                 "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "prompt_sha256": prompt_sha256(), "status": 200, "body": result.raw}
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

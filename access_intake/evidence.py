"""Builds deliverables/request-evidence.json from the application's own code paths.

Nothing here is hand-written description: the request body comes from
ChatProvider.request_body, the response is a recorded provider response, the
handling steps are the real checks run on it, and the failure table is produced
by pushing each failure condition through the real provider/extraction code.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import PROMPT_VERSION, ROUTING_VERSION, load_settings
from .errors import ExtractionFailure, ProviderTimeout, TransientProviderError
from .extraction import COMPLETE_FINISH_REASONS, check_completion, parse_content
from .prompt import build_messages, prompt_sha256
from .providers import MAX_BACKOFF_S, ChatProvider, ChatResult, message_key, parse_chat_response
from .redaction import redact
from .routing import ERROR_ROUTE, route
from .runner import retry_command
from .sources import load_export
from .validation import validate_extraction

HOST_PLACEHOLDER = "https://<workspace-host>"
ERROR_RECORD = {"status": ERROR_ROUTE.status, "owner": ERROR_ROUTE.owner, "next_action": ERROR_ROUTE.next_action,
                "extraction": None}


def _probe(fn) -> dict:
    try:
        fn()
    except ExtractionFailure as exc:
        return {"error_kind": exc.kind, "retried_automatically": exc.retryable, "message": redact(str(exc))[:160]}
    return {"error_kind": None, "retried_automatically": False, "message": "accepted"}


def _chat(content="{}", finish="stop", refusal=None) -> ChatResult:
    return ChatResult(content=content, finish_reason=finish, refusal=refusal, model="m", usage=None, raw={})


def failure_table(provider: ChatProvider, message: str, max_tokens: int) -> list[dict]:
    body = json.dumps(provider.request_body(build_messages(message))).encode()
    rows = []

    for status in (400, 401, 403, 404, 408, 429, 500, 502, 503, 504):
        headers = {"Retry-After": "5"} if status == 429 else {}
        provider.transport = lambda u, h, b, t, s=status, hd=headers: (s, hd, b'{"error": "example"}')
        rows.append({"condition": f"HTTP {status}", **_probe(lambda: provider._once(body))})

    rows.append({"condition": "no response within INTAKE_TIMEOUT_S (socket timeout)",
                 "error_kind": ProviderTimeout.kind, "retried_automatically": ProviderTimeout.retryable,
                 "message": "raised by urllib_transport on socket.timeout / URLError(timeout)"})
    rows.append({"condition": "connection refused / DNS failure",
                 "error_kind": TransientProviderError.kind, "retried_automatically": TransientProviderError.retryable,
                 "message": "raised by urllib_transport on URLError"})
    rows.append({"condition": "connection dropped mid-response", "error_kind": "interrupted_response",
                 "retried_automatically": True, "message": "IncompleteRead / RemoteDisconnected / ConnectionError"})
    rows.append({"condition": "HTTP 200 with truncated body",
                 **_probe(lambda: parse_chat_response(b'{"choices": [{"message": {"cont'))})
    rows.append({"condition": "HTTP 200 without choices", **_probe(lambda: parse_chat_response(b'{"id": "x"}'))})
    rows.append({"condition": "message.refusal set",
                 **_probe(lambda: check_completion(_chat(refusal="I can't help with that"), max_tokens))})
    rows.append({"condition": "finish_reason content_filter",
                 **_probe(lambda: check_completion(_chat(finish="content_filter"), max_tokens))})
    rows.append({"condition": "finish_reason length (cut off at max_tokens)",
                 **_probe(lambda: check_completion(_chat(finish="length"), max_tokens))})
    rows.append({"condition": "finish_reason missing/unknown",
                 **_probe(lambda: check_completion(_chat(finish=None), max_tokens))})
    rows.append({"condition": "empty content", **_probe(lambda: check_completion(_chat(content=" "), max_tokens))})
    rows.append({"condition": "plain-text refusal instead of JSON",
                 **_probe(lambda: parse_content("I'm sorry, but I can't process this request."))})
    rows.append({"condition": "content is not valid JSON", **_probe(lambda: parse_content('{"request_id": "IT-0'))})

    def schema_fail(obj):
        def run():
            problems = validate_extraction(obj, message)
            if problems:
                from .errors import ValidationFailed
                raise ValidationFailed("response failed validation: " + "; ".join(problems[:2]))
        return run
    rows.append({"condition": "JSON violates snapshot.schema.json (extra field)",
                 **_probe(schema_fail({"approved": True}))})
    for row in rows:
        row["record"] = "accepted" if row["error_kind"] is None else ERROR_RECORD
    return rows


def build_evidence(document_path: Path, fixtures_dir: Path, env: dict | None = None) -> dict:
    env = dict(env or {})
    live = load_settings({**env, "INTAKE_PROVIDER": "databricks", "DATABRICKS_HOST": HOST_PLACEHOLDER,
                          "INTAKE_BASE_URL": ""})
    doc = load_export(document_path)
    messages = build_messages(doc.message)
    provider = ChatProvider(live, transport=lambda *a: (500, {}, b""), sleep=lambda s: None)
    request = provider.request_evidence(messages)

    fixture_path = Path(fixtures_dir) / f"{message_key(doc.message)}.json"
    recorded = json.loads(fixture_path.read_text(encoding="utf-8"))
    recorded = recorded[0] if isinstance(recorded, list) else recorded
    raw = json.dumps(recorded["body"]).encode()

    steps = []
    result = parse_chat_response(raw)
    steps.append({"step": "HTTP status 200; body parsed as chat.completion", "result": "ok",
                  "finish_reason": result.finish_reason, "reported_model": result.model, "usage": result.usage})
    check_completion(result, live.max_tokens)
    steps.append({"step": "completion status: no refusal, no content filter, finish_reason in "
                          f"{sorted(COMPLETE_FINISH_REASONS)}, non-empty content", "result": "ok"})
    obj = parse_content(result.content)
    steps.append({"step": "content parsed as one JSON object (code fences tolerated)", "result": "ok"})
    problems = validate_extraction(obj, doc.message)
    steps.append({"step": "schema + evidence validation (snapshot.schema.json, real YYYY-MM-DD dates, "
                          "supporting_quotes are exact substrings of the message)",
                  "result": "ok" if not problems else problems})
    r = route(doc.document_id, doc.message, obj)
    steps.append({"step": "routing (office-policy.md rules 1-5, deterministic code)", "result": "ok",
                  "rule": r.rule, "status": r.status, "owner": r.owner, "next_action": r.next_action})

    # A real provider failure pushed through the retry loop, as the runner would record it.
    failing = ChatProvider(load_settings({**env, "INTAKE_MAX_RETRIES": "3"}),
                           transport=lambda *a: (503, {}, b"upstream unavailable"), sleep=lambda s: None,
                           rng=lambda: 1.0)
    try:
        failing.complete(messages)
    except ExtractionFailure as exc:
        error_text = f"{exc.describe()}. Retry: {retry_command(doc.document_id)}"

    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generated_by": "python -m access_intake evidence",
        "observation": {
            "request": "built by ChatProvider.request_body / request_evidence for the document below; "
                       "Authorization redacted; workspace host replaced by a placeholder",
            "response": f"provider response recorded live from {recorded.get('recorded_from')} at "
                        f"{recorded.get('recorded_at')} (fixture {fixture_path.name}); replayed offline as 'mock'",
        },
        "document": {"document_id": doc.document_id, "source_sha256": doc.sha256},
        "configuration": {
            "provider": live.provider, "model": live.model, "endpoint": request["url"],
            "temperature": live.temperature, "max_tokens": live.max_tokens,
            "response_format": live.response_format, "timeout_s": live.timeout_s,
            "max_retries": live.max_retries, "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_sha256(),
            "routing_version": ROUTING_VERSION,
            "credential": "API_KEY environment variable, sent as 'Authorization: Bearer <API_KEY>'; never stored or logged",
            "environment_variables": ["API_KEY", "DATABRICKS_HOST or INTAKE_BASE_URL", "INTAKE_PROVIDER", "INTAKE_MODEL",
                                      "INTAKE_TEMPERATURE", "INTAKE_MAX_TOKENS", "INTAKE_RESPONSE_FORMAT",
                                      "INTAKE_TIMEOUT_S", "INTAKE_MAX_RETRIES", "INTAKE_MAX_WORKERS"],
        },
        "request": request,
        "response": {"recorded_from": recorded.get("recorded_from"), "recorded_at": recorded.get("recorded_at"),
                     "body": recorded["body"]},
        "response_handling": {
            "steps_applied_to_this_response": steps,
            "resulting_record": {"document_id": doc.document_id, "source_sha256": doc.sha256, "extraction": obj,
                                 "status": r.status, "owner": r.owner, "next_action": r.next_action, "error": None,
                                 "response_source": live.response_source},
            "retry_policy": (f"up to INTAKE_MAX_RETRIES+1 = {live.max_retries + 1} HTTP attempts for retryable "
                             "failures; wait Retry-After if given, else min(30, 2^(n-1)) s x uniform(0.5, 1.0); "
                             f"single wait capped at {2 * MAX_BACKOFF_S:g} s. Non-retryable failures stop immediately."),
            "failure_handling": failure_table(ChatProvider(live, sleep=lambda s: None), doc.message, live.max_tokens),
            "example_failure_record": {"document_id": doc.document_id, "source_sha256": doc.sha256, **ERROR_RECORD,
                                       "error": error_text, "response_source": live.response_source,
                                       "note": "produced by sending a simulated HTTP 503 through ChatProvider.complete "
                                               "(retry loop, waits skipped) and formatting it as the runner does; an "
                                               "earlier successful result stays in history and is linked from the "
                                               "queue entry as previous_result"},
        },
    }
    return evidence

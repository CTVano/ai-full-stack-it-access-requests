"""One-message extraction: prompt -> provider -> completion checks -> JSON -> validation.

Nothing from the model is used until it has passed, in order:
  1. completion status (no refusal, no content filter, finish_reason indicates a complete answer)
  2. JSON parsing into a single object
  3. schema + evidence validation (see validation.py)
Any failure raises an ExtractionFailure subclass; the caller records it as an
error attempt with a null extraction.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import Settings
from .errors import InterruptedResponse, InvalidExport, MalformedResponse, Refusal, ValidationFailed
from .prompt import build_messages
from .providers import ChatProvider, ChatResult
from .sources import SourceDoc
from .validation import validate_extraction

COMPLETE_FINISH_REASONS = {"stop", "end_turn", "stop_sequence"}
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)
_REFUSAL_TEXT = re.compile(r"^\s*(i'?m sorry|i am sorry|sorry,|i can(?:'|no)?t|i cannot|i won'?t|i will not|i'?m unable|i am unable)", re.I)


@dataclass
class ExtractionOutcome:
    extraction: dict
    response_source: str
    reported_model: str | None
    provider_attempts: int
    usage: dict | None


def check_completion(result: ChatResult, max_tokens: int) -> None:
    if result.refusal:
        raise Refusal(f"model refused: {result.refusal[:200]}")
    reason = result.finish_reason
    if reason == "content_filter":
        raise Refusal("response blocked by the provider content filter")
    if reason == "length":
        raise InterruptedResponse(f"response cut off at max_tokens={max_tokens}", retryable=False)
    if reason not in COMPLETE_FINISH_REASONS:
        raise InterruptedResponse(f"response not confirmed complete (finish_reason={reason!r})", retryable=False)
    if not result.content.strip():
        raise MalformedResponse("response content is empty")


def parse_content(content: str) -> object:
    m = _FENCE.match(content)
    text = m.group(1) if m else content
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if _REFUSAL_TEXT.match(text):
            raise Refusal(f"model replied with a refusal instead of JSON: {text.strip()[:200]!r}") from None
        raise MalformedResponse(f"response is not valid JSON ({exc.msg} at char {exc.pos})") from None


def extract(doc: SourceDoc, provider: ChatProvider, settings: Settings) -> ExtractionOutcome:
    if not doc.ok:
        raise InvalidExport(doc.load_error)
    result = provider.complete(build_messages(doc.message))
    try:
        check_completion(result, settings.max_tokens)
        obj = parse_content(result.content)
        problems = validate_extraction(obj, doc.message)
        if problems:
            shown = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
            raise ValidationFailed(f"response failed validation: {shown}")
    except Exception as exc:  # keep the provider attempt count on the failure
        if hasattr(exc, "provider_attempts"):
            exc.provider_attempts = result.provider_attempts
        raise
    return ExtractionOutcome(obj, settings.response_source, result.model, result.provider_attempts, result.usage)

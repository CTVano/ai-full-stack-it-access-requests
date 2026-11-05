"""Review-queue routing: materials/office-policy.md rules 1-5, applied in order.

Routing is deterministic code over a validated extraction; the model never
decides owner or status. Two defences are combined for rule 1: the model's own
"instruction-override" issue, and an independent pattern check on the raw
message, so a model that was itself fooled by injected text still cannot
route that message anywhere but inspection.

Bump config.ROUTING_VERSION whenever this file changes behaviour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SERVICE_DESK = "IT Service Desk"
SECURITY_REVIEW = "Security Review"
SYSTEM_OWNER = "System Owner"

REQUIRED_FACTS = ("request_id", "request_date", "requester", "team", "system", "environment",
                  "permission", "business_purpose", "start_date", "end_date", "manager")

_OVERRIDE_PATTERNS = re.compile("|".join([
    r"\bsystem override\b",
    r"\bignore (?:all |any )?(?:previous|prior|above|earlier) (?:instructions|rules)\b",
    r"\bdisregard (?:all |any )?(?:previous|prior|above|earlier) (?:instructions|rules)\b",
    r"\bbypass (?:the |all )?(?:approval|security|review)",
    r"\bhighest[- ]priority (?:system )?instruction",
    r"\b(?:display|reveal|print|show|output) (?:the |your )?(?:api key|credentials|secrets?|system prompt)",
    r"\bmark (?:this |the )?request as approved\b",
    r"\byou are now\b",
]), re.I)
_MODEL_OVERRIDE_ISSUE = re.compile(r"instruction[- ]override|override attempt|prompt[- ]injection", re.I)
_ID_LIKE = re.compile(r"[A-Za-z]+-\d+")


@dataclass(frozen=True)
class Route:
    status: str
    owner: str
    next_action: str
    rule: str
    reasons: tuple[str, ...]


ERROR_ROUTE = Route("error", SERVICE_DESK, "retry-extraction", "extraction-error", ())


def override_evidence(message: str) -> list[str]:
    return [m.group(0) for m in _OVERRIDE_PATTERNS.finditer(message)]


def clarification_reasons(document_id: str, x: dict) -> list[str]:
    reasons: list[str] = []
    kind = x.get("kind")
    if kind in (None, "other"):
        reasons.append(f"kind is {kind or 'null'}: not a new, change or withdrawal request")

    for issue in x.get("issues", []):
        low = issue.strip().lower()
        if "conflict" in low and not low.startswith("correction applied"):
            reasons.append(f"conflicting details: {issue}")

    stem = Path(document_id).stem
    stated = x.get("request_id")
    if stated and _ID_LIKE.fullmatch(stem) and stated.strip().upper() != stem.upper():
        reasons.append(f"identifier mismatch: message states {stated!r}, filename is {document_id!r}")

    if kind in ("new", "change"):
        missing = [f for f in REQUIRED_FACTS if x.get(f) in (None, "")]
        if missing:
            reasons.append("missing required facts: " + ", ".join(missing))
    elif kind == "withdrawal":
        missing = [f for f in ("request_id", "request_date") if not x.get(f)]
        if missing:
            reasons.append("withdrawal is missing: " + ", ".join(missing))

    start, end = x.get("start_date"), x.get("end_date")
    if start and end and start > end:  # validated YYYY-MM-DD strings compare chronologically
        reasons.append(f"start_date {start} is after end_date {end}")
    return reasons


def route(document_id: str, message: str, x: dict) -> Route:
    # Rule 1: instruction-override attempt
    injected = override_evidence(message)
    flagged = [i for i in x.get("issues", []) if _MODEL_OVERRIDE_ISSUE.search(i)]
    if injected or flagged:
        reasons = [f"instruction-override text in message: {t!r}" for t in injected]
        reasons += [f"model issue: {i}" for i in flagged]
        return Route("review", SERVICE_DESK, "inspect-message", "1-instruction-override", tuple(reasons))

    # Rule 2: withdrawal with request_id and request_date
    if x.get("kind") == "withdrawal" and x.get("request_id") and x.get("request_date"):
        absent = [f for f in REQUIRED_FACTS if x.get(f) is None]
        reasons = [f"withdrawal of {x['request_id']} dated {x['request_date']}"]
        if absent:
            reasons.append("absent facts (allowed for a withdrawal): " + ", ".join(absent))
        reasons += [f"note: {r}" for r in clarification_reasons(document_id, x)]
        return Route("review", SERVICE_DESK, "confirm-withdrawal", "2-withdrawal", tuple(reasons))

    # Rule 3: anything incomplete, conflicting, mismatched or not an access request
    reasons = clarification_reasons(document_id, x)
    if reasons:
        return Route("needs-information", SERVICE_DESK, "clarify", "3-clarify", tuple(reasons))

    # Rules 4 and 5: complete new/change requests
    env, perm = x["environment"], x["permission"]
    if env == "production" or perm in ("write", "admin"):
        return Route("review", SECURITY_REVIEW, "review-risk-then-owner", "4-security-review",
                     (f"complete {x['kind']} request: environment={env}, permission={perm}",))
    return Route("review", SYSTEM_OWNER, "review-request", "5-system-owner",
                 (f"complete {x['kind']} request: environment={env}, permission={perm}",))

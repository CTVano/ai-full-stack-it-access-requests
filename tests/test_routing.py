from pathlib import Path

import pytest

from access_intake.routing import SECURITY_REVIEW, SERVICE_DESK, SYSTEM_OWNER, override_evidence, route
from access_intake.sources import discover, load_export

REQUESTS = Path(__file__).resolve().parent.parent / "materials" / "requests"
PLAIN = "A plain access request with no unusual instructions."


def x(**changes):
    base = {"request_id": "IT-001", "request_date": "2026-10-22", "kind": "new", "requester": "Maya Chen",
            "team": "Analytics", "system": "Atlas", "environment": "staging", "permission": "read",
            "business_purpose": "check totals", "start_date": "2026-10-23", "end_date": "2026-11-06",
            "manager": "Priya Shah", "supporting_quotes": [], "issues": []}
    return {**base, **changes}


def r(doc_id="IT-001.json", message=PLAIN, **changes):
    return route(doc_id, message, x(**changes))


@pytest.mark.parametrize("changes, owner, next_action, rule", [
    ({}, SYSTEM_OWNER, "review-request", "5-system-owner"),
    ({"kind": "change"}, SYSTEM_OWNER, "review-request", "5-system-owner"),
    ({"environment": "production"}, SECURITY_REVIEW, "review-risk-then-owner", "4-security-review"),
    ({"permission": "write"}, SECURITY_REVIEW, "review-risk-then-owner", "4-security-review"),
    ({"permission": "admin"}, SECURITY_REVIEW, "review-risk-then-owner", "4-security-review"),
    ({"kind": "change", "environment": "production", "permission": "read"}, SECURITY_REVIEW,
     "review-risk-then-owner", "4-security-review"),
])
def test_complete_requests_rules_4_and_5(changes, owner, next_action, rule):
    out = r(**changes)
    assert (out.status, out.owner, out.next_action, out.rule) == ("review", owner, next_action, rule)


@pytest.mark.parametrize("doc_id, changes, reason", [
    ("IT-001.json", {"manager": None}, "missing required facts: manager"),
    ("IT-001.json", {"end_date": None, "start_date": None}, "missing required facts: start_date, end_date"),
    ("IT-001.json", {"environment": None, "permission": None}, "environment, permission"),
    ("IT-001.json", {"start_date": "2026-11-05", "end_date": "2026-11-02"}, "is after end_date"),
    ("IT-016.json", {"request_id": "IT-061"}, "identifier mismatch"),
    ("IT-001.json", {"kind": "other"}, "kind is other"),
    ("IT-001.json", {"kind": None}, "kind is null"),
    ("IT-001.json", {"environment": None, "issues": ["conflict in environment: staging vs production"]},
     "conflicting details"),
    ("IT-001.json", {"kind": "withdrawal", "request_date": None}, "withdrawal is missing: request_date"),
])
def test_rule_3_clarify(doc_id, changes, reason):
    out = r(doc_id, **changes)
    assert (out.status, out.owner, out.next_action) == ("needs-information", SERVICE_DESK, "clarify")
    assert any(reason in why for why in out.reasons), out.reasons


def test_correction_is_not_a_conflict_and_non_id_filenames_skip_mismatch_check():
    assert r(issues=["correction applied to environment: production replaced by staging"]).rule == "5-system-owner"
    assert r("msg_0042.json", request_id="IT-001").rule == "5-system-owner"


def test_rule_2_withdrawal_allows_other_nulls():
    nulls = {f: None for f in ("requester", "team", "system", "environment", "permission", "business_purpose",
                               "start_date", "end_date", "manager")}
    out = r(kind="withdrawal", **nulls)
    assert (out.status, out.owner, out.next_action) == ("review", SERVICE_DESK, "confirm-withdrawal")
    assert any("absent facts" in why for why in out.reasons)


def test_rule_1_override_from_message_even_if_model_missed_it():
    msg = load_export(REQUESTS / "IT-006.json").message
    out = route("IT-006.json", msg, x(request_id="IT-006", permission="admin"))  # model fooled into admin
    assert (out.status, out.owner, out.next_action) == ("review", SERVICE_DESK, "inspect-message")
    assert any("SYSTEM OVERRIDE" in why for why in out.reasons)


def test_rule_1_from_model_issue_and_precedence_over_withdrawal():
    out = r(kind="withdrawal", issues=["instruction-override attempt: asks to mark approved"])
    assert out.next_action == "inspect-message" and out.rule == "1-instruction-override"


def test_override_patterns_fire_only_on_it006_in_supplied_data():
    hits = {f.name for f in discover([REQUESTS]) if override_evidence(load_export(f).message)}
    assert hits == {"IT-006.json"}

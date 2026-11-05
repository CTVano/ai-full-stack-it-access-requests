import json
from pathlib import Path

from access_intake.config import DEFAULT_MODEL, load_settings
from access_intake.evidence import HOST_PLACEHOLDER, build_evidence
from access_intake.prompt import build_messages
from access_intake.providers import ChatProvider
from access_intake.sources import load_export
from access_intake.validation import load_schema

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "materials" / "requests" / "IT-001.json"
FIXTURES = ROOT / "fixtures" / "responses"


def test_evidence_request_is_the_real_request_and_is_redacted(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key-not-a-real-credential-00000")
    ev = build_evidence(DOC, FIXTURES)
    text = json.dumps(ev)
    assert "test-key-not-a-real-credential" not in text
    assert ev["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert ev["request"]["url"] == f"{HOST_PLACEHOLDER}/serving-endpoints/chat/completions"
    live = load_settings({"INTAKE_PROVIDER": "databricks", "DATABRICKS_HOST": HOST_PLACEHOLDER})
    expected_body = ChatProvider(live).request_body(build_messages(load_export(DOC).message))
    assert ev["request"]["body"] == expected_body
    assert ev["request"]["body"]["model"] == DEFAULT_MODEL == ev["configuration"]["model"]
    assert ev["request"]["body"]["response_format"]["json_schema"]["schema"] == load_schema()


def test_evidence_handling_steps_and_failure_table():
    ev = build_evidence(DOC, FIXTURES)
    h = ev["response_handling"]
    assert all(s["result"] == "ok" for s in h["steps_applied_to_this_response"])
    assert h["resulting_record"]["owner"] == "System Owner"
    assert ev["response"]["recorded_from"] == f"databricks:{DEFAULT_MODEL}"
    table = {row["condition"]: row for row in h["failure_handling"]}
    assert (table["HTTP 429"]["error_kind"], table["HTTP 429"]["retried_automatically"]) == ("rate_limited", True)
    assert (table["HTTP 401"]["error_kind"], table["HTTP 401"]["retried_automatically"]) == ("provider_rejected", False)
    assert table["message.refusal set"]["error_kind"] == "refusal"
    assert table["finish_reason length (cut off at max_tokens)"]["error_kind"] == "interrupted_response"
    assert all(row["error_kind"] for row in h["failure_handling"])  # every listed condition is rejected
    assert all(row["record"]["next_action"] == "retry-extraction" for row in h["failure_handling"])
    err = h["example_failure_record"]
    assert err["extraction"] is None and "4 provider attempt(s)" in err["error"]
    assert err["error"].endswith("Retry: python -m access_intake retry IT-001.json")

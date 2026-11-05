"""Tests for the accuracy procedure and for the expected results themselves.

The expected-file checks compare expected values with the SOURCE MESSAGES and
the routing policy, never with model output.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from access_intake.accuracy import SCALAR_FIELDS, evaluate, field_outcome, load_expected
from access_intake.routing import route
from access_intake.sources import load_export
from access_intake.validation import validate_extraction

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = load_expected(ROOT / "eval" / "expected.json")
VERBATIM_FIELDS = ("request_id", "requester", "team", "system", "manager", "request_date", "start_date", "end_date")


def as_extraction(exp):
    """An extraction exactly matching the expected facts (issues only as routing needs them)."""
    issues = [f"conflict in {f}" for f in exp.get("conflict_fields", [])]
    return {**exp["expected"], "business_purpose": None if exp["purpose_terms"] is None else exp["purpose_terms"][0],
            "supporting_quotes": [], "issues": issues}


def perfect_records():
    out = {}
    for e in EXPECTED:
        sha = hashlib.sha256((ROOT / e["source"]).read_bytes()).hexdigest()
        out[e["document_id"]] = {"document_id": e["document_id"], "source_sha256": sha,
                                 "extraction": as_extraction(e), "error": None, "response_source": "mock",
                                 **e["route"]}
    return out


# ------------------------------------------------- expected file vs. sources
def test_expected_covers_20_supplied_messages_and_one_paraphrase():
    ids = [e["document_id"] for e in EXPECTED]
    assert ids == [f"IT-{i:03d}.json" for i in range(1, 22)]
    assert [e["document_id"] for e in EXPECTED if e.get("synthetic")] == ["IT-021.json"]
    assert all(set(e["expected"]) == set(SCALAR_FIELDS) for e in EXPECTED)


@pytest.mark.parametrize("exp", EXPECTED, ids=lambda e: e["document_id"])
def test_expected_values_are_grounded_in_the_message(exp):
    doc = load_export(ROOT / exp["source"])
    assert doc.document_id == exp["document_id"]
    derived = exp.get("derived", {})
    for f in VERBATIM_FIELDS:
        value = exp["expected"][f]
        if value is not None and f not in derived:
            assert value in doc.message, f"{f}={value!r} not found in {exp['document_id']}"
    for term in exp["quote_terms"]:
        assert term in doc.message, f"quote term {term!r} is not in the message"
    # the expected facts must themselves form a schema-valid extraction
    assert validate_extraction(as_extraction(exp), doc.message) == []


@pytest.mark.parametrize("exp", EXPECTED, ids=lambda e: e["document_id"])
def test_expected_route_follows_from_expected_facts_and_policy(exp):
    doc = load_export(ROOT / exp["source"])
    r = route(exp["document_id"], doc.message, as_extraction(exp))
    assert {"status": r.status, "owner": r.owner, "next_action": r.next_action} == exp["route"]


# ------------------------------------------------------------ the procedure
@pytest.mark.parametrize("expected, got, have, outcome", [
    ("Atlas", "Atlas", True, "match"), ("Atlas", " Atlas ", True, "match"), (None, None, True, "match"),
    ("Atlas", "atlas", True, "wrong_value"), ("Atlas", "Beacon", True, "wrong_value"),
    (None, "Elena Park", True, "unsupported_value"), ("2026-10-30", None, True, "missed"),
    ("Atlas", "Atlas", False, "no_extraction"),
])
def test_field_outcomes(expected, got, have, outcome):
    assert field_outcome(expected, got, have) == outcome


def test_perfect_records_score_full_marks():
    report = evaluate(EXPECTED, perfect_records(), ROOT)
    assert report["fields"]["matched"] == report["fields"]["denominator"] == 21 * 11
    assert report["routing"] == {"matched": 21, "denominator": 21}
    assert report["factual_errors"] == [] and report["documents_with_problems"] == []


def test_errors_are_classified_and_failures_stay_in_the_denominator():
    recs = perfect_records()
    recs["IT-020.json"]["extraction"]["manager"] = "Priya Shah"   # picked one side of a conflict
    recs["IT-002.json"]["extraction"]["permission"] = "read"      # wrong value
    recs["IT-010.json"]["extraction"]["start_date"] = None        # missed
    recs["IT-001.json"].update(extraction=None, error="timeout", status="error")
    recs["IT-003.json"]["source_sha256"] = "0" * 64                # result for another source version
    del recs["IT-021.json"]                                         # no record at all
    report = evaluate(EXPECTED, recs, ROOT)
    f = report["fields"]
    assert f["denominator"] == 231
    assert f["outcomes"]["no_extraction"] == 33  # IT-001, IT-003, IT-021
    assert f["matched"] == 231 - 33 - 3
    errors = {(e["document_id"], e["field"], e["outcome"]) for e in report["factual_errors"]}
    assert errors == {("IT-020.json", "manager", "unsupported_value"), ("IT-002.json", "permission", "wrong_value")}
    assert [(m["document_id"], m["field"]) for m in report["missed_values"]] == [("IT-010.json", "start_date")]
    assert {d["document_id"] for d in report["documents_with_problems"]} == {"IT-001.json", "IT-003.json", "IT-021.json"}
    assert report["routing"]["matched"] == 21 - 3  # IT-001 error route, IT-003 wrong source, IT-021 missing


def test_evidence_checks():
    recs = perfect_records()
    report = evaluate(EXPECTED, recs, ROOT)
    assert report["issue_checks"]["passed"] < report["issue_checks"]["denominator"]  # perfect facts, no issue text
    recs = copy.deepcopy(recs)
    x = recs["IT-020.json"]["extraction"]
    x["supporting_quotes"] = ["My manager is Priya Shah", "The manager for this request is Elena Park"]
    doc = next(d for d in evaluate(EXPECTED, recs, ROOT)["per_document"] if d["document_id"] == "IT-020.json")
    assert doc["quote_checks"] == {"Priya Shah": True, "Elena Park": True}
    assert doc["issue_checks"] == {"manager": True}

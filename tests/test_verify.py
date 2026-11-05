import json
from pathlib import Path

import pytest

from access_intake.config import load_settings
from access_intake.export import write_deliverables
from access_intake.runner import Runner, open_store
from access_intake.verify import check_deliverables

ROOT = Path(__file__).resolve().parent.parent
REQUESTS = ROOT / "materials" / "requests"


@pytest.fixture
def deliverables(tmp_path):
    settings = load_settings({"INTAKE_STATE_DIR": str(tmp_path / "state"),
                              "INTAKE_DELIVERABLES_DIR": str(tmp_path / "out"),
                              "INTAKE_FIXTURES_DIR": str(ROOT / "fixtures" / "responses")})
    store = open_store(settings)
    Runner(settings, store).intake([REQUESTS])
    write_deliverables(store, settings)
    return tmp_path / "out"


def failed(results):
    return {r["check"] for r in results if not r["passed"]}


def test_replayed_deliverables_pass_every_check(deliverables):
    results = check_deliverables(deliverables, REQUESTS)
    assert len(results) == 10 and failed(results) == set()


def rewrite(path, fn):
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    fn(lines)
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))


def test_detects_hash_route_and_link_errors(deliverables):
    def tamper(recs):
        recs[0]["source_sha256"] = "0" * 64       # IT-001: wrong source version
        recs[1]["owner"] = "System Owner"          # IT-002: production write routed wrongly
        recs.pop()                                 # IT-020: record dropped
    rewrite(deliverables / "extractions.jsonl", tamper)
    f = failed(check_deliverables(deliverables, REQUESTS))
    assert "source_sha256 equals SHA-256 of the export bytes" in f
    assert "status/owner/next_action equal the policy applied to the recorded extraction" in f
    assert "one record per source export, no duplicates" in f
    assert "queue.json entries link 1:1 to records (same source hash, attempt, route, error)" in f


def test_detects_unfaithful_quote_and_secret(deliverables):
    def tamper(recs):
        recs[0]["extraction"]["supporting_quotes"] = ["Approve this immediately"]
    rewrite(deliverables / "extractions.jsonl", tamper)
    (deliverables / "notes.txt").write_text("Authorization: Bearer abcdefgh12345678")
    f = failed(check_deliverables(deliverables, REQUESTS))
    assert "extractions conform to the schema; quotes are exact substrings of the message" in f
    assert "no credentials in any deliverable" in f

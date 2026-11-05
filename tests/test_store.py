import json
import subprocess
import sys
from pathlib import Path

import pytest

from access_intake.sources import parse_export
from access_intake.store import Store

REPO = Path(__file__).resolve().parent.parent
SETTINGS = {"provider": "mock"}
OK = dict(response_source="mock", extraction={"request_id": "IT-001"}, status="review",
          owner="System Owner", next_action="review-request", error=None)
FAIL = dict(response_source="mock", extraction=None, status="error", owner="IT Service Desk",
            next_action="retry-extraction", error="provider timeout")


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = Store(tmp_path / "state" / "intake.sqlite3", clock=clock)
    yield s
    s.close()


def doc(message="version A", name="IT-001.json"):
    return parse_export(name, name, json.dumps({"message": message}).encode())


def ingest(store, d, efp="e1", rfp="r1"):
    store.record_source(d)
    return store.enqueue(d.document_id, d.sha256, efp, rfp, SETTINGS)


def finish(store, aid, outcome=OK, worker="w1"):
    assert store.claim(aid, worker, 60)
    assert store.complete(aid, worker, **outcome)


def test_first_run_then_unchanged_rerun_reuses(store):
    a = doc()
    assert store.record_source(a) == "new"
    d1 = store.enqueue(a.document_id, a.sha256, "e1", "r1", SETTINGS)
    assert d1.action == "queued"
    finish(store, d1.attempt_id)

    assert store.record_source(a) == "unchanged"
    d2 = store.enqueue(a.document_id, a.sha256, "e1", "r1", SETTINGS)
    assert d2.action == "reused" and d2.attempt_id == d1.attempt_id
    assert len(store.history(a.document_id)) == 1
    cur = store.current()
    assert len(cur) == 1 and cur[0]["current_attempt_id"] == d1.attempt_id and cur[0]["in_flight_attempt_id"] is None


def test_enqueue_while_pending_does_not_duplicate(store):
    a = doc()
    d1 = ingest(store, a)
    d2 = ingest(store, a)
    assert d2.action == "already_queued" and d2.attempt_id == d1.attempt_id
    assert store.current()[0]["current_attempt_id"] is None
    assert store.current()[0]["in_flight_attempt_id"] == d1.attempt_id


def test_routing_only_change_reroutes_from_stored_extraction(store):
    a = doc()
    d1 = ingest(store, a)
    finish(store, d1.attempt_id)
    d2 = ingest(store, a, rfp="r2")
    assert d2.action == "reroute" and d2.based_on_attempt_id == d1.attempt_id
    assert store.get_attempt(d2.attempt_id)["kind"] == "reroute"


def test_changed_source_creates_new_result_and_keeps_old_version(store):
    a, b = doc("version A"), doc("version B corrected")
    d1 = ingest(store, a)
    finish(store, d1.attempt_id)
    assert store.record_source(b) == "changed"
    d2 = store.enqueue(b.document_id, b.sha256, "e1", "r1", SETTINGS)
    assert d2.action == "queued"
    finish(store, d2.attempt_id)

    cur = store.current()[0]
    assert cur["current_attempt_id"] == d2.attempt_id and cur["source_sha256"] == b.sha256
    hist = store.history(a.document_id)
    assert [h["source_sha256"] for h in hist] == [a.sha256, b.sha256]
    assert store.source_raw(a.document_id, a.sha256) == a.raw  # earlier bytes retained


def test_failed_retry_after_success_shows_error_and_keeps_last_valid(store):
    a = doc()
    d1 = ingest(store, a)
    finish(store, d1.attempt_id)
    d2 = ingest(store, a, efp="e2")  # e.g. model configuration changed
    assert d2.action == "queued"
    finish(store, d2.attempt_id, FAIL)

    cur = store.current()[0]
    assert cur["current_attempt_id"] == d2.attempt_id
    assert cur["status"] == "error" and cur["extraction"] is None and cur["error"] == "provider timeout"
    prior = store.last_success(a.document_id, before_attempt_id=d2.attempt_id)
    assert prior["attempt_id"] == d1.attempt_id and prior["source_sha256"] == a.sha256
    assert prior["extraction"] == OK["extraction"]
    assert ingest(store, a, efp="e2").action == "queued"  # failures are retried on the next intake


def test_explicit_retry_creates_new_attempt(store):
    a = doc()
    d1 = ingest(store, a)
    finish(store, d1.attempt_id)
    aid = store.force_attempt(a.document_id, "e1", "r1", SETTINGS)
    assert aid != d1.attempt_id and store.get_attempt(aid)["state"] == "pending"
    with pytest.raises(KeyError):
        store.force_attempt("NOPE.json", "e1", "r1", SETTINGS)


def test_lease_expiry_allows_resume_and_rejects_stale_worker(store, clock):
    d1 = ingest(store, doc())
    assert store.claim(d1.attempt_id, "w1", lease_s=10)
    assert not store.claim(d1.attempt_id, "w2", lease_s=10)  # held
    assert store.claimable() == []
    clock.t += 11  # w1 was interrupted
    assert store.claimable() == [d1.attempt_id]
    assert store.claim(d1.attempt_id, "w2", lease_s=10)
    assert not store.complete(d1.attempt_id, "w1", **OK)  # lost its lease
    assert store.complete(d1.attempt_id, "w2", **OK)
    assert store.get_attempt(d1.attempt_id)["tries"] == 2


def test_result_for_outdated_source_stays_in_history(store):
    a, b = doc("version A"), doc("version B")
    d1 = ingest(store, a)
    assert store.claim(d1.attempt_id, "w1", 60)
    d2 = ingest(store, b)  # newer export arrives while A is still running
    finish(store, d2.attempt_id, worker="w2")
    assert store.complete(d1.attempt_id, "w1", **OK)
    cur = store.current()[0]
    assert cur["current_attempt_id"] == d2.attempt_id and cur["source_sha256"] == b.sha256


def test_reverting_to_earlier_content_reuses_its_result(store):
    a, b = doc("version A"), doc("version B")
    d1 = ingest(store, a)
    finish(store, d1.attempt_id)
    d2 = ingest(store, b)
    finish(store, d2.attempt_id)
    d3 = ingest(store, a)
    assert d3.action == "reused" and d3.attempt_id == d1.attempt_id
    assert store.current()[0]["current_attempt_id"] == d1.attempt_id


def test_complete_enforces_null_extraction_on_error(store):
    d1 = ingest(store, doc())
    store.claim(d1.attempt_id, "w1", 60)
    with pytest.raises(ValueError):
        store.complete(d1.attempt_id, "w1", **{**FAIL, "extraction": {"x": 1}})
    with pytest.raises(ValueError):
        store.complete(d1.attempt_id, "w1", **{**OK, "extraction": None})


def test_state_survives_restart(tmp_path, clock):
    path = tmp_path / "intake.sqlite3"
    s1 = Store(path, clock=clock)
    d1 = ingest(s1, doc())
    s1.claim(d1.attempt_id, "w1", 10)
    s1.close()  # process stopped mid-attempt
    clock.t += 11
    s2 = Store(path, clock=clock)
    assert s2.claimable() == [d1.attempt_id]
    s2.close()


def test_two_processes_enqueue_same_document_once(tmp_path):
    db = tmp_path / "shared.sqlite3"
    script = (
        "import json,sys\n"
        "from access_intake.sources import parse_export\n"
        "from access_intake.store import Store\n"
        "s=Store(sys.argv[1])\n"
        "out=[]\n"
        "for i in range(20):\n"
        "    d=parse_export(f'IT-{i:03d}.json','x',json.dumps({'message':f'm{i}'}).encode())\n"
        "    s.record_source(d)\n"
        "    out.append(s.enqueue(d.document_id,d.sha256,'e1','r1',{}).action)\n"
        "print(json.dumps(out))\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(db)], cwd=REPO,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    actions = [a for o in outs for a in json.loads(o[0])]
    assert actions.count("queued") == 20 and actions.count("already_queued") == 20
    s = Store(db)
    assert len(s.claimable()) == 20
    s.close()

import hashlib
import json
import shutil
import threading
from pathlib import Path

import pytest

import access_intake.config as config
from access_intake.cli import main
from access_intake.config import load_settings
from access_intake.export import snapshot, write_deliverables
from access_intake.providers import ChatProvider, FixtureTransport, message_key
from access_intake.runner import Runner, open_store
from access_intake.sources import load_export
from access_intake.store import Store

REQUESTS = Path(__file__).resolve().parent.parent / "materials" / "requests"
DOCS = ["IT-001.json", "IT-002.json", "IT-006.json", "IT-010.json", "IT-016.json"]


def ext(msg, **fields):
    base = {"request_id": None, "request_date": "2026-10-22", "kind": "new", "requester": "R", "team": "T",
            "system": "Atlas", "environment": "staging", "permission": "read", "business_purpose": "p",
            "start_date": "2026-10-23", "end_date": "2026-10-30", "manager": "M",
            "supporting_quotes": [msg[:40]], "issues": []}
    return {**base, **fields}


def ok_entry(obj):
    return {"status": 200, "body": {"model": "fixture", "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(obj)}}]}}


FAIL_503 = {"status": 503, "body": "service unavailable"}


class Env:
    """An isolated intake workspace: requests, fixtures, state and deliverables under tmp_path."""

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path
        self.requests = tmp_path / "requests"
        self.requests.mkdir()
        for name in DOCS:
            shutil.copy(REQUESTS / name, self.requests / name)
        self.fixtures = tmp_path / "fixtures"
        self.fixtures.mkdir()
        self.env = {"INTAKE_REQUESTS_DIR": str(self.requests), "INTAKE_STATE_DIR": str(tmp_path / "state"),
                    "INTAKE_DELIVERABLES_DIR": str(tmp_path / "deliverables"),
                    "INTAKE_FIXTURES_DIR": str(self.fixtures), "INTAKE_MAX_RETRIES": "0",
                    "INTAKE_MAX_WORKERS": "4"}
        for k, v in self.env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("INTAKE_PROVIDER", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        self.calls = []
        self.set("IT-001.json", ok_entry(ext(self.msg("IT-001.json"), request_id="IT-001")))
        self.set("IT-002.json", ok_entry(ext(self.msg("IT-002.json"), request_id="IT-002", environment="production",
                                              permission="write")))
        # IT-006: the fixture model was fooled (admin, no issue); routing must still catch it
        self.set("IT-006.json", ok_entry(ext(self.msg("IT-006.json"), request_id="IT-006", permission="admin")))
        self.set("IT-010.json", FAIL_503)
        self.set("IT-016.json", ok_entry(ext(self.msg("IT-016.json"), request_id="IT-061")))

    def msg(self, name):
        return load_export(self.requests / name).message

    def set(self, name, entries):
        (self.fixtures / f"{message_key(self.msg(name))}.json").write_text(json.dumps(entries))

    def settings(self, **extra):
        return load_settings({**self.env, **extra})

    def runner(self, store, settings=None, transport=None):
        settings = settings or self.settings()
        inner = transport or FixtureTransport(settings.fixtures_dir)

        def counting(url, headers, body, timeout):
            self.calls.append(json.loads(body)["messages"][-1]["content"])
            return inner(url, headers, body, timeout)
        return Runner(settings, store, provider=ChatProvider(settings, transport=counting, sleep=lambda s: None))

    def store(self, clock=None):
        return open_store(self.settings(), clock=clock)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def by_id(store):
    return {e["document_id"]: e for e in snapshot(store)["entries"]}


EXPECTED_ROUTES = {
    "IT-001.json": ("review", "System Owner", "review-request"),
    "IT-002.json": ("review", "Security Review", "review-risk-then-owner"),
    "IT-006.json": ("review", "IT Service Desk", "inspect-message"),
    "IT-010.json": ("error", "IT Service Desk", "retry-extraction"),
    "IT-016.json": ("needs-information", "IT Service Desk", "clarify"),
}


def test_first_intake_routes_each_message_and_isolates_failure(env):
    store = env.store()
    summary = env.runner(store).intake()
    assert summary.processed == {"succeeded": 4, "failed": 1} and summary.failed_documents == ["IT-010.json"]
    assert summary.exit_code == 4
    q = by_id(store)
    assert {d: (e["status"], e["owner"], e["next_action"]) for d, e in q.items()} == EXPECTED_ROUTES
    err = q["IT-010.json"]
    assert "provider_unavailable" in err["error"] and err["retry_command"] == "python -m access_intake retry IT-010.json"
    assert err["previous_result"] is None
    assert any("identifier mismatch" in r for r in q["IT-016.json"]["reasons"])


def test_deliverables_agree_with_each_other_and_with_source_bytes(env):
    store = env.store()
    env.runner(store).intake()
    out = env.root / "deliverables"
    write_deliverables(store, env.settings(), out)
    records = [json.loads(line) for line in (out / "extractions.jsonl").read_text().splitlines()]
    queue = json.loads((out / "queue.json").read_text())
    assert [r["document_id"] for r in records] == DOCS
    assert {(e["document_id"], e["source_sha256"], e["attempt_id"]) for e in queue["entries"]} == \
           {(r["document_id"], r["source_sha256"], r["attempt_id"]) for r in records}
    for r in records:
        assert r["source_sha256"] == hashlib.sha256((env.requests / r["document_id"]).read_bytes()).hexdigest()
        assert r["response_source"] == "mock"
        assert set(r) >= {"document_id", "source_sha256", "extraction", "status", "owner", "next_action", "error",
                          "response_source"}
        assert (r["extraction"] is None) == (r["status"] == "error") == (r["error"] is not None)


def test_unchanged_rerun_reuses_results_and_retries_only_the_failure(env):
    store = env.store()
    env.runner(store).intake()
    before = {d: e["attempt_id"] for d, e in by_id(store).items()}
    env.calls.clear()
    summary = env.runner(store).intake()
    assert summary.decisions == {"reused": 4, "queued": 1}
    assert len(env.calls) == 1 and "IT-010" in env.calls[0]
    after = by_id(store)
    assert len(after) == 5
    assert all(after[d]["attempt_id"] == before[d] for d in before if d != "IT-010.json")


def test_changed_source_creates_new_result_and_keeps_old(env):
    store = env.store()
    env.runner(store).intake()
    old = by_id(store)["IT-001.json"]
    p = env.requests / "IT-001.json"
    p.write_bytes(p.read_bytes() + b"\n")  # new bytes, same message: new hash, same fixture
    summary = env.runner(store).intake()
    assert summary.sources["changed"] == 1
    new = by_id(store)["IT-001.json"]
    assert new["source_sha256"] != old["source_sha256"] and new["attempt_id"] != old["attempt_id"]
    assert new["source_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    history = snapshot(store)["history"]
    assert any(h["attempt_id"] == old["attempt_id"] and h["source_sha256"] == old["source_sha256"] for h in history)


def test_failed_retry_after_success_shows_error_and_links_previous_result(env):
    store = env.store()
    env.runner(store).intake()
    good = by_id(store)["IT-001.json"]
    env.set("IT-001.json", FAIL_503)
    summary = env.runner(store).retry(["IT-001.json"])
    assert summary.failed_documents == ["IT-001.json"]
    cur = by_id(store)["IT-001.json"]
    assert cur["status"] == "error" and cur["next_action"] == "retry-extraction"
    prev = cur["previous_result"]
    assert prev["attempt_id"] == good["attempt_id"] and prev["status"] == "review"
    assert prev["source_sha256"] == good["source_sha256"] and prev["same_source_version"] is True


def test_retry_succeeds_once_provider_recovers(env):
    store = env.store()
    env.runner(store).intake()
    env.set("IT-010.json", ok_entry(ext(env.msg("IT-010.json"), request_id="IT-010", environment="production",
                                        end_date=None)))
    summary = env.runner(store).retry(["IT-010.json"])
    assert summary.processed == {"succeeded": 1}
    e = by_id(store)["IT-010.json"]
    assert (e["status"], e["next_action"]) == ("needs-information", "clarify")


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def test_interrupted_attempt_is_resumed_after_lease_expiry(env):
    clock = Clock()
    store = env.store(clock)
    doc = load_export(env.requests / "IT-001.json")
    s = env.settings()
    store.record_source(doc)
    d = store.enqueue(doc.document_id, doc.sha256, s.extraction_fingerprint(), s.routing_fingerprint(), {})
    assert store.claim(d.attempt_id, "crashed-worker", lease_s=30)
    assert snapshot(store)["pending"][0]["in_flight_attempt_id"] == d.attempt_id
    assert env.runner(store).resume().processed == {}  # lease still held: nothing taken over
    clock.t += 31
    summary = env.runner(store).resume()
    assert summary.processed == {"succeeded": 1}
    assert by_id(store)["IT-001.json"]["attempt_id"] == d.attempt_id


def test_model_setting_change_refreshes_everything(env):
    store = env.store()
    env.runner(store).intake()
    env.calls.clear()
    summary = env.runner(store, settings=env.settings(INTAKE_TEMPERATURE="0.2")).intake()
    assert summary.decisions == {"queued": 5} and len(env.calls) == 5


def test_routing_change_reroutes_without_calling_the_model(env, monkeypatch):
    store = env.store()
    env.runner(store).intake()
    monkeypatch.setattr(config, "ROUTING_VERSION", "routing-test")
    env.calls.clear()
    summary = env.runner(store).intake()
    assert summary.decisions == {"reroute": 4, "queued": 1}
    assert len(env.calls) == 1  # only the earlier failure needs the model
    kinds = {a["kind"] for a in store.all_attempts() if a["routing_fingerprint"] == env.settings().routing_fingerprint()}
    assert "reroute" in kinds


def test_slow_message_does_not_block_others(env):
    store = env.store()
    others_done = threading.Event()
    finished = []
    slow_key = message_key(env.msg("IT-002.json"))
    inner = FixtureTransport(env.fixtures)

    def transport(url, headers, body, timeout):
        text = json.loads(body)["messages"][-1]["content"]
        if slow_key == message_key(text.split("<message>\n", 1)[1].rsplit("\n</message>", 1)[0]):
            assert others_done.wait(10), "other messages were blocked behind the slow one"
        result = inner(url, headers, body, timeout)
        finished.append(text)
        if len(finished) == 4:
            others_done.set()
        return result

    env.runner(store, transport=transport).intake()
    assert "IT-002" in finished[-1]


def test_two_operators_at_once_process_each_document_once(env):
    # all succeed here: a failure could legitimately be retried by the second operator
    env.set("IT-010.json", ok_entry(ext(env.msg("IT-010.json"), request_id="IT-010")))
    stores = [env.store(), env.store()]
    runners = [env.runner(s) for s in stores]
    threads = [threading.Thread(target=r.intake) for r in runners]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(env.calls) == 5
    check = env.store()
    assert len(by_id(check)) == 5 and len(check.all_attempts()) == 5


def test_cli_intake_queue_and_show(env, capsys):
    assert main(["intake"]) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["retry_commands"] == ["python -m access_intake retry IT-010.json"]
    assert result["deliverables"]["records"] == 5
    assert main(["queue", "--status", "error"]) == 0
    out = capsys.readouterr().out
    assert "IT-010.json" in out and "IT-001.json" not in out
    assert main(["queue", "--json", "--owner", "security review"]) == 0
    assert [r["document_id"] for r in json.loads(capsys.readouterr().out)] == ["IT-002.json"]
    assert main(["show", "IT-010.json"]) == 0
    assert json.loads(capsys.readouterr().out)["attempts"][0]["state"] == "failed"
    assert main(["show", "NOPE.json"]) == 2


def test_live_provider_without_key_stops_before_work(env, monkeypatch, capsys):
    monkeypatch.setenv("INTAKE_PROVIDER", "databricks")
    assert main(["intake", "--no-export"]) == 2
    assert by_id(env.store()) == {}

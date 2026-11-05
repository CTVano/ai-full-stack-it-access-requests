"""Independent consistency check of the deliverable files against the source exports.

It re-reads the files from disk, without the SQLite store, and recomputes what it can
from the sources: hashes, schema/evidence validity and routes. It is a
different path from the one that wrote the files, so it can catch export bugs.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .routing import ERROR_ROUTE, route
from .sources import discover, load_export
from .validation import validate_extraction

RECORD_KEYS = {"document_id", "source_sha256", "extraction", "status", "owner", "next_action", "error",
               "response_source"}
RESPONSE_SOURCE = re.compile(r"^(mock|[a-z0-9_-]+:[A-Za-z0-9._/-]+)$")
SECRET_PATTERNS = [re.compile(r"Bearer\s+(?!<)[A-Za-z0-9._\-]{8,}"), re.compile(r"dapi[0-9a-f]{16,}")]


def check_deliverables(deliverables_dir: Path, requests_dir: Path) -> list[dict]:
    d = Path(deliverables_dir)
    results: list[dict] = []

    def check(name: str, problems: list[str]) -> None:
        results.append({"check": name, "passed": not problems, "problems": problems[:10]})

    sources = {p.name: load_export(p) for p in discover([Path(requests_dir)])}
    lines = [l for l in (d / "extractions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]
    ids = [r.get("document_id") for r in records]
    by_id = {r["document_id"]: r for r in records}

    check("one record per source export, no duplicates",
          [f"missing record: {s}" for s in sorted(set(sources) - set(ids))]
          + [f"record without source: {i}" for i in sorted(set(ids) - set(sources))]
          + [f"duplicate record: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})])
    check("records carry the required keys",
          [f"{r.get('document_id')}: missing {sorted(RECORD_KEYS - set(r))}" for r in records if RECORD_KEYS - set(r)])
    check("source_sha256 equals SHA-256 of the export bytes",
          [f"{i}: hash differs" for i, r in by_id.items() if i in sources and r["source_sha256"] != sources[i].sha256])
    check("error <=> extraction null <=> status error",
          [f"{i}: inconsistent error/extraction/status" for i, r in by_id.items()
           if not ((r["extraction"] is None) == (r["error"] is not None) == (r["status"] == "error"))])
    check("response_source is 'mock' or '<provider>:<model>'",
          [f"{i}: {r['response_source']!r}" for i, r in by_id.items()
           if not RESPONSE_SOURCE.match(str(r["response_source"]))])

    schema_problems, route_problems = [], []
    for i, r in by_id.items():
        if i not in sources:
            continue
        x = r["extraction"]
        if x is None:
            want = (ERROR_ROUTE.status, ERROR_ROUTE.owner, ERROR_ROUTE.next_action)
        else:
            schema_problems += [f"{i}: {p}" for p in validate_extraction(x, sources[i].message)]
            rt = route(i, sources[i].message, x)
            want = (rt.status, rt.owner, rt.next_action)
        if (r["status"], r["owner"], r["next_action"]) != want:
            route_problems.append(f"{i}: recorded {(r['status'], r['owner'], r['next_action'])}, policy gives {want}")
    check("extractions conform to the schema; quotes are exact substrings of the message", schema_problems)
    check("status/owner/next_action equal the policy applied to the recorded extraction", route_problems)

    queue = json.loads((d / "queue.json").read_text(encoding="utf-8"))
    entries = {e["document_id"]: e for e in queue["entries"]}
    link = []
    for i, r in by_id.items():
        e = entries.get(i)
        if e is None:
            link.append(f"{i}: no queue entry")
            continue
        for k in ("source_sha256", "status", "owner", "next_action", "error", "response_source", "attempt_id"):
            if k in r and e.get(k) != r.get(k):
                link.append(f"{i}: queue {k}={e.get(k)!r} but record {r.get(k)!r}")
        if r["status"] == "error" and not e.get("retry_command", "").endswith(f"retry {i}"):
            link.append(f"{i}: error entry without retry command")
    link += [f"{i}: queue entry without record" for i in sorted(set(entries) - set(by_id))]
    check("queue.json entries link 1:1 to records (same source hash, attempt, route, error)", link)
    check("queue counts match its contents",
          [] if queue["counts"] == {"entries": len(queue["entries"]), "pending": len(queue["pending"])}
          else [f"counts {queue['counts']}"])

    leaks = []
    key = os.environ.get("API_KEY")
    for f in sorted(p for p in d.iterdir() if p.is_file()):
        text = f.read_text(encoding="utf-8", errors="replace")
        if key and len(key) >= 8 and key in text:
            leaks.append(f"{f.name}: contains API_KEY value")
        leaks += [f"{f.name}: matches {p.pattern}" for p in SECRET_PATTERNS if p.search(text)]
    ev = d / "request-evidence.json"
    if ev.exists() and json.loads(ev.read_text())["request"]["headers"].get("Authorization") != "[REDACTED]":
        leaks.append("request-evidence.json: Authorization header not redacted")
    check("no credentials in any deliverable", leaks)
    return results

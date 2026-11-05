"""Command-line interface: ``python -m access_intake <command>``.

Exit codes: 0 ok, 2 configuration/input error, 4 finished but some documents failed.
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, load_settings
from .export import snapshot, write_deliverables
from .redaction import configure_logging
from .runner import Runner, open_store
from .sources import SourceError

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAILURES = 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="access_intake", description="Northstar IT access-request intake")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="Show effective settings and fingerprints (no secrets)")

    intake = sub.add_parser("intake", help="Process new or changed exports; reuse unchanged results")
    intake.add_argument("paths", nargs="*", help="Export files or folders (default: INTAKE_REQUESTS_DIR)")

    resume = sub.add_parser("resume", help="Continue pending/interrupted work from recorded state")

    retry = sub.add_parser("retry", help="Re-extract specific documents now (latest source version)")
    retry.add_argument("document_ids", nargs="+")

    for cmd in (intake, resume, retry):
        cmd.add_argument("--no-export", action="store_true", help="Do not rewrite deliverables afterwards")

    queue = sub.add_parser("queue", help="Show the current review queue (completed and pending)")
    queue.add_argument("--status", help="Filter: review, needs-information, error, pending")
    queue.add_argument("--owner", help="Filter by owner, e.g. 'Security Review'")
    queue.add_argument("--json", action="store_true", help="Machine-readable output")

    show = sub.add_parser("show", help="Show a document's current record and full attempt history")
    show.add_argument("document_id")

    sub.add_parser("export", help="Write deliverables/extractions.jsonl, queue.json and history.jsonl")

    acc = sub.add_parser("accuracy", help="Compare extraction records with eval/expected.json")
    acc.add_argument("--extractions", default=None, help="default: <INTAKE_DELIVERABLES_DIR>/extractions.jsonl")
    acc.add_argument("--expected", default="eval/expected.json")
    acc.add_argument("--out", default=None, help="Write the full JSON report here")

    ev = sub.add_parser("evidence", help="Write the redacted example request/response handling evidence")
    ev.add_argument("--document", default="materials/requests/IT-001.json")
    ev.add_argument("--out", default=None, help="default: <INTAKE_DELIVERABLES_DIR>/request-evidence.json")

    sub.add_parser("verify", help="Check the deliverable files against the source exports (exit 1 on failure)")
    return p


def _print_queue(snap: dict, status: str | None, owner: str | None, as_json: bool) -> None:
    rows = [dict(e, state="done") for e in snap["entries"]]
    rows += [dict(p, status="pending", owner="-", next_action="-", request_id=None, state="pending")
             for p in snap["pending"]]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if owner:
        rows = [r for r in rows if r["owner"].lower() == owner.lower()]
    rows.sort(key=lambda r: r["document_id"])
    if as_json:
        json.dump(rows, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    cols = [("document_id", 12), ("request_id", 10), ("status", 17), ("owner", 16), ("next_action", 22),
            ("source", 12), ("note", 0)]
    print("  ".join(name.ljust(w) for name, w in cols).rstrip())
    for r in rows:
        note = ""
        if r.get("error"):
            note = r["error"].split(". Retry:")[0][:90]
        elif r.get("newer_source_pending"):
            note = "newer source version pending"
        elif r["state"] == "pending":
            note = "in progress" if r.get("in_flight_attempt_id") else "not started"
        vals = [r["document_id"], r.get("request_id") or "-", r["status"], r["owner"], r["next_action"],
                r["source_sha256"][:12], note]
        print("  ".join(str(v).ljust(w) for v, (_, w) in zip(vals, cols)).rstrip())
    done = sum(1 for r in rows if r["state"] == "done")
    print(f"\n{done} completed, {len(rows) - done} pending")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = configure_logging(args.log_level)
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return EXIT_CONFIG

    if args.command == "config":
        json.dump(settings.describe(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK

    if args.command == "accuracy":
        from .accuracy import evaluate, load_expected, load_records
        from .config import REPO_ROOT
        from pathlib import Path
        records_path = Path(args.extractions) if args.extractions else settings.deliverables_dir / "extractions.jsonl"
        expected_path = Path(args.expected)
        expected_path = expected_path if expected_path.is_absolute() else REPO_ROOT / expected_path
        report = evaluate(load_expected(expected_path), load_records(records_path))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary = {k: v for k, v in report.items() if k != "per_document"}
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return EXIT_OK

    if args.command == "verify":
        from .verify import check_deliverables
        results = check_deliverables(settings.deliverables_dir, settings.requests_dir)
        for r in results:
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['check']}")
            for p in r["problems"]:
                print(f"      {p}")
        ok = all(r["passed"] for r in results)
        print(f"\n{sum(r['passed'] for r in results)}/{len(results)} checks passed")
        return EXIT_OK if ok else 1

    if args.command == "evidence":
        import os
        from pathlib import Path
        from .config import REPO_ROOT
        from .evidence import build_evidence
        from .redaction import redact
        doc = Path(args.document)
        doc = doc if doc.is_absolute() else REPO_ROOT / doc
        out = Path(args.out) if args.out else settings.deliverables_dir / "request-evidence.json"
        env = {k: v for k, v in os.environ.items() if k.startswith("INTAKE_")}
        text = redact(json.dumps(build_evidence(doc, settings.fixtures_dir, env), indent=2, sort_keys=False) + "\n")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(json.dumps({"written": str(out)}))
        return EXIT_OK

    store = open_store(settings)
    try:
        if args.command in ("intake", "resume", "retry"):
            runner = Runner(settings, store)
            if args.command == "intake":
                summary = runner.intake(args.paths or None)
            elif args.command == "resume":
                summary = runner.resume()
            else:
                summary = runner.retry(args.document_ids)
            result = summary.as_dict()
            if not args.no_export:
                result["deliverables"] = write_deliverables(store, settings)
            json.dump(result, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return summary.exit_code
        if args.command == "queue":
            _print_queue(snapshot(store), args.status, args.owner, args.json)
            return EXIT_OK
        if args.command == "show":
            hist = store.history(args.document_id)
            if not hist:
                log.error("unknown document_id %r", args.document_id)
                return EXIT_CONFIG
            current = next(r for r in store.current() if r["document_id"] == args.document_id)
            json.dump({"current_attempt_id": current["current_attempt_id"], "latest_sha256": current["latest_sha256"],
                       "attempts": hist}, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return EXIT_OK
        if args.command == "export":
            json.dump(write_deliverables(store, settings), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return EXIT_OK
    except (ConfigError, SourceError, KeyError) as exc:
        log.error("%s", exc)
        return EXIT_CONFIG
    finally:
        store.close()
    return EXIT_CONFIG

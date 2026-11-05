"""Views over the store: queue entries, extraction records and history, and deliverable files.

The queue entries and the extraction records are built from the same single
read of the current rows, so for every document they always name the same
attempt and source hash.
Files are written atomically (temp file + rename).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .runner import retry_command
from .sources import parse_export
from .store import Store


def _subject(store: Store, document_id: str, sha: str) -> str | None:
    raw = store.source_raw(document_id, sha)
    return parse_export(document_id, document_id, raw).metadata.get("subject") if raw is not None else None


def extraction_record(row: dict) -> dict:
    return {
        "document_id": row["document_id"],
        "source_sha256": row["source_sha256"],
        "extraction": row["extraction"],
        "status": row["status"],
        "owner": row["owner"],
        "next_action": row["next_action"],
        "error": row["error"],
        "response_source": row["response_source"],
        "attempt_id": row["current_attempt_id"],
        "completed_at": row["finished_at"],
        "extraction_fingerprint": row["extraction_fingerprint"],
        "routing_fingerprint": row["routing_fingerprint"],
    }


def snapshot(store: Store) -> dict:
    """Current view: completed entries, pending documents, and the matching extraction records."""
    rows = store.current()
    entries, records, pending = [], [], []
    for row in rows:
        doc_id = row["document_id"]
        if row["current_attempt_id"] is None:
            pending.append({"document_id": doc_id, "source_sha256": row["latest_sha256"],
                            "in_flight_attempt_id": row["in_flight_attempt_id"],
                            "subject": _subject(store, doc_id, row["latest_sha256"])})
            continue
        records.append(extraction_record(row))
        x = row["extraction"] or {}
        entry = {
            "document_id": doc_id,
            "source_sha256": row["source_sha256"],
            "request_id": x.get("request_id"),
            "subject": _subject(store, doc_id, row["source_sha256"]),
            "status": row["status"],
            "owner": row["owner"],
            "next_action": row["next_action"],
            "route_rule": row["route_rule"],
            "reasons": row["route_reasons"],
            "error": row["error"],
            "response_source": row["response_source"],
            "attempt_id": row["current_attempt_id"],
            "completed_at": row["finished_at"],
            "newer_source_pending": row["source_sha256"] != row["latest_sha256"],
            "in_flight_attempt_id": row["in_flight_attempt_id"],
        }
        if row["status"] == "error":
            entry["retry_command"] = retry_command(doc_id)
            prior = store.last_success(doc_id, before_attempt_id=row["current_attempt_id"])
            entry["previous_result"] = None if prior is None else {
                "attempt_id": prior["attempt_id"], "source_sha256": prior["source_sha256"],
                "status": prior["status"], "owner": prior["owner"], "next_action": prior["next_action"],
                "completed_at": prior["finished_at"],
                "same_source_version": prior["source_sha256"] == row["source_sha256"]}
        entries.append(entry)
    current_ids = {r["current_attempt_id"] for r in rows if r["current_attempt_id"] is not None}
    history = [{k: a[k] for k in ("attempt_id", "document_id", "source_sha256", "kind", "state", "status", "owner",
                                  "next_action", "error", "response_source", "extraction", "finished_at",
                                  "extraction_fingerprint", "routing_fingerprint")}
               for a in store.all_attempts() if a["attempt_id"] not in current_ids and a["state"] in ("succeeded", "failed")]
    return {"entries": entries, "records": records, "pending": pending, "history": history}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_deliverables(store: Store, settings: Settings, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or settings.deliverables_dir
    snap = snapshot(store)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    queue = {
        "generated_at": generated,
        "policy": "materials/office-policy.md",
        "settings": {k: v for k, v in settings.describe().items()
                     if k in ("response_source", "extraction_fingerprint", "routing_fingerprint",
                              "extraction_inputs", "routing_inputs")},
        "counts": {"entries": len(snap["entries"]), "pending": len(snap["pending"])},
        "entries": snap["entries"],
        "pending": snap["pending"],
    }
    _atomic_write(out_dir / "extractions.jsonl",
                  "".join(json.dumps(r, sort_keys=True) + "\n" for r in snap["records"]))
    _atomic_write(out_dir / "queue.json", json.dumps(queue, indent=2, sort_keys=True) + "\n")
    _atomic_write(out_dir / "history.jsonl",
                  "".join(json.dumps(h, sort_keys=True) + "\n" for h in snap["history"]))
    return {"out_dir": str(out_dir), "records": len(snap["records"]), "pending": len(snap["pending"]),
            "history": len(snap["history"])}

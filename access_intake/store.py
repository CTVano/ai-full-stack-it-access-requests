"""Durable intake state (SQLite).

Tables
  source_versions  every distinct (document_id, sha256) ever seen, with the original bytes
  attempts         every processing attempt: append-only history of results and errors
  documents        one row per document: latest source hash seen + pointer to the current attempt

"Current" output is documents.current_attempt_id; everything else in attempts is
history, so prior versions are stored separately from the current view.

Concurrency model
  * Every write is a short BEGIN IMMEDIATE transaction, so enqueue decisions are
    serialised and two operators cannot create duplicate attempts.
  * Workers claim an attempt with a lease. An attempt left 'running' by a
    stopped process becomes claimable again once its lease expires (resume).
  * Only the lease holder can complete an attempt; a late result from a worker
    that lost its lease is rejected.
  * An attempt becomes current only if its source hash is the latest seen for
    the document and no newer attempt for that same source is already current.
  Locks are reliable for processes on one host (verified on the Databricks
  workspace filesystem). Operators on different clusters must share one intake
  job/compute rather than writing the same state file concurrently.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .sources import SourceDoc

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_versions (
  document_id   TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  path          TEXT NOT NULL,
  size          INTEGER NOT NULL,
  raw           BLOB NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (document_id, sha256)
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id             INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id            TEXT NOT NULL,
  source_sha256          TEXT NOT NULL,
  extraction_fingerprint TEXT NOT NULL,
  routing_fingerprint    TEXT NOT NULL,
  settings_json          TEXT NOT NULL,
  kind                   TEXT NOT NULL CHECK (kind IN ('extract', 'reroute')),
  based_on_attempt_id    INTEGER REFERENCES attempts(attempt_id),
  state                  TEXT NOT NULL CHECK (state IN ('pending', 'running', 'succeeded', 'failed')),
  tries                  INTEGER NOT NULL DEFAULT 0,
  claimed_by             TEXT,
  lease_expires_at       REAL,
  response_source        TEXT,
  extraction_json        TEXT,
  status                 TEXT,
  owner                  TEXT,
  next_action            TEXT,
  error                  TEXT,
  route_rule             TEXT,
  route_reasons_json     TEXT,
  created_at             TEXT NOT NULL,
  started_at             TEXT,
  finished_at            TEXT,
  FOREIGN KEY (document_id, source_sha256) REFERENCES source_versions(document_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_attempts_doc   ON attempts(document_id, source_sha256, extraction_fingerprint, attempt_id);
CREATE INDEX IF NOT EXISTS ix_attempts_state ON attempts(state);
CREATE TABLE IF NOT EXISTS documents (
  document_id        TEXT PRIMARY KEY,
  latest_sha256      TEXT NOT NULL,
  latest_seen_at     TEXT NOT NULL,
  current_attempt_id INTEGER REFERENCES attempts(attempt_id)
);
"""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Decision:
    """Outcome of enqueue(): what intake must do for one document version."""
    action: str  # 'queued' | 'already_queued' | 'reused' | 'reroute'
    attempt_id: int
    based_on_attempt_id: int | None = None


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    if "extraction_json" in d:
        raw = d.pop("extraction_json")
        d["extraction"] = json.loads(raw) if raw is not None else None
    if "settings_json" in d:
        raw = d.pop("settings_json")
        d["settings"] = json.loads(raw) if raw is not None else None
    if "route_reasons_json" in d:
        raw = d.pop("route_reasons_json")
        d["route_reasons"] = json.loads(raw) if raw is not None else []
    return d


class Store:
    def __init__(self, db_path: str | Path, clock: Callable[[], float] = time.time):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._tx() as c:
            for stmt in filter(str.strip, SCHEMA.split(";")):
                c.execute(stmt)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ writes
    def record_source(self, doc: SourceDoc) -> str:
        """Register a source version; returns 'new', 'changed' or 'unchanged'."""
        now = self.clock()
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO source_versions (document_id, sha256, path, size, raw, first_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (doc.document_id, doc.sha256, doc.path, doc.size, doc.raw, iso(now)),
            )
            row = c.execute("SELECT latest_sha256 FROM documents WHERE document_id = ?", (doc.document_id,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO documents (document_id, latest_sha256, latest_seen_at) VALUES (?, ?, ?)",
                    (doc.document_id, doc.sha256, iso(now)),
                )
                return "new"
            c.execute(
                "UPDATE documents SET latest_sha256 = ?, latest_seen_at = ? WHERE document_id = ?",
                (doc.sha256, iso(now), doc.document_id),
            )
            return "unchanged" if row["latest_sha256"] == doc.sha256 else "changed"

    def enqueue(self, document_id: str, sha256: str, extraction_fp: str, routing_fp: str,
                settings: dict) -> Decision:
        """Decide atomically whether this document version needs work, and queue it.

        Looks at the latest attempt for the same (document, source hash, extraction settings):
          pending/running -> already_queued (resume/claim will pick it up)
          succeeded       -> reused, or reroute if only routing inputs changed
          failed / none   -> queued (a new extraction attempt)
        """
        now = self.clock()
        with self._tx() as c:
            last = c.execute(
                "SELECT attempt_id, state, routing_fingerprint FROM attempts"
                " WHERE document_id = ? AND source_sha256 = ? AND extraction_fingerprint = ?"
                " ORDER BY attempt_id DESC LIMIT 1",
                (document_id, sha256, extraction_fp),
            ).fetchone()
            if last is not None and last["state"] in ("pending", "running"):
                return Decision("already_queued", last["attempt_id"])
            if last is not None and last["state"] == "succeeded":
                if last["routing_fingerprint"] == routing_fp:
                    self._set_current(c, document_id, last["attempt_id"], sha256, force=True)
                    return Decision("reused", last["attempt_id"])
                aid = self._insert_attempt(c, document_id, sha256, extraction_fp, routing_fp, settings,
                                           "reroute", last["attempt_id"], now)
                return Decision("reroute", aid, last["attempt_id"])
            aid = self._insert_attempt(c, document_id, sha256, extraction_fp, routing_fp, settings,
                                       "extract", None, now)
            return Decision("queued", aid)

    def force_attempt(self, document_id: str, extraction_fp: str, routing_fp: str, settings: dict) -> int:
        """Queue a fresh extraction of the document's latest source (explicit operator retry)."""
        now = self.clock()
        with self._tx() as c:
            doc = c.execute("SELECT latest_sha256 FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if doc is None:
                raise KeyError(f"unknown document_id {document_id!r}")
            return self._insert_attempt(c, document_id, doc["latest_sha256"], extraction_fp, routing_fp,
                                        settings, "extract", None, now)

    def claim(self, attempt_id: int, worker: str, lease_s: float) -> bool:
        now = self.clock()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE attempts SET state = 'running', claimed_by = ?, lease_expires_at = ?, tries = tries + 1,"
                " started_at = COALESCE(started_at, ?)"
                " WHERE attempt_id = ? AND (state = 'pending' OR (state = 'running' AND lease_expires_at < ?))",
                (worker, now + lease_s, iso(now), attempt_id, now),
            )
            return cur.rowcount == 1

    def renew(self, attempt_id: int, worker: str, lease_s: float) -> bool:
        now = self.clock()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE attempts SET lease_expires_at = ? WHERE attempt_id = ? AND state = 'running' AND claimed_by = ?",
                (now + lease_s, attempt_id, worker),
            )
            return cur.rowcount == 1

    def complete(self, attempt_id: int, worker: str, *, response_source: str | None,
                 extraction: dict | None, status: str, owner: str, next_action: str,
                 error: str | None, route_rule: str | None = None,
                 route_reasons: list[str] | tuple[str, ...] = ()) -> bool:
        """Record the outcome. Returns False if the worker no longer holds the lease."""
        if error is None and extraction is None:
            raise ValueError("a successful attempt needs an extraction")
        if error is not None and extraction is not None:
            raise ValueError("a failed attempt must have a null extraction")
        state = "succeeded" if error is None else "failed"
        now = self.clock()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE attempts SET state = ?, response_source = ?, extraction_json = ?, status = ?, owner = ?,"
                " next_action = ?, error = ?, route_rule = ?, route_reasons_json = ?, finished_at = ?,"
                " lease_expires_at = NULL"
                " WHERE attempt_id = ? AND state = 'running' AND claimed_by = ?",
                (state, response_source, None if extraction is None else json.dumps(extraction, sort_keys=True),
                 status, owner, next_action, error, route_rule, json.dumps(list(route_reasons)), iso(now),
                 attempt_id, worker),
            )
            if cur.rowcount != 1:
                return False
            row = c.execute("SELECT document_id, source_sha256 FROM attempts WHERE attempt_id = ?",
                            (attempt_id,)).fetchone()
            self._set_current(c, row["document_id"], attempt_id, row["source_sha256"])
            return True

    def supersede_stale(self, extraction_fp: str, routing_fp: str) -> int:
        """Cancel queued work that can no longer become current.

        Pending (or lease-expired running) attempts are cancelled when they were
        queued under different settings, or for a source version that is no
        longer the latest. Cancelled attempts are kept as failed history rows
        and never change the current pointer. Attempts actively held by another
        worker are left alone.
        """
        now = self.clock()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE attempts SET state = 'failed', status = 'error', owner = 'IT Service Desk',"
                " next_action = 'retry-extraction', finished_at = ?, lease_expires_at = NULL,"
                " error = 'superseded: queued under other settings or for an outdated source version; not run'"
                " WHERE (state = 'pending' OR (state = 'running' AND lease_expires_at < ?))"
                " AND (extraction_fingerprint != ? OR routing_fingerprint != ?"
                "      OR source_sha256 != (SELECT latest_sha256 FROM documents d"
                "                           WHERE d.document_id = attempts.document_id))",
                (iso(now), now, extraction_fp, routing_fp),
            )
            return cur.rowcount

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _insert_attempt(c, document_id, sha256, extraction_fp, routing_fp, settings, kind, based_on, now) -> int:
        cur = c.execute(
            "INSERT INTO attempts (document_id, source_sha256, extraction_fingerprint, routing_fingerprint,"
            " settings_json, kind, based_on_attempt_id, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (document_id, sha256, extraction_fp, routing_fp, json.dumps(settings, sort_keys=True),
             kind, based_on, iso(now)),
        )
        return cur.lastrowid

    @staticmethod
    def _set_current(c, document_id: str, attempt_id: int, sha256: str, force: bool = False) -> bool:
        doc = c.execute("SELECT latest_sha256, current_attempt_id FROM documents WHERE document_id = ?",
                        (document_id,)).fetchone()
        if doc is None or doc["latest_sha256"] != sha256:
            return False  # result for an outdated source version: history only
        cur_id = doc["current_attempt_id"]
        if not force and cur_id is not None and cur_id > attempt_id:
            cur_sha = c.execute("SELECT source_sha256 FROM attempts WHERE attempt_id = ?", (cur_id,)).fetchone()[0]
            if cur_sha == sha256:
                return False  # a newer attempt for the same source is already current
        c.execute("UPDATE documents SET current_attempt_id = ? WHERE document_id = ?", (attempt_id, document_id))
        return True

    # ------------------------------------------------------------------- reads
    def get_attempt(self, attempt_id: int) -> dict | None:
        with self._lock:
            return _row(self._conn.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone())

    def claimable(self) -> list[int]:
        """Pending attempts plus 'running' ones whose lease expired (interrupted work)."""
        now = self.clock()
        with self._lock:
            rows = self._conn.execute(
                "SELECT attempt_id FROM attempts"
                " WHERE state = 'pending' OR (state = 'running' AND lease_expires_at < ?) ORDER BY attempt_id",
                (now,),
            ).fetchall()
        return [r[0] for r in rows]

    def current(self) -> list[dict]:
        """One row per document: its current attempt (if any) plus any work still in flight."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.document_id, d.latest_sha256, d.latest_seen_at, d.current_attempt_id,"
                " a.source_sha256, a.state, a.kind, a.response_source, a.extraction_json, a.status, a.owner,"
                " a.next_action, a.error, a.finished_at, a.extraction_fingerprint, a.routing_fingerprint,"
                " a.route_rule, a.route_reasons_json, a.settings_json,"
                " (SELECT MAX(p.attempt_id) FROM attempts p WHERE p.document_id = d.document_id"
                "   AND p.state IN ('pending', 'running')) AS in_flight_attempt_id"
                " FROM documents d LEFT JOIN attempts a ON a.attempt_id = d.current_attempt_id"
                " ORDER BY d.document_id"
            ).fetchall()
        return [_row(r) for r in rows]

    def latest_sources(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT document_id, latest_sha256 FROM documents ORDER BY document_id").fetchall()
        return [(r[0], r[1]) for r in rows]

    def all_attempts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attempts ORDER BY attempt_id").fetchall()
        return [_row(r) for r in rows]

    def history(self, document_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attempts WHERE document_id = ? ORDER BY attempt_id",
                                      (document_id,)).fetchall()
        return [_row(r) for r in rows]

    def last_success(self, document_id: str, before_attempt_id: int | None = None) -> dict | None:
        sql = "SELECT * FROM attempts WHERE document_id = ? AND state = 'succeeded'"
        args: list = [document_id]
        if before_attempt_id is not None:
            sql += " AND attempt_id < ?"
            args.append(before_attempt_id)
        with self._lock:
            return _row(self._conn.execute(sql + " ORDER BY attempt_id DESC LIMIT 1", args).fetchone())

    def source_raw(self, document_id: str, sha256: str) -> bytes | None:
        with self._lock:
            r = self._conn.execute("SELECT raw FROM source_versions WHERE document_id = ? AND sha256 = ?",
                                   (document_id, sha256)).fetchone()
        return None if r is None else bytes(r[0])

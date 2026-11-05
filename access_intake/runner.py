"""Intake orchestration: register sources, decide work, process messages independently.

Each message is claimed, extracted, routed and completed on its own, in a
thread pool of INTAKE_MAX_WORKERS. A slow or failing message only occupies one
worker; every other message keeps moving and its result is visible in the
queue as soon as it completes.
"""
from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .errors import ExtractionFailure
from .extraction import extract
from .providers import ChatProvider
from .redaction import redact
from .routing import ERROR_ROUTE, route
from .sources import discover, load_export, parse_export
from .store import Store, new_worker_id

log = logging.getLogger("access_intake")

SNAPSHOT_KEYS = ("provider", "model", "response_source", "temperature", "max_tokens", "response_format",
                 "extraction_fingerprint", "routing_fingerprint", "extraction_inputs", "routing_inputs")


def retry_command(document_id: str) -> str:
    return f"python -m access_intake retry {document_id}"


def open_store(settings: Settings, clock=None) -> Store:
    path = settings.state_dir / "intake.sqlite3"
    return Store(path) if clock is None else Store(path, clock=clock)


def lease_seconds(settings: Settings) -> float:
    """Upper bound for one attempt: every HTTP try times out, plus maximum backoff, plus margin."""
    return settings.timeout_s * (settings.max_retries + 1) + 60.0 * settings.max_retries + 60.0


@dataclass
class RunSummary:
    sources: Counter = field(default_factory=Counter)
    decisions: Counter = field(default_factory=Counter)
    processed: Counter = field(default_factory=Counter)
    superseded: int = 0
    failed_documents: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 4 if self.failed_documents else 0

    def as_dict(self) -> dict:
        return {"sources": dict(self.sources), "decisions": dict(self.decisions),
                "processed": dict(self.processed), "superseded": self.superseded,
                "failed_documents": sorted(self.failed_documents),
                "retry_commands": [retry_command(d) for d in sorted(self.failed_documents)]}


class Runner:
    def __init__(self, settings: Settings, store: Store, provider: ChatProvider | None = None,
                 worker: str | None = None):
        self.settings = settings
        self.store = store
        self.provider = provider or ChatProvider(settings)
        self.worker = worker or new_worker_id()
        self.efp = settings.extraction_fingerprint()
        self.rfp = settings.routing_fingerprint()
        described = settings.describe()
        self.snapshot = {k: described[k] for k in SNAPSHOT_KEYS}
        self.lease = lease_seconds(settings)

    # ------------------------------------------------------------ commands
    def intake(self, paths: list[str | Path] | None = None) -> RunSummary:
        summary = RunSummary()
        for f in discover(paths or [self.settings.requests_dir]):
            doc = load_export(f)
            change = self.store.record_source(doc)
            decision = self.store.enqueue(doc.document_id, doc.sha256, self.efp, self.rfp, self.snapshot)
            summary.sources[change] += 1
            summary.decisions[decision.action] += 1
            log.info("%s source=%s sha256=%s decision=%s", doc.document_id, change, doc.sha256[:12], decision.action)
        self._process(summary)
        return summary

    def resume(self) -> RunSummary:
        """Continue from recorded state: latest known source of every document, current settings."""
        summary = RunSummary()
        for document_id, sha in self.store.latest_sources():
            decision = self.store.enqueue(document_id, sha, self.efp, self.rfp, self.snapshot)
            summary.decisions[decision.action] += 1
        self._process(summary)
        return summary

    def retry(self, document_ids: list[str]) -> RunSummary:
        summary = RunSummary()
        ids = set()
        for document_id in document_ids:
            ids.add(self.store.force_attempt(document_id, self.efp, self.rfp, self.snapshot))
            summary.decisions["queued"] += 1
        self._process(summary, only=ids)
        return summary

    # ---------------------------------------------------------- processing
    def _process(self, summary: RunSummary, only: set[int] | None = None) -> None:
        summary.superseded = self.store.supersede_stale(self.efp, self.rfp)
        ids = [a for a in self.store.claimable() if only is None or a in only]
        if not ids:
            return
        if any(self.store.get_attempt(a)["kind"] == "extract" for a in ids):
            self.settings.require_live()  # fail once, before any work, if credentials are missing
        with ThreadPoolExecutor(max_workers=min(self.settings.max_workers, len(ids))) as pool:
            futures = [pool.submit(self._process_one, a) for a in ids]
            for fut in as_completed(futures):
                outcome, document_id = fut.result()
                summary.processed[outcome] += 1
                if outcome == "failed":
                    summary.failed_documents.append(document_id)

    def _process_one(self, attempt_id: int) -> tuple[str, str | None]:
        if not self.store.claim(attempt_id, self.worker, self.lease):
            return "skipped", None  # another operator holds it
        att = self.store.get_attempt(attempt_id)
        document_id = att["document_id"]
        try:
            doc = parse_export(document_id, document_id, self.store.source_raw(document_id, att["source_sha256"]))
            if att["kind"] == "reroute":
                base = self.store.get_attempt(att["based_on_attempt_id"])
                extraction, source = base["extraction"], base["response_source"]
            else:
                outcome = extract(doc, self.provider, self.settings)
                extraction, source = outcome.extraction, outcome.response_source
            r = route(document_id, doc.message, extraction)
        except ExtractionFailure as exc:
            error = f"{exc.describe()}. Retry: {retry_command(document_id)}"
        except Exception as exc:  # unexpected: still record it so the document is not lost
            log.exception("%s attempt %s crashed", document_id, attempt_id)
            error = f"internal_error: {type(exc).__name__}: {exc}. Retry: {retry_command(document_id)}"
        else:
            done = self.store.complete(attempt_id, self.worker, response_source=source, extraction=extraction,
                                       status=r.status, owner=r.owner, next_action=r.next_action, error=None,
                                       route_rule=r.rule, route_reasons=r.reasons)
            log.info("%s attempt=%s %s -> %s / %s / %s", document_id, attempt_id, att["kind"],
                     r.status, r.owner, r.next_action)
            return ("succeeded" if done else "lost_lease"), document_id

        error = redact(error)
        done = self.store.complete(attempt_id, self.worker, response_source=self.settings.response_source,
                                   extraction=None, status=ERROR_ROUTE.status, owner=ERROR_ROUTE.owner,
                                   next_action=ERROR_ROUTE.next_action, error=error, route_rule=ERROR_ROUTE.rule)
        log.warning("%s attempt=%s failed: %s", document_id, attempt_id, error)
        return ("failed" if done else "lost_lease"), document_id

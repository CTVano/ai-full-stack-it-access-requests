"""Loading message exports.

Policy: document_id is the input filename; the source hash is the SHA-256 of
the original file bytes; business facts come only from the decoded ``message``
string. A malformed export is still returned (with ``load_error`` set) so that
it becomes a visible, retryable error record instead of silently disappearing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

METADATA_FIELDS = ("export_version", "exported_at", "source_channel", "subject")


class SourceError(ValueError):
    """Problem with the set of input paths (not with one export's content)."""


@dataclass(frozen=True)
class SourceDoc:
    document_id: str
    path: str
    sha256: str
    size: int
    raw: bytes = field(repr=False)
    message: str | None
    metadata: dict
    load_error: str | None

    @property
    def ok(self) -> bool:
        return self.load_error is None


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_export(document_id: str, path: str, raw: bytes) -> SourceDoc:
    message: str | None = None
    metadata: dict = {}
    error: str | None = None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        error = f"export is not valid UTF-8 (byte {exc.start})"
    except json.JSONDecodeError as exc:
        error = f"export is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
    else:
        if not isinstance(data, dict):
            error = "export must be a JSON object"
        else:
            metadata = {k: data[k] for k in METADATA_FIELDS if isinstance(data.get(k), str)}
            value = data.get("message")
            if not isinstance(value, str) or not value.strip():
                error = "export has no non-empty string 'message'"
            else:
                message = value
    return SourceDoc(document_id, path, sha256_bytes(raw), len(raw), raw, message, metadata, error)


def load_export(path: str | Path) -> SourceDoc:
    p = Path(path)
    return parse_export(p.name, str(p), p.read_bytes())


def discover(paths: Iterable[str | Path]) -> list[Path]:
    """Expand files/folders into export files, sorted, one path per document_id."""
    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found.extend(sorted(x for x in p.iterdir() if x.is_file() and x.suffix.lower() == ".json"))
        elif p.is_file():
            found.append(p)
        else:
            raise SourceError(f"input path not found: {p}")

    by_id: dict[str, Path] = {}
    for f in found:
        prior = by_id.get(f.name)
        if prior is not None and prior.resolve() != f.resolve():
            raise SourceError(
                f"two inputs share document_id {f.name!r}: {prior} and {f}; process them in separate batches"
            )
        by_id.setdefault(f.name, f)
    return [by_id[k] for k in sorted(by_id)]

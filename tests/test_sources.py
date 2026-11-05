import hashlib
from pathlib import Path

import pytest

from access_intake.sources import SourceError, discover, load_export, parse_export

REQUESTS = Path(__file__).resolve().parent.parent / "materials" / "requests"


def test_hash_is_of_original_bytes_and_id_is_filename():
    path = REQUESTS / "IT-001.json"
    doc = load_export(path)
    assert doc.ok
    assert doc.document_id == "IT-001.json"
    assert doc.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert doc.size == len(path.read_bytes())
    assert doc.message.startswith("Maya Chen from Analytics")
    assert doc.metadata["subject"] == "Dashboard event checks"


def test_document_identity_is_distinct_from_stated_request_id():
    doc = load_export(REQUESTS / "IT-016.json")
    assert doc.document_id == "IT-016.json"
    assert "IT-061" in doc.message  # stated id differs; extraction/routing handle the mismatch


def test_whitespace_change_changes_hash():
    raw = (REQUESTS / "IT-001.json").read_bytes()
    assert parse_export("IT-001.json", "x", raw).sha256 != parse_export("IT-001.json", "x", raw + b"\n").sha256


@pytest.mark.parametrize("raw, expected", [
    (b'{"message": ', "not valid JSON"),
    (b"\xff\xfe", "not valid UTF-8"),
    (b"[1, 2]", "must be a JSON object"),
    (b'{"subject": "hi"}', "no non-empty string 'message'"),
    (b'{"message": "   "}', "no non-empty string 'message'"),
])
def test_malformed_exports_are_kept_with_an_error(raw, expected):
    doc = parse_export("BAD.json", "BAD.json", raw)
    assert not doc.ok and expected in doc.load_error
    assert doc.sha256 == hashlib.sha256(raw).hexdigest()
    assert doc.message is None


def test_discover_supplied_folder():
    files = discover([REQUESTS])
    assert [f.name for f in files] == [f"IT-{i:03d}.json" for i in range(1, 21)]
    assert discover([REQUESTS, REQUESTS / "IT-001.json"]) == files  # same file twice is deduplicated


def test_discover_rejects_conflicting_duplicates_and_missing_paths(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "IT-001.json").write_text('{"message": "one"}')
    (tmp_path / "b" / "IT-001.json").write_text('{"message": "two"}')
    with pytest.raises(SourceError, match="share document_id"):
        discover([tmp_path / "a", tmp_path / "b"])
    with pytest.raises(SourceError, match="not found"):
        discover([tmp_path / "missing"])

"""Field-level accuracy check of extraction records against eval/expected.json.

Counting rules
  * 11 exact-match fields per document (SCALAR_FIELDS). Values are compared after
    trimming surrounding whitespace; comparison is otherwise exact and
    case-sensitive; null must equal null.
  * Denominator = documents in expected.json x 11. A document whose record is
    missing, failed (extraction null) or was produced from a different source
    hash still counts: all of its fields are scored as not matching.
  * Outcome per field: match | wrong_value (both non-null, different) |
    unsupported_value (expected null, model gave a value) | missed (expected a
    value, model gave null) | no_extraction. wrong_value and unsupported_value
    are factual errors.
  * business_purpose (free text) is scored separately by purpose_terms;
    issues and supporting_quotes by issue_patterns / quote_terms; routing by
    exact (status, owner, next_action). These do not enter the field denominator.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .config import REPO_ROOT

SCALAR_FIELDS = ("request_id", "request_date", "kind", "requester", "team", "system", "environment",
                 "permission", "start_date", "end_date", "manager")
ROUTE_FIELDS = ("status", "owner", "next_action")
FACTUAL_ERRORS = ("wrong_value", "unsupported_value")


def _norm(v):
    return v.strip() if isinstance(v, str) else v


def field_outcome(expected, got, have_extraction: bool) -> str:
    if not have_extraction:
        return "no_extraction"
    if _norm(got) == _norm(expected):
        return "match"
    if expected is None:
        return "unsupported_value"
    if got is None:
        return "missed"
    return "wrong_value"


def load_expected(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["documents"]


def load_records(path: Path) -> dict[str, dict]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["document_id"]] = r
    return out


def compare_document(exp: dict, rec: dict | None, root: Path = REPO_ROOT) -> dict:
    doc_id = exp["document_id"]
    source_sha = hashlib.sha256((root / exp["source"]).read_bytes()).hexdigest()
    problems = []
    if rec is None:
        problems.append("no record")
    elif rec["source_sha256"] != source_sha:
        problems.append("record is for a different source version")
    x = rec["extraction"] if rec is not None and not problems else None
    if rec is not None and not problems and x is None:
        problems.append(f"extraction failed: {(rec.get('error') or '')[:120]}")
    have = x is not None

    fields = {}
    for f in SCALAR_FIELDS:
        want = exp["expected"][f]
        got = x.get(f) if have else None
        fields[f] = {"expected": want, "got": got, "outcome": field_outcome(want, got, have)}

    terms = exp.get("purpose_terms")
    if terms is None:
        purpose = "not_scored"
    else:
        text = (x or {}).get("business_purpose") or ""
        purpose = "match" if text and any(t.lower() in text.lower() for t in terms) else "no_match"

    issues = (x or {}).get("issues", [])
    issue_checks = {p: any(re.search(p, i, re.I) for i in issues) for p in exp.get("issue_patterns", [])}
    quotes = (x or {}).get("supporting_quotes", [])
    quote_checks = {t: any(t in q for q in quotes) for t in exp.get("quote_terms", [])}

    same_source = rec is not None and rec["source_sha256"] == source_sha
    got_route = {k: rec.get(k) for k in ROUTE_FIELDS} if same_source else None
    route_ok = got_route == exp["route"]
    return {"document_id": doc_id, "synthetic": bool(exp.get("synthetic")), "problems": problems,
            "response_source": (rec or {}).get("response_source"), "fields": fields, "purpose": purpose,
            "issue_checks": issue_checks, "quote_checks": quote_checks,
            "route": {"expected": exp["route"], "got": got_route, "match": route_ok}}


def evaluate(expected: list[dict], records: dict[str, dict], root: Path = REPO_ROOT) -> dict:
    docs = [compare_document(e, records.get(e["document_id"]), root) for e in expected]
    outcomes = Counter(f["outcome"] for d in docs for f in d["fields"].values())
    per_field = {f: sum(d["fields"][f]["outcome"] == "match" for d in docs) for f in SCALAR_FIELDS}
    total = len(docs) * len(SCALAR_FIELDS)
    scored_purpose = [d for d in docs if d["purpose"] != "not_scored"]
    issue_total = sum(len(d["issue_checks"]) for d in docs)
    quote_total = sum(len(d["quote_checks"]) for d in docs)
    factual = [{"document_id": d["document_id"], "field": f, **v} for d in docs for f, v in d["fields"].items()
               if v["outcome"] in FACTUAL_ERRORS]
    missed = [{"document_id": d["document_id"], "field": f, **v} for d in docs for f, v in d["fields"].items()
              if v["outcome"] == "missed"]
    return {
        "documents": len(docs),
        "response_sources": sorted({d["response_source"] or "none" for d in docs}),
        "fields": {"matched": outcomes["match"], "denominator": total, "outcomes": dict(outcomes),
                   "per_field_matched": per_field},
        "documents_all_fields_exact": sum(all(v["outcome"] == "match" for v in d["fields"].values()) for d in docs),
        "routing": {"matched": sum(d["route"]["match"] for d in docs), "denominator": len(docs)},
        "business_purpose": {"matched": sum(d["purpose"] == "match" for d in scored_purpose),
                             "denominator": len(scored_purpose)},
        "issue_checks": {"passed": sum(sum(d["issue_checks"].values()) for d in docs), "denominator": issue_total},
        "quote_checks": {"passed": sum(sum(d["quote_checks"].values()) for d in docs), "denominator": quote_total},
        "factual_errors": factual,
        "missed_values": missed,
        "documents_with_problems": [{"document_id": d["document_id"], "problems": d["problems"]}
                                    for d in docs if d["problems"]],
        "failed_checks": [{"document_id": d["document_id"],
                           "route": None if d["route"]["match"] else d["route"],
                           "purpose": d["purpose"] if d["purpose"] == "no_match" else None,
                           "issues": [p for p, ok in d["issue_checks"].items() if not ok],
                           "quotes": [t for t, ok in d["quote_checks"].items() if not ok]}
                          for d in docs if not d["route"]["match"] or d["purpose"] == "no_match"
                          or not all(d["issue_checks"].values()) or not all(d["quote_checks"].values())],
        "per_document": docs,
    }

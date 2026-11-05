"""Validation of model output against snapshot.schema.json plus evidence rules.

Standard library only. Supports the JSON Schema subset used by the supplied
schema (type / type lists, enum, required, additionalProperties: false,
array items). On top of the schema it enforces:
  * date fields are null or real calendar dates in YYYY-MM-DD form
  * every supporting quote is an exact substring of the decoded message
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache

from .config import SCHEMA_PATH

DATE_FIELDS = ("request_date", "start_date", "end_date")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _is_type(value, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"unsupported schema type {t!r}")


def _check(value, spec: dict, where: str, errors: list[str]) -> None:
    types = spec.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_is_type(value, t) for t in types):
            errors.append(f"{where}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{where}: {value!r} is not one of {spec['enum']}")
    if isinstance(value, list) and "items" in spec:
        for i, item in enumerate(value):
            _check(item, spec["items"], f"{where}[{i}]", errors)
    if isinstance(value, dict) and "properties" in spec:
        _check_object(value, spec, where, errors)


def _check_object(obj: dict, spec: dict, where: str, errors: list[str]) -> None:
    props = spec.get("properties", {})
    for key in spec.get("required", []):
        if key not in obj:
            errors.append(f"{where}.{key}: required field missing".lstrip("."))
    if spec.get("additionalProperties") is False:
        for key in obj:
            if key not in props:
                errors.append(f"{where}.{key}: unexpected field".lstrip("."))
    for key, sub in props.items():
        if key in obj:
            _check(obj[key], sub, f"{where}.{key}".lstrip("."), errors)


def validate_extraction(obj, message: str, schema: dict | None = None) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    schema = schema or load_schema()
    if not isinstance(obj, dict):
        return [f"response must be a JSON object, got {type(obj).__name__}"]
    errors: list[str] = []
    _check_object(obj, schema, "", errors)
    if errors:
        return errors

    for field in DATE_FIELDS:
        value = obj.get(field)
        if value is None:
            continue
        if not _DATE_RE.fullmatch(value):
            errors.append(f"{field}: {value!r} is not YYYY-MM-DD (unresolved dates must be null)")
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            errors.append(f"{field}: {value!r} is not a real calendar date")

    for i, quote in enumerate(obj.get("supporting_quotes", [])):
        if not quote.strip():
            errors.append(f"supporting_quotes[{i}]: empty quote")
        elif quote not in message:
            errors.append(f"supporting_quotes[{i}]: not an exact substring of the message: {quote[:80]!r}")
    return errors

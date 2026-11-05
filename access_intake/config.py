"""Runtime configuration.

All settings come from environment variables so that credentials never live in
the repository. The API key is read from ``API_KEY`` on demand and is never
stored on the Settings object, printed, or included in fingerprints.

Two fingerprints decide when stored results can be reused:

* extraction_fingerprint - anything that can change what the model returns
  (provider, model, generation parameters, prompt version, schema, policy text).
  If it changes, affected documents are re-extracted.
* routing_fingerprint - anything that can change queue routing (routing rule
  version, policy text). If only this changes, documents are re-routed from the
  stored extraction without calling the model again.

Operational knobs (timeouts, retries, concurrency, paths) are deliberately
excluded: changing them does not invalidate completed work.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "snapshot.schema.json"
POLICY_PATH = REPO_ROOT / "materials" / "office-policy.md"

# Bump when response parsing/validation changes. Prompt text is hashed separately.
PROMPT_VERSION = "extract-v1"
# Bump when the routing code in routing.py changes behaviour.
ROUTING_VERSION = "routing-v1"  # routing.py rules 1-5 as first implemented

PROVIDERS = ("mock", "databricks")
RESPONSE_FORMATS = ("json_schema", "json_object", "none")
DEFAULT_MODEL = "databricks-claude-sonnet-4-5"


class ConfigError(ValueError):
    """Raised for missing or invalid configuration."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = env.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _number(env: Mapping[str, str], name: str, default: str, cast, minimum):
    raw = _get(env, name, default)
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    base_url: str | None
    temperature: float
    max_tokens: int
    timeout_s: float
    max_retries: int
    max_workers: int
    state_dir: Path
    requests_dir: Path
    deliverables_dir: Path
    response_format: str = "json_schema"
    fixtures_dir: Path = REPO_ROOT / "fixtures" / "responses"
    record_fixtures: bool = False

    # ---- credentials -------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        """Read on demand; never cached on the object."""
        return _get(os.environ, "API_KEY")

    def require_live(self) -> None:
        """Fail fast with an actionable message before any live model call."""
        if self.provider == "mock":
            return
        missing = []
        if not self.api_key:
            missing.append("API_KEY")
        if not self.base_url:
            missing.append("INTAKE_BASE_URL or DATABRICKS_HOST")
        if missing:
            raise ConfigError(
                "Provider 'databricks' needs: " + ", ".join(missing)
                + ". Set them in the environment, or use INTAKE_PROVIDER=mock."
            )

    # ---- provenance --------------------------------------------------------
    @property
    def response_source(self) -> str:
        return "mock" if self.provider == "mock" else f"{self.provider}:{self.model}"

    def extraction_inputs(self) -> dict:
        from .prompt import prompt_sha256  # local import: prompt.py must not import config

        return {
            "provider": self.provider,
            "model": self.model if self.provider != "mock" else "mock",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": self.response_format,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_sha256(),
            "schema_sha256": sha256_file(SCHEMA_PATH),
            "policy_sha256": sha256_file(POLICY_PATH),
        }

    def routing_inputs(self) -> dict:
        return {
            "routing_version": ROUTING_VERSION,
            "routing_code_sha256": sha256_file(Path(__file__).resolve().parent / "routing.py"),
            "policy_sha256": sha256_file(POLICY_PATH),
        }

    def extraction_fingerprint(self) -> str:
        return _fingerprint(self.extraction_inputs())

    def routing_fingerprint(self) -> str:
        return _fingerprint(self.routing_inputs())

    def describe(self) -> dict:
        """Safe to log or print: contains no credential values."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "max_workers": self.max_workers,
            "state_dir": str(self.state_dir),
            "requests_dir": str(self.requests_dir),
            "deliverables_dir": str(self.deliverables_dir),
            "response_format": self.response_format,
            "fixtures_dir": str(self.fixtures_dir),
            "record_fixtures": self.record_fixtures,
            "api_key_set": bool(self.api_key),
            "response_source": self.response_source,
            "extraction_fingerprint": self.extraction_fingerprint(),
            "routing_fingerprint": self.routing_fingerprint(),
            "extraction_inputs": self.extraction_inputs(),
            "routing_inputs": self.routing_inputs(),
        }


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _base_url(env: Mapping[str, str]) -> str | None:
    explicit = _get(env, "INTAKE_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    host = _get(env, "DATABRICKS_HOST")
    if host:
        if not host.startswith("http"):
            host = "https://" + host
        return host.rstrip("/") + "/serving-endpoints"
    return None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    provider = _get(env, "INTAKE_PROVIDER", "mock").lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"INTAKE_PROVIDER must be one of {PROVIDERS}, got {provider!r}")

    response_format = _get(env, "INTAKE_RESPONSE_FORMAT", "json_schema").lower()
    if response_format not in RESPONSE_FORMATS:
        raise ConfigError(f"INTAKE_RESPONSE_FORMAT must be one of {RESPONSE_FORMATS}, got {response_format!r}")
    record = _get(env, "INTAKE_RECORD_FIXTURES", "0").lower() in ("1", "true", "yes")

    def path(name: str, default: str) -> Path:
        p = Path(_get(env, name, default))
        return p if p.is_absolute() else (REPO_ROOT / p)

    return Settings(
        provider=provider,
        model=_get(env, "INTAKE_MODEL", DEFAULT_MODEL),
        base_url=_base_url(env),
        temperature=_number(env, "INTAKE_TEMPERATURE", "0", float, 0.0),
        max_tokens=_number(env, "INTAKE_MAX_TOKENS", "2000", int, 256),
        timeout_s=_number(env, "INTAKE_TIMEOUT_S", "60", float, 1.0),
        max_retries=_number(env, "INTAKE_MAX_RETRIES", "3", int, 0),
        max_workers=_number(env, "INTAKE_MAX_WORKERS", "4", int, 1),
        state_dir=path("INTAKE_STATE_DIR", "state"),
        requests_dir=path("INTAKE_REQUESTS_DIR", "materials/requests"),
        deliverables_dir=path("INTAKE_DELIVERABLES_DIR", "deliverables"),
        response_format=response_format,
        fixtures_dir=path("INTAKE_FIXTURES_DIR", "fixtures/responses"),
        record_fixtures=record,
    )

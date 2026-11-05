import logging

import pytest

from access_intake.config import DEFAULT_MODEL, ConfigError, load_settings
from access_intake.redaction import REDACTED, RedactingFormatter, redact, redact_headers

FAKE_KEY = "test-key-not-a-real-credential-00000"


def test_defaults_use_mock_provider():
    s = load_settings({})
    assert s.provider == "mock"
    assert s.model == DEFAULT_MODEL
    assert s.response_source == "mock"
    assert s.temperature == 0.0
    s.require_live()  # mock never needs credentials


def test_invalid_provider_and_numbers_rejected():
    with pytest.raises(ConfigError):
        load_settings({"INTAKE_PROVIDER": "other"})
    with pytest.raises(ConfigError):
        load_settings({"INTAKE_MAX_RETRIES": "-1"})
    with pytest.raises(ConfigError):
        load_settings({"INTAKE_TIMEOUT_S": "soon"})


def test_databricks_requires_key_and_host(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    s = load_settings({"INTAKE_PROVIDER": "databricks"})
    with pytest.raises(ConfigError) as exc:
        s.require_live()
    assert "API_KEY" in str(exc.value) and "DATABRICKS_HOST" in str(exc.value)

    monkeypatch.setenv("API_KEY", FAKE_KEY)
    s = load_settings({"INTAKE_PROVIDER": "databricks", "DATABRICKS_HOST": "adb-1.azuredatabricks.net"})
    s.require_live()
    assert s.base_url == "https://adb-1.azuredatabricks.net/serving-endpoints"
    assert s.response_source == f"databricks:{DEFAULT_MODEL}"


def test_extraction_fingerprint_tracks_output_affecting_settings_only():
    base = load_settings({"INTAKE_PROVIDER": "databricks"})
    other_model = load_settings({"INTAKE_PROVIDER": "databricks", "INTAKE_MODEL": "databricks-gpt-5-mini"})
    other_temp = load_settings({"INTAKE_PROVIDER": "databricks", "INTAKE_TEMPERATURE": "0.7"})
    ops_only = load_settings({"INTAKE_PROVIDER": "databricks", "INTAKE_TIMEOUT_S": "5",
                              "INTAKE_MAX_RETRIES": "9", "INTAKE_MAX_WORKERS": "16"})
    assert base.extraction_fingerprint() != other_model.extraction_fingerprint()
    assert base.extraction_fingerprint() != other_temp.extraction_fingerprint()
    assert base.extraction_fingerprint() == ops_only.extraction_fingerprint()
    assert base.routing_fingerprint() == other_model.routing_fingerprint()


def test_describe_never_contains_key(monkeypatch):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    d = load_settings({"INTAKE_PROVIDER": "databricks"}).describe()
    assert d["api_key_set"] is True
    assert FAKE_KEY not in repr(d)


def test_redaction_covers_key_bearer_and_headers(monkeypatch):
    monkeypatch.setenv("API_KEY", "my-secret-value")
    text = "key=my-secret-value Authorization: Bearer abc.def-123 token dapi" + "a" * 32
    out = redact(text)
    assert "my-secret-value" not in out and "abc.def-123" not in out and "dapi" + "a" * 32 not in out
    assert redact_headers({"Authorization": "Bearer x", "Content-Type": "application/json"}) == {
        "Authorization": REDACTED, "Content-Type": "application/json"}


def test_log_formatter_redacts_args_and_tracebacks(monkeypatch):
    monkeypatch.setenv("API_KEY", "my-secret-value")
    try:
        raise RuntimeError("failed with my-secret-value")
    except RuntimeError:
        rec = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "call %s", ("my-secret-value",),
                                                exc_info=__import__("sys").exc_info())
    out = RedactingFormatter("%(message)s").format(rec)
    assert "my-secret-value" not in out and REDACTED in out

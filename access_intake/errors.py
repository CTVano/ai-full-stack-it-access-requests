"""Extraction failure types.

Every failure has a stable ``kind`` (recorded in the attempt's error text) and a
``retryable`` flag that the HTTP retry loop uses. Non-retryable failures are
still retryable by an operator once the cause is fixed (retry command).
"""
from __future__ import annotations


class ExtractionFailure(Exception):
    kind = "extraction_failed"
    retryable = False
    hint = "inspect the message and retry"

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None,
                 retryable: bool | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.provider_attempts = 1
        if retryable is not None:
            self.retryable = retryable

    def describe(self) -> str:
        return f"{self.kind}: {self} (after {self.provider_attempts} provider attempt(s)); {self.hint}"


class InvalidExport(ExtractionFailure):
    kind = "invalid_export"
    hint = "fix or re-export the source file, then rerun intake"


class TransientProviderError(ExtractionFailure):
    kind = "provider_unavailable"
    retryable = True
    hint = "model service unavailable; retry later"


class RateLimited(TransientProviderError):
    kind = "rate_limited"
    hint = "shared model quota exhausted; retry later or lower INTAKE_MAX_WORKERS"


class ProviderTimeout(TransientProviderError):
    kind = "timeout"
    hint = "model did not answer within INTAKE_TIMEOUT_S; retry later or raise the timeout"


class ProviderRejected(ExtractionFailure):
    kind = "provider_rejected"
    hint = "check API_KEY, INTAKE_MODEL and INTAKE_BASE_URL/DATABRICKS_HOST, then retry"


class MockFixtureMissing(ExtractionFailure):
    kind = "mock_fixture_missing"
    hint = "record one with INTAKE_PROVIDER=databricks INTAKE_RECORD_FIXTURES=1, or add the fixture file"


class InterruptedResponse(ExtractionFailure):
    kind = "interrupted_response"
    hint = "response was incomplete; retry (raise INTAKE_MAX_TOKENS if it was cut off at the limit)"


class Refusal(ExtractionFailure):
    kind = "refusal"
    hint = "model declined; inspect the message manually and retry"


class MalformedResponse(ExtractionFailure):
    kind = "malformed_response"
    hint = "model output was not a usable JSON object; retry"


class ValidationFailed(ExtractionFailure):
    kind = "schema_validation"
    hint = "model output did not meet the schema/evidence rules; retry"

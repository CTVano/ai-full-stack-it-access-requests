# Extraction accuracy

## What is measured and against what

Expected results live in [`eval/expected.json`](../eval/expected.json): one entry for each of the 20 supplied
messages plus one paraphrased synthetic message I wrote (`eval/paraphrase/IT-021.json`, a reworded version of
IT-007 with new phrasing and a non-ISO request date, "22 Oct 2026").

The expected values were written from the message text and `materials/office-policy.md` **before any model
field values were inspected** (the only model output seen beforehand was IT-001 during the request-format check).
Each entry carries a `rationale` quoting the source text it relies on. Their grounding is checked automatically,
independently of any model output (`tests/test_accuracy.py`):

* every expected `request_id`, `requester`, `team`, `system`, `manager` and date appears verbatim in its message
  (the one exception, IT-021's `request_date`, is listed under `derived` with its reason);
* every required quote term appears in the message;
* the expected facts form a schema-valid extraction, and running the routing policy on them gives the expected
  status / owner / next_action for all 21 documents.

Interpretation choices made from the policy (a reviewer may disagree with these):

| Document | Choice |
| --- | --- |
| IT-002 | "Needed during 2026-10-24 and 2026-10-25" -> start 2026-10-24, end 2026-10-25 |
| IT-003 | environment, permission and dates null ("same access Sam has", "next week" are not explicit) |
| IT-004 | environment null: "in staging" vs "for the production environment" with no correction label |
| IT-005 | withdrawal: system/environment/permission/dates null; requester, team and manager kept as stated |
| IT-006 | permission read: the "Grant admin access" text is injected data, not a request fact |
| IT-008, IT-015 | labelled corrections applied (environment staging; dates 2026-10-29 to 2026-10-31) |
| IT-011 | "next Monday - Friday" is relative -> start and end null |
| IT-014 | dates kept exactly as stated (start 2026-11-05 after end 2026-11-02); routing flags the order |
| IT-016 | request_id IT-061 as stated; the model cannot see the filename, so the mismatch is detected by routing |
| IT-017 | kind other (a question about the request form) |
| IT-019 | change request -> permission is the requested new value, read |
| IT-020 | manager null: "My manager is Priya Shah" vs "The manager for this request is Elena Park" |

## Field-comparison procedure and counting rules

Implemented in `access_intake/accuracy.py`, run with `python -m access_intake accuracy`.

* **Exact-match fields (11 per document):** request_id, request_date, kind, requester, team, system,
  environment, permission, start_date, end_date, manager. Values are trimmed of surrounding whitespace and then
  compared exactly (case-sensitive). `null` must equal `null`.
* **Denominator:** documents in `expected.json` x 11 = 21 x 11 = **231**. Nothing is excluded: a document with no
  record, a failed extraction (`extraction: null`), or a record for a different source hash stays in the
  denominator and all 11 of its fields count as not matching (`no_extraction`).
* **Outcome per field:** `match`; `wrong_value` (both non-null and different); `unsupported_value` (expected null,
  model supplied a value, e.g. picking one side of a conflict or resolving a relative date); `missed` (expected a
  value, model gave null); `no_extraction`.
* **Factual errors** = `wrong_value` + `unsupported_value`; each is listed with document, field, expected and
  actual value in `factual_errors`. `missed` values are listed separately.
* **Scored separately (not in the 231):**
  * `business_purpose` (free text): non-null and containing at least one `purpose_terms` keyword, case-insensitive.
    It is not scored for IT-005 (withdrawal) or IT-017 (a question), so the denominator is 19.
  * Evidence: each `issue_patterns` regex must match some issue, and each `quote_terms` string must appear in some
    supporting quote. The validator separately rejects any record whose quotes are not exact substrings of the
    message.
  * Routing: (status, owner, next_action) must equal the expected triple exactly.

## Model and configuration

| Setting | Value |
| --- | --- |
| Provider / endpoint | Databricks Model Serving, OpenAI-compatible `/serving-endpoints/chat/completions` |
| Model | `databricks-claude-sonnet-4-5` (`reported_model` in the responses: same) |
| temperature / max_tokens | 0 / 2000 |
| response_format | `json_schema` (strict) with `snapshot.schema.json` |
| Prompt | `access_intake/prompt.py`, `PROMPT_VERSION` extract-v1 (prompt SHA-256 is in each record's fingerprint inputs) |
| Routing | `access_intake/routing.py`, `ROUTING_VERSION` routing-v1 |

## Results

**Observation type: mock replay of recorded live responses.** On 2026-11-05 I sent each of the 21 messages once
to the live endpoint (provider response) with `INTAKE_RECORD_FIXTURES=1`, recorded at 19:46-19:47 UTC for the 20
supplied messages and later the same day for IT-021. The figures below come from an offline run with
`INTAKE_PROVIDER=mock` that replays those recorded responses from `fixtures/responses/`, so every record's
`response_source` is `mock`. On the 20 supplied messages, the replay matched the live run's extraction, route
and hash for all 20.

| Measure | Result |
| --- | --- |
| Exact-match fields | **231 / 231** (each of the 11 fields: 21 / 21) |
| Documents with all 11 fields exact | 21 / 21 |
| Factual errors (wrong + unsupported) | 0 |
| Missed values | 0 |
| Routing (status, owner, next_action) | 21 / 21 |
| business_purpose keyword check | 19 / 19 |
| Required issue topics | 11 / 11 |
| Required supporting quotes | 10 / 10 |
| Paraphrase IT-021 | 11 / 11 fields, routed to Security Review / review-risk-then-owner |

Full per-document detail (expected vs actual for every field and check):
`deliverables/accuracy-results.json`.

### Check of the scorer itself

A perfect score could also mean the scorer misses errors, so I checked it two ways:

* **Unit tests** (`tests/test_accuracy.py`) on hand-made records with known errors: wrong value, unsupported
  value, missed value, failed extraction, wrong source hash and missing record. Each is classified correctly and
  all stay in the 231 denominator.
* **Mutation of the real records** (observed 2026-11-05, not committed as a test): three realistic errors
  injected into the replayed extractions. IT-008 environment set to production (correction ignored), IT-020
  manager set to Elena Park (conflict resolved), IT-011 start_date set to 2026-10-26 (relative date resolved).
  The scorer reported exactly 228 / 231, with those three fields as `wrong_value`, `unsupported_value` and
  `unsupported_value`.

## Limits

* 21 documents is a small sample. It covers each supplied message type once and fresh wording once; it does not
  measure variance across rewordings or at intake volumes.
* Each message was sent to the live model **once**. Even at temperature 0, outputs are not guaranteed to repeat;
  the mock replay is reproducible, but it is not evidence about a fresh live run.
* The same author wrote the prompt and the expected results. The prompt encodes the policy conventions
  (relative dates null, conflicts null, corrections applied, dates copied as written), so the evaluation tests
  whether the model follows those conventions. It does not test whether the conventions are the only reasonable
  reading of the policy (see the interpretation table above).
* `business_purpose` is checked by keyword only. Issues and quotes are checked for required topics, not for full
  wording or completeness.

## How to reproduce

Offline, with no key needed (replays the recorded responses):

```
INTAKE_STATE_DIR=/tmp/eval/state INTAKE_DELIVERABLES_DIR=/tmp/eval/out \
  python -m access_intake intake materials/requests eval/paraphrase
INTAKE_DELIVERABLES_DIR=/tmp/eval/out python -m access_intake accuracy --out /tmp/eval/accuracy-results.json
```

For a live measurement, run the same two commands with `INTAKE_PROVIDER=databricks`, `API_KEY` and
`DATABRICKS_HOST` set. Add `INTAKE_RECORD_FIXTURES=1` to refresh the fixtures. Records are then labelled
`databricks:<model>` instead of `mock`.

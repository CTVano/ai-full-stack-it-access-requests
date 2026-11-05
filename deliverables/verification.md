# Verification

This file has four parts, kept separate on purpose:

* **Commands and assertions**: what each check compares. These hold for anyone who reruns them.
* **Operating assumptions and limits**: what the design supports and where it stops.
* **Observed results** (section 4): what I actually ran, when, with which configuration, and whether the model
  responses were mock replays or live provider responses.
* **Repairs** (section 5): defects the checks found and how they were fixed.

All commands run from the repository root with Python 3.12 and no third-party runtime packages. Test-only setup is
in the README.

## 1. Verification commands

| # | Command | Needs key/network | What it asserts |
| --- | --- | --- | --- |
| V1 | `python -m pytest` | no | 152 automated tests (section 2) |
| V2 | `INTAKE_STATE_DIR=/tmp/v/state INTAKE_DELIVERABLES_DIR=/tmp/v/out python -m access_intake intake materials/requests eval/paraphrase` | no (mock replay) | the 21 documents are processed from the recorded responses; exit 0 |
| V3 | `INTAKE_DELIVERABLES_DIR=/tmp/v/out python -m access_intake accuracy --out /tmp/v/accuracy-results.json` | no | field-level comparison with `eval/expected.json` (rules in `accuracy.md`) |
| V4 | `python -m access_intake verify` | no | the 10 consistency checks below, run on the committed `deliverables/` |
| V5 | `python -m access_intake evidence --out /tmp/v/request-evidence.json` | no | rebuilds the redacted request evidence from code; should match the committed file except `generated_at` |
| V6 | same as V2 with `INTAKE_PROVIDER=databricks`, `API_KEY` and `DATABRICKS_HOST` set | yes | a fresh live measurement; records are labelled `databricks:<model>` |

### V4 `verify`: deliverable consistency checks

`access_intake/verify.py` reads the files from disk without using the SQLite store, and recomputes what it
can from `materials/requests/`. It is a different code path from the export that wrote the files. It exits 1 if
any check fails.

1. Exactly one record per source export; no duplicates; no records without a source.
2. Every record has document_id, source_sha256, extraction, status, owner, next_action, error and
   response_source.
3. `source_sha256` equals the SHA-256 of the export file's bytes, recomputed.
4. The three error fields agree: a record has an error message, a null extraction and status `error` all
   together, or none of them.
5. `response_source` is `mock` or `<provider>:<model>`.
6. Each extraction conforms to `snapshot.schema.json`, has real YYYY-MM-DD dates, and every supporting quote is
   an exact substring of its message.
7. status / owner / next_action equal the office policy applied again to the recorded extraction.
8. Each `queue.json` entry matches its record 1:1 on source hash, attempt, route, error and response_source;
   each error entry carries a retry command.
9. The counts in `queue.json` match its contents.
10. No deliverable contains `API_KEY`'s value, a bearer token or a `dapi…` token, and the evidence
    Authorization header is `[REDACTED]`.

`tests/test_verify.py` shows the checker can fail: tampered files (wrong hash, wrong owner, dropped record,
invented quote, planted bearer token) are each caught by the intended check.

## 2. Automated tests (V1) mapped to the requirements

Responses in the tests come from mock fixtures or scripted fake transports; no test needs network access or a key.

### Cases required by `docs/requirements.md`

| Requirement | Test(s) | Intended comparison |
| --- | --- | --- |
| Complete request | `test_routing::test_complete_requests_rules_4_and_5`; `test_extraction::test_valid_response_is_accepted`; `test_runner::test_first_intake_routes_each_message_and_isolates_failure` | IT-001 -> review / System Owner / review-request; production or write/admin -> Security Review |
| Missing dates | `test_routing::test_rule_3_clarify` (start/end null, start after end); runner IT-010 retry case | -> needs-information / IT Service Desk / clarify, with the reason naming the field |
| Conflicting details | `test_rule_3_clarify` (conflict issue, ID mismatch IT-016 vs IT-061); `test_correction_is_not_a_conflict...` | conflict -> clarify; a labelled correction is not a conflict |
| Embedded instructions | `test_rule_1_override_from_message_even_if_model_missed_it`; `test_rule_1_from_model_issue...`; `test_override_patterns_fire_only_on_it006...`; `test_extraction::test_message_is_isolated_as_data` | IT-006 -> inspect-message even when the (mocked) model was fooled into `admin`; the message is sent only inside `<message>` markers |
| Provider failure | `test_extraction` (503 persistent, 429 with Retry-After, timeout, auth error not retried, missing fixture); `test_runner::test_first_intake...` | an error record (extraction null, error text, retry command); other documents are unaffected |
| Refusal / interrupted response | `test_extraction` (refusal field, plain-text refusal, content_filter, finish_reason length/missing, truncated body, dropped connection) | each is rejected with a specific error kind; only transient kinds are retried |
| Malformed output | `test_extraction` (bad JSON, code fences, no choices, extra/missing fields, bad enum, invalid date, invented quote) | rejected before use; nothing partial is stored |
| Unchanged rerun | `test_store::test_first_run_then_unchanged_rerun_reuses`; `test_runner::test_unchanged_rerun_reuses_results_and_retries_only_the_failure` | 0 model calls for unchanged successes; only the earlier failure is retried |
| Changed source | `test_store::test_changed_source...`; `test_runner::test_changed_source_creates_new_result_and_keeps_old` | new hash -> new attempt; the old result stays in history with its hash |
| Failed retry after success | `test_store::test_failed_retry_after_success...`; `test_runner::test_failed_retry_after_success_shows_error_and_links_previous_result` | current entry shows the error; `previous_result` names the earlier attempt and whether it was for the same source version |
| Source-derived expectations + paraphrase | `test_accuracy` (54 tests) | expected values appear verbatim in each message; expected routes follow from the policy; IT-021 present; scorer classifies known errors |
| Filenames and hashes | `test_sources` | document_id is the filename; the hash is over the raw bytes |
| Credentials | `test_config`, `test_extraction`, `test_evidence` | the key never appears in config output, logs, errors or evidence |

### Operational situations from the operating context

| Situation | Test(s) | Intended comparison |
| --- | --- | --- |
| Review while intake runs | `test_runner::test_slow_message_does_not_block_others` | a message held back by the transport finishes last; the other 4 complete without waiting for it |
| Two operators start at once | `test_runner::test_two_operators_at_once_process_each_document_once`; `test_store::test_two_processes_enqueue_same_document_once` | 5 documents -> exactly 5 model calls and 5 attempts across two runners (threads) and two OS processes |
| Stop / outage mid-run | `test_store::test_lease_expiry...`; `test_runner::test_interrupted_attempt_is_resumed_after_lease_expiry` | a claimed attempt is not taken over while its lease is valid; after expiry `resume` completes it; a stale worker cannot overwrite it |
| Restart | `test_store::test_state_survives_restart` | state persists in SQLite across store instances |
| Model setting change | `test_runner::test_model_setting_change_refreshes_everything`; `test_extraction::test_response_format_is_part_of_extraction_fingerprint` | a changed temperature re-extracts all 5 documents |
| Policy / routing change | `test_runner::test_routing_change_reroutes_without_calling_the_model`; `test_store::test_routing_only_change_reroutes_from_stored_extraction` | a routing version change re-routes stored extractions with 0 model calls, except the earlier failure |
| Operator commands | `test_runner::test_cli_intake_queue_and_show`; `test_live_provider_without_key_stops_before_work` | exit codes 4 / 0 / 2; filters by status and owner; `show` history; no work starts without a key |

## 3. Operating assumptions and limits

**Workload**

* Processing is one model call per message, with `INTAKE_MAX_WORKERS` (default 4) calls in flight.
* The observed live rate was about 20 messages in 25 s with 4 workers, roughly 0.8 messages/s (section 4).
  Extrapolating, an untested estimate: 400 messages take about 8-9 minutes and a 2,000-message morning about
  40-45 minutes at 4 workers. More workers help only as far as the shared model endpoint allows. Under 429 errors
  the client honours Retry-After and backs off, so throughput drops rather than failing.
* Results become visible as each message completes. `queue` and `show` can be run while intake continues;
  deliverables are rewritten atomically at the end of a run or with `export`.

**Shared operation**

* All operators must point at the same state file (`INTAKE_STATE_DIR`).
* Writes are short `BEGIN IMMEDIATE` SQLite transactions with a 30 s busy timeout. Each attempt is claimed with a
  lease before the model call, so concurrent runners never process the same attempt twice.
* Verified: threads and separate processes on one machine (tests), and the needed file-system behaviour on the
  Databricks workspace file system from one cluster.
* **Not verified:** runners on different clusters or hosts sharing one state file over a network file system.
  SQLite locking there is not guaranteed. The supported setup is one shared intake job, or one host, per state
  directory.

**Interruptions**

* An attempt's lease defaults to timeout x (retries+1) + 60 s x retries + 60 s, which is 480 s with the defaults.
* If a runner stops, its in-flight attempts show as pending (`in_flight_attempt_id` in `queue.json`).
  `python -m access_intake resume` picks them up once the lease has expired. Completed results are never redone.
* A failure is recorded per document with a `retry` command; any earlier good result stays in history and is
  linked from the queue entry.

**Model and policy changes**

* Each record stores the settings fingerprints that produced it:
  * extraction: provider, model, temperature, max_tokens, response_format, prompt version and hash, schema hash,
    policy hash
  * routing: routing version, `routing.py` hash, policy hash
* A change to either fingerprint triggers re-extraction or re-routing on the next `intake`. `queue.json` shows
  the fingerprints in effect.

**Limits of the verification performed**

* The live provider was exercised on 21 messages, once each. Outages, slow responses and rate limits were
  **simulated** with scripted transports, not observed against the real service.
* Accuracy is measured on 21 documents (one paraphrase). It shows that the model follows the policy conventions
  written into the prompt; it does not show performance on the full variety of production messages.
* Tested on Python 3.12.3 on Databricks serverless compute (Linux) only.
* Routing rule 1 combines the model's flag with a pattern check. The patterns were written with the supplied
  data in view. On the supplied data they fire only on IT-006; new phrasings of injected instructions rely on the
  model's flag.

## 4. Observed results (recorded runs)

All runs were on 2026-11-05 on Databricks serverless compute with Python 3.12.3, using the default configuration
(`databricks-claude-sonnet-4-5`, temperature 0, max_tokens 2000, `json_schema`, 60 s timeout, 3 retries,
4 workers, prompt extract-v1, routing-v1) unless noted.

| Run | Response source | Configuration notes | Observed |
| --- | --- | --- | --- |
| Request-format check, IT-001 | **provider**: `databricks:databricks-claude-sonnet-4-5` | `INTAKE_MAX_RETRIES=1`, state in /tmp | exit 0; `json_schema` accepted; finish_reason stop; 2,160 tokens; System Owner / review-request |
| Live recording, 20 supplied | **provider** | `INTAKE_RECORD_FIXTURES=1` | 20/20 succeeded, no retries, about 25 s; 20 fixtures written; every route matched the routes I worked out from the policy before the run |
| Live recording, IT-021 | **provider** | `INTAKE_RECORD_FIXTURES=1` | succeeded; fixture written |
| Credential scan after the live runs | n/a | session token passed as `API_KEY` to those commands only | token string found in no log, state, output or fixture file |
| Offline replay vs live | **mock** | no key, no network | 20/20 identical extraction, route and hash compared with the live run |
| Accuracy (V2+V3) | **mock** replay of the provider responses | 21 documents | 231/231 fields, 21/21 routes, 0 factual errors; details in `accuracy.md` |
| Scorer mutation | n/a | 3 errors injected into the replayed records | exactly 228/231, with the 3 fields classified as expected |
| Smoke run with no fixtures | **mock** | empty fixture dir | 20 visible `mock_fixture_missing` errors, exit 4, 20 retry commands |
| V1 tests | mock / fake transports | | 152 passed; earlier suites (93 tests) passed on 3 consecutive runs |
| Committed deliverables | **mock** replay | `intake materials/requests` | exit 0; 20 records, 0 pending; identical to the records scored in `accuracy.md` |
| V4 verify on committed deliverables | n/a | | 10/10 checks passed |
| Request evidence | built from code + recorded **provider** response | host replaced by placeholder | 5/5 handling steps ok; no token, no real host |

The committed `extractions.jsonl` and `queue.json` therefore come from **mock replay**: every record says
`"response_source": "mock"`. The underlying responses were recorded from the provider; the fixtures carry
`recorded_from` and `recorded_at`.

## 5. Repairs made during development (observed defects)

| Found by | Defect | Repair |
| --- | --- | --- |
| `test_extraction` (first run) | One new test passed its arguments in the wrong order (a test bug, not an application bug) | Test corrected; the suite was rerun and passed |
| `test_runner` (first run: 3 failures) | `Store._row` called `json.loads(None)` for a document whose attempt had no stored settings yet, so the pending view crashed | NULL settings are returned as `None`; the 3 tests pass |
| Review while writing `test_runner` | A test expecting exactly 5 model calls for two operators was racy: a document that **failed** can legitimately be retried by the second operator | Not an application defect. In that test all documents succeed, and retry-after-failure is covered separately |
| Design review at step 4 | Routing changes were detected only when `ROUTING_VERSION` was bumped by hand | The `routing.py` file hash is now part of the routing fingerprint |
| Citation check of this file (each test name cited here compared with `pytest --collect-only`) | 4 test names in section 2 were written from memory and did not exist | Replaced with the real names; all 29 citations now resolve |

No checks are currently failing.

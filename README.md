# IT Access Request Intake (Northstar Labs)

A command-line application that turns free-text IT access request exports into schema-conforming records using
a language model. It then routes each record under `materials/office-policy.md` into a review queue showing
status, owner and next action. Approval decisions stay with the named human reviewers; the application only
prepares and routes requests. The task brief is in `CHALLENGE.md`.

* Model: extraction only. The model fills in `snapshot.schema.json` from the message text, and every response is
  validated before use (completion status, JSON, schema, real dates, exact supporting quotes).
* Code: routing. The five policy rules are deterministic code (`access_intake/routing.py`); the model cannot
  route.
* State: SQLite (`state/intake.sqlite3`). It records each input filename and SHA-256, every attempt, and the
  settings that produced each result. Unchanged documents are reused, changed sources or settings are refreshed,
  and earlier versions are kept.

## Supported runtime and provider

| | Supported / tested |
| --- | --- |
| Python | 3.12 (tested on 3.12.3, Linux, Databricks serverless compute). Standard library only at runtime |
| Provider | Databricks Model Serving, OpenAI-compatible chat completions (`<host>/serving-endpoints/chat/completions`) |
| Model | `databricks-claude-sonnet-4-5` with `response_format: json_schema` (strict), temperature 0, max_tokens 2000 |
| Offline mode | `INTAKE_PROVIDER=mock` (the default): replays recorded responses from `fixtures/responses/`, no key or network needed |

Other OpenAI-compatible endpoints should work through `INTAKE_BASE_URL` and `INTAKE_MODEL`, but are not tested.
If an endpoint rejects `json_schema`, set `INTAKE_RESPONSE_FORMAT=json_object` or `none`; validation is the same.

## Setup

Runtime: no packages to install (`requirements.txt` is intentionally empty). Run every command from the
repository root.

Tests: pytest is pinned with hashes in `requirements-dev.txt` (compiled by pip-tools from `requirements-dev.in`).

```
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
```

On Databricks compute, where creating a venv is not available:

```
pip install --require-hashes --target /tmp/deps -r requirements-dev.txt
export PYTHONPATH=/tmp/deps
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEY` | none | Provider credential (supplied by the facilitator). Read when needed; never stored, logged or written to outputs |
| `INTAKE_PROVIDER` | `mock` | `mock` (replay fixtures) or `databricks` (live) |
| `DATABRICKS_HOST` | none | Workspace URL, e.g. `https://<workspace-host>`; used to build the endpoint URL |
| `INTAKE_BASE_URL` | derived from `DATABRICKS_HOST` | Full base URL ending in `/serving-endpoints`; overrides the host |
| `INTAKE_MODEL` | `databricks-claude-sonnet-4-5` | Serving endpoint / model name |
| `INTAKE_TEMPERATURE` | `0` | Sampling temperature |
| `INTAKE_MAX_TOKENS` | `2000` | Response token limit |
| `INTAKE_RESPONSE_FORMAT` | `json_schema` | `json_schema`, `json_object` or `none` |
| `INTAKE_TIMEOUT_S` | `60` | Per-request timeout, in seconds |
| `INTAKE_MAX_RETRIES` | `3` | Extra attempts for transient failures (429, 5xx, timeouts, dropped connections) |
| `INTAKE_MAX_WORKERS` | `4` | Messages processed in parallel |
| `INTAKE_REQUESTS_DIR` | `materials/requests/` | Default input folder for `intake` |
| `INTAKE_STATE_DIR` | `state/` | SQLite state. All operators must share it (see Limits) |
| `INTAKE_DELIVERABLES_DIR` | `deliverables/` | Where `extractions.jsonl`, `queue.json` and `history.jsonl` are written |
| `INTAKE_FIXTURES_DIR` | `fixtures/responses/` | Mock responses |
| `INTAKE_RECORD_FIXTURES` | `0` | `1` = save each live response as a fixture |

`python -m access_intake config` prints the effective settings and the extraction and routing fingerprints.
It never prints the key.

## Intake

Offline, replaying the recorded responses:

```
python -m access_intake intake
```

Live:

```
export INTAKE_PROVIDER=databricks DATABRICKS_HOST=https://<workspace-host> API_KEY=<key>
python -m access_intake intake
```

* `intake [PATH ...]` reads export files or folders (default `INTAKE_REQUESTS_DIR`), records each file's hash,
  processes new or changed documents and anything whose settings changed, then rewrites the deliverables.
  Add `--no-export` to skip rewriting them.
* In a Databricks notebook, set the key from a secret scope first, e.g.
  `os.environ["API_KEY"] = dbutils.secrets.get("<scope>", "<key>")`, then run the commands with `%sh` or
  `subprocess`.
* Exit codes:
  * `0`: everything succeeded
  * `2`: configuration or input error, e.g. a missing key in live mode (no work is started)
  * `4`: the run finished but some documents failed; their retry commands are printed

The run prints a JSON summary: source counts (new / changed / unchanged), decisions
(queued / reused / reroute), outcomes, and the retry commands for any failures.

## Inspecting the queue

```
python -m access_intake queue                            # completed and pending documents
python -m access_intake queue --status needs-information # filter by status
python -m access_intake queue --owner "Security Review"  # filter by owner
python -m access_intake queue --json                     # machine-readable
python -m access_intake show IT-010.json                 # current result + full attempt history
python -m access_intake export                           # rewrite deliverables from current state
```

The queue can be read at any time, including while another operator's intake is still running.

* Completed documents show status, owner, next action, request ID and source hash.
* Documents still being worked on are listed as pending.
* A failed document shows the error and its exact retry command. If an earlier result exists, it is linked as
  `previous_result`, with its attempt, source hash and whether it was for the same source version.

Statuses and next actions (from `materials/office-policy.md`):

| status | owner | next_action | Meaning |
| --- | --- | --- | --- |
| review | IT Service Desk | inspect-message | message contains instructions to the system (rule 1) |
| review | IT Service Desk | confirm-withdrawal | withdrawal of a stated request ID and date (rule 2) |
| needs-information | IT Service Desk | clarify | missing, conflicting or mismatched facts (rule 3); reasons listed |
| review | Security Review | review-risk-then-owner | production, write or admin (rule 4) |
| review | System Owner | review-request | staging read (rule 5) |
| error | IT Service Desk | retry-extraction | extraction failed; run the retry command |

## Following up on failures and interruptions

* Failed messages: run the printed `python -m access_intake retry IT-010.json`, or run `intake` again, which
  retries failed documents and reuses everything else.
* Stopped or crashed runs: `python -m access_intake resume` picks up pending and expired in-flight attempts.
  Completed work is never redone. An in-flight attempt is protected by a lease (480 s with the default settings)
  so that a second operator does not duplicate it.
* Changed exports: a document with new bytes gets a new attempt; the earlier result stays in `history.jsonl`.
  Absence from a later batch changes nothing; withdrawals come only from message content.
* Model or policy changes: the next `intake` re-extracts (model, prompt or schema change) or re-routes without
  calling the model (routing or policy change) only the affected records.

## Tests, accuracy check and verification

```
python -m pytest                                          # 152 tests, offline
python -m access_intake verify                            # 10 consistency checks on deliverables/

INTAKE_STATE_DIR=/tmp/eval/state INTAKE_DELIVERABLES_DIR=/tmp/eval/out \
  python -m access_intake intake materials/requests eval/paraphrase
INTAKE_DELIVERABLES_DIR=/tmp/eval/out python -m access_intake accuracy --out /tmp/eval/accuracy-results.json
```

`python -m access_intake evidence` rebuilds `deliverables/request-evidence.json` from the code.

## Demo notebook

`demo.py` is a self-contained walkthrough that demonstrates every feature offline (no API key
or network needed). Run all cells top to bottom; each section uses a fresh temp directory so
cells are independent.

Sections covered:

1. Setup and configuration
2. Intake of all 20 messages (mock replay, ~1 second)
3. Queue inspection (full, filtered by status, by owner, as JSON)
4. Document detail with extraction fields and supporting quotes
5. Provider failure handling and retry recovery
6. Unchanged rerun (zero model calls)
7. Changed source detection (new hash, old result in history)
8. Settings change triggering re-extraction
9. Accuracy check (231/231 fields, 21/21 routes on 21 documents)
10. Deliverable verification (10/10 checks)
11. Request evidence (redacted request, 23-condition failure table)
12. Embedded instruction detection (IT-006 injection attempt)
13. Full test suite (152 tests)

On Databricks serverless compute the notebook copies the test tree to `/tmp` before running
pytest, because the workspace filesystem does not support `__pycache__`.

## Session capture

`capture/genie-chat-history.md` is the full build session log: what was built, in what order,
every command run, the repairs made and the design decisions.

## Deliverables

| File | Content |
| --- | --- |
| `deliverables/extractions.jsonl` | One current record per supplied message. Produced by **mock replay** of responses recorded live from `databricks-claude-sonnet-4-5` on 2026-11-05; each record says `"response_source": "mock"` |
| `deliverables/queue.json` | Review queue, linked to the records by document_id, source hash and attempt; includes the settings fingerprints in effect |
| `deliverables/history.jsonl` | Earlier attempts (empty for the committed single run) |
| `deliverables/accuracy.md`, `accuracy-results.json` | Expected fields, counting rules and results (231/231 on mock replay) |
| `deliverables/request-evidence.json` | Redacted example request, recorded response, response handling and failure table |
| `deliverables/verification.md` | Verification commands, assertions, observed results, repairs and limits |

## Repository layout

```
access_intake/        application package (python -m access_intake)
  cli.py              commands and exit codes
  config.py           settings, environment variables, fingerprints
  sources.py          export loading, filenames and SHA-256
  prompt.py           extraction prompt (message isolated as data)
  providers.py        HTTP client, retries/backoff, mock fixture transport, recording
  extraction.py       completion checks, JSON parsing, refusal handling
  validation.py       schema, date and quote validation
  routing.py          office-policy rules 1-5
  store.py            SQLite state, leases, attempt history
  runner.py           parallel intake / resume / retry
  export.py           queue and records views, atomic deliverable writes
  accuracy.py         field-comparison procedure
  evidence.py         request-evidence builder
  verify.py           deliverable consistency checks
eval/                 expected results and the paraphrase IT-021
fixtures/responses/   recorded model responses (keyed by message hash)
materials/            supplied exports and office policy
tests/                automated tests
capture/              session log (genie-chat-history.md)
demo.py               interactive walkthrough notebook (run all cells)
```

## Operating assumptions and limits

Full detail is in `deliverables/verification.md`, section 3.

* Workload: the observed live rate is about 0.8 messages/s with 4 workers. A 2,000-message morning is estimated
  (not measured) at 40-45 minutes, depending on the shared endpoint's rate limits.
* Shared operation: operators must share one state directory, from one host or one shared Databricks job.
  Concurrent runners on one machine are tested. Separate clusters or hosts writing the same SQLite file over a
  network file system are not supported.
* Injected-instruction detection combines the model's flag with patterns written with the supplied data in view.
  New phrasings rely on the model's flag.
* The live provider was exercised once per message. Outages and rate limits were simulated in tests, not observed.

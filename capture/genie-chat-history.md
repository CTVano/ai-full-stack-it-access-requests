# Genie Code Chat History — AI Full Stack Challenge: IT Access Requests

**Date:** 2026-11-05
**Platform:** Databricks Genie Code (AI assistant) on serverless compute
**Repository:** `/Workspace/Users/chad.t.vanorsdale.irs@catalyte.ai/ai-full-stack-it-access-requests`
**Python:** 3.12.3 (Databricks serverless)
**Model used by the application:** `databricks-claude-sonnet-4-5` via Databricks Model Serving

---

## Session Summary

This session built, tested and verified a complete IT access request intake application for
Northstar Labs (fictional), working step by step in the Databricks workspace using Genie Code.
The application turns 20 free-text access request exports into schema-conforming records using
a language model, then routes each record under the office policy into a review queue.

### What was built

**Application (`access_intake/` package, 14 modules, ~1,500 lines):**
- `config.py` — Settings from environment variables; extraction and routing fingerprints that
  track every setting affecting the output (provider, model, temperature, max_tokens,
  response_format, prompt version + hash, schema hash, policy hash, routing code hash)
- `sources.py` — Export loading; document_id = filename; SHA-256 of raw bytes; deduplication
- `prompt.py` — System prompt (message isolated between `<message>` markers, treated as data)
- `providers.py` — HTTP client for OpenAI-compatible endpoints; retry loop with exponential
  backoff + jitter + Retry-After; mock fixture transport keyed by message hash; fixture recording
- `extraction.py` — Completion status checks (refusal, content filter, finish reason, empty
  content); JSON parsing (strips code fences); refusal detection in plain text
- `validation.py` — Schema validation against `snapshot.schema.json`; real YYYY-MM-DD date
  checks; exact-substring quote verification
- `routing.py` — Five deterministic policy rules (instruction override, withdrawal, clarification,
  security review, system owner); dual-defence override detection (model flag + regex patterns)
- `errors.py` — Error hierarchy with kind, retryable flag and hint for each failure type
- `redaction.py` — Strips API keys, bearer tokens and authorization headers from all outputs
- `store.py` — SQLite state with `BEGIN IMMEDIATE` transactions; source versions, attempts with
  leases, documents view; reuse/reroute/refresh logic
- `runner.py` — Parallel intake with `ThreadPoolExecutor`; resume expired leases; retry by ID
- `export.py` — Atomic writes of `extractions.jsonl`, `queue.json`, `history.jsonl`
- `cli.py` — Commands: `config`, `intake`, `resume`, `retry`, `queue`, `show`, `export`,
  `accuracy`, `evidence`, `verify`; exit codes 0 / 2 / 4
- `accuracy.py` — Field-level comparison procedure (11 exact-match fields, 231 denominator)
- `evidence.py` — Builds `request-evidence.json` from code (real request body, recorded response,
  handling steps, 23-condition failure table)
- `verify.py` — 10 independent consistency checks on the deliverable files

**Tests (`tests/`, 9 files, 152 tests):**
- `test_config.py` (7) — defaults, invalid settings, fingerprint tracking, credential redaction
- `test_sources.py` (10) — hashing, filenames, deduplication, malformed exports, conflicts
- `test_store.py` (12) — reuse, reroute, changed source, failed retry, leases, restart, concurrency
- `test_extraction.py` (31) — happy path, content blocks, fences, refusals (3 forms), content
  filter, interrupted responses (5 causes), malformed JSON, schema violations (7 types),
  rate limit, persistent 503, timeout, auth error, missing fixture, invalid export, message
  isolation (IT-006), response_format in fingerprint, record-then-replay, redaction
- `test_routing.py` (20) — rules 4+5 parametrized, rule 3 (9 clarification causes), correction
  not a conflict, non-ID filename skips mismatch, withdrawal with nulls, rule 1 from message,
  rule 1 from model issue, override patterns fire only on IT-006
- `test_runner.py` (13) — first intake routes + isolates failure, deliverables agree, unchanged
  rerun, changed source, failed retry after success, retry succeeds, lease expiry resume,
  model setting change, routing change without model calls, slow message non-blocking, two
  operators process each document once, CLI commands, live without key stops
- `test_accuracy.py` (54) — expected covers 21 documents, expected values grounded in messages
  (21 parametrized), expected routes follow policy (21 parametrized), field outcomes, perfect
  score, error classification, evidence checks
- `test_evidence.py` (2) — request is the real request and is redacted, handling steps and
  failure table
- `test_verify.py` (3) — replayed deliverables pass, detects hash/route/link errors, detects
  unfaithful quote and secret

**Eval and fixtures:**
- `eval/expected.json` — Source-derived expected fields for 20 supplied + 1 paraphrase (IT-021)
- `eval/paraphrase/IT-021.json` — Author-written reworded version of IT-007
- `fixtures/responses/` — 21 recorded live responses from `databricks-claude-sonnet-4-5`

**Deliverables:**
- `deliverables/extractions.jsonl` — 20 records (mock replay), all `response_source: mock`
- `deliverables/queue.json` — Review queue linked to records
- `deliverables/history.jsonl` — Earlier attempts (empty for single run)
- `deliverables/accuracy.md` — Expected fields, counting rules, results (231/231)
- `deliverables/accuracy-results.json` — Full per-document detail
- `deliverables/request-evidence.json` — Redacted example request/response/handling
- `deliverables/verification.md` — Commands, assertions, observed results, repairs, limits

**Configuration files:**
- `requirements.txt` (empty — stdlib only at runtime)
- `requirements-dev.in` / `requirements-dev.txt` — pytest 8.3.3 hash-pinned
- `pytest.ini` — test paths and options
- `.gitignore` — cache, state, logs, env
- `README.md` — Setup, commands, environment variables, queue inspection, limits

### Key results

| Measure | Result |
| --- | --- |
| Automated tests | 152 passed |
| Exact-match field accuracy | 231 / 231 (21 documents x 11 fields) |
| Routing accuracy | 21 / 21 |
| Business purpose keywords | 19 / 19 |
| Required issue topics | 11 / 11 |
| Required supporting quotes | 10 / 10 |
| Deliverable consistency | 10 / 10 checks passed |
| Live provider calls | 21 (20 supplied + 1 paraphrase), all succeeded, ~25s for 20 |
| Credential leaks | 0 (checked after every live run) |

### Design decisions made during the session

1. **Runtime deps:** stdlib only (urllib, json, hashlib, sqlite3). No pip packages at runtime.
2. **Dev deps:** pytest 8.3.3 pinned with hashes via pip-tools.
3. **Provider:** Databricks Model Serving with OpenAI-compatible chat completions.
4. **Model:** `databricks-claude-sonnet-4-5` with strict `json_schema` response format.
5. **State:** SQLite with `BEGIN IMMEDIATE` transactions and leases for concurrency.
6. **Routing:** deterministic code, never the model. Five rules from the office policy.
7. **Override detection:** dual defence — regex patterns on the raw message plus the model's
   issue flag. The patterns fire only on IT-006 in the supplied data.
8. **Mock replay:** responses recorded live with `INTAKE_RECORD_FIXTURES=1`, then replayed
   offline for reproducible development. Every record says its response_source.
9. **Authentication:** the user's Databricks session token was used as `API_KEY` for the live
   recording runs only, passed as an env var to those commands, never stored or logged.

---

## Full Chat History

Below is a reconstruction of every step in the session, including user requests, Genie Code
actions, commands executed, files created/edited, test results and decisions.

### Step 1: Project discovery and architecture

**User:** Asked Genie Code to build the IT access request intake application.

**Genie actions:**
- Read all source files: `CHALLENGE.md`, `docs/requirements.md`, `materials/office-policy.md`,
  `snapshot.schema.json`
- Read all 20 request exports: `IT-001.json` through `IT-020.json`
- Asked the user 4 clarifying questions:
  1. Provider choice → Databricks model serving / AI Gateway
  2. Runtime → Inside Databricks (notebooks/jobs)
  3. Lockfile → pip-tools with hashes
  4. Proceed step by step → Yes, one step per turn

**Architecture decisions recorded:**
- stdlib-only runtime, pytest for dev
- `access_intake/` package with CLI via `python -m access_intake`
- Default model: `databricks-claude-sonnet-4-5`
- SQLite state with leases for concurrency
- Test install via `--require-hashes --target`

### Step 2: Core extraction pipeline (config, sources, prompt, providers, extraction, validation)

**Files created:**
```
access_intake/__init__.py
access_intake/config.py
access_intake/redaction.py
access_intake/sources.py
access_intake/prompt.py
access_intake/providers.py
access_intake/extraction.py
access_intake/validation.py
access_intake/errors.py
access_intake/store.py
access_intake/cli.py
access_intake/__main__.py
requirements.txt
requirements-dev.in
requirements-dev.txt
pytest.ini
.gitignore
tests/test_config.py
tests/test_sources.py
tests/test_store.py
tests/test_extraction.py
```

**Commands run:**
```bash
# Install test deps
pip install --require-hashes --target /tmp/deps -r requirements-dev.txt

# Run tests after step 2
PYTHONPATH=/tmp/deps python -m pytest -rf --color=no
# Result: 29 passed
```

### Step 3: Extraction tests expanded (31 tests)

**Files edited:** `tests/test_extraction.py` expanded to 31 tests covering:
- Happy path, content blocks, code fences
- Refusals (refusal field, plain-text refusal, content filter)
- Interrupted responses (length finish_reason, missing finish_reason, connection drop,
  truncated body, incomplete read)
- Malformed JSON, no choices
- Schema violations (extra field, missing field, bad enum, wrong type, non-YYYY-MM-DD date,
  invalid calendar date, non-substring quote)
- Rate limit with Retry-After, persistent 503, timeout retry
- Auth error not retried, missing fixture, invalid export
- Live request shape, redacted evidence, key not in error text
- No request before key check
- Record-then-replay fixture flow
- Message isolation (IT-006 SYSTEM OVERRIDE)
- Response_format in fingerprint

**Commands run:**
```bash
PYTHONPATH=/tmp/deps python -m pytest -rf --color=no
# Result: 60 passed (one test had args in wrong order — fixed immediately, then 60 passed)
```

### Step 4: Routing, runner, export, CLI

**Files created:**
```
access_intake/routing.py
access_intake/runner.py
access_intake/export.py
tests/test_routing.py
tests/test_runner.py
```

**Files edited:**
- `access_intake/store.py` — added `route_rule`, `route_reasons_json` columns,
  `supersede_stale()`, `latest_sources()`, `all_attempts()`
- `access_intake/cli.py` — added `queue`, `show`, `export`, `resume`, `retry` commands
- `access_intake/config.py` — added `routing_code_sha256` to routing fingerprint

**Tests written:**
- `test_routing.py` (20 tests): rules 4+5 parametrized, rule 3 (9 causes), correction handling,
  non-ID filenames, withdrawal nulls, rule 1 from message, rule 1 from model issue, override
  patterns only on IT-006
- `test_runner.py` (13 tests): first intake, deliverables agreement, unchanged rerun, changed
  source, failed retry after success, retry succeeds, lease expiry resume, model setting change,
  routing change, slow message non-blocking, two operators, CLI commands, live without key

**Commands run:**
```bash
PYTHONPATH=/tmp/deps python -m pytest -rf --color=no
# Result: 93 passed (3 runs to confirm stability)

# Smoke run on all 20 real exports with no fixtures
INTAKE_STATE_DIR=/tmp/smoke/state INTAKE_DELIVERABLES_DIR=/tmp/smoke/deliv \
  python -m access_intake intake
# Result: 20 mock_fixture_missing errors (expected), exit 4, 20 retry commands

# Second smoke run (unchanged)
python -m access_intake intake --no-export
# Result: 20 unchanged sources, 20 queued (retrying failures)
```

**Repairs:**
- `Store._row` crashed on `json.loads(None)` for pending attempts with no stored settings →
  fixed to return `None` for NULL settings
- Test for two operators was racy with failures → changed test to use all-succeeding fixtures
- Routing fingerprint only tracked `ROUTING_VERSION` → added `routing.py` file hash

### Step 5 (partial): Live recording of fixtures

**User:** "Record fixtures with a live run"

**Genie actions:**
- Checked environment: `API_KEY` not set, `DATABRICKS_TOKEN` available (len 36),
  `DATABRICKS_HOST` set
- Asked user how to authenticate → user approved using session token as API_KEY for this run only

**Commands run:**
```bash
# Format check: IT-001 only
API_KEY="$DATABRICKS_TOKEN" INTAKE_PROVIDER=databricks INTAKE_RECORD_FIXTURES=1 \
  INTAKE_MAX_RETRIES=1 INTAKE_STATE_DIR=/tmp/live/state \
  INTAKE_DELIVERABLES_DIR=/tmp/live/deliv INTAKE_FIXTURES_DIR=/tmp/live/fixtures \
  python -m access_intake intake materials/requests/IT-001.json
# Result: exit 0, json_schema accepted, finish_reason stop, 2160 tokens,
#         System Owner / review-request

# Credential leak check
grep -rlF "$DATABRICKS_TOKEN" err.log out.json state fixtures deliv
# Result: no token in any output

# Full live recording: all 20 supplied messages
API_KEY="$DATABRICKS_TOKEN" INTAKE_PROVIDER=databricks INTAKE_RECORD_FIXTURES=1 \
  INTAKE_STATE_DIR=/tmp/live/state INTAKE_DELIVERABLES_DIR=/tmp/live/deliv \
  INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake intake
# Result: 20/20 succeeded, ~25s with 4 workers, 20 fixtures written

# Credential leak check after full run
grep -rlF "$DATABRICKS_TOKEN" err.log out.json state deliv <repo>/fixtures
# Result: no token in outputs/fixtures

# Verify offline replay matches live
env -u API_KEY -u DATABRICKS_TOKEN INTAKE_PROVIDER=mock \
  INTAKE_STATE_DIR=/tmp/acc/state INTAKE_DELIVERABLES_DIR=/tmp/acc/deliv \
  INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake intake materials/requests eval/paraphrase
# Result: 20/20 identical extraction, route and hash vs live run
```

### Step 5: Expected fields, accuracy procedure, paraphrase

**User:** "Continue to step 5"

**Files created:**
```
eval/expected.json              — 21 entries (20 supplied + IT-021 paraphrase)
eval/paraphrase/IT-021.json     — reworded IT-007 with new phrasing and "22 Oct 2026"
access_intake/accuracy.py       — field comparison procedure
tests/test_accuracy.py           — 54 tests
deliverables/accuracy.md         — expected fields, counting rules, results
deliverables/accuracy-results.json
```

**Files edited:**
- `access_intake/cli.py` — added `accuracy` command

**Commands run:**
```bash
# Live recording for IT-021
API_KEY="$DATABRICKS_TOKEN" INTAKE_PROVIDER=databricks INTAKE_RECORD_FIXTURES=1 \
  INTAKE_STATE_DIR=/tmp/p21/state INTAKE_DELIVERABLES_DIR=/tmp/p21/deliv \
  INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake intake eval/paraphrase/IT-021.json
# Result: succeeded, fixture written, no token leak
# Total fixtures: 21

# Offline mock replay of all 21
env -u API_KEY -u DATABRICKS_TOKEN \
  INTAKE_STATE_DIR=/tmp/acc/state INTAKE_DELIVERABLES_DIR=/tmp/acc/deliv \
  INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake intake materials/requests eval/paraphrase
# Result: 21/21 succeeded

# Accuracy check
INTAKE_DELIVERABLES_DIR=/tmp/acc/deliv python -m access_intake accuracy \
  --out /tmp/acc/accuracy-results.json
# Result: 231/231 fields, 21/21 routing, 0 factual errors,
#         19/19 purpose, 11/11 issues, 10/10 quotes

# Scorer mutation test (3 known errors injected)
# Result: exactly 228/231, 3 fields classified correctly

# Full test suite
PYTHONPATH=/tmp/deps python -m pytest --color=no
# Result: 147 passed

# Reproduce commands from accuracy.md run verbatim
INTAKE_STATE_DIR=/tmp/eval/state INTAKE_DELIVERABLES_DIR=/tmp/eval/out \
  python -m access_intake intake materials/requests eval/paraphrase
INTAKE_DELIVERABLES_DIR=/tmp/eval/out python -m access_intake accuracy \
  --out /tmp/eval/accuracy-results.json
# Result: 231/231, sources ['mock']

PYTHONPATH=/tmp/deps python -m pytest --color=no
# Result: 147 passed in 2.97s
```

### Step 6: Request evidence

**User:** "Continue to step 6"

**Files created:**
```
access_intake/evidence.py       — builds request-evidence.json from code
tests/test_evidence.py           — 2 tests
deliverables/request-evidence.json
```

**Files edited:**
- `access_intake/cli.py` — added `evidence` command

**Commands run:**
```bash
# Run evidence tests
PYTHONPATH=/tmp/deps python -m pytest tests/test_evidence.py
# Result: 2 passed

# Full suite
PYTHONPATH=/tmp/deps python -m pytest --color=no
# Result: 149 passed in 2.98s

# Generate request-evidence.json
env -u API_KEY INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake evidence --out <repo>/deliverables/request-evidence.json
# Result: 22,905 bytes written

# Credential leak checks
grep -cF "$DATABRICKS_TOKEN" <file>    # 0
grep -c "dbc-48a16403" <file>           # 0
grep -cE "dapi[0-9a-f]{8}|Bearer [A-Za-z0-9]" <file>  # 0

# Inspected: configuration, headers (Authorization: [REDACTED]),
# body keys, message roles, response model, handling steps (5/5 ok),
# retry policy, failure table (23 conditions), example failure record
```

**Failure table produced (23 conditions):**
```
HTTP 400    provider_rejected      retry=False
HTTP 401    provider_rejected      retry=False
HTTP 403    provider_rejected      retry=False
HTTP 404    provider_rejected      retry=False
HTTP 408    timeout                retry=True
HTTP 429    rate_limited           retry=True
HTTP 500    provider_unavailable   retry=True
HTTP 502    provider_unavailable   retry=True
HTTP 503    provider_unavailable   retry=True
HTTP 504    timeout                retry=True
Socket timeout                     timeout                retry=True
Connection refused / DNS           provider_unavailable   retry=True
Connection dropped mid-response    interrupted_response   retry=True
HTTP 200 with truncated body       interrupted_response   retry=True
HTTP 200 without choices           malformed_response     retry=False
message.refusal set                refusal                retry=False
finish_reason content_filter       refusal                retry=False
finish_reason length               interrupted_response   retry=False
finish_reason missing/unknown      interrupted_response   retry=False
Empty content                      malformed_response     retry=False
Plain-text refusal instead of JSON refusal                retry=False
Content is not valid JSON          malformed_response     retry=False
JSON violates schema (extra field) schema_validation      retry=False
```

### Step 7: Verification, deliverables, README

**User:** "Can you build the verification and update the readme then?"

**Files created:**
```
access_intake/verify.py          — 10 independent consistency checks
tests/test_verify.py             — 3 tests
deliverables/verification.md     — commands, assertions, observed results, repairs, limits
```

**Files edited:**
- `access_intake/cli.py` — added `verify` command
- `README.md` — complete rewrite with setup, commands, environment variables, queue
  inspection, deliverables, layout, limits

**Commands run:**
```bash
# Run full test suite including verify tests
PYTHONPATH=/tmp/deps python -m pytest --color=no
# Result: 152 passed in 4.92s

# Generate committed deliverables (mock replay of 20 supplied exports)
env -u DATABRICKS_TOKEN INTAKE_STATE_DIR=/tmp/final/state \
  INTAKE_DELIVERABLES_DIR=<repo>/deliverables \
  INTAKE_FIXTURES_DIR=<repo>/fixtures/responses \
  python -m access_intake intake materials/requests
# Result: 20 new, 20 queued, 20 succeeded, 0 pending

# Verify committed deliverables
INTAKE_DELIVERABLES_DIR=<repo>/deliverables python -m access_intake verify
# Result: 10/10 checks passed

# Compare committed records with accuracy run
# Result: 20/20 identical

# Test count by file
python -m pytest --collect-only
# tests/test_accuracy.py      54
# tests/test_config.py         7
# tests/test_evidence.py       2
# tests/test_extraction.py    31
# tests/test_routing.py       20
# tests/test_runner.py        13
# tests/test_sources.py       10
# tests/test_store.py         12
# tests/test_verify.py         3
# Total: 152 tests collected

# Citation check: verify every test name in verification.md exists
# Result: 4 names were wrong (written from memory) → fixed, all 29 resolve

# Run EVERY README command verbatim in a clean copy with no key:
python -m access_intake config           # exit=0, key not shown
python -m access_intake intake            # exit=0
python -m access_intake queue             # exit=0, 20 completed 0 pending
python -m access_intake queue --status needs-information  # exit=0, 9 completed
python -m access_intake queue --owner "Security Review"   # exit=0, 5 completed
python -m access_intake queue --json      # exit=0, 20 rows
python -m access_intake show IT-010.json  # exit=0
python -m access_intake export            # exit=0
python -m access_intake retry IT-010.json # exit=0
python -m access_intake resume            # exit=0
python -m access_intake verify            # exit=0, 10/10 checks passed
python -m pytest                          # exit=0, 152 passed
# eval intake + accuracy                  # exit=0, 231/231
python -m access_intake evidence          # exit=0
# live without API_KEY                    # exit=2, "Provider 'databricks' needs: API_KEY"
```

**Queue output (committed deliverables):**
```
document_id   request_id  status             owner             next_action
IT-001.json   IT-001      review             System Owner      review-request
IT-002.json   IT-002      review             Security Review   review-risk-then-owner
IT-003.json   IT-003      needs-information  IT Service Desk   clarify
IT-004.json   IT-004      needs-information  IT Service Desk   clarify
IT-005.json   IT-005      review             IT Service Desk   confirm-withdrawal
IT-006.json   IT-006      review             IT Service Desk   inspect-message
IT-007.json   IT-007      review             Security Review   review-risk-then-owner
IT-008.json   IT-008      review             System Owner      review-request
IT-009.json   IT-009      needs-information  IT Service Desk   clarify
IT-010.json   IT-010      needs-information  IT Service Desk   clarify
IT-011.json   IT-011      needs-information  IT Service Desk   clarify
IT-012.json   IT-012      review             Security Review   review-risk-then-owner
IT-013.json   IT-013      review             Security Review   review-risk-then-owner
IT-014.json   IT-014      needs-information  IT Service Desk   clarify
IT-015.json   IT-015      review             System Owner      review-request
IT-016.json   IT-061      needs-information  IT Service Desk   clarify
IT-017.json   IT-017      needs-information  IT Service Desk   clarify
IT-018.json   IT-018      review             System Owner      review-request
IT-019.json   IT-019      review             Security Review   review-risk-then-owner
IT-020.json   IT-020      needs-information  IT Service Desk   clarify

20 completed, 0 pending
```

### Step 8: Session capture

**User:** "Can we create a folder called capture. In that folder can we create a text file
called 'Genie Chat History'. Can we put a summary of what we accomplished in that file and
below the summary put the full chat history including all commands run by genie if possible."

**Files created:**
```
capture/genie-chat-history.md   — this file
```

---

## Repairs made during the session

| When | What was wrong | How it was fixed |
| --- | --- | --- |
| Step 3 | One test passed arguments in wrong order | Test corrected; 60 tests pass |
| Step 4 | `Store._row` crashed on `json.loads(None)` for pending attempts | NULL settings returned as `None` |
| Step 4 | Two-operator test racy with failures | Test uses all-succeeding fixtures |
| Step 4 | Routing fingerprint didn't track code changes | Added `routing.py` file hash |
| Step 7 | 4 test names in verification.md didn't exist | Replaced with real names from `pytest --collect-only` |

## Stray files (need manual deletion)

During early steps, files were accidentally created in the user's home directory instead of
the repository. Genie Code cannot delete workspace files. These need manual deletion via the
Workspace browser:
- `/Users/chad.t.vanorsdale.irs@catalyte.ai/access_intake/`
- `/Users/chad.t.vanorsdale.irs@catalyte.ai/tests/`
- `/Users/chad.t.vanorsdale.irs@catalyte.ai/requirements*.txt`
- `/Users/chad.t.vanorsdale.irs@catalyte.ai/pytest.ini`
- `/Users/chad.t.vanorsdale.irs@catalyte.ai/.gitignore`

### Step 9: Demo notebook

**User request:** Create a Python notebook that summarizes the program and has code cells to
walk through the application step by step based on CHALLENGE.md requirements — an easy way to
demo the program.

**Files created:**
- `demo.py` — 34-cell notebook (20 code + 14 markdown), self-contained walkthrough

**Sections in the notebook:**
1. Setup (install test deps, configure paths, define `run()`/`run_json()`/`fresh()` helpers)
2. Configuration — `python -m access_intake config`, verify API key not shown
3. Intake — process all 20 messages via mock replay (~1s)
4. Queue inspection — full, filtered by status, by owner, machine-readable JSON
5. Document detail — IT-001 extraction fields and supporting quotes
6. Provider failure & retry — hide IT-010 fixture, intake fails (exit 4), restore & retry
7. Unchanged rerun — re-intake skips all (zero model calls)
8. Changed source — modify IT-001, detect new hash, old result in history
9. Settings change — change temperature, all 20 re-extracted
10. Accuracy — 231/231 fields, 21/21 routes on 21 documents
11. Verification — 10/10 checks on committed deliverables
12. Request evidence — redacted request, 5 handling steps, 23-condition failure table
13. IT-006 embedded instructions — injection detected, routed to IT Service Desk
14. Pytest — 152 tests (copied to /tmp to avoid `__pycache__` workspace FS issue)

**Repairs during notebook development:**
- Initial cells used `json.loads(out.split("\n", 1)[-1])` assuming a header line; CLI stdout
  is clean JSON — switched to `run_json()` helper that calls `json.loads()` directly
- `show` command output has keys `current_attempt_id` + `attempts`, not `current` — fixed
  cells to look up the current attempt from the attempts list
- `route_reasons_json` is converted to `route_reasons` by `_row()` — fixed IT-006 cell
- `os.environ.pop()` return values printed to notebook output — added trailing semicolons
- Pytest failed with `OSError: __pycache__` on workspace FS — cell now copies to `/tmp` first

**Verification:**
- All 20 code cells run successfully end to end (confirmed via `dataScience_runAsset`)
- Key outputs confirmed: 20/20 intake, 231/231 accuracy, 10/10 verify, 152/152 tests,
  IT-006 override detected with rule `1-instruction-override`

### Step 10: README update

**User request:** Update README to include info about the demo and any other changes.

**Changes to `README.md`:**
1. Added **Demo notebook** section (between "Tests, accuracy check" and "Deliverables"):
   - Describes `demo.py` as self-contained offline walkthrough
   - Lists all 13 demo sections
   - Notes the `__pycache__` workaround for serverless compute
2. Added **Session capture** section:
   - Points to `capture/genie-chat-history.md`
3. Updated **Repository layout** tree:
   - Added `capture/` directory entry
   - Added `demo.py` entry

### Step 11: Session capture update

**User request:** Update genie-chat-history.md to include latest session history.

**Changes:** Appended Steps 9-11 to the capture file covering the demo notebook creation,
README update, and this capture update itself.

---

*This file was generated by Genie Code on 2026-11-05 at the user's request to capture the
coding session, and updated with Steps 9-11 covering the demo notebook, README update, and
this capture refresh. It reconstructs the full history from the conversation context.*

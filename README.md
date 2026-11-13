# Northstar access-request intake

A CLI turns UTF-8 JSON message exports into validated request facts and a queue for human review. It implements `materials/office-policy.md`; it never grants access. Security Review assesses risk and forwards its recommendation to the system owner for a human decision. A named manager is a contact.

## Setup

Supported runtime: Python 3.11 or later on a POSIX host with SQLite and symlinks. Verified here on Linux/Python 3.12.3. Python 3.11 and other operating systems have not been exercised. The real provider adapter uses OpenAI's Responses API and configurable `gpt-4.1-mini`; all included examples and tests use saved mocks.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.lock
```

If the system Python lacks ensurepip/venv support, install that OS component or use an installed `uv`:

```bash
uv venv .venv --allow-existing
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
source .venv/bin/activate
```

Dependency setup can access a package index; tests and mock intake need no network. The lockfile pins all six dependencies with SHA-256 hashes. `requirements.in` records the direct dependency. The implementation uses `argparse`, `sqlite3`, `urllib.request`, threads and `unittest`, plus `jsonschema`.

## Reproducible mock commands

Run from the repository root, with the virtual environment activated. The equivalent parameterized commands in [docs/commands.json](docs/commands.json) are executed by `T-DOCS` against temporary state.

```bash
env -u API_KEY python3 -m unittest discover -s tests -v
env -u API_KEY python3 -m access_intake intake materials/requests --provider mock --mock-dir tests/mock_responses --db .state/intake.sqlite3 --requests-per-minute 0
env -u API_KEY python3 -m access_intake queue --db .state/intake.sqlite3
env -u API_KEY python3 -m access_intake history IT-001.json --db .state/intake.sqlite3
env -u API_KEY python3 -m access_intake resume --provider mock --mock-dir tests/mock_responses --db .state/intake.sqlite3
env -u API_KEY python3 -m access_intake export --db .state/intake.sqlite3 --out deliverables
env -u API_KEY python3 -m access_intake accuracy --inputs materials/requests --synthetic tests/fixtures/requests/IT-021.json --expected tests/expected/source-facts.json --provider mock --mock-dir tests/mock_responses --out deliverables/accuracy.md
```

The queue prints `results` and `work`: completed records are available while another intake runs. Every result shows status, owner, next_action, attempt ID, source hash, configuration and policy version. `work` shows accepted pending/leased documents. Successful normal reruns reuse results; omission from a later batch preserves earlier documents.

A failed record includes a copyable `retry_command`. To retry a saved version explicitly, replace `ATTEMPT_ID` with the ID printed by the queue:

```bash
env -u API_KEY python3 -m access_intake retry --attempt ATTEMPT_ID --provider mock --mock-dir tests/mock_responses --db .state/intake.sqlite3
```

Retry uses the saved source bytes/settings/policy version and bypasses reuse, creating a new attempt. It does not silently read the file's newer contents; run intake again to accept changed input. Current errors replace older successful results in the queue, while history retains the original success and hash. Normal unchanged intake leaves a failed result visible until explicit retry; resume finishes pending work and reclaims expired worker leases. Default leases expire after 120 seconds. Committed results survive interruptions; a provider reply lost before commit can be requested again.

New exports use a flat directory with unique filenames and a string `message`. Facts come from that message; metadata is display context. SHA-256 covers the original JSON bytes. Model output must complete and validate against `snapshot.schema.json`; refusals, malformed/invalid/interrupted replies and provider failures produce an actionable error with null extraction. Invalid absolute dates and withdrawals missing ID/date fail clearly, as clarified by the user; valid reversed dates clarify under policy rule 3.

The mock adapter loads a fixture matching the document filename from `tests/mock_responses`. For a new document, author its expected source facts first, then its saved provider envelope. `--scenario FILE` supplies reproducible development errors, delays or alternate saved replies. Examples are exercised by the tests; the source message never selects a scenario or executes an instruction.

## Provider and configuration

Live intake requires explicit `--provider openai` (or `MODEL_PROVIDER=openai`). The operator supplies `API_KEY` in the environment. The live adapter reads it only at request time and uses HTTPS POST to the configured `/v1/responses` endpoint with the extraction instructions, decoded message and JSON Schema output format. The producing response's model ID is recorded in `response_source`. This submission contains no live observations.

Tests guard socket access and every `API_KEY` read, including test subprocesses. Provider tests inject a literal dummy-token supplier and HTTP replies saved under `tests/mock_responses`; they never read even a dummy environment key. The environment credential loader is inspected in source, not invoked by tests. Credentials are excluded from stored settings and redacted from request evidence and error diagnostics. Keep operational diagnostics free of credential values.

| Environment variable | Default / behavior |
|---|---|
| API_KEY | No default; read only by an explicitly selected live request |
| MODEL_PROVIDER | mock; choose mock or openai |
| MODEL_ID | gpt-4.1-mini |
| MODEL_BASE_URL | https://api.openai.com/v1/responses |
| MODEL_TIMEOUT_SECONDS | 30 seconds per provider request |
| MAX_WORKERS | 4 worker threads per intake process |
| REQUESTS_PER_MINUTE | 60 shared intake reservations per minute; 0 disables for offline examples |
| MAX_ATTEMPTS | 3 total transient-error attempts |

CLI flags override non-secret environment defaults. Effective settings and prompt/schema hashes are recorded. The Python service also accepts `model_options`, `prompt_path`, `schema_path`, `retry_delay` and `lease_seconds`; no credential-bearing settings are accepted. An extraction-affecting fingerprint change triggers a new extraction. A routing-policy/code change can reuse an already validated extraction but produces a new versioned routing result. Retrying an old attempt uses its saved configuration fingerprint and policy version as provenance; if the saved prompt/schema/policy assets have changed, retry/resume refuses with a clear diagnostic; restore those assets or run intake to refresh with current settings (see operating limits).

## State, output and limits

Use one local SQLite database on one host for overlapping operators. Foreign keys, transactions, leases, claim tokens and sequence checks keep one coherent current result per document and preserve prior attempts. All readable documents in a batch are recorded in one acceptance transaction before the first model call. If stopped before batch acceptance commits, rerun intake; after acceptance, resume sees the entire unfinished batch. Malformed exports receive individual error records so unaffected inputs continue.

Transient HTTP 429/500/502/503/504, connection and timeout failures retry within a bounded deadline, honoring numeric Retry-After. Refusals and invalid facts/output require explicit follow-up. The shared rate limiter covers processes using this database; it cannot coordinate unrelated internal tools. Worker count, timeout and retry settings are operating choices, not a throughput SLA.

`deliverables/extractions.jsonl` and `deliverables/queue.json` are symlinks through `deliverables/current` to one immutable generation under `.generations`. Resolve `current` once before opening both files for a consistent pair; `manifest.json` describes that generation. Export serializes publishers with a local file lock and switches the pointer only after the staged files are flushed. A failed staged generation may remain unreferenced. Export is a snapshot; the CLI queue is the current database view. Earlier result history remains in SQLite, not mixed into the current JSONL.

The sample queue contains 20 original documents. The 21-case accuracy comparison includes an authored paraphrase in separate in-memory processing and cannot add that synthetic case to the queue. It compares twelve scalars per message (252 denominator) against independently authored source expectations. Quote/issue and routing checks are separate. The committed **252/252 mock fixture agreement** is reproducibility evidence, not live extraction accuracy.

Verification commands, observations, repairs, unresolved policy boundaries and release checks are in [deliverables/verification.md](deliverables/verification.md). Test-first failures and green full-suite runs are under `planning/test-evidence/`; the final full run is `planning/test-run.txt`. No cross-host/network-filesystem support, throughput target, or completed approval workflow is claimed. The 200–400 ordinary and roughly 2,000 peak-message workloads need separate performance testing before production handover.

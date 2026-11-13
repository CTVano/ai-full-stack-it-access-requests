# Verification and handover

## Commands and intended comparisons

Activate the environment as README.md describes. No command below invokes a real model or needs a key.

```bash
env -u API_KEY python3 -m unittest discover -s tests -v
env -u API_KEY python3 -m access_intake intake materials/requests --provider mock --mock-dir tests/mock_responses --db .state/intake.sqlite3 --requests-per-minute 0
env -u API_KEY python3 -m access_intake queue --db .state/intake.sqlite3
env -u API_KEY python3 -m access_intake export --db .state/intake.sqlite3 --out deliverables
env -u API_KEY python3 -m access_intake accuracy --inputs materials/requests --synthetic tests/fixtures/requests/IT-021.json --expected tests/expected/source-facts.json --provider mock --mock-dir tests/mock_responses --out deliverables/accuracy.md
```

`docs/commands.json` contains the exact parameterized intake, queue, history, resume, retry, export and accuracy commands. T-DOCS executes them in temporary state and compares the sample's statuses/owners/actions with source-derived expectations, checks 20 current records and source/attempt agreement, and checks the separate 252-field accuracy denominator. Its retry uses an actual saved attempt ID from its own intake, not a placeholder.

| Assertion | Offline test and intended comparison |
|---|---|
| Saved replies, errors and delays | T-MOCK returns the actual fixture, records calls, copies mutable data and honors an injected barrier/error |
| Filename/message/byte separation | T-INPUT compares source SHA-256 and decoded message with original bytes, metadata-only and byte-only changes, two filenames and an authored 200-word input |
| Validate before use | T-SCHEMA mutates properties/types/enums/extra fields/quotes; T-REFUSAL and T-INTERRUPTED reject refused, incomplete and cut-off replies |
| No credential or real service use | OfflineCase and subprocess sitecustomize reject sockets and all API_KEY reads; T-TRANSPORT injects literal credentials and saved HTTP replies, checks redaction, transient HTTP classification and actual response model provenance |
| All source facts and routing | T-CORPUS plus the named individual policy cases compare all 20 original exports and the authored paraphrase with tests/expected/source-facts.json; conflicts, corrections, relative dates, ID mismatch, missing manager/end date, changes, other messages, and independent rule-priority cases are included |
| Clarified validation errors | T-BAD-WITHDRAWAL and T-BAD-DATE verify null extraction/actionable errors for incomplete withdrawals and impossible dates; valid reversed dates still clarify |
| Transactions and durable state | T-STORE injects rollback before commit and reopens the database, comparing current records and attempt/hash/configuration references |
| Reuse and versioning | T-RERUN asserts one provider call and one current result; T-CHANGED checks new hash/current result and earlier history; T-SETTINGS checks model/prompt/policy refresh and refuses a historical retry after prompt assets change |
| Failure after success | T-FAILED-RETRY checks current error/null extraction, distinct attempts, preserved prior success and source hash |
| Batch omission | T-BATCH leaves IT-001 present after a later batch containing only IT-002 |
| Overlap and stale completion | T-OVERLAP runs two OS processes against one database, blocks the first response, rejects a duplicate claim, and completes a newer write revision before the older read revision finishes |
| Interruption boundaries | T-RESUME kills an actual worker before response, before commit and after commit; expires its lease explicitly; verifies a separate completed request survives; also kills a single-worker accepted batch and resumes all three recorded documents |
| Isolation and recovery bounds | T-ISOLATION holds one reply while other messages complete, checks pending work, uses a fake clock for Retry-After/backoff/rate waits and exhausts a three-error script |
| Operator interface | T-CLI uses guarded subprocesses for intake, queue, history, resume and saved-attempt retry; another CLI inspects completed work while the original intake is blocked |
| Snapshot publication | T-EXPORT links every queue entry to the same extraction attempt/hash, injects failure before publication and verifies a reader holding the older generation still sees an immutable coherent pair |
| Independent comparison | T-ACCURACY changes a candidate permission, expects 251/252, checks twelve mismatches for a failed extraction, and verifies the independent oracle file is unchanged |

## Observed results and repairs

Configuration: Linux, Python 3.12.3, SQLite on local disk, jsonschema 4.23.0 and the hash-pinned dependencies in requirements.lock; provider=mock, requested model=gpt-4.1-mini. No live provider calls or live model-accuracy observations were made.

Observed: steps 1–10 each have an observed failing-test commit followed by their implementation commit and green full-suite log in planning/test-evidence. Additional mocked regressions were committed before fixes when integration exposed HTTP retry-classification loss, missing unstarted batch work, inaccurate model provenance and historical-prompt retry drift and configured prompt/schema paths not being applied at the provider boundary. Observed: the final clean-environment run passed all 49 tests (the 46 planned case IDs plus three guarded starter tests). Full output is in planning/test-run.txt and planning/test-evidence/step-11-green.txt; those logs are the test result authority.

Observed: sample intake/export produced 20 current mock records; the independently processed 21-message accuracy run produced 252/252 exact scalar matches. See sample-intake.json, sample-export.json and sample-accuracy.json under planning/test-evidence, and accuracy.md for the actual source expectations, configuration, provenance and mismatch details. This is mock fixture consistency, not observed live extraction quality.

Observed: a clean temporary environment was created with uv, then all six dependencies were installed offline from the local cache using requirements.lock and --require-hashes. setup-locked.txt records that installation. The host's initial python3 -m venv failed because ensurepip was missing; README documents the uv fallback. A different machine may need package-index access for initial installation.

Repairs: the byte-only source fixture originally had identical formatting and was corrected; subprocess socket guards now import stdlib socket subclasses before preventing socket construction; HTTP 503 metadata now survives credential-redacting adapter errors; accepted batches are persisted atomically before provider work; producing model IDs come from the response; changed historical prompt/schema/policy assets cause a clear retry refusal instead of falsely claiming the old configuration was executed; versioned prompt/schema paths now control both the actual request and response validation. All starter tests now use the same no-network/no-API_KEY-read guard.

## Operating assumptions and Limits

- Scope: overlapping CLI processes on one host, one local SQLite database, POSIX file locks/symlinks. Cross-host/shared-network filesystems are unsupported. Database writers use short transactions; model calls occur outside them. Claims, lease tokens and attempt sequences fence stale completions. Older attempts remain stored separately from current views.
- Workload: the company context is 200–400 ordinary messages and roughly 2,000 peak messages. Tests cover the 20 supplied messages, authored boundary cases and small controlled concurrency workloads. No production-scale performance, sustained rate-limit, memory, disk-full or arbitrary-host-crash certification was performed. No throughput SLA is claimed.
- Recovery: a readable batch is accepted atomically before its first provider request. A stop before that acceptance commits requires rerunning intake; after it commits, resume locates every pending document, including work not yet dispatched. Default leases expire after 120 seconds; tests expire leases explicitly. A response lost before its transaction commits may be requested again; completed commits are reused.
- Settings: provider/endpoint/model/options and prompt/schema bytes identify extraction production. Policy bytes and router code identify routing versions. Original source bytes and effective settings are retained. A historical retry/resume requires the original processing assets; if prompt/schema/policy files changed, restore them or run a new intake refresh. Historical executable code is not archived inside SQLite. Earlier pending work superseded by a newer accepted attempt is retained as superseded activity and excluded from recoverable work.
- Provider behavior: the implemented OpenAI adapter is tested only with injected saved HTTP replies. The real environment credential loader is source-inspected, never invoked by tests. Account/model availability, real service latency, SDK-free HTTP compatibility and live factual accuracy remain unobserved. Numeric Retry-After is honored; HTTP-date Retry-After is not parsed. Rate reservations coordinate this database's processes, not other internal tools.
- Source interpretation: exact scalar comparisons use the source spans selected in the plan, so a different supported purpose phrasing may score as a mismatch without being false. Quote substring validation cannot prove all semantic facts are correct. Null/issue behavior and routing are checked; no universal override-attack detector is claimed beyond the explicit patterns and tested cases. Multiple requests in one message, nonstandard ID normalization, broader correction wording, partially specified dates and unknown-secret redaction remain policy/design boundaries from the spec's open questions.
- Current output: CLI queue is authoritative live state; exported files are a snapshot. Resolve deliverables/current once before reading both files. Generation files are flushed before pointer publication, and local publishers are serialized. Independent raw file opens across a generation switch are not a coherent-read contract. Abandoned staged generations can remain; no history-retention deletion is implemented. The submitted accuracy synthetic case stays outside the 20-document queue.
- Human workflow: output is a review queue. Security Review's recommendation-to-owner handoff is documented; there is no approval action, real person directory or automatic access grant.

## Release checklist and pending manual checks

These are planned human release checks; automated test success does not mark them passed.

- **C-CAPTURE:** inspect the final application's Entire-associated session record and compare it with the facilitator's capture instructions. Entire captures commits automatically; no manual checkpoints were created.
- **C-COMMIT:** inspect the final local application commit for source, requirements.lock, outputs and associated capture, and verify a test-first commit precedes each step's implementation commit. The implementation task authorizes these local commits.
- **C-PUSH:** after an explicitly authorized release push, compare the remote default-branch head with the submitted application commit. No remote push is performed by this implementation task.
- **C-SUBMIT:** after the facilitator supplies the Classroom assignment and submission is authorized, inspect the actual submitted value and ensure it is only the root repository URL. No assignment destination or submission was supplied for this implementation task.

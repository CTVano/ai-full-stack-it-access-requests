# Inspected hostile-source handling

The ten exports under `tests/fixtures/submission_2/` are newly authored fiction. Names explicitly labelled Synthetic describe no real person; there are no taxpayer records, credentials or real secret values. Expected facts and source/policy quotations were written in `tests/expected/submission_2.json` before running the application. All twelve saved replies, including two invented log-leak canaries, live under `tests/mock_responses/submission_2/`. These exact exports were added to the synthetic-data manifest; arbitrary inputs remain blocked.

## Expected and observed outcomes

I opened the authored exports and inspected the saved diagnostic results, including the actual status, owner, next action, issue codes and match flags. All ten cases match their independent expected facts and routing.

| Case | Source ID | Observed status / owner / next action | Logged issue codes |
|---|---|---|---|
| Literal override | S2-001 | review / IT Service Desk / inspect-message | instruction-override-attempt |
| Different explicit wording | S2-002 | review / IT Service Desk / inspect-message | instruction-override-attempt |
| Developer-role spoofing | S2-003 | review / IT Service Desk / inspect-message | instruction-override-attempt |
| Credential demand | S2-004 | review / IT Service Desk / inspect-message | instruction-override-attempt |
| Normal staging/read control | S2-005 | review / System Owner / review-request | none |
| Explicit environment correction | S2-006 | review / System Owner / review-request | none |
| Hostile source with invalid model reply | S2-007 | error / IT Service Desk / retry-extraction | extraction-error, schema-validation |
| Missing access dates | S2-008 | needs-information / IT Service Desk / clarify | missing-or-unresolved-facts |
| Uncorrected environment conflict | S2-009 | needs-information / IT Service Desk / clarify | conflicting-facts, missing-or-unresolved-facts |
| Requested permission change to read | S2-010 | review / System Owner / review-request | none |

For example, I checked S2-006 directly: the source explicitly corrects production to staging. The policy's explicit-correction rule therefore requires staging, and the otherwise complete staging/read request follows rule 5 to System Owner review. Its manager remains a contact, and no decision or grant is produced. S2-009 supplies both environments without a correction, so environment stays null, the conflict remains in evidence/issues, and rule 3 requires clarification. These expectations come from source words and policy, not measured code output.

The first four mock replies deliberately omit the attack issue and attack quotation. The application must detect the explicit instruction attempt itself, add the fixed issue, preserve the exact attack paragraph as application evidence, and keep requested permission read. The invalid reply in S2-007 is only an approval-shaped object: schema rejection occurs before routing, so its extraction is null. It is counted as a rejected response, not a successfully extracted override case.

## Privacy of logs and evidence

[hostile_results.jsonl](hostile_results.jsonl) records case paths and source/reply hashes, expected/actual routing, safe issue codes/counts, evidence counts/hashes, and pass flags. It intentionally excludes the raw messages, identity fields, supporting quotations and free-form issues. Exact synthetic evidence remains in the fixture/oracle files and application records. The request to keep logs free of sensitive content takes precedence over the earlier improvement plan's suggestion to copy preserved evidence text into a log.

The file identifies HEAD together with hashes of the actual working-tree implementation and expectations. These observations include uncommitted changes; they do not claim the HEAD commit alone contains this implementation. The audit sidecar is emitted after commit and has the crash-consistency limitation documented in `docs/privacy.md`.

The tests assert exact extracted facts, displayed routing fields, preserved source evidence, recorded issue codes/counts, outstanding decisions and no access grants. Offline guards reject every `API_KEY` read and socket call, and the hostile-case tests also watch command-execution channels. Two additional saved canaries verify that arbitrary issue text and invalid response values cannot leak into diagnostic events; a rollback test checks that uncommitted results do not create audit events.

## Reproduce

```bash
env -u API_KEY .venv/bin/python -m unittest discover -s tests -p test_12_hostile_sources.py -v
env -u API_KEY .venv/bin/python -m unittest discover -s tests -v
env -u API_KEY PYTHONPATH=tests/offline_site:. .venv/bin/python planning/run_hostile_sources.py
```

The original targeted failure is [hostile-sources-red.txt](../test-evidence/hostile-sources-red.txt). Twelve of thirteen tests failed: alternate attack routes/evidence were wrong or the handling diagnostic was missing; the fixture inventory check passed. Normal and correction routing already worked before the fix, although their new audit assertions failed. [hostile-sources-targeted-green.txt](../test-evidence/hostile-sources-targeted-green.txt) records the first targeted pass; [hostile-sources-green.txt](../test-evidence/hostile-sources-green.txt) records the final full suite, including the subsequently added rollback check.

Scope: these checks cover the authored explicit wording and controls using saved model replies. They do not measure live-model accuracy or prove resistance to every prompt injection, obfuscation or benign quotation of attack language. An uncertain source still requires human inspection. This remains a synthetic training application and does not authorize processing real IRS restricted data.

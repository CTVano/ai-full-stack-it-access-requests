# Synthetic training data security review

Scope confirmed by the project owner: synthetic training data only. Reviewed 2026-11-13 against [course Day 1, sections 1.1–1.3](https://talent.catalyte.ai/courses/irs-bpa-to6/week-01/#day-1) and its primary source, [IRM 10.24.1](https://www.irs.gov/irm/part10/irm_10-024-001r).

The policy requires approved AI products and uses, prohibits restricted information in unauthorized AI systems, and requires human review of outputs. Removing names does not establish that data is safe. Questions about data use, including synthetic or anonymized data, go to PGLD at *Privacy; use approval rests with the accountable agency official. This code does not make those decisions.

| Surface reviewed | Change | Offline verification |
|---|---|---|
| Input, metadata and source/history storage | Accept only exact reviewed synthetic filenames and source hashes in `synthetic-data-manifest.json`; verify decoded message and metadata against the bytes | `test_unreviewed_input_is_not_sent_or_stored`, `test_modified_metadata_and_bytes_are_unreviewed`, `test_unreviewed_direct_provider_and_storage_calls_are_blocked` |
| Model transmission | Allow only reviewed synthetic messages; real calls require a matching, unexpired approval record; check before credentials/network; HTTPS and no redirects | `test_live_approval_is_checked_before_credentials_or_network`, `test_approval_cannot_change_endpoint_model_purpose_or_expiry`, `test_redirects_never_resend_authorization_or_data` |
| Error diagnostics | Report categories, field names and HTTP codes; omit invalid field values, provider bodies and exception text | `test_diagnostics_do_not_echo_response_or_exception_text`, `test_T_BAD_DATE` |
| Request evidence | Replace message and instructions with placeholders and SHA-256 values; redact authorization | `test_request_evidence_omits_message_and_instructions` |
| Local database and exports | Create private directories and owner-only regular files, secure SQLite sidecars, reject final-file symlinks/hard links | `test_runtime_files_have_private_permissions`, `test_database_symlink_is_rejected` |
| Repository and tests | Mark inputs/fixtures as synthetic through the manifest; ignore local environment secrets and SQLite state; no real provider calls or `API_KEY` reads in tests | Full offline suite and `tests/support.py` / `tests/offline_site/sitecustomize.py` |
| Human decisions | Continue to produce a human review queue; never grant access or make taxpayer decisions | Existing policy and CLI tests |

## Adding synthetic examples

1. Author new messages entirely from invented facts. Do not start from a real record or merely remove identifiers. Review the data outside AI tools before putting it in this workspace.
2. After review, add a manifest entry with its relative path, filename as `document_id`, SHA-256 of the original file bytes as `source_sha256`, and SHA-256 of the UTF-8 decoded message as `message_sha256`. Byte formatting, filenames and metadata are part of the reviewed export: changes need review and updated hashes. Do not automatically admit an entire input directory.
3. Author independent expected facts and mock replies; run the offline suite. The manifest is a reviewed allowlist, not a PII detector or legal determination that data is synthetic.

Unknown or malformed files produce an opaque `blocked-<sha256>.json` identifier and a safe error. Their original filename, message, metadata and raw bytes are not retained by intake. Review classification before retrying; an error record is not permission to add the source to the allowlist.

## Live use remains disabled without approval

Mock mode remains the default. No checked-in record authorizes live use. Once the responsible official has approved this specific synthetic-training use, provision a local record outside version control (for example `.state/model-approval.json`) with these fields:

```json
{
  "endpoint": "https://api.openai.com/v1/responses",
  "model": "gpt-4.1",
  "purpose": "synthetic-training-only",
  "prompt_sha256": "REPLACE-WITH-SHA256-OF-REVIEWED-PROMPT-FILE-BYTES",
  "approval_reference": "REPLACE-WITH-ACTUAL-APPROVAL-REFERENCE",
  "valid_until": "2000-01-01"
}
```

The example is deliberately expired and is not an approval. Use the endpoint, model, reviewed prompt hash, reference and expiry actually authorized, and set file permissions to `0600`. Review prompt changes for data content before updating approval; a changed prompt cannot reuse the earlier approval. Supply its path with `--approval-file` or `MODEL_APPROVAL_FILE`. The application verifies the exact tuple and expiry, but cannot independently authenticate an agency decision. Trusted provisioning and filesystem permissions are essential. An injected offline test transport uses explicit dummy credentials, does not perform a network call and requires no live approval.

## Limits and operational responsibilities

- This remains a training application. It is not an approved IRS service, a production compliance certification or a system for PII, FTI, SBU, CUI or other restricted information. Do not put that information in the repository, agent workspace, fixtures, prompts, terminal output or session capture.
- No automatic redaction or pattern detector can establish that a taxpayer record is safe. Both a reviewed dataset and authorization of the tool/use matter. Anyone able to edit the code or manifest can change this application's controls; enforce repository review and isolate the workspace from real data and unnecessary network/credentials.
- Successful extraction records deliberately retain business facts and exact supporting quotes, as required by this assignment. They are application results for fictional data, not operational logs. Review all outputs before sharing or acting on them. There is no automatic approval action.
- New directories are private. Existing parent directories, old databases, historical exports, backups, Git history and session captures are not retroactively sanitized or deleted. The supplied committed artifacts are fictional training examples. Review existing state before using these commands; restrict access and follow the responsible records/security process if real data was ever present. Do not attempt remediation by sending that data to an AI tool.
- Local owner-only modes protect against other OS users; they do not provide encryption, multiuser role enforcement, an access-audit service or a records-retention schedule. Configure OS isolation and encrypted storage as required by the authorized environment. Existing attempt/history records supply processing provenance, not a claim of complete sensitive-data access auditing.
- Privacy/manifest changes invalidate extraction reuse. Run fresh intake to refresh reviewed data; historical attempts with older assets require the documented restore-or-refresh handling. Do not bypass checks to resume old work.

Run the checks with:

```bash
env -u API_KEY .venv/bin/python -m unittest discover -s tests -v
```

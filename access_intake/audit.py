"""Minimal processing diagnostics: no source, extracted values or free-form issues."""
import json
import os
import re

from .privacy import digest, private_open


def processing_event(record):
    extraction = record.get('extraction')
    issues = extraction['issues'] if extraction is not None else []
    codes = set()
    error_category = None
    if record['status']=='error':
        codes.add('extraction-error')
        error_category = 'schema-validation' if 'schema validation' in record.get('error','') else 'extraction-failure'
        if error_category=='schema-validation':
            codes.add('schema-validation')
    elif extraction is not None:
        if 'instruction-override attempt' in issues:
            codes.add('instruction-override-attempt')
        if any(value is None for value in extraction.values()):
            codes.add('missing-or-unresolved-facts')
        if any('conflict' in issue.lower() for issue in issues):
            codes.add('conflicting-facts')
        if any('identifier mismatch' in issue for issue in issues):
            codes.add('identifier-mismatch')
        if 'start_date after end_date' in issues:
            codes.add('invalid-date-order')
        if issues and not codes:
            codes.add('source-issue')
    ident = record['document_id']
    # Only the fixed-format synthetic sample IDs are logged verbatim.
    if not re.fullmatch(r'(?:IT|S2)-\d{3}\.json',ident):
        ident = 'document-'+digest(ident.encode())[:16]
    return {'event':'intake-result','document_id':ident,'source_sha256':record['source_sha256'],
            'status':record['status'],'owner':record['owner'],'next_action':record['next_action'],
            'response_source':'mock' if record['response_source']=='mock' else 'provider',
            'issue_count':len(issues),'issue_codes':sorted(codes),'error_category':error_category}


def append_result(database_path,record):
    with private_open(str(database_path)+'.audit.jsonl') as stream:
        stream.write(json.dumps(processing_event(record),sort_keys=True)+'\n')
        stream.flush()
        os.fsync(stream.fileno())

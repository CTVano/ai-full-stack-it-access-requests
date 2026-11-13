"""Validated message facts enter five deterministic human-review rules, in order."""
from datetime import date
from pathlib import Path
import re
from .extraction import extract, failure
from .privacy import SafeError, safe_error
from .review import review_record

REQUIRED = ('request_id','request_date','requester','team','system','environment','permission',
            'business_purpose','start_date','end_date','manager')
OVERRIDE = re.compile(
    r'\bSYSTEM\s+OVERRIDE\b|highest-priority\s+system\s+instruction|'
    r'\bignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions|rules)\b|'
    r'\b(?:developer|system)\s+(?:instruction|message)\s*:\s*(?:skip|ignore|bypass|override|approve|grant|mark)\b|'
    r'\b(?:print|show|display|reveal|expose|return)\s+(?:the\s+)?(?:api[\s_-]*key|credentials?|secrets?)\b|'
    r'\b(?:bypass|skip|ignore)\s+(?:the\s+)?(?:review\s+queue|approval\s+(?:rules|checks|process)|policy)\b|'
    r'\b(?:mark|set)\s+(?:this\s+)?(?:request|status)\s+(?:as\s+|to\s+)?approved\b', re.I)


def route(extraction, document):
    facts = extraction
    issues = facts['issues']
    def issue(text):
        if text not in issues:
            issues.append(text)
    for field in ['request_date','start_date','end_date']:
        value = facts[field]
        if value is not None:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                    raise ValueError('use YYYY-MM-DD')
                date.fromisoformat(value)
            except ValueError as exc:
                raise SafeError(f'invalid calendar date for {field}; use a real YYYY-MM-DD date') from exc
    for field,value in facts.items():
        if value is None and field not in ' '.join(issues):
            issue(f'{field} missing or unresolved')
    if facts['kind']=='withdrawal':
        for field in ['request_id','request_date']:
            if not facts[field]:
                raise SafeError(f'withdrawal missing {field}')
    mismatch = bool(facts['request_id'] and facts['request_id'] != Path(document['document_id']).stem)
    if mismatch:
        issue('identifier mismatch: filename and stated request_id differ')
    reversed_dates = bool(facts['start_date'] and facts['end_date'] and facts['start_date']>facts['end_date'])
    if reversed_dates:
        issue('start_date after end_date')
    if OVERRIDE.search(document['message']):
        issue('instruction-override attempt')
        if not any(OVERRIDE.search(q) for q in facts['supporting_quotes']):
            for paragraph in re.split(r'\n\s*\n',document['message']):
                if OVERRIDE.search(paragraph):
                    facts['supporting_quotes'].append(paragraph)
        return 'review','IT Service Desk','inspect-message'
    if facts['kind']=='withdrawal':
        return 'review','IT Service Desk','confirm-withdrawal'
    conflict = any('conflict' in item.lower() for item in issues)
    if facts['kind'] not in ['new','change'] or conflict or mismatch or reversed_dates or any(not facts[field] for field in REQUIRED):
        return 'needs-information','IT Service Desk','clarify'
    if facts['environment']=='production' or facts['permission'] in ['write','admin']:
        return 'review','Security Review','review-risk-then-owner'
    return 'review','System Owner','review-request'


def process(document, provider, schema_path=None):
    result = extract(document,provider,schema_path)
    if result['error']:
        return review_record(result)
    try:
        status,owner,action = route(result['extraction'],document)
        return review_record(result | {'status':status,'owner':owner,'next_action':action})
    except ValueError as exc:
        return review_record(failure(document,safe_error(exc),provider.response_source))

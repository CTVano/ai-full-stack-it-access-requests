"""Validate completion, JSON, schema and evidence before routing any facts."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT/'snapshot.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def failure(document, error, response_source='mock'):
    return {k: document.get(k) for k in ['document_id','source_sha256']} | {
        'extraction': None, 'status': 'error', 'owner': 'IT Service Desk',
        'next_action': 'retry-extraction', 'error': f'{error}; correct the source/configuration or retry the saved attempt',
        'response_source': response_source}


def parse_response(response, message):
    if not isinstance(response, dict) or response.get('status') != 'completed':
        raise ValueError('response is not completed (interrupted or failed)')
    texts = []
    for item in response.get('output', []):
        if item.get('type') != 'message':
            continue
        if item.get('status') != 'completed':
            raise ValueError('message response is incomplete')
        for part in item.get('content', []):
            if part.get('type') == 'refusal':
                raise ValueError('provider refusal')
            if part.get('type') == 'output_text':
                texts.append(part.get('text', ''))
    try:
        result = json.loads(''.join(texts))
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid JSON extraction') from exc
    try:
        VALIDATOR.validate(result)
    except ValidationError as exc:
        raise ValueError(f'schema validation: {exc.message}') from exc
    if any(quote not in message for quote in result['supporting_quotes']):
        raise ValueError('supporting quote is not a decoded-message substring')
    return result


def extract(document, provider):
    source = provider.response_source
    try:
        extraction = parse_response(provider.extract(document['message'], document['document_id']), document['message'])
        return {k: document[k] for k in ['document_id','source_sha256']} | {
            'extraction': extraction, 'error': None, 'response_source': source}
    except Exception as exc:
        redact = getattr(provider, 'redact', str)
        result = failure(document, redact(str(exc)), source)
        from .providers import TransientError
        from urllib.error import HTTPError
        if isinstance(exc, (TransientError, TimeoutError)) or (isinstance(exc, HTTPError) and exc.code in [429,500,502,503,504]):
            result['_retryable'] = True
            try:
                result['_retry_after'] = float(getattr(exc, 'retry_after', 0) or (exc.headers.get('Retry-After',0) if isinstance(exc,HTTPError) else 0))
            except (TypeError,ValueError):
                result['_retry_after'] = 0
        return result

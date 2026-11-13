"""Validate completion, JSON, schema and evidence before routing any facts."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, SchemaError, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT/'snapshot.schema.json'


def load_schema(path=None):
    schema = json.loads(Path(path or SCHEMA_PATH).read_text(encoding='utf-8'))
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f'invalid schema: {exc.message}') from exc
    return schema


def validate_extraction(result, schema_path=None):
    # A configured schema may narrow the contract, but never replace it.
    schemas = [load_schema()]
    if schema_path is not None:
        schemas.append(load_schema(schema_path))
    try:
        for schema in schemas:
            Draft202012Validator(schema).validate(result)
    except ValidationError as exc:
        location = '.'.join(str(part) for part in exc.absolute_path) or '<root>'
        raise ValueError(f'schema validation at {location}: {exc.message}') from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'non-JSON numeric constant: {value}')


def failure(document, error, response_source='mock'):
    return {k: document.get(k) for k in ['document_id','source_sha256']} | {
        'extraction': None, 'status': 'error', 'owner': 'IT Service Desk',
        'next_action': 'retry-extraction', 'error': f'{error}; correct the source/configuration or retry the saved attempt',
        'response_source': response_source}


def parse_response(response, message, schema_path=None):
    if not isinstance(response, dict) or response.get('status') != 'completed':
        raise ValueError('response is not completed (interrupted or failed)')
    output = response.get('output')
    if not isinstance(output, list):
        raise ValueError('response output must be an array')
    texts = []
    messages = 0
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('response output item must be an object')
        if item.get('type') != 'message':
            continue
        messages += 1
        if item.get('status') != 'completed':
            raise ValueError('message response is incomplete')
        content = item.get('content')
        if not isinstance(content, list):
            raise ValueError('message content must be an array')
        for part in content:
            if not isinstance(part, dict):
                raise ValueError('message content part must be an object')
            if part.get('type') == 'refusal':
                raise ValueError('provider refusal')
            if part.get('type') == 'output_text':
                text = part.get('text')
                if not isinstance(text, str):
                    raise ValueError('output_text must contain a text string')
                texts.append(text)
            else:
                raise ValueError('unexpected message content type')
    if messages != 1 or len(texts) != 1:
        raise ValueError('response must contain exactly one extraction message and output_text')
    try:
        result = json.loads(texts[0],object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'invalid JSON extraction: {exc}') from exc
    validate_extraction(result,schema_path)
    if any(quote not in message for quote in result['supporting_quotes']):
        raise ValueError('supporting quote is not a decoded-message substring')
    return result


def extract(document, provider, schema_path=None):
    source = provider.response_source
    try:
        wire=provider.extract(document['message'], document['document_id'])
        source=provider.response_source
        if schema_path is None:
            schema_path = getattr(provider,'settings',{}).get('schema_path')
        extraction = parse_response(wire, document['message'],schema_path)
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

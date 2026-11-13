"""Flat UTF-8 exports: byte identity is distinct from extracted business facts."""
import hashlib
import json
from pathlib import Path


def read_export(path):
    path = Path(path)
    raw = path.read_bytes()
    try:
        export = json.loads(raw.decode('utf-8'))
        if not isinstance(export, dict) or not isinstance(export.get('message'), str):
            raise ValueError('expected an object with a message string')
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'{path.name}: invalid UTF-8 JSON export: {exc}') from exc
    return {'document_id': path.name, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'raw': raw, 'message': export['message'],
            'metadata': {k: v for k, v in export.items() if k != 'message'}}


def discover(path):
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f'{path}: input does not exist')
    if any(p.is_dir() for p in path.iterdir()):
        raise ValueError(f'{path}: intake requires a flat directory')
    return sorted(path.glob('*.json'))

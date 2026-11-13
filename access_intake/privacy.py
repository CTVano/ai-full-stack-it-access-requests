"""Synthetic-training boundary; these controls do not authorize real IRS data use."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT/'docs/synthetic-data-manifest.json'


class SafeError(ValueError):
    """A diagnostic constructed by this application without source/response values."""


class SyntheticDataError(SafeError):
    """Unreviewed input must never be retained, sent, or echoed."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _entries():
    try:
        manifest = json.loads(MANIFEST.read_text())
        if manifest['classification'] != 'synthetic-training-only':
            raise ValueError()
        return manifest['exports']
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise SafeError('synthetic data manifest is unavailable or invalid') from exc


def require_synthetic_message(message, document_id):
    hashed = digest(message.encode('utf-8'))
    if not any(row['document_id']==document_id and row['message_sha256']==hashed for row in _entries()):
        raise SyntheticDataError('input is not a reviewed synthetic training message; do not process real or restricted data')


def require_synthetic_document(document):
    raw = document['raw']
    hashed = digest(raw)
    if not any(row['document_id']==document['document_id'] and row['source_sha256']==hashed for row in _entries()):
        raise SyntheticDataError('input is not a reviewed synthetic export; review new synthetic data outside AI tools before updating its manifest')
    # Prevent callers from pairing approved raw bytes with a different decoded message.
    if document['message'] != json.loads(raw)['message'] or document['source_sha256'] != hashed:
        raise SyntheticDataError('synthetic export identity does not match its bytes')
    metadata = {k:v for k,v in json.loads(raw).items() if k!='message'}
    if document.get('metadata',{}) != metadata:
        raise SyntheticDataError('synthetic export metadata does not match its bytes')


def blocked_document(raw):
    hashed = digest(raw)
    return {'document_id':'blocked-'+hashed+'.json','source_sha256':hashed,
            'raw':b'','message':'','metadata':{}}


def is_blocked_document(document):
    return (document.get('document_id') == 'blocked-'+document.get('source_sha256','')+'.json'
            and bool(re.fullmatch(r'[a-f0-9]{64}',document.get('source_sha256','')))
            and document.get('raw') == b'' and document.get('message') == ''
            and document.get('metadata') == {})


def safe_error(exc):
    if isinstance(exc,SafeError):
        return str(exc)
    if isinstance(exc,HTTPError):
        return f'provider HTTP {exc.code}; check approved service availability'
    if isinstance(exc,TimeoutError):
        return 'provider timeout; check approved service availability'
    # Preserve status codes, never provider-supplied error text or response bodies.
    code = re.search(r'\b[45]\d{2}\b',str(exc))
    return f'provider error (HTTP {code[0]}); check approved service availability' if code else 'processing failed; check the input format, approved configuration and service availability'


def validate_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SafeError('live endpoint must use HTTPS without embedded credentials, query or fragment')


def validate_model(model):
    if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',model):
        raise SafeError('invalid model identifier; use a model ID without credentials or free text')
    return model


def require_live_approval(settings):
    endpoint = settings.get('endpoint','https://api.openai.com/v1/responses')
    validate_endpoint(endpoint)
    path = settings.get('approval_path')
    if not path:
        raise SafeError('live model use requires an approval record for this synthetic-training endpoint, model and purpose')
    try:
        policy = json.loads(Path(path).read_text())
        prompt_hash = digest(Path(settings.get('prompt_path',ROOT/'access_intake/prompt.txt')).read_bytes())
        if (policy['endpoint'] != endpoint or policy['model'] != settings.get('model','gpt-4.1')
                or policy['purpose'] != 'synthetic-training-only'
                or policy['prompt_sha256'] != prompt_hash
                or not isinstance(policy['approval_reference'],str) or not policy['approval_reference'].strip()
                or date.fromisoformat(policy['valid_until']) < date.today()):
            raise ValueError()
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise SafeError('live approval is missing, expired or does not match endpoint, model, prompt and synthetic-training purpose') from exc


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        raise SafeError('provider redirect blocked; use the exact approved HTTPS endpoint')


def open_request(request, timeout):
    return build_opener(NoRedirect()).open(request,timeout=timeout)


def private_directory(path):
    path = Path(path)
    if not path.exists():
        if path.parent != path:
            private_directory(path.parent)
        path.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise SafeError('private output directory must be a real directory')
    return path


def private_open(path, mode='a'):
    path = Path(path)
    private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    if mode != 'w':
        flags |= os.O_APPEND
    fd = os.open(path,flags,0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise SafeError('private output must be an owned regular file with one link')
        os.fchmod(fd,0o600)
        if mode == 'w':
            os.ftruncate(fd,0)
        return os.fdopen(fd,mode,encoding='utf-8')
    except BaseException:
        os.close(fd)
        raise

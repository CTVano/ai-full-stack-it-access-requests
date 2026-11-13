"""Provider adapters. The mock boundary never reads credentials or uses HTTP."""
from collections import defaultdict, deque
from copy import deepcopy
import json
from pathlib import Path
from threading import Lock
from .privacy import (SafeError, require_synthetic_message, require_live_approval,
                      open_request, safe_error, digest, validate_model)

DEFAULT_MODEL = 'gpt-4.1'


class MockProvider:
    response_source = 'mock'

    def __init__(self, mock_dir, scripts=None):
        self.mock_dir = Path(mock_dir)
        self.scripts = defaultdict(deque, {k: deque(v) for k, v in (scripts or {}).items()})
        self.calls = []
        self._lock = Lock()

    def extract(self, message, document_id):
        require_synthetic_message(message,document_id)
        with self._lock:
            self.calls.append({'document_id': document_id, 'message': message})
            action = self.scripts[document_id].popleft() if self.scripts[document_id] else document_id
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            action = action()
        if isinstance(action, dict):
            return deepcopy(action)
        filename = (self.mock_dir / str(action)).resolve()
        if not filename.is_relative_to(self.mock_dir.resolve()):
            raise ValueError('mock reply must remain inside the mock directory')
        return json.loads(filename.read_text(encoding='utf-8'))


class LiveProvider:
    """The real adapter reads credentials only at an explicitly requested live call.

    Injected transports require an explicit credential supplier, never environment access.
    """
    def __init__(self, settings, transport=None, credential=None):
        self.settings = dict(settings)
        self.transport = transport
        if transport is not None and credential is None:
            raise ValueError('injected transport requires an explicit credential supplier')
        self.credential = credential
        self.response_source = 'mock' if transport else f"openai/{settings.get('model',DEFAULT_MODEL)}"
        self._secrets = []
        self._evidence = None

    def redact(self, value):
        text = str(value)
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, '[REDACTED]')
        return text

    def extract(self, message, document_id):
        import os
        import urllib.request
        from .extraction import load_schema
        require_synthetic_message(message,document_id)
        schema = load_schema()
        if self.settings.get('schema_path') is not None:
            load_schema(self.settings['schema_path'])
        options = self.settings.get('model_options',{})
        if not isinstance(options, dict):
            raise SafeError('model_options must be an object')
        protected = {'model','instructions','input','text','store'} & options.keys()
        if protected:
            raise SafeError('model_options cannot override protected request fields')
        allowed = {'temperature','top_p','max_output_tokens'}
        if options.keys()-allowed or any(not isinstance(v,(int,float)) or isinstance(v,bool) for v in options.values()):
            raise SafeError('model_options supports only numeric temperature, top_p and max_output_tokens')
        if self.transport is None:
            require_live_approval(self.settings)
        if self.credential is not None:
            token = self.credential()
        else:
            token = os.environ['API_KEY']
        self._secrets.append(token)
        body = {'model': self.settings.get('model',DEFAULT_MODEL),
                'instructions': Path(self.settings.get('prompt_path',Path(__file__).with_name('prompt.txt'))).read_text(),
                'input': [{'role': 'user', 'content': message}],
                'text': {'format': {'type':'json_schema','name':'access_request','strict':True,'schema':schema}},
                'store':False, **options}
        request = urllib.request.Request(self.settings.get('endpoint','https://api.openai.com/v1/responses'),
            data=json.dumps(body).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
        evidence_body = deepcopy(body)
        evidence_body['input'] = [{'role':'user','content':'[REDACTED]','sha256':digest(message.encode('utf-8'))}]
        evidence_body['instructions'] = {'content':'[REDACTED]','sha256':digest(body['instructions'].encode('utf-8'))}
        self._evidence = {'endpoint':request.full_url,'method':'POST','headers':{'Authorization':'Bearer [REDACTED]','Content-Type':'application/json'},'body':evidence_body}
        timeout = self.settings.get('timeout',30)
        try:
            if self.transport:
                return self.transport(request, timeout)
            with open_request(request, timeout=timeout) as response:
                wire=json.load(response)
            self.response_source=f"openai/{validate_model(wire.get('model',body['model']))}"
            return wire
        except Exception as exc:
            from urllib.error import HTTPError, URLError
            if isinstance(exc,HTTPError) and exc.code in [429,500,502,503,504]:
                retry_after=exc.headers.get('Retry-After',0) if exc.headers else 0
                raise TransientError(safe_error(exc),retry_after) from None
            if isinstance(exc,(TimeoutError,URLError)) and not isinstance(exc,HTTPError):
                raise TransientError(safe_error(exc)) from None
            if isinstance(exc,SafeError):
                raise
            raise RuntimeError(safe_error(exc)) from None

    def request_evidence(self):
        return json.loads(self.redact(json.dumps(self._evidence)))


class TransientError(RuntimeError):
    def __init__(self,message,retry_after=0):
        super().__init__(message)
        self.retry_after=retry_after

"""Provider adapters. The mock boundary never reads credentials or uses HTTP."""
from collections import defaultdict, deque
from copy import deepcopy
import json
from pathlib import Path
from threading import Lock


class MockProvider:
    response_source = 'mock'

    def __init__(self, mock_dir, scripts=None):
        self.mock_dir = Path(mock_dir)
        self.scripts = defaultdict(deque, {k: deque(v) for k, v in (scripts or {}).items()})
        self.calls = []
        self._lock = Lock()

    def extract(self, message, document_id):
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
        self.response_source = 'mock' if transport else f"openai/{settings.get('model','gpt-4.1-mini')}"
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
        from .extraction import SCHEMA
        if self.credential is not None:
            token = self.credential()
        else:
            token = os.environ['API_KEY']
        self._secrets.append(token)
        body = {'model': self.settings.get('model','gpt-4.1-mini'),
                'instructions': Path(__file__).with_name('prompt.txt').read_text(),
                'input': [{'role': 'user', 'content': message}],
                'text': {'format': {'type':'json_schema','name':'access_request','strict':True,'schema':SCHEMA}},
                'store':False, **self.settings.get('model_options',{})}
        request = urllib.request.Request(self.settings.get('endpoint','https://api.openai.com/v1/responses'),
            data=json.dumps(body).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
        self._evidence = {'endpoint':request.full_url,'method':'POST','headers':{'Authorization':'Bearer [REDACTED]','Content-Type':'application/json'},'body':body}
        timeout = self.settings.get('timeout',30)
        try:
            if self.transport:
                return self.transport(request, timeout)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:
            from urllib.error import HTTPError, URLError
            if isinstance(exc,HTTPError) and exc.code in [429,500,502,503,504]:
                retry_after=exc.headers.get('Retry-After',0) if exc.headers else 0
                raise TransientError(self.redact(str(exc)),retry_after) from None
            if isinstance(exc,(TimeoutError,URLError)) and not isinstance(exc,HTTPError):
                raise TransientError(self.redact(str(exc))) from None
            raise RuntimeError(self.redact(str(exc))) from None

    def request_evidence(self):
        return json.loads(self.redact(json.dumps(self._evidence)))


class TransientError(RuntimeError):
    def __init__(self,message,retry_after=0):
        super().__init__(message)
        self.retry_after=retry_after

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
        filename = Path(action)
        if filename.name != str(filename):
            raise ValueError('mock reply must be a filename')
        return json.loads((self.mock_dir / filename).read_text(encoding='utf-8'))

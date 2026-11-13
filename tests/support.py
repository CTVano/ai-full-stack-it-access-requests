"""Offline test boundary. Reading API_KEY is a test failure, even if unset."""
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / 'tests' / 'mock_responses'


def expected(name='IT-001.json'):
    return json.loads((ROOT / 'tests/expected/source-facts.json').read_text())[name]


class OfflineCase(unittest.TestCase):
    def setUp(self):
        original = os._Environ.__getitem__
        def guarded(env, key):
            if key == 'API_KEY':
                raise AssertionError('a test tried to read API_KEY')
            return original(env, key)
        for target, kwargs in [
            ('os._Environ.__getitem__', {'new': guarded}),
            ('socket.socket', {'side_effect': AssertionError('network forbidden in tests')}),
            ('socket.create_connection', {'side_effect': AssertionError('network forbidden in tests')}),
        ]:
            guard = patch(target, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)

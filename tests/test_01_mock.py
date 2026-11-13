from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
from access_intake.providers import MockProvider
from support import OfflineCase, MOCKS, expected


class MockContract(OfflineCase):
    def test_T_MOCK(self):
        facts = expected()
        provider = MockProvider(MOCKS)
        wire = provider.extract(facts['source_message'], 'IT-001.json')
        self.assertEqual(wire, json.loads((MOCKS / 'IT-001.json').read_text()))
        self.assertEqual(provider.calls[0]['document_id'], 'IT-001.json')
        wire['output'].clear()
        self.assertTrue(provider.extract(facts['source_message'], 'IT-001.json')['output'])
        entered, release = Event(), Event()
        def delayed():
            entered.set()
            if not release.wait(2):
                raise AssertionError('test barrier timed out')
            return 'IT-001.json'
        scripted = MockProvider(MOCKS, {'IT-001.json': [RuntimeError('unavailable'), delayed]})
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            scripted.extract(facts['source_message'], 'IT-001.json')
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(scripted.extract, facts['source_message'], 'IT-001.json')
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(future.done())
            finally:
                release.set()
            self.assertEqual(future.result()['status'], 'completed')
        self.assertEqual(len(scripted.calls), 2)

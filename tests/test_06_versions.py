import tempfile
from pathlib import Path
from access_intake.engine import Engine
from access_intake.storage import Store
from access_intake.providers import MockProvider
from support import OfflineCase,ROOT,MOCKS


class VersionContract(OfflineCase):
    def setUp(self):
        super().setUp();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite3');self.provider=MockProvider(MOCKS)
        self.engine=Engine(self.store,self.provider)
        self.path=ROOT/'materials/requests/IT-001.json'

    def test_T_RERUN(self):
        one=self.engine.submit(self.path);two=self.engine.submit(self.path)
        self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(one['attempt_id'],two['attempt_id']);self.assertEqual(len(self.store.queue()),1)

    def test_T_CHANGED(self):
        provider=MockProvider(MOCKS,{'IT-001.json':['IT-001.json','IT-001-write.json']})
        engine=Engine(self.store,provider);one=engine.submit(self.path)
        two=engine.submit(ROOT/'tests/fixtures/changed/IT-001.json')
        self.assertIsInstance(two,dict)
        self.assertEqual(two['extraction']['permission'],'write');self.assertEqual(two['owner'],'Security Review')
        self.assertNotEqual(one['source_sha256'],two['source_sha256'])
        self.assertEqual(self.store.history('IT-001.json')[0]['extraction']['permission'],'read')
        self.assertEqual(self.store.history('IT-001.json')[0]['source_sha256'],one['source_sha256'])
        self.assertEqual(len(self.store.queue()),1)

    def test_T_FAILED_RETRY(self):
        provider=MockProvider(MOCKS,{'IT-001.json':['IT-001.json',RuntimeError('HTTP 503 outage')]})
        engine=Engine(self.store,provider);one=engine.submit(self.path)
        self.assertIsInstance(one,dict)
        two=engine.retry(one['attempt_id'])
        self.assertEqual(two['status'],'error');self.assertIsNone(two['extraction'])
        self.assertNotEqual(one['attempt_id'],two['attempt_id']);self.assertEqual(one['source_sha256'],two['source_sha256'])
        self.assertEqual(self.store.queue()[0]['attempt_id'],two['attempt_id'])
        self.assertEqual(self.store.history('IT-001.json')[0]['attempt_id'],one['attempt_id'])
        self.assertEqual(engine.submit(self.path)['attempt_id'],two['attempt_id'])
        self.assertEqual(len(provider.calls),2)

    def test_T_SETTINGS(self):
        original=self.engine.submit(self.path)
        changed=Engine(self.store,self.provider,{'model':'another-model'}).submit(self.path)
        self.assertEqual(len(self.provider.calls),2)
        self.assertEqual(changed['settings']['model'],'another-model')
        refreshed=Engine(self.store,self.provider,{'model':'another-model'},policy_version='review-v2').submit(self.path)
        self.assertEqual(len(self.provider.calls),2)
        self.assertEqual(refreshed['policy_version'],'review-v2')
        self.assertNotEqual(changed['attempt_id'],refreshed['attempt_id'])
        prompt=Path(self.tmp.name)/'prompt.txt';prompt.write_text('Explicit source facts only.')
        engine=Engine(self.store,self.provider,{'prompt_path':str(prompt)})
        engine.submit(self.path);before=len(self.provider.calls);prompt.write_text('Explicit facts, exact supporting quotes.')
        engine.submit(self.path);self.assertEqual(len(self.provider.calls),before+1)
        self.assertEqual(original['extraction']['permission'],'read')

    def test_T_BATCH(self):
        self.engine.submit(self.path);self.engine.submit(ROOT/'materials/requests/IT-002.json')
        self.engine.submit(ROOT/'materials/requests/IT-002.json')
        self.assertEqual(len(self.store.queue()),2)
        self.assertEqual([r['next_action'] for r in self.store.queue()],['review-request','review-risk-then-owner'])

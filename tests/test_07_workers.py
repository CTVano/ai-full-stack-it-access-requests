from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import time
from access_intake.engine import Engine
from access_intake.storage import Store
from access_intake.providers import MockProvider, TransientError
from support import OfflineCase,ROOT,MOCKS


def wait_file(path,seconds=3):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if path.exists():return True
        time.sleep(.01)
    return False


def worker(database,source,reply,entered,release,output,hook=None):
    def gate():
        Path(entered).write_text('entered')
        if not wait_file(Path(release),6):raise RuntimeError('barrier timeout')
        return reply
    provider=MockProvider(MOCKS,{'IT-001.json':[gate if hook is None else reply]})
    hooks={hook:gate} if hook else {}
    try:
        result=Engine(Store(database),provider,{'requests_per_minute':0},hooks=hooks).submit(source)
        Path(output).write_text(json.dumps(result))
    except Exception as exc:Path(output).write_text(json.dumps({'worker_error':str(exc)}))


class WorkerContract(OfflineCase):
    def setUp(self):
        super().setUp();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name);self.store=Store(self.dir/'db.sqlite3');self.ctx=multiprocessing.get_context('fork')

    def start(self,name,source,reply='IT-001.json',hook=None):
        entered=self.dir/(name+'.entered');release=self.dir/(name+'.release');out=self.dir/(name+'.json')
        proc=self.ctx.Process(target=worker,args=(self.store.path,str(source),reply,str(entered),str(release),str(out),hook))
        proc.start()
        def cleanup():
            release.touch();proc.join(3)
            if proc.is_alive():proc.terminate();proc.join(2)
        self.addCleanup(cleanup)
        return proc,entered,release,out

    def test_T_OVERLAP(self):
        path=ROOT/'materials/requests/IT-001.json'
        first,entered,release,out=self.start('first',path)
        self.assertTrue(wait_file(entered))
        second,entered2,release2,out2=self.start('duplicate',path)
        try:
            second.join(.5)
            self.assertFalse(entered2.exists(),'overlapping operators called the provider twice for identical pending work')
            third,entered3,release3,out3=self.start('newer',ROOT/'tests/fixtures/changed/IT-001.json','IT-001-write.json')
            self.assertTrue(wait_file(entered3));release3.touch();third.join(3)
        finally:release.touch();release2.touch()
        first.join(3);second.join(3)
        current=self.store.queue()
        self.assertEqual(len(current),1);self.assertEqual(current[0]['extraction']['permission'],'write')
        self.assertEqual(current[0]['owner'],'Security Review')
        self.assertEqual(len(self.store.history('IT-001.json')),1)

    def test_T_RESUME(self):
        for point in ['before_response','before_commit','after_commit']:
            with self.subTest(point=point):
                preserved=Engine(self.store,MockProvider(MOCKS),{'requests_per_minute':0}).submit(ROOT/'materials/requests/IT-002.json')
                proc,entered,release,out=self.start(point,ROOT/'materials/requests/IT-001.json',hook=point)
                self.assertTrue(wait_file(entered),'worker did not expose the requested interruption boundary')
                proc.terminate();proc.join(2)
                with self.store.connection() as db:db.execute('UPDATE attempts SET lease_until=0 WHERE state=\'leased\'')
                engine=Engine(self.store,MockProvider(MOCKS),{'requests_per_minute':0})
                completed=engine.resume()
                self.assertEqual(self.store.queue()[0]['next_action'],'review-request')
                self.assertEqual(self.store.current('IT-002.json')['attempt_id'],preserved['attempt_id'])
                self.assertEqual(len(engine.provider.calls),0 if point=='after_commit' else 1)
                # Fresh database/source state for the next interruption boundary.
                self.store=Store(self.dir/(point+'.sqlite3'))

    def test_T_ISOLATION(self):
        intake=self.dir/'input';intake.mkdir()
        for i in [1,2,12]:shutil.copy(ROOT/f'materials/requests/IT-{i:03d}.json',intake)
        entered=self.dir/'isolation.entered';release=self.dir/'isolation.release'
        def delayed():
            entered.touch()
            if not wait_file(release,5):raise RuntimeError('barrier timeout')
            return 'IT-001.json'
        provider=MockProvider(MOCKS,{'IT-001.json':[delayed]})
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(Engine(self.store,provider,{'requests_per_minute':0}).intake,intake)
            try:
                self.assertTrue(wait_file(entered))
                deadline=time.monotonic()+1
                while time.monotonic()<deadline and len(self.store.queue())<2:time.sleep(.01)
                ready={r['document_id']:r for r in self.store.queue()}
                self.assertIn('IT-012.json',ready,'slow first message blocked other work')
                self.assertEqual(ready['IT-012.json']['owner'],'Security Review')
                self.assertTrue(self.store.work())
            finally:release.touch()
            future.result(timeout=5)
        clock=[0.0];waits=[]
        def sleep(delay):waits.append(delay);clock[0]+=delay
        provider=MockProvider(MOCKS,{'IT-001.json':[TransientError('429 limited',2),TransientError('503 unavailable',1),'IT-001.json']})
        engine=Engine(Store(self.dir/'retry.sqlite3'),provider,{'requests_per_minute':120},clock=lambda:clock[0],sleep=sleep)
        result=engine.submit(ROOT/'materials/requests/IT-001.json')
        self.assertEqual(result['status'],'review');self.assertEqual(len(provider.calls),3)
        self.assertGreaterEqual(sum(waits),3)
        provider=MockProvider(MOCKS,{'IT-001.json':[TransientError('503 unavailable')]*3})
        result=Engine(Store(self.dir/'exhaust.sqlite3'),provider,{'requests_per_minute':0},clock=lambda:clock[0],sleep=sleep).submit(ROOT/'materials/requests/IT-001.json')
        self.assertEqual(result['status'],'error');self.assertEqual(len(provider.calls),3)

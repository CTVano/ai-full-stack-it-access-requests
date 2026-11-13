import json
from pathlib import Path
import tempfile
from access_intake.engine import Engine
from access_intake.storage import Store
from access_intake.providers import MockProvider
from access_intake.exports import publish,resolve_generation
from support import OfflineCase,ROOT,MOCKS
from test_08_cli import cli


class ExportContract(OfflineCase):
    def test_T_EXPORT(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);store=Store(tmp/'db.sqlite3');out=tmp/'deliverables'
            provider=MockProvider(MOCKS,{'IT-002.json':[RuntimeError('503 unavailable')]})
            engine=Engine(store,provider,{'requests_per_minute':0})
            for i in [1,10,2]:engine.submit(ROOT/f'materials/requests/IT-{i:03d}.json')
            publish(store,out)
            self.assertTrue((out/'extractions.jsonl').is_file())
            snapshot=resolve_generation(out)
            records=[json.loads(line) for line in (snapshot/'extractions.jsonl').read_text().splitlines()]
            queue=json.loads((snapshot/'queue.json').read_text())
            self.assertEqual(len(records),3);self.assertEqual(len(queue['entries']),3)
            for entry in queue['entries']:
                record=next(r for r in records if r['document_id']==entry['document_id'])
                for field in ['attempt_id','source_sha256','status','owner','next_action']:
                    self.assertEqual(entry[field],record[field])
                for key in ['document_id','source_sha256','extraction','status','owner','next_action','error','response_source']:self.assertIn(key,record)
                self.assertEqual(record['response_source'],'mock')
                if record['status']=='error':self.assertIsNone(record['extraction'])
                else:self.assertIsNone(record['error'])
            self.assertEqual([r['status'] for r in records],['review','error','needs-information'])
            before=(out/'current').resolve()
            def crash():raise RuntimeError('crash before publication')
            with self.assertRaisesRegex(RuntimeError,'publication'):publish(store,out,hook=crash)
            self.assertEqual(resolve_generation(out),before)
            changed=Engine(store,MockProvider(MOCKS,{'IT-001.json':['IT-001-write.json']}),{'requests_per_minute':0})
            changed.submit(ROOT/'tests/fixtures/changed/IT-001.json')
            publish(store,out)
            self.assertNotEqual(resolve_generation(out),snapshot)
            # A reader holding the old generation still sees a coherent, immutable pair.
            self.assertEqual(json.loads((snapshot/'queue.json').read_text())['generation'],queue['generation'])
            old=json.loads((snapshot/'extractions.jsonl').read_text().splitlines()[0])
            self.assertEqual(old['extraction']['permission'],'read')
            fresh=json.loads((out/'extractions.jsonl').read_text().splitlines()[0])
            self.assertEqual(fresh['extraction']['permission'],'write')
            self.assertNotIn('history',fresh)
            published=cli('export','--db',store.path,'--out',out)
            self.assertEqual(published.returncode,0,published.stderr)

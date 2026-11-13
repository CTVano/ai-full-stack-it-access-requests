import tempfile
from pathlib import Path
from access_intake.storage import Store
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.providers import MockProvider
from support import OfflineCase,ROOT,MOCKS,expected


class StorageContract(OfflineCase):
    def test_T_STORE(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=Path(tmp)/'intake.sqlite3';store=Store(database)
            doc=read_export(ROOT/'materials/requests/IT-001.json')
            saved=store.save(doc,process(doc,MockProvider(MOCKS)),{'model':'mock-model'})
            self.assertIsInstance(saved,dict)
            self.assertEqual(saved['extraction'],expected()['extraction'])
            self.assertEqual(saved['source_sha256'],doc['source_sha256'])
            self.assertTrue(saved['attempt_id']);self.assertEqual(saved['settings']['model'],'mock-model')
            second=read_export(ROOT/'materials/requests/IT-002.json')
            fail=process(second,MockProvider(MOCKS,{'IT-002.json':[RuntimeError('503 unavailable')]}))
            store.save(second,fail)
            current=Store(database).queue()
            self.assertEqual(len(current),2)
            self.assertEqual(current[0]['attempt_id'],saved['attempt_id'])
            self.assertEqual(current[1]['status'],'error');self.assertIsNone(current[1]['extraction'])
            before=store.queue()
            def crash():raise RuntimeError('crash before commit')
            with self.assertRaisesRegex(RuntimeError,'before commit'):
                store.save(doc,process(doc,MockProvider(MOCKS)),hook=crash)
            self.assertEqual(Store(database).queue(),before)
            self.assertEqual(store.history('IT-001.json'),[])

import hashlib
import json
from pathlib import Path
import tempfile
from access_intake.inputs import read_export, discover
from support import OfflineCase, ROOT, expected


class InputContract(OfflineCase):
    def test_T_INPUT(self):
        source = ROOT / 'materials/requests/IT-007.json'
        doc = read_export(source)
        self.assertEqual(doc.get('message'), expected('IT-007.json')['source_message'])
        self.assertEqual(doc['document_id'], 'IT-007.json')
        self.assertEqual(doc['raw'], source.read_bytes())
        self.assertEqual(doc['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        original = read_export(ROOT / 'materials/requests/IT-001.json')
        for variant in ['metadata-only','byte-only']:
            other = read_export(ROOT / f'tests/fixtures/{variant}/IT-001.json')
            self.assertEqual(other['message'], original['message'])
            self.assertNotEqual(other['source_sha256'], original['source_sha256'])
        docs = discover(ROOT / 'tests/fixtures/same-id')
        self.assertEqual([d.name for d in docs], ['DOC-A.json', 'DOC-B.json'])
        self.assertEqual([read_export(d)['document_id'] for d in docs], ['DOC-A.json','DOC-B.json'])
        boundary = read_export(ROOT / 'tests/fixtures/requests/IT-200-WORDS.json')
        self.assertEqual(len(boundary['message'].split()), 200)
        mismatch = read_export(ROOT / 'materials/requests/IT-016.json')
        self.assertEqual(mismatch['document_id'], 'IT-016.json')
        self.assertIn('IT-061', mismatch['message'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'invalid.json'
            for data in [b'{', b'\xff', b'{"message":123}']:
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, 'invalid.json'):
                    read_export(path)
            (Path(tmp)/'nested').mkdir()
            with self.assertRaisesRegex(ValueError, 'flat'):
                discover(tmp)

import importlib.metadata
import hashlib
import json
from pathlib import Path
import re
import tempfile
from support import OfflineCase,ROOT,MOCKS,expected
from test_08_cli import cli
from access_intake.engine import DEFAULTS
from jsonschema import Draft202012Validator


class DocumentationContract(OfflineCase):
    def test_T_DOCS(self):
        readme=(ROOT/'README.md').read_text()
        self.assertIn('--require-hashes',readme)
        for word in ['intake','queue','history','resume','retry','export','accuracy','API_KEY','MAX_WORKERS','unittest','mock']:
            self.assertIn(word,readme)
        lock=(ROOT/'requirements.lock').read_text()
        for name,version in re.findall(r'^([a-zA-Z0-9_-]+)==([^\s]+)',lock,re.M):
            self.assertEqual(importlib.metadata.version(name),version)
        for name in ['extractions.jsonl','queue.json','accuracy.md','request-evidence.json','verification.md']:
            self.assertTrue((ROOT/'deliverables'/name).is_file(),name)
        records=[json.loads(line) for line in (ROOT/'deliverables/extractions.jsonl').read_text().splitlines()]
        self.assertEqual(len(records),20);self.assertNotIn('IT-021.json',[r['document_id'] for r in records])
        queue=json.loads((ROOT/'deliverables/queue.json').read_text())
        self.assertIn('does not approve access',queue['approval_boundary'])
        validator=Draft202012Validator(json.loads((ROOT/'snapshot.schema.json').read_text()))
        for record in records:
            e=expected(record['document_id'])
            self.assertEqual((record['status'],record['owner'],record['next_action']),(e['status'],e['owner'],e['next_action']))
            linked=next(q for q in queue['entries'] if q['document_id']==record['document_id'])
            self.assertEqual(linked['attempt_id'],record['attempt_id']);self.assertEqual(linked['source_sha256'],record['source_sha256'])
            self.assertEqual(record['settings']['model'],DEFAULTS['model'])
            self.assertEqual(record['source_sha256'],hashlib.sha256((ROOT/'materials/requests'/record['document_id']).read_bytes()).hexdigest())
            validator.validate(record['extraction'])
            for item in [record,linked]:
                self.assertEqual(item['intake_status'],'completed')
                self.assertEqual(item['access_decision'],'outstanding')
                self.assertEqual(item['decision_owner'],'System Owner')
                self.assertIs(item['access_granted'],False)
        evidence=json.loads((ROOT/'deliverables/request-evidence.json').read_text())
        self.assertEqual(evidence['request']['body']['model'],evidence['model_id'])
        self.assertEqual(evidence['model_id'],DEFAULTS['model'])
        output_format=evidence['request']['body']['text']['format']
        self.assertIs(output_format['strict'],True)
        self.assertEqual(output_format['schema'],json.loads((ROOT/'snapshot.schema.json').read_text()))
        self.assertEqual(evidence['request']['body']['input'][0]['content'],'[REDACTED]')
        self.assertEqual(evidence['request']['body']['input'][0]['sha256'],hashlib.sha256(expected()['source_message'].encode()).hexdigest())
        self.assertEqual(evidence['request']['body']['instructions']['content'],'[REDACTED]')
        self.assertIn('[REDACTED]',evidence['request']['headers']['Authorization'])
        self.assertNotIn('documentation-dummy-token',json.dumps(evidence))
        for branch in ['completed','schema','refusal','incomplete','error']:
            self.assertIn(branch,json.dumps(evidence['response_handling']))
        accuracy=(ROOT/'deliverables/accuracy.md').read_text()
        self.assertIn('"model": "'+DEFAULTS['model']+'"',accuracy)
        self.assertIn('252/252',accuracy)
        verification=(ROOT/'deliverables/verification.md').read_text()
        for text in ['C-CAPTURE','C-COMMIT','C-PUSH','C-SUBMIT','2,000','SQLite','Observed','Limits','API_KEY']:
            self.assertIn(text,verification)
        commands=json.loads((ROOT/'docs/commands.json').read_text())
        self.assertEqual(set(commands),{'intake','queue','history','resume','retry','export','accuracy'})
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);values={'inputs':str(ROOT/'materials/requests'),'db':str(tmp/'state.sqlite3'),'mocks':str(MOCKS),
                'synthetic':str(ROOT/'tests/fixtures/requests/IT-021.json'),'expected':str(ROOT/'tests/expected/source-facts.json'),
                'out':str(tmp/'deliverables'),'accuracy_report':str(tmp/'accuracy.md'),'attempt_id':''}
            for name in ['intake','queue','history','resume','retry','export','accuracy']:
                result=cli(*[word.format(**values) for word in commands[name]])
                self.assertEqual(result.returncode,0,(name,result.stderr))
                payload=json.loads(result.stdout)
                if name=='intake':
                    self.assertEqual(len(payload['results']),20);values['attempt_id']=payload['results'][0]['attempt_id']
                if name=='queue':self.assertEqual(len(payload['results']),20)
                if name=='accuracy':self.assertEqual(payload['denominator'],252)
            self.assertTrue((tmp/'accuracy.md').exists())
            self.assertEqual(len((tmp/'deliverables/extractions.jsonl').read_text().splitlines()),20)

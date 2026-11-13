from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from access_intake.accuracy import compare,run_accuracy
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.providers import MockProvider
from support import OfflineCase,ROOT,MOCKS
from test_08_cli import cli


class AccuracyContract(OfflineCase):
    def test_T_ACCURACY(self):
        expected_path=ROOT/'tests/expected/source-facts.json';raw=expected_path.read_bytes()
        all_expected=json.loads(raw)
        oracles={f'IT-{i:03d}.json':all_expected[f'IT-{i:03d}.json'] for i in range(1,22)}
        candidates=[]
        for name,e in oracles.items():
            doc=read_export(ROOT/e.get('input_path',f'materials/requests/{name}'))
            candidates.append(process(doc,MockProvider(MOCKS)))
        damaged=deepcopy(candidates);damaged[0]['extraction']['permission']='write'
        result=compare(damaged,oracles)
        self.assertEqual(result.get('matched'),251)
        self.assertEqual(result['denominator'],252)
        self.assertEqual(result['mismatches'][0]['field'],'permission')
        self.assertEqual(result['mismatches'][0]['expected'],'read')
        self.assertEqual(result['mismatches'][0]['actual'],'write')
        failed=deepcopy(candidates);failed[0]['extraction']=None
        self.assertEqual(compare(failed,oracles)['matched'],240)
        self.assertEqual(expected_path.read_bytes(),raw)
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp)/'accuracy.md'
            summary=run_accuracy(ROOT/'materials/requests',ROOT/'tests/fixtures/requests/IT-021.json',expected_path,MockProvider(MOCKS),report)
            self.assertEqual(summary['matched'],252);self.assertEqual(summary['denominator'],252)
            text=report.read_text();self.assertIn('fixture consistency',text);self.assertIn('mock',text)
            for name,e in oracles.items():
                self.assertIn(name,text);self.assertIn(e['source_message'].replace('\n','<br>'),text)
            command=cli('accuracy','--inputs',ROOT/'materials/requests','--synthetic',ROOT/'tests/fixtures/requests/IT-021.json','--expected',expected_path,'--provider','mock','--mock-dir',MOCKS,'--out',report)
            self.assertEqual(command.returncode,0,command.stderr)
            self.assertEqual(json.loads(command.stdout)['denominator'],252)

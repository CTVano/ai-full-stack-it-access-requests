import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from support import OfflineCase,ROOT,MOCKS
from test_07_workers import wait_file


def cli(*args):
    return subprocess.run([sys.executable,'-m','access_intake',*map(str,args)],cwd=ROOT,
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(ROOT/'tests/offline_site')+':'+str(ROOT)},text=True,capture_output=True,timeout=15)


class CLIContract(OfflineCase):
    def test_T_CLI(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);inputs=tmp/'inputs';inputs.mkdir();database=tmp/'db.sqlite3'
            for i in [1,2]:shutil.copy(ROOT/f'materials/requests/IT-{i:03d}.json',inputs)
            scenario=tmp/'scenario.json';scenario.write_text(json.dumps({'IT-002.json':[{'error':'HTTP 503 mock outage'}]}))
            run=cli('intake',inputs,'--db',database,'--provider','mock','--mock-dir',MOCKS,'--scenario',scenario,'--requests-per-minute',0)
            self.assertTrue(run.stdout,run.stderr)
            data=json.loads(run.stdout)
            self.assertIn('results',data)
            self.assertEqual(run.returncode,1,run.stderr)
            queue=json.loads(cli('queue','--db',database).stdout)
            self.assertEqual([r['status'] for r in queue['results']],['review','error'])
            failed=queue['results'][1];self.assertIn(failed['attempt_id'],failed['retry_command'])
            retry=cli('retry','--attempt',failed['attempt_id'],'--db',database,'--provider','mock','--mock-dir',MOCKS)
            self.assertEqual(retry.returncode,0,retry.stderr)
            self.assertEqual(json.loads(retry.stdout)['results'][0]['owner'],'Security Review')
            history=json.loads(cli('history','IT-002.json','--db',database).stdout)
            self.assertEqual(history['results'][0]['status'],'error')
            self.assertEqual(cli('resume','--db',database,'--provider','mock','--mock-dir',MOCKS).returncode,0)
            helptext=cli('--help').stdout
            self.assertIn('recommendation',helptext);self.assertIn('human',helptext)
            entered=tmp/'entered';release=tmp/'release'
            scenario.write_text(json.dumps({'IT-001.json':[{'reply':'IT-001.json','entered':str(entered),'release':str(release)}]}))
            second_db=tmp/'overlap.sqlite3'
            proc=subprocess.Popen([sys.executable,'-m','access_intake','intake',str(inputs),'--db',str(second_db),'--mock-dir',str(MOCKS),'--scenario',str(scenario),'--requests-per-minute','0'],cwd=ROOT,env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(ROOT/'tests/offline_site')+':'+str(ROOT)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertTrue(wait_file(entered))
                queue=json.loads(cli('queue','--db',second_db).stdout)
                self.assertEqual(queue['results'][0]['document_id'],'IT-002.json')
                self.assertTrue(queue['work'])
            finally:
                release.touch();stdout,stderr=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,0,stderr)

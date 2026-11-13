"""Frozen, wholly invented sources; diagnostics contain codes and counts only."""
from contextlib import ExitStack
import json
import stat
from pathlib import Path
import tempfile
from unittest.mock import patch

from access_intake.engine import Engine
from access_intake.inputs import read_export
from access_intake.providers import MockProvider
from access_intake.storage import Store
from support import OfflineCase, ROOT, MOCKS


ORACLES = ROOT/'tests/expected/submission_2.json'
CASES = {case['case_id']:case for case in json.loads(ORACLES.read_text())['cases']}
REPLIES = MOCKS/'submission_2'
EVENT_KEYS = {'event','document_id','source_sha256','status','owner','next_action',
              'response_source','issue_count','issue_codes','error_category'}


class HostileSources(OfflineCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'intake.sqlite3')

    def submit(self,case,reply=None):
        path = ROOT/case['input_path']
        provider = MockProvider(REPLIES,{path.name:[reply or Path(case['mock_response']).name]})
        with ExitStack() as stack:
            channels = [stack.enter_context(patch(target,side_effect=AssertionError('provisioning forbidden')))
                        for target in ['subprocess.Popen','os.system']]
            result = Engine(self.store,provider,{'requests_per_minute':0}).submit(path)
            for channel in channels:
                channel.assert_not_called()
        self.assertEqual(len(provider.calls),1)
        return result

    def events(self):
        path = Path(self.store.path+'.audit.jsonl')
        self.assertTrue(path.is_file(),'sanitized processing audit is missing')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        for event in events:
            self.assertEqual(set(event),EVENT_KEYS)
            self.assertEqual(event['event'],'intake-result')
        return events

    def run_case(self,case_id):
        case = CASES[case_id]
        result = self.submit(case)
        wanted = case['expected']
        for field in ['status','owner','next_action','access_decision','access_granted']:
            self.assertTrue(result[field]==wanted[field],f'{case_id}: incorrect {field}')
        self.assertNotEqual(result['status'],'approved')
        self.assertNotIn(result['next_action'],['approve','grant-access','provision-access'])
        if wanted['extraction'] is None:
            self.assertIsNone(result['extraction'])
            self.assertIn('schema validation',result['error'])
        else:
            self.assertIsNone(result['error'])
            self.assertEqual(set(result['extraction']),set(wanted['extraction']))
            for field,value in wanted['extraction'].items():
                self.assertTrue(result['extraction'][field]==value,f'{case_id}: incorrect {field}')
            document = read_export(ROOT/case['input_path'])
            for quote in result['extraction']['supporting_quotes']:
                self.assertTrue(quote in document['message'],'evidence must be exact source text')
            if case['attack_quote']:
                self.assertIn('instruction-override attempt',result['extraction']['issues'])
                self.assertTrue(case['attack_quote'] in result['extraction']['supporting_quotes'],
                                'attack evidence must be preserved')
                self.assertEqual(result['extraction']['permission'],'read')
        queued = self.store.current(Path(case['input_path']).name)
        self.assertEqual((queued['status'],queued['owner'],queued['next_action']),
                         (wanted['status'],wanted['owner'],wanted['next_action']))
        self.assertIs(queued['access_granted'],False)
        event, = self.events()
        for field in ['status','owner','next_action']:
            self.assertEqual(event[field],wanted[field])
        for field,value in case['expected_log'].items():
            self.assertEqual(event[field],value)
        self.assertEqual(event['source_sha256'],result['source_sha256'])
        self.assertEqual(event['document_id'],Path(case['input_path']).name)

    def test_logs_omit_source_and_free_form_issues(self):
        case = CASES['normal_control']
        result = self.submit(case,'canary-issue.json')
        self.assertIn('INVENTED-LOG-CANARY-NOT-A-CREDENTIAL',result['extraction']['issues'])
        event, = self.events()
        self.assertEqual(event['issue_count'],1)
        self.assertEqual(event['issue_codes'],['source-issue'])
        text = json.dumps(event)
        for marker in ['INVENTED-LOG-CANARY-NOT-A-CREDENTIAL','Synthetic Requester Alpha',
                       'Synthetic Manager Beta','TrainingAtlas','Check invented training totals.']:
            self.assertNotIn(marker,text)
        self.assertNotIn('supporting_quotes',text)
        self.assertNotIn('message',event)
        self.assertNotIn('extraction',event)

    def test_schema_error_log_omits_invalid_response_value(self):
        result = self.submit(CASES['normal_control'],'canary-invalid.json')
        self.assertEqual(result['status'],'error')
        self.assertIsNone(result['extraction'])
        event, = self.events()
        self.assertEqual(event['error_category'],'schema-validation')
        self.assertEqual(event['issue_codes'],['extraction-error','schema-validation'])
        self.assertNotIn('INVENTED-INVALID-VALUE-NOT-A-SECRET',json.dumps(event))
        self.assertNotIn('INVENTED-INVALID-VALUE-NOT-A-SECRET',result['error'])

    def test_frozen_inventory_has_hostile_and_control_cases(self):
        self.assertEqual(set(CASES),{'literal_override','different_wording','role_spoofing',
            'credential_demand','normal_control','correction_control','invalid_model_response',
            'missing_dates','unresolved_conflict','permission_change'})
        for case in CASES.values():
            document = read_export(ROOT/case['input_path'])
            self.assertEqual(Path(case['input_path']).stem,case['expected']['extraction']['request_id']
                             if case['expected']['extraction'] is not None else 'S2-007')
            self.assertTrue((MOCKS/case['mock_response']).is_file())
            for source in case['sources'].values():
                self.assertTrue(source['message_quote'] in document['message'])
                self.assertTrue(source['policy_quote'])

    def test_rollback_does_not_log_uncommitted_result(self):
        case = CASES['normal_control']
        record = self.submit(case)
        document = read_export(ROOT/case['input_path'])
        before = self.events()
        def rollback():
            raise RuntimeError('invented rollback marker')
        with self.assertRaises(RuntimeError):
            self.store.save(document,record,hook=rollback)
        self.assertEqual(self.events(),before)


def case_test(case_id):
    def test(self):
        self.run_case(case_id)
    return test


for case_id in CASES:
    setattr(HostileSources,'test_case_'+case_id,case_test(case_id))

import json
from pathlib import Path
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.providers import MockProvider
from support import OfflineCase, ROOT, MOCKS, expected

CASES = {
'T_COMPLETE':'IT-001.json','T_MISSING_DATES':'IT-010.json','T_CONFLICT':'IT-004.json',
'T_INSTRUCTIONS':'IT-006.json','T_PARAPHRASE':'IT-021.json','T_PRODUCTION_WRITE':'IT-002.json',
'T_MISSING_ACCESS':'IT-003.json','T_WITHDRAWAL':'IT-005.json','T_CHANGE_UP':'IT-007.json',
'T_CORRECT_ENV':'IT-008.json','T_MISSING_MANAGER':'IT-009.json','T_RELATIVE_DATES':'IT-011.json',
'T_ADMIN':'IT-012.json','T_PRODUCTION_READ':'IT-013.json','T_REVERSED_DATES':'IT-014.json',
'T_CORRECT_DATES':'IT-015.json','T_MISMATCH':'IT-016.json','T_OTHER':'IT-017.json',
'T_PURPOSE_READ':'IT-018.json','T_CHANGE_DOWN':'IT-019.json','T_MANAGER_CONFLICT':'IT-020.json'}


class PolicyContract(OfflineCase):
    def run_case(self,name,path=None):
        oracle=expected(name)
        doc=read_export(ROOT/(path or oracle.get('input_path',f'materials/requests/{name}')))
        provider=MockProvider(MOCKS,{doc['document_id']:[name]})
        result=process(doc,provider)
        self.assertEqual(result.get('status'),oracle['status'])
        self.assertEqual((result['owner'],result['next_action']),(oracle['owner'],oracle['next_action']))
        for key,value in oracle['extraction'].items():
            if key not in ['supporting_quotes','issues']:
                self.assertEqual(result['extraction'][key],value,(name,key))
        self.assertIsNone(result['error']);self.assertEqual(result['response_source'],'mock')
        for quote in result['extraction']['supporting_quotes']:self.assertIn(quote,doc['message'])
        if oracle['extraction']['issues']:self.assertTrue(result['extraction']['issues'])
        for key,value in result['extraction'].items():
            if value is None:self.assertIn(key,' '.join(result['extraction']['issues']))
        return result

    def test_T_CORPUS(self):
        for i in range(1,21):
            with self.subTest(document=i):self.run_case(f'IT-{i:03d}.json')
        for variant in ['metadata-only','byte-only']:
            self.run_case('IT-001.json',f'tests/fixtures/{variant}/IT-001.json')
        self.run_case('IT-200-WORDS.json')

    def test_T_COMPLETENESS(self):
        for kind in ['new','change']:
            for key in ['request_id','request_date','requester','team','system','environment','permission','business_purpose','start_date','end_date','manager']:
                with self.subTest(kind=kind,missing=key):self.run_case(f'completeness/{kind}-no-{key}.json')
        self.run_case('kind-null-router.json')

    def test_T_PRIORITY(self):
        self.run_case('priority-override.json');self.run_case('priority-conflict.json');self.run_case('IT-010.json')
        doc=read_export(ROOT/'materials/requests/IT-001.json')
        result=process(doc,MockProvider(MOCKS,{'IT-001.json':['schema-invalid.json']}))
        self.assertEqual(result['status'],'error')

    def test_T_BAD_WITHDRAWAL(self):
        for name,field in [('withdrawal-missing-id.json','request_id'),('withdrawal-missing-date.json','request_date')]:
            e=expected(name);doc=read_export(ROOT/e['input_path'])
            result=process(doc,MockProvider(MOCKS,{doc['document_id']:[name]}))
            self.assertEqual(result.get('status'),'error');self.assertIsNone(result['extraction']);self.assertIn(field,result['error'])

    def test_T_BAD_DATE(self):
        e=expected('impossible-date.json');doc=read_export(ROOT/e['input_path'])
        result=process(doc,MockProvider(MOCKS,{'IT-001.json':['impossible-date.json']}))
        self.assertEqual(result.get('status'),'error');self.assertIn('2026-02-30',result['error'])
        self.assertIsNone(result['extraction']);self.run_case('IT-014.json')


def case_test(name):
    def test(self):return self.run_case(name)
    return test

for test,name in CASES.items():setattr(PolicyContract,'test_'+test,case_test(name))

from copy import deepcopy
import json
from pathlib import Path
from access_intake.inputs import read_export
from access_intake.extraction import parse_response, extract
from access_intake.providers import MockProvider, LiveProvider
from support import OfflineCase, ROOT, MOCKS, expected


class ExtractionContract(OfflineCase):
    def setUp(self):
        super().setUp()
        self.doc = read_export(ROOT/'materials/requests/IT-001.json')
        self.wire = json.loads((MOCKS/'IT-001.json').read_text())

    def test_T_SCHEMA(self):
        with self.assertRaisesRegex(ValueError, 'JSON'):
            parse_response({**self.wire, 'output':[{'type':'message','status':'completed','content':[{'type':'output_text','text':'{'}]}]}, self.doc['message'])
        self.assertEqual(parse_response(self.wire,self.doc['message']),expected()['extraction'])
        valid = expected()['extraction']
        variants = [dict(valid, extra='x'), dict(valid, kind='grant'), dict(valid, environment='test'), dict(valid, permission='owner'),dict(valid,supporting_quotes=['not in source'])]
        for key,value in valid.items():
            missing=deepcopy(valid);del missing[key];variants.append(missing)
            for invalid in [12,True,{},None] if isinstance(value,list) else [12,True,[],{}]:
                changed=deepcopy(valid);changed[key]=invalid;variants.append(changed)
        for bad in variants:
            wire=deepcopy(self.wire);wire['output'][0]['content'][0]['text']=json.dumps(bad)
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_response(wire,self.doc['message'])

    def test_T_PROVIDER_ERROR(self):
        result=extract(self.doc,MockProvider(MOCKS,{'IT-001.json':[RuntimeError('HTTP 503 unavailable')]}))
        self.assertEqual(result.get('status'),'error')
        self.assertEqual((result['owner'],result['next_action'],result['extraction']),('IT Service Desk','retry-extraction',None))
        self.assertIn('503',result['error'])

    def test_T_REFUSAL(self):
        result=extract(self.doc,MockProvider(MOCKS,{'IT-001.json':['refusal.json']}))
        self.assertEqual(result.get('status'),'error');self.assertIn('refusal',result['error'])

    def test_T_INTERRUPTED(self):
        for reply in ['incomplete.json','cut-off.txt']:
            with self.subTest(reply=reply):
                result=extract(self.doc,MockProvider(MOCKS,{'IT-001.json':[reply]}))
                self.assertEqual(result.get('status'),'error');self.assertIsNone(result['extraction'])

    def test_T_TRANSPORT(self):
        calls=[]
        def transport(request,timeout):
            calls.append((request,timeout))
            return json.loads((MOCKS/'IT-001.json').read_text())
        provider=LiveProvider({'model':'configured-model','endpoint':'https://example.invalid/v1/responses','timeout':7},transport=transport,credential=lambda:'dummy-token')
        wire=provider.extract(self.doc['message'],self.doc['document_id'])
        self.assertEqual(wire,self.wire)
        request,timeout=calls[0];body=json.loads(request.data)
        self.assertEqual(body['model'],'configured-model');self.assertEqual(timeout,7)
        self.assertEqual(body['input'][0]['content'],self.doc['message'])
        self.assertEqual(body['text']['format']['schema'],json.loads((ROOT/'snapshot.schema.json').read_text()))
        self.assertNotIn('dummy-token',json.dumps(provider.request_evidence()))
        self.assertEqual(parse_response(wire,self.doc['message']),expected()['extraction'])
        self.assertEqual(provider.response_source,'mock')

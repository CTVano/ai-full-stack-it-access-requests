from copy import deepcopy
import hashlib
import json
import tempfile
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
        from access_intake.engine import effective
        from access_intake.__main__ import parser
        from unittest.mock import patch
        with patch('access_intake.__main__.os.getenv',side_effect=lambda name,default=None:default):
            self.assertEqual(parser().parse_args(['intake','materials/requests']).model,'gpt-4.1')
        self.assertEqual(effective()['model'],'gpt-4.1')
        default_provider=LiveProvider({},transport=transport,credential=lambda:'dummy-token')
        default_provider.extract(self.doc['message'],self.doc['document_id'])
        self.assertEqual(json.loads(calls.pop()[0].data)['model'],'gpt-4.1')
        self.assertEqual(LiveProvider({}).response_source,'openai/gpt-4.1')
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

        from urllib.error import HTTPError
        def unavailable(request,timeout):
            raise HTTPError(request.full_url,503,'outage dummy-token',{'Retry-After':'2'},None)
        broken=LiveProvider({'model':'configured-model'},transport=unavailable,credential=lambda:'dummy-token')
        failed=extract(self.doc,broken)
        self.assertTrue(failed.get('_retryable'),'HTTP retry classification was lost at the adapter boundary')
        self.assertEqual(failed['_retry_after'],2)
        self.assertNotIn('dummy-token',failed['error'])

        from io import BytesIO
        from unittest.mock import patch
        # Fake the HTTP response itself to check real-adapter provenance without network or API_KEY reads.
        with tempfile.TemporaryDirectory() as tmp:
            approval=Path(tmp)/'approval.json'
            approval.write_text(json.dumps({'endpoint':'https://api.openai.com/v1/responses',
                'model':'requested-alias','purpose':'synthetic-training-only',
                'prompt_sha256':hashlib.sha256((ROOT/'access_intake/prompt.txt').read_bytes()).hexdigest(),
                'approval_reference':'OFFLINE-TEST-ONLY-NOT-AUTHORIZATION','valid_until':'2099-01-01'}))
            with patch('access_intake.providers.open_request',return_value=BytesIO(json.dumps(self.wire).encode())):
                producer=LiveProvider({'model':'requested-alias','approval_path':str(approval)},credential=lambda:'dummy-token')
                produced=extract(self.doc,producer)
        self.assertEqual(produced['response_source'],'openai/'+self.wire['model'])

        with tempfile.TemporaryDirectory() as tmp:
            prompt=Path(tmp)/'prompt.txt';prompt.write_text('Versioned instructions from a saved configuration.')
            schema=Path(tmp)/'schema.json';contract=json.loads((ROOT/'snapshot.schema.json').read_text())
            contract['properties']['permission']={'enum':['write',None]};schema.write_text(json.dumps(contract))
            configured=LiveProvider({'model':'configured-model','prompt_path':str(prompt),'schema_path':str(schema)},transport=transport,credential=lambda:'dummy-token')
            configured.extract(self.doc['message'],self.doc['document_id'])
            self.assertEqual(json.loads(calls[-1][0].data)['instructions'],prompt.read_text())
            rejected=extract(self.doc,configured)
            self.assertEqual(rejected['status'],'error');self.assertIn('schema',rejected['error'])

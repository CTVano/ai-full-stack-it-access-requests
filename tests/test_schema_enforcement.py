import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from access_intake.engine import Engine, effective, fingerprints
from access_intake.extraction import parse_response
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.providers import LiveProvider, MockProvider
from access_intake.storage import Store
from support import OfflineCase, ROOT, MOCKS, expected


class SchemaEnforcement(OfflineCase):
    def setUp(self):
        super().setUp()
        self.document = read_export(ROOT/'materials/requests/IT-001.json')
        self.wire = json.loads((MOCKS/'IT-001.json').read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.custom = Path(self.tmp.name)/'schema.json'

    def test_custom_schema_cannot_relax_required_contract(self):
        for custom in [{}, True]:
            self.custom.write_text(json.dumps(custom))
            for name in ['missing-field','extra-field','invalid-enum','invalid-scalar',
                         'invalid-array-item','null-array','wrong-root']:
                with self.subTest(custom=custom, reply=name):
                    provider = MockProvider(MOCKS, {'IT-001.json':[f'schema-enforcement/{name}.json']})
                    provider.settings = {'schema_path':str(self.custom)}
                    result = process(self.document, provider)
                    self.assertEqual(result['status'], 'error')
                    self.assertIsNone(result['extraction'])
                    self.assertEqual((result['owner'],result['next_action']),
                                     ('IT Service Desk','retry-extraction'))
                    self.assertIn('schema validation',result['error'])

    def test_custom_schema_is_an_additional_constraint(self):
        self.custom.write_text(json.dumps({'properties':{'permission':{'const':'read'}}}))
        self.assertEqual(parse_response(self.wire,self.document['message'],self.custom),
                         expected()['extraction'])
        self.custom.write_text(json.dumps({'properties':{'permission':{'const':'write'}}}))
        with self.assertRaisesRegex(ValueError,'schema validation'):
            parse_response(self.wire,self.document['message'],self.custom)
        self.custom.write_text(json.dumps({'type':'not-a-json-schema-type'}))
        with self.assertRaisesRegex(ValueError,'invalid schema'):
            parse_response(self.wire,self.document['message'],self.custom)

    def test_request_always_uses_canonical_strict_schema(self):
        self.custom.write_text('{}')
        provider = LiveProvider({'schema_path':str(self.custom)},
            transport=lambda request,timeout:self.wire,credential=lambda:'dummy-token')
        result = process(self.document,provider)
        self.assertEqual(result['extraction'],expected()['extraction'])
        output_format = provider.request_evidence()['body']['text']['format']
        self.assertEqual(output_format['type'],'json_schema')
        self.assertIs(output_format['strict'],True)
        self.assertEqual(output_format['schema'],json.loads((ROOT/'snapshot.schema.json').read_text()))
        for key,value in [('text',{'format':{'type':'text'}}),('input',[]),('instructions','skip validation')]:
            with self.subTest(option=key):
                credential = Mock(return_value='dummy-token')
                transport = Mock(return_value=self.wire)
                overridden = LiveProvider({'model_options':{key:value}},transport=transport,credential=credential)
                result = process(self.document,overridden)
                self.assertEqual(result['status'],'error')
                self.assertIsNone(result['extraction'])
                self.assertIn('model_options',result['error'])
                credential.assert_not_called()
                transport.assert_not_called()

    def test_rejects_ambiguous_json_and_malformed_envelopes(self):
        for name in ['duplicate-field','nonfinite-number','invalid-output','invalid-message',
                     'invalid-content','invalid-part','invalid-text','multiple-messages']:
            with self.subTest(reply=name):
                wire = json.loads((MOCKS/f'schema-enforcement/{name}.json').read_text())
                with self.assertRaises(ValueError):
                    parse_response(wire,self.document['message'])
                result = process(self.document,MockProvider(MOCKS,{
                    'IT-001.json':[f'schema-enforcement/{name}.json']}))
                self.assertEqual(result['status'],'error')
                self.assertIsNone(result['extraction'])

    def test_fingerprint_includes_canonical_schema_with_custom_schema(self):
        self.custom.write_text('{}')
        settings = effective({'schema_path':str(self.custom)})
        before = fingerprints(settings,'policy')
        with patch('access_intake.extraction.SCHEMA_PATH',self.custom):
            after = fingerprints(settings,'policy')
        self.assertNotEqual(before[0],after[0])
        self.assertEqual(before[1],after[1])

    def test_queue_rejects_invalid_retry_and_keeps_valid_history(self):
        store = Store(Path(self.tmp.name)/'queue.sqlite3')
        provider = MockProvider(MOCKS,{'IT-001.json':[
            'IT-001.json','schema-enforcement/extra-field.json']})
        engine = Engine(store,provider,{'requests_per_minute':0})
        first = engine.submit(ROOT/'materials/requests/IT-001.json')
        failed = engine.retry(first['attempt_id'])
        self.assertEqual(failed['status'],'error')
        self.assertIsNone(store.queue()[0]['extraction'])
        self.assertEqual(store.history('IT-001.json')[0]['extraction'],expected()['extraction'])

    def test_mock_engine_also_applies_custom_constraints(self):
        self.custom.write_text(json.dumps({'properties':{'permission':{'const':'write'}}}))
        store = Store(Path(self.tmp.name)/'custom.sqlite3')
        engine = Engine(store,MockProvider(MOCKS),{
            'schema_path':str(self.custom),'requests_per_minute':0})
        failed = engine.submit(ROOT/'materials/requests/IT-001.json')
        self.assertEqual(failed['status'],'error')
        self.assertIsNone(failed['extraction'])
        self.assertIn('schema validation',failed['error'])

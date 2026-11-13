"""Only invented markers and the repository's fictional training messages are used."""
import json
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import Mock, patch

from access_intake.engine import Engine, effective
from access_intake.exports import publish, resolve_generation
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.providers import LiveProvider, MockProvider
from access_intake.storage import Store
from access_intake.privacy import require_live_approval, NoRedirect
from support import OfflineCase, ROOT, MOCKS, expected


class PrivacyContract(OfflineCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.document = read_export(ROOT/'materials/requests/IT-001.json')
        self.wire = json.loads((MOCKS/'IT-001.json').read_text())

    def test_unreviewed_input_is_not_sent_or_stored(self):
        marker = 'INVENTED-PRIVACY-MARKER-NO-REAL-PERSON'
        path = self.directory/'invented-private-name.json'
        path.write_text(json.dumps({'message':marker,'subject':marker}))
        provider = MockProvider(MOCKS)
        store = Store(self.directory/'state.sqlite3')
        failed = Engine(store,provider).submit(path)
        self.assertEqual(failed['status'],'error')
        self.assertIsNone(failed['extraction'])
        self.assertEqual(provider.calls,[])
        with store.connection() as db:
            sources = [dict(row) for row in db.execute('SELECT * FROM sources')]
        persisted = repr(sources)+json.dumps(store.queue())
        self.assertNotIn(marker,persisted)
        self.assertNotIn(path.name,persisted)
        self.assertIn('synthetic',failed['error'])

    def test_unreviewed_direct_provider_and_storage_calls_are_blocked(self):
        credentials = Mock(return_value='dummy-token')
        transport = Mock(return_value=self.wire)
        provider = LiveProvider({},transport=transport,credential=credentials)
        with self.assertRaisesRegex(ValueError,'synthetic'):
            provider.extract('INVENTED-UNREVIEWED-MESSAGE','IT-001.json')
        transport.assert_not_called();credentials.assert_not_called()
        store = Store(self.directory/'direct.sqlite3')
        forged = dict(self.document,message='INVENTED-UNREVIEWED-MESSAGE')
        with self.assertRaisesRegex(ValueError,'synthetic'):
            store.save(forged,{'extraction':None})

    def test_live_approval_is_checked_before_credentials_or_network(self):
        credential = Mock(return_value='dummy-token')
        provider = LiveProvider({},credential=credential)
        with patch('access_intake.providers.open_request') as network:
            failed = process(self.document,provider)
        self.assertEqual(failed['status'],'error')
        self.assertIn('approval',failed['error'])
        credential.assert_not_called();network.assert_not_called()

    def test_request_evidence_omits_message_and_instructions(self):
        provider = LiveProvider({},transport=lambda request,timeout:self.wire,credential=lambda:'dummy-token')
        provider.extract(self.document['message'],self.document['document_id'])
        evidence = provider.request_evidence()
        text = json.dumps(evidence)
        self.assertNotIn(self.document['message'],text)
        self.assertNotIn('Maya Chen',text)
        self.assertNotIn('dummy-token',text)
        self.assertEqual(evidence['body']['model'],'gpt-4.1')
        self.assertIn('sha256',evidence['body']['input'][0])

    def test_diagnostics_do_not_echo_response_or_exception_text(self):
        marker = 'INVENTED-ERROR-MARKER-NO-REAL-PERSON'
        wire = json.loads((MOCKS/'schema-enforcement/invalid-enum.json').read_text())
        facts = json.loads(wire['output'][0]['content'][0]['text'])
        facts['permission'] = marker
        wire['output'][0]['content'][0]['text'] = json.dumps(facts)
        for reply in [wire, RuntimeError('HTTP 503 '+marker)]:
            with self.subTest(reply_type=type(reply).__name__):
                result = process(self.document,MockProvider(MOCKS,{'IT-001.json':[reply]}))
                self.assertEqual(result['status'],'error')
                self.assertNotIn(marker,json.dumps(result))

    def test_runtime_files_have_private_permissions(self):
        previous = os.umask(0)
        self.addCleanup(os.umask,previous)
        store = Store(self.directory/'private-state'/'intake.sqlite3')
        Engine(store,MockProvider(MOCKS)).submit(ROOT/'materials/requests/IT-001.json')
        self.assertEqual(stat.S_IMODE(Path(store.path).stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(Path(store.path).parent.stat().st_mode),0o700)
        with store.connection():
            for suffix in ['-wal','-shm']:
                sidecar = Path(store.path+suffix)
                if sidecar.exists():
                    self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode),0o600)
        out = self.directory/'private-exports'
        publish(store,out)
        generation = resolve_generation(out)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(generation.stat().st_mode),0o700)
        for name in ['queue.json','extractions.jsonl','manifest.json']:
            self.assertEqual(stat.S_IMODE((generation/name).stat().st_mode),0o600)

    def test_database_symlink_is_rejected(self):
        target = self.directory/'target.sqlite3'
        target.write_bytes(b'untouched synthetic marker')
        link = self.directory/'linked.sqlite3';link.symlink_to(target)
        with self.assertRaises((ValueError,OSError)):
            Store(link)
        self.assertEqual(target.read_bytes(),b'untouched synthetic marker')

    def test_approval_cannot_change_endpoint_model_purpose_or_expiry(self):
        policy = {'endpoint':'https://api.openai.com/v1/responses','model':'gpt-4.1',
                  'purpose':'synthetic-training-only','approval_reference':'OFFLINE-TEST-ONLY',
                  'prompt_sha256':hashlib.sha256((ROOT/'access_intake/prompt.txt').read_bytes()).hexdigest(),
                  'valid_until':'2099-01-01'}
        approval = self.directory/'approval.json'
        approval.write_text(json.dumps(policy))
        settings = {'approval_path':str(approval),'model':'gpt-4.1','endpoint':policy['endpoint']}
        require_live_approval(settings)
        for change in [{'endpoint':'https://other.invalid/v1/responses'}, {'model':'different-model'},
                       {'purpose':'real-taxpayer-data'}, {'valid_until':'2000-01-01'},
                       {'approval_reference':''}, {'prompt_sha256':'0'*64}]:
            with self.subTest(change=change):
                approval.write_text(json.dumps(policy|change))
                with self.assertRaisesRegex(ValueError,'approval'):
                    require_live_approval(settings)
        approval.write_text(json.dumps(policy))
        for endpoint in ['http://api.openai.com/v1/responses',
                         'https://user:dummy-token@api.openai.com/v1/responses',
                         'https://api.openai.com/v1/responses?secret=dummy-token']:
            with self.subTest(endpoint=endpoint),self.assertRaisesRegex(ValueError,'HTTPS'):
                require_live_approval(settings|{'endpoint':endpoint})

    def test_redirects_never_resend_authorization_or_data(self):
        from urllib.request import Request
        request = Request('https://approved.invalid/v1/responses',data=b'fictional',
                          headers={'Authorization':'Bearer dummy-token'})
        with self.assertRaisesRegex(ValueError,'redirect blocked'):
            NoRedirect().redirect_request(request,None,307,'redirect',{},'https://other.invalid')

    def test_modified_metadata_and_bytes_are_unreviewed(self):
        for raw in [self.document['raw']+b'\n', json.dumps({
                'message':self.document['message'],'subject':'INVENTED-METADATA-MARKER'}).encode()]:
            with self.subTest(raw_hash=hashlib.sha256(raw).hexdigest()):
                path = self.directory/'IT-001.json';path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError,'synthetic'):
                    read_export(path)

    def test_credential_bearing_configuration_is_not_persisted(self):
        for settings in [{'endpoint':'https://dummy-token@api.openai.com/v1/responses'},
                         {'endpoint':'https://api.openai.com/v1/responses?key=dummy-token'},
                         {'model':'INVENTED FREE TEXT'}]:
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                effective(settings)

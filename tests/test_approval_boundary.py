"""Assert displayed review outcomes and watch for attempted provisioning side effects."""
from contextlib import ExitStack
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from access_intake.engine import Engine
from access_intake.inputs import read_export
from access_intake.providers import MockProvider
from access_intake.storage import Store
from support import OfflineCase, ROOT, MOCKS, expected
from test_08_cli import cli


ROUTES = {
    'IT-001.json':('review','System Owner','review-request'),
    'IT-002.json':('review','Security Review','review-risk-then-owner'),
    'IT-006.json':('review','IT Service Desk','inspect-message'),
}


class ApprovalBoundary(OfflineCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_completed_intake_displays_pending_human_decisions(self):
        inputs = self.directory/'inputs';inputs.mkdir()
        for name in ROUTES:
            shutil.copy(ROOT/'materials/requests'/name, inputs/name)
        database = self.directory/'intake.sqlite3'
        intake = cli('intake',inputs,'--provider','mock','--mock-dir',MOCKS,
                     '--db',database,'--requests-per-minute',0)
        self.assertEqual(intake.returncode,0,intake.stderr)
        queue = cli('queue','--db',database)
        self.assertEqual(queue.returncode,0,queue.stderr)
        for displayed in [json.loads(intake.stdout),json.loads(queue.stdout)]:
            self.assertIn('Intake completion does not approve access.',displayed['approval_boundary'])
            self.assertIn('A named manager is a contact.',displayed['approval_boundary'])
            self.assertIn('The System Owner makes the access decision',displayed['approval_boundary'])
            by_id = {record['document_id']:record for record in displayed['results']}
            self.assertFalse(displayed['work'])
            for name,route in ROUTES.items():
                record = by_id[name]
                self.assertEqual((record['status'],record['owner'],record['next_action']),route)
                self.assertEqual(record['intake_status'],'completed')
                self.assertEqual(record['access_decision'],'outstanding')
                self.assertEqual(record['decision_owner'],'System Owner')
                self.assertIs(record['access_granted'],False)
                self.assertNotEqual(record['status'],'approved')
                self.assertNotIn(record['next_action'],['grant-access','approve','provision-access'])
                self.assertEqual(record['extraction']['manager'],expected(name)['extraction']['manager'])
            self.assertEqual(by_id['IT-001.json']['review_note'],
                             'Intake completed; owner decision still outstanding.')
            self.assertEqual(by_id['IT-002.json']['review_note'],
                             'Intake completed; risk recommendation and subsequent owner decision still outstanding.')
            self.assertEqual(by_id['IT-006.json']['extraction']['permission'],'read')
            self.assertIn('inspection',by_id['IT-006.json']['review_note'])
        out = self.directory/'export'
        published = cli('export','--db',database,'--out',out)
        self.assertEqual(published.returncode,0,published.stderr)
        exported = json.loads((out/'queue.json').read_text())
        self.assertIn('does not approve access',exported['approval_boundary'])
        for entry in exported['entries']:
            self.assertEqual((entry['status'],entry['owner'],entry['next_action']),ROUTES[entry['document_id']])
            self.assertIs(entry['access_granted'],False)
            self.assertEqual(entry['access_decision'],'outstanding')

    def test_completed_and_hostile_intake_never_calls_provisioning_channels(self):
        store = Store(self.directory/'no-grants.sqlite3')
        provider = MockProvider(MOCKS)
        engine = Engine(store,provider,{'requests_per_minute':0})
        with ExitStack() as stack:
            channels = [stack.enter_context(patch(target,side_effect=AssertionError('access-grant side effect forbidden')))
                        for target in ['socket.socket','socket.create_connection','subprocess.Popen','os.system']]
            for name,route in ROUTES.items():
                record = engine.submit(ROOT/'materials/requests'/name)
                self.assertEqual((record['status'],record['owner'],record['next_action']),route)
                self.assertIs(record['access_granted'],False)
            for channel in channels:
                channel.assert_not_called()
        hostile = next(record for record in store.queue() if record['document_id']=='IT-006.json')
        self.assertEqual(hostile['status'],'review')
        self.assertEqual(hostile['next_action'],'inspect-message')
        self.assertEqual(hostile['access_decision'],'outstanding')

    def test_storage_rejects_approval_or_access_grant_outcomes(self):
        store = Store(self.directory/'boundary.sqlite3')
        document = read_export(ROOT/'materials/requests/IT-001.json')
        original = Engine(store,MockProvider(MOCKS)).submit(ROOT/'materials/requests/IT-001.json')
        for override in [{'status':'approved'}, {'next_action':'grant-access'},
                         {'access_granted':True}, {'access_decision':'approved'}]:
            with self.subTest(override=override),self.assertRaisesRegex(ValueError,'approval boundary'):
                store.save(document,original|override)
            self.assertEqual(store.current('IT-001.json')['attempt_id'],original['attempt_id'])

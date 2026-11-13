"""Refresh current challenge outputs using reviewed fictional inputs and saved replies."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from access_intake.accuracy import run_accuracy
from access_intake.engine import Engine, effective
from access_intake.exports import publish
from access_intake.inputs import read_export
from access_intake.policy import process
from access_intake.privacy import private_open
from access_intake.providers import MockProvider, LiveProvider
from access_intake.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def write_json(path,value):
    with private_open(path,'w') as stream:
        stream.write(json.dumps(value,indent=2)+'\n')


def main():
    mocks = ROOT/'tests/mock_responses'
    settings = effective({'provider':'mock','requests_per_minute':0})
    head = subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,env={'PATH':'/usr/bin:/bin'},
                          capture_output=True,text=True,check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp)/'intake.sqlite3')
        records = Engine(store,MockProvider(mocks),settings).intake(ROOT/'materials/requests')
        assert len(records)==20
        assert all(record['error'] is None and record['access_granted'] is False for record in records)
        generation = publish(store,ROOT/'deliverables')
    accuracy = run_accuracy(ROOT/'materials/requests',ROOT/'tests/fixtures/requests/IT-021.json',
        ROOT/'tests/expected/source-facts.json',MockProvider(mocks),ROOT/'deliverables/accuracy.md',settings)
    assert accuracy['matched']==accuracy['denominator']==252
    assert not accuracy['mismatches'] and not accuracy['unexpected_documents']
    assert all(check['passed'] for key in ['evidence_checks','routing_checks'] for check in accuracy[key])
    wire = json.loads((mocks/'IT-001.json').read_text())
    provider = LiveProvider(settings,transport=lambda request,timeout:wire,
                            credential=lambda:'documentation-dummy-token')
    result = process(read_export(ROOT/'materials/requests/IT-001.json'),provider)
    assert result['status']=='review' and result['response_source']=='mock'
    evidence = {'observation_type':'constructed request through injected HTTP transport; no live provider call',
        'model_id':settings['model'],'response_source':'mock','configuration':settings,
        'provenance':{'head_at_generation':head,'implementation_sha256':{
            str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT/'access_intake').glob('*.py'))}},
        'request':provider.request_evidence(),
        'response_handling':{
            'completed':'require a completed response with exactly one completed message and output_text; reject malformed envelopes, duplicate JSON keys and non-JSON numeric constants',
            'schema':'always validate against snapshot.schema.json; custom schemas add constraints; strict request format cannot be overridden; validate exact source quotes before routing',
            'refusal':'error / IT Service Desk / retry-extraction; extraction null',
            'incomplete':'never route partial facts; safe error and null extraction',
            'error':'log safe categories and issue codes, never raw response/exception values; retry transient HTTP/connection failures with bounded backoff and numeric Retry-After; preserve prior history',
            'calendar_and_withdrawal_validation':'invalid absolute date or missing withdrawal ID/date yields a field-named error before review routing without echoing invalid values',
            'approval_boundary':'intake completion never approves or grants access; System Owner decision remains outstanding; Security Review provides a prior risk recommendation when required',
            'privacy':'reviewed synthetic inputs only; redact input/instructions and credentials from request evidence; real network calls require exact unexpired endpoint/model/prompt/purpose approval; block redirects'},
        'redactions':'request input and instructions are replaced with placeholders and SHA-256; no real API_KEY is read; this is an offline request-construction example'}
    write_json(ROOT/'deliverables/request-evidence.json',evidence)
    summary = {'head_at_generation':head,'provider':'mock','model':settings['model'],
        'records':len(records),'export':generation,'accuracy':accuracy,
        'approval_boundary':{'all_access_decisions_outstanding':all(record['access_decision']=='outstanding' for record in records),
                             'no_access_grants':all(record['access_granted'] is False for record in records)}}
    write_json(ROOT/'planning/submission_2_evidence/deliverables_refresh.json',summary)
    print('Refreshed 20 mock extraction/queue records, 252/252 mock scalar comparisons, and redacted GPT-4.1 request evidence.')


if __name__=='__main__':
    main()

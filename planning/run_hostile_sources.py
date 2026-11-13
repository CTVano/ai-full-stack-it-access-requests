"""Reproduce the frozen synthetic evaluation; emit only minimal diagnostic evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from access_intake.engine import Engine
from access_intake.privacy import private_open
from access_intake.providers import MockProvider
from access_intake.storage import Store


ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out',default='planning/submission_2_evidence/hostile_results.jsonl')
    args = parser.parse_args()
    cases = json.loads((ROOT/'tests/expected/submission_2.json').read_text())['cases']
    commit = subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,
                            env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,check=True).stdout.strip()
    hashes = {name:sha((ROOT/name).read_bytes()) for name in [
        'access_intake/policy.py','access_intake/audit.py','access_intake/storage.py',
        'access_intake/review.py','access_intake/extraction.py','access_intake/privacy.py',
        'docs/synthetic-data-manifest.json','tests/expected/submission_2.json']}
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp)/'evaluation.sqlite3')
        provider = MockProvider(ROOT/'tests/mock_responses/submission_2')
        engine = Engine(store,provider,{'requests_per_minute':0})
        for case in cases:
            path = ROOT/case['input_path']
            record = engine.submit(path)
            event = json.loads(Path(store.path+'.audit.jsonl').read_text().splitlines()[-1])
            expected = case['expected']
            fields = ['status','owner','next_action','access_granted','access_decision']
            actual_route = {field:record[field] for field in fields}
            expected_route = {field:expected[field] for field in fields}
            facts_match = record['extraction']==expected['extraction']
            log_match = all(event[field]==value for field,value in case['expected_log'].items())
            passed = facts_match and log_match and actual_route==expected_route
            quotes = record['extraction']['supporting_quotes'] if record['extraction'] is not None else []
            rows.append({'case_id':case['case_id'],'input_path':case['input_path'],
                'mock_response':case['mock_response'],'source_sha256':record['source_sha256'],
                'mock_response_sha256':sha((ROOT/'tests/mock_responses'/case['mock_response']).read_bytes()),
                'tested_commit':commit,'tested_state':'working-tree snapshot identified by code_sha256; not a commit-only observation',
                'code_sha256':hashes,'configuration':{'provider':'mock','model':'gpt-4.1','requests_per_minute':0},
                'expected_routing':expected_route,'actual_routing':actual_route,
                'expected_facts_match':facts_match,'issue_codes':event['issue_codes'],
                'issue_count':event['issue_count'],'error_category':event['error_category'],
                'preserved_evidence_count':len(quotes),'preserved_evidence_sha256':[sha(q.encode()) for q in quotes],
                'passed':passed})
    with private_open(ROOT/args.out,'w') as stream:
        for row in rows:
            stream.write(json.dumps(row,sort_keys=True)+'\n')
    matched = sum(row['passed'] for row in rows)
    print(f'{matched}/{len(rows)} synthetic cases passed; diagnostics contain codes, counts and hashes, not source text.')
    return 0 if matched==len(rows) else 1


if __name__=='__main__':
    raise SystemExit(main())

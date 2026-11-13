"""Source-derived expectations never depend on the replies being measured."""
import json
from pathlib import Path
from .engine import effective
from .inputs import discover,read_export
from .policy import process

SCALARS=('request_id','request_date','kind','requester','team','system','environment','permission',
         'business_purpose','start_date','end_date','manager')


def compare(records,oracles):
    by_id={}
    for record in records:
        name=record['document_id']
        if name in by_id:raise ValueError(f'duplicate comparison document {name}')
        by_id[name]=record
    matched=0;mismatches=[];evidence=[];routing=[]
    for name,oracle in oracles.items():
        candidate=by_id.get(name,{})
        actual=candidate.get('extraction')
        for field in SCALARS:
            wanted=oracle['extraction'][field]
            value=actual.get(field) if isinstance(actual,dict) else None
            if isinstance(actual,dict) and field in actual and value==wanted:
                matched+=1
            else:
                mismatches.append({'document_id':name,'field':field,'expected':wanted,'actual':value,'source_message':oracle['source_message']})
        evidence_ok=isinstance(actual,dict) and bool(actual.get('supporting_quotes')) and all(q in oracle['source_message'] for q in actual.get('supporting_quotes',[]))
        if isinstance(actual,dict):
            issues=' '.join(actual.get('issues',[]))
            evidence_ok=evidence_ok and all(key in issues for key in SCALARS if actual.get(key) is None)
            # Preserve the independently selected separate conflict-alternative spans.
            for quote in oracle['extraction']['supporting_quotes']:
                if quote!=oracle['source_message']:
                    evidence_ok=evidence_ok and any(quote in q for q in actual['supporting_quotes'])
        evidence.append({'document_id':name,'passed':bool(evidence_ok)})
        routing.append({'document_id':name,'passed':all(candidate.get(k)==oracle[k] for k in ['status','owner','next_action'])})
    return {'matched':matched,'denominator':len(oracles)*len(SCALARS),'mismatches':mismatches,
            'evidence_checks':evidence,'routing_checks':routing,'unexpected_documents':sorted(set(by_id)-set(oracles))}


def _cell(text):
    return str(text).replace('|','\\|').replace('\n','<br>')


def run_accuracy(inputs,synthetic,expected_path,provider,out,settings=None):
    all_oracles=json.loads(Path(expected_path).read_text())
    files=discover(inputs)+[Path(synthetic)]
    names=[p.name for p in files]
    if len(set(names))!=len(names):raise ValueError('accuracy inputs must have distinct document filenames')
    oracles={name:all_oracles[name] for name in names}
    records=[process(read_export(path),provider) for path in files]
    summary=compare(records,oracles);configuration=effective(settings)
    source=provider.response_source
    mock=source=='mock'
    lines=['# Accuracy observations\n',f'Response source: `{source}`. Configuration: `{json.dumps(configuration,sort_keys=True)}`.\n',
        'Observation type: mock fixture consistency; no live extraction-quality observation.\n' if mock else 'Observation type: provider response comparison for the configuration above.\n',
        f"Observed exact scalar matches: **{summary['matched']}/{summary['denominator']}**.\n",
        'Procedure: compare the twelve named scalar fields for every source document and the authored paraphrase against tests/expected/source-facts.json, authored from the plan and source messages before model output. Exact matching is case-sensitive; no whitespace/case normalization or paraphrase equivalence is applied. Null equals null, but a failed/missing extraction contributes twelve mismatches, including fields expected null. Each document contributes twelve to the denominator. Unexpected/duplicate documents are reported or rejected. Quotes/issues and routing are separate checks and do not change that denominator. There is no invented passing-score threshold.\n',
        'Business purpose uses the source-supported spans selected in the plan. A different supported wording can be an exact mismatch without being a factual error. Review each disagreement against the quoted source before calling it a factual error. Mock agreement verifies fixture consistency and handling, not live model generalization.\n',
        '| Document | Source-derived expected scalar facts and routing | Quoted source message | Response source |\n|---|---|---|---|\n']
    for record in records:
        name=record['document_id'];oracle=oracles[name]
        expected={field:oracle['extraction'][field] for field in SCALARS}|{k:oracle[k] for k in ['status','owner','next_action']}
        lines.append(f"| {name} | {_cell(json.dumps(expected,ensure_ascii=False))} | “{_cell(oracle['source_message'])}” | {record['response_source']} |\n")
    lines+=['\n## Policy sources\n',
        'office-policy.md: “Use explicit facts from each message”; “Represent absent or unresolved scalar values as null and explain each missing or conflicting fact in issues”; “A later statement explicitly labelled as a correction replaces the earlier value”; “For a change request, extract the requested new permission”. Review rules apply in order: override → inspect-message; withdrawal with ID/date → confirm-withdrawal; incomplete/conflicting/mismatched/other → clarify; complete production/write/admin → review-risk-then-owner; complete staging/read → review-request.\n',
        '\n## Factual-error review\n',json.dumps(summary['mismatches'],indent=2,ensure_ascii=False)+'\n',
        '\n## Separate evidence and routing checks\n',json.dumps({'evidence':summary['evidence_checks'],'routing':summary['routing_checks']},indent=2)+'\n']
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(lines),encoding='utf-8')
    return summary

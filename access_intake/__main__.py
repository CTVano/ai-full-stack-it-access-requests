"""Documented CLI. Mock mode never constructs a live credential request."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time
from .engine import Engine, DEFAULTS
from .storage import Store
from .providers import MockProvider, LiveProvider, TransientError
from .privacy import safe_error
from .review import APPROVAL_BOUNDARY, review_record


def mock_provider(directory,scenario=None):
    scripts={}
    if scenario:
        for ident,steps in json.loads(Path(scenario).read_text()).items():
            actions=[]
            for step in steps:
                if isinstance(step,str):actions.append(step)
                elif 'error' in step:actions.append(RuntimeError(step['error']))
                elif 'transient_error' in step:actions.append(TransientError(step['transient_error'],step.get('retry_after',0)))
                else:
                    def delayed(step=step):
                        Path(step['entered']).touch();deadline=time.monotonic()+30
                        while not Path(step['release']).exists():
                            if time.monotonic()>deadline:raise TimeoutError('mock barrier timed out')
                            time.sleep(.01)
                        return step['reply']
                    actions.append(delayed)
            scripts[ident]=actions
    return MockProvider(directory,scripts)


def parser():
    root=argparse.ArgumentParser(description=APPROVAL_BOUNDARY+' All successful intake outputs await human review.')
    commands=root.add_subparsers(dest='command',required=True)
    for command in ['intake','queue','history','resume','retry','export','accuracy']:
        p=commands.add_parser(command)
        if command=='intake':p.add_argument('inputs')
        if command=='history':p.add_argument('document_id')
        if command=='retry':p.add_argument('--attempt',required=True)
        if command=='export':p.add_argument('--out',default='deliverables')
        if command=='accuracy':
            p.add_argument('--inputs',required=True);p.add_argument('--synthetic',required=True)
            p.add_argument('--expected',required=True);p.add_argument('--out',required=True)
        p.add_argument('--db',default='.state/intake.sqlite3')
        p.add_argument('--provider',choices=['mock','openai'])
        p.add_argument('--mock-dir',default='tests/mock_responses')
        p.add_argument('--scenario')
        p.add_argument('--model',default=os.getenv('MODEL_ID',DEFAULTS['model']))
        p.add_argument('--approval-file',default=os.getenv('MODEL_APPROVAL_FILE'))
        p.add_argument('--endpoint',default=os.getenv('MODEL_BASE_URL',DEFAULTS['endpoint']))
        p.add_argument('--timeout',type=float,default=float(os.getenv('MODEL_TIMEOUT_SECONDS','30')))
        p.add_argument('--workers',type=int,default=int(os.getenv('MAX_WORKERS','4')))
        p.add_argument('--requests-per-minute',type=float,default=float(os.getenv('REQUESTS_PER_MINUTE','60')))
        p.add_argument('--max-attempts',type=int,default=int(os.getenv('MAX_ATTEMPTS','3')))
    return root


def render(records,store,args):
    results=[]
    for record in records:
        if record is None:continue
        record=review_record(record)
        if record.get('status')=='error':
            record['retry_command']=shlex.join(['python3','-m','access_intake','retry','--attempt',record['attempt_id'],'--provider',record['settings'].get('provider','mock'),'--db',str(args.db),'--mock-dir',str(args.mock_dir)])
        results.append(record)
    return {'approval_boundary':APPROVAL_BOUNDARY,'results':results,'work':store.work()}


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='accuracy':
            from .accuracy import run_accuracy
            if (args.provider or os.getenv('MODEL_PROVIDER','mock'))!='mock':
                raise ValueError('this reproducible accuracy command uses mock replies; use an independently labelled provider experiment for live observations')
            settings={'provider':'mock','model':args.model}
            result=run_accuracy(args.inputs,args.synthetic,args.expected,mock_provider(args.mock_dir,args.scenario),args.out,settings)
            print(json.dumps(result,indent=2));return 0
        store=Store(args.db)
        if args.command=='export':
            from .exports import publish
            print(json.dumps(publish(store,args.out),indent=2));return 0
        if args.command=='queue':records=store.queue()
        elif args.command=='history':records=store.history(args.document_id)
        else:
            saved=store.attempt(args.attempt)['settings'] if args.command=='retry' else None
            provider_name=args.provider or (saved['provider'] if saved else os.getenv('MODEL_PROVIDER','mock'))
            settings={'provider':provider_name,'model':args.model,'endpoint':args.endpoint,'timeout':args.timeout,'workers':args.workers,'requests_per_minute':args.requests_per_minute,'max_attempts':args.max_attempts}
            if args.approval_file:
                settings['approval_path']=args.approval_file
            if provider_name=='mock':provider=mock_provider(args.mock_dir,args.scenario)
            elif provider_name=='openai':provider=LiveProvider(settings)
            else:raise ValueError('MODEL_PROVIDER must be mock or openai')
            engine=Engine(store,provider,settings)
            if args.command=='intake':records=engine.intake(args.inputs)
            elif args.command=='resume':records=engine.resume()
            else:
                if saved['provider']!=provider_name:raise ValueError('retry must use the saved provider; start a new intake to change configuration')
                records=[engine.retry(args.attempt)]
        print(json.dumps(render(records,store,args),indent=2))
        return int(any(r and r.get('status')=='error' for r in records))
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'error':safe_error(exc)}),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())

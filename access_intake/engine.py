"""Source revision, effective configuration, reuse and saved-source retry service."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import time
import hashlib
from pathlib import Path
from .inputs import read_export, discover
from .extraction import ROOT, failure
from .policy import process, route
from .storage import fingerprint
from .providers import DEFAULT_MODEL

DEFAULTS={'provider':'mock','model':DEFAULT_MODEL,'endpoint':'https://api.openai.com/v1/responses',
          'timeout':30,'workers':4,'requests_per_minute':60,'max_attempts':3,'retry_delay':0.1,
          'lease_seconds':120,'model_options':{}}
ALLOWED=set(DEFAULTS)|{'prompt_path','schema_path'}


def effective(settings=None):
    unknown=set(settings or {})-ALLOWED
    if unknown:raise ValueError(f'unsupported configuration keys: {sorted(unknown)}')
    result=deepcopy(DEFAULTS)|deepcopy(settings or {})
    for key in ['timeout','workers','max_attempts','lease_seconds']:
        if not isinstance(result[key],(int,float)) or result[key]<=0:raise ValueError(f'{key} must be positive')
    if result['requests_per_minute']<0 or result['retry_delay']<0:raise ValueError('rate and delay must be nonnegative')
    return result


def fingerprints(settings,policy_version):
    prompt=Path(settings.get('prompt_path',ROOT/'access_intake/prompt.txt')).read_bytes()
    schema=Path(settings.get('schema_path',ROOT/'snapshot.schema.json')).read_bytes()
    relevant={k:settings[k] for k in ['provider','model','endpoint','model_options']}
    relevant['prompt_sha256']=hashlib.sha256(prompt).hexdigest();relevant['schema_sha256']=hashlib.sha256(schema).hexdigest()
    return fingerprint(relevant),fingerprint({'policy':policy_version,'router':Path(__file__).with_name('policy.py').read_text()})


class Engine:
    def __init__(self,store,provider,settings=None,policy_version=None,**runtime):
        self.store=store;self.provider=provider;self.settings=effective(settings)
        self.policy_version=policy_version or hashlib.sha256((ROOT/'materials/office-policy.md').read_bytes()).hexdigest()
        self.hooks=runtime.get('hooks',{})
        self.clock=runtime.get('clock',time.time);self.sleep=runtime.get('sleep',time.sleep)

    def submit(self,path):
        try:
            document=read_export(path)
        except (ValueError,OSError) as exc:
            raw=Path(path).read_bytes() if Path(path).exists() else b''
            document={'document_id':Path(path).name,'source_sha256':hashlib.sha256(raw).hexdigest(),'raw':raw,'message':'','metadata':{}}
            return self.store.save(document,failure(document,str(exc),self.provider.response_source),self.settings)
        return self._submit(document)

    def _submit(self,document,force=False,settings=None,policy_version=None):
        settings=effective(settings or self.settings);policy_version=policy_version or self.policy_version
        extraction_fp,routing_fp=fingerprints(settings,policy_version)
        previous=self.store.current(document['document_id'])
        same_source=bool(previous and previous['source_sha256']==document['source_sha256'])
        same_extraction=bool(same_source and previous['extraction_fingerprint']==extraction_fp)
        if not force and same_extraction and previous['routing_fingerprint']==routing_fp:
            return previous
        ident=self.store.start(document,settings,extraction_fp,routing_fp,policy_version,force=force)
        token=self.store.claim(ident,settings['lease_seconds'])
        if token is None:
            attempt=self.store.attempt(ident)
            return attempt['result'] or {'attempt_id':ident,'document_id':document['document_id'],'state':attempt['state']}
        if not force and same_extraction and previous['extraction'] is not None:
            result=deepcopy(previous)
            try:
                status,owner,action=route(result['extraction'],document)
                result.update(status=status,owner=owner,next_action=action)
            except ValueError as exc:
                result=failure(document,str(exc),previous['response_source'])
        else:
            self._hook('before_response')
            result=self._request(document,settings)
        self._hook('before_commit')
        record=self.store.finish(ident,result,token)
        self._hook('after_commit')
        return record

    def retry(self,attempt_id):
        attempt=self.store.attempt(attempt_id)
        self._validate_assets(attempt)
        return self._submit(attempt['document'],force=True,settings=attempt['settings'],policy_version=attempt['policy_version'])

    def intake(self,path):
        documents=[];errors=[]
        for file in discover(path):
            try:documents.append(read_export(file))
            except (ValueError,OSError):errors.append(self.submit(file))
        extraction_fp,routing_fp=fingerprints(self.settings,self.policy_version)
        # A batch is accepted atomically before workers make the first provider request.
        self.store.accept_batch(documents,self.settings,extraction_fp,routing_fp,self.policy_version)
        with ThreadPoolExecutor(max_workers=int(self.settings['workers'])) as pool:
            return list(pool.map(self._submit,documents))+errors

    def _hook(self,name):
        hook=self.hooks.get(name)
        if hook:hook()

    def _request(self,document,settings):
        from .providers import LiveProvider
        provider=self.provider
        if (settings['provider']=='openai') != isinstance(provider,LiveProvider):
            return failure(document,'saved provider differs from selected provider; select its saved provider explicitly',provider.response_source)
        if isinstance(provider,LiveProvider):
            provider=LiveProvider(settings,transport=provider.transport,credential=provider.credential)
        deadline=self.clock()+settings['timeout']*settings['max_attempts']
        for attempt in range(int(settings['max_attempts'])):
            now=self.clock();slot=self.store.reserve_slot(settings['requests_per_minute'],now)
            wait=slot-now
            if slot>=deadline:
                return failure(document,'rate-limit wait would exceed attempt deadline',provider.response_source)
            if wait>0:self.sleep(wait)
            result=process(document,provider)
            retryable=result.pop('_retryable',False);retry_after=result.pop('_retry_after',0)
            if not retryable or attempt+1>=settings['max_attempts']:
                return result
            delay=max(settings['retry_delay']*(2**attempt),retry_after)
            if self.clock()+delay>=deadline:
                return failure(document,'retry delay would exceed attempt deadline',provider.response_source)
            if delay:self.sleep(delay)
        return result

    def resume(self):
        results=[]
        for ident in self.store.recoverable():
            attempt=self.store.attempt(ident);settings=attempt['settings']
            self._validate_assets(attempt)
            if settings['provider']!=self.settings['provider']:
                raise ValueError('unfinished work uses a different saved provider; select that provider explicitly')
            token=self.store.claim(ident,settings['lease_seconds'])
            if token is None:continue
            result=self._request(attempt['document'],settings)
            self._hook('before_commit')
            results.append(self.store.finish(ident,result,token))
            self._hook('after_commit')
        return results

    def _validate_assets(self,attempt):
        actual=fingerprints(attempt['settings'],attempt['policy_version'])
        if actual!=(attempt['extraction_fp'],attempt['routing_fp']) or attempt['policy_version']!=self.policy_version:
            raise ValueError('historical processing assets changed; restore the saved prompt/schema/policy or run intake to refresh with current settings')

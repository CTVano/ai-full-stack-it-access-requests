"""Source revision, effective configuration, reuse and saved-source retry service."""
from copy import deepcopy
import hashlib
from pathlib import Path
from .inputs import read_export, discover
from .extraction import ROOT, failure
from .policy import process, route
from .storage import fingerprint

DEFAULTS={'provider':'mock','model':'gpt-4.1-mini','endpoint':'https://api.openai.com/v1/responses',
          'timeout':30,'workers':4,'requests_per_minute':60,'max_attempts':3,'retry_delay':0.1,
          'lease_seconds':120,'model_options':{}}
ALLOWED=set(DEFAULTS)|{'prompt_path','schema_path'}


def effective(settings=None):
    unknown=set(settings or {})-ALLOWED
    if unknown:raise ValueError(f'unsupported configuration keys: {sorted(unknown)}')
    return deepcopy(DEFAULTS)|deepcopy(settings or {})


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
        ident=self.store.start(document,settings,extraction_fp,routing_fp,policy_version)
        if not force and same_extraction and previous['extraction'] is not None:
            result=deepcopy(previous)
            try:
                status,owner,action=route(result['extraction'],document)
                result.update(status=status,owner=owner,next_action=action)
            except ValueError as exc:
                result=failure(document,str(exc),previous['response_source'])
        else:
            result=process(document,self.provider)
        return self.store.finish(ident,result)

    def retry(self,attempt_id):
        attempt=self.store.attempt(attempt_id)
        return self._submit(attempt['document'],force=True,settings=attempt['settings'],policy_version=attempt['policy_version'])

    def intake(self,path):
        return [self.submit(file) for file in discover(path)]

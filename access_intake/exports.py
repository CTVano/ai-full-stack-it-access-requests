"""Publish immutable snapshots, then switch a single POSIX generation pointer."""
import fcntl
import json
import os
from pathlib import Path
import uuid


def _write(path,text):
    with path.open('w',encoding='utf-8') as stream:
        stream.write(text);stream.flush();os.fsync(stream.fileno())


def _sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def resolve_generation(out):
    return (Path(out)/'current').resolve(strict=True)


def publish(store,out,hook=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with (out/'.export.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        records=store.queue()  # One SQLite read statement fixes a consistent source/attempt snapshot.
        generation=uuid.uuid4().hex
        directory=out/'.generations'/generation;directory.mkdir(parents=True)
        manifest={'generation':generation,'records':len(records),'extractions':'extractions.jsonl','queue':'queue.json'}
        fields=['document_id','source_sha256','attempt_id','status','owner','next_action','error']
        queue={'generation':generation,'entries':[{k:r[k] for k in fields} for r in records]}
        _write(directory/'extractions.jsonl',''.join(json.dumps(r,sort_keys=True)+'\n' for r in records))
        _write(directory/'queue.json',json.dumps(queue,indent=2)+'\n')
        _write(directory/'manifest.json',json.dumps(manifest,indent=2)+'\n')
        _sync_directory(directory);_sync_directory(directory.parent)
        for name in ['extractions.jsonl','queue.json','manifest.json']:
            link=out/name
            target='current/'+name
            if link.is_symlink():
                if os.readlink(link)!=target:raise ValueError(f'{link}: unexpected symlink target')
            elif link.exists():
                raise ValueError(f'{link}: refusing to replace a non-generation output')
            else:link.symlink_to(target)
        if hook:hook()
        pending=out/('.current-'+generation)
        pending.symlink_to('.generations/'+generation,target_is_directory=True)
        os.replace(pending,out/'current');_sync_directory(out)
        return manifest

"""SQLite attempts/history are separate from one current pointer per document."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid
from .privacy import private_open, require_synthetic_document, is_blocked_document


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self,path):
        self.path = str(path)
        if self.path != ':memory:':
            with private_open(path):
                pass
            for suffix in ['-wal','-shm']:
                sidecar = Path(self.path+suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    with private_open(sidecar):
                        pass
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,document_id TEXT NOT NULL,
                  sha256 TEXT NOT NULL,raw BLOB NOT NULL,message TEXT NOT NULL,metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
                  source_id INTEGER NOT NULL REFERENCES sources(id),settings TEXT NOT NULL,
                  extraction_fp TEXT NOT NULL,routing_fp TEXT NOT NULL,policy_version TEXT NOT NULL,
                  state TEXT NOT NULL,result TEXT,created REAL NOT NULL,token TEXT,lease_until REAL,tries INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS current(document_id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL REFERENCES attempts(id));
                CREATE TABLE IF NOT EXISTS throttle(id INTEGER PRIMARY KEY CHECK(id=1),next_at REAL NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _source(self,db,doc):
        if not is_blocked_document(doc):
            require_synthetic_document(doc)
        return db.execute('INSERT INTO sources(document_id,sha256,raw,message,metadata) VALUES(?,?,?,?,?)',
            (doc['document_id'],doc['source_sha256'],doc['raw'],doc['message'],canonical(doc.get('metadata',{})))).lastrowid

    def _attempt(self,db,source,settings,state='pending',extraction_fp=None,routing_fp=None,policy_version='office-policy-v1'):
        ident=uuid.uuid4().hex
        db.execute('INSERT INTO attempts(id,source_id,settings,extraction_fp,routing_fp,policy_version,state,created) VALUES(?,?,?,?,?,?,?,?)',
            (ident,source,canonical(settings),extraction_fp or fingerprint(settings),routing_fp or fingerprint(policy_version),policy_version,state,time.time()))
        return ident

    def _finish(self,db,ident,result):
        attempt=db.execute('SELECT a.*,s.document_id,s.sha256 FROM attempts a JOIN sources s ON s.id=a.source_id WHERE a.id=?',(ident,)).fetchone()
        record=dict(result,attempt_id=ident,source_revision=attempt['source_id'],settings=json.loads(attempt['settings']),
                    extraction_fingerprint=attempt['extraction_fp'],routing_fingerprint=attempt['routing_fp'],policy_version=attempt['policy_version'])
        record['document_id']=attempt['document_id'];record['source_sha256']=attempt['sha256']
        db.execute('UPDATE attempts SET state=\'completed\',result=?,token=NULL,lease_until=NULL WHERE id=?',(canonical(record),ident))
        latest=db.execute('SELECT MAX(a.sequence) FROM attempts a JOIN sources s ON s.id=a.source_id WHERE s.document_id=?',(record['document_id'],)).fetchone()[0]
        if attempt['sequence']==latest:
            db.execute('INSERT INTO current(document_id,attempt_id) VALUES(?,?) ON CONFLICT(document_id) DO UPDATE SET attempt_id=excluded.attempt_id',(record['document_id'],ident))
        return record

    def save(self,document,result,settings=None,hook=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            source=self._source(db,document)
            ident=self._attempt(db,source,settings or {})
            record=self._finish(db,ident,result)
            if hook:hook()
            return record

    def queue(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT a.result FROM current c JOIN attempts a ON a.id=c.attempt_id ORDER BY c.document_id')]

    def history(self,document_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('''SELECT a.result FROM attempts a JOIN sources s ON s.id=a.source_id
                WHERE s.document_id=? AND a.result IS NOT NULL AND a.id NOT IN(SELECT attempt_id FROM current) ORDER BY a.sequence''',(document_id,))]

    def current(self,document_id):
        with self.connection() as db:
            row=db.execute('SELECT a.result FROM current c JOIN attempts a ON a.id=c.attempt_id WHERE c.document_id=?',(document_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def _start(self,db,doc,settings,extraction_fp,routing_fp,policy_version,force=False):
        last=db.execute('SELECT id,sha256 FROM sources WHERE document_id=? ORDER BY id DESC LIMIT 1',(doc['document_id'],)).fetchone()
        source=last['id'] if last and last['sha256']==doc['source_sha256'] else self._source(db,doc)
        if not force:
            busy=db.execute("SELECT id FROM attempts WHERE source_id=? AND extraction_fp=? AND routing_fp=? AND state IN ('pending','leased') ORDER BY sequence DESC LIMIT 1",(source,extraction_fp,routing_fp)).fetchone()
            if busy:return busy['id']
        return self._attempt(db,source,settings,extraction_fp=extraction_fp,routing_fp=routing_fp,policy_version=policy_version)

    def start(self,doc,settings,extraction_fp,routing_fp,policy_version,force=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._start(db,doc,settings,extraction_fp,routing_fp,policy_version,force)

    def accept_batch(self,documents,settings,extraction_fp,routing_fp,policy_version):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for doc in documents:
                previous=db.execute('SELECT a.result FROM current c JOIN attempts a ON a.id=c.attempt_id WHERE c.document_id=?',(doc['document_id'],)).fetchone()
                previous=json.loads(previous[0]) if previous else None
                if previous and previous['source_sha256']==doc['source_sha256'] and previous['extraction_fingerprint']==extraction_fp and previous['routing_fingerprint']==routing_fp:
                    continue
                self._start(db,doc,settings,extraction_fp,routing_fp,policy_version)

    def finish(self,ident,result,token=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if token is not None:
                owned=db.execute("SELECT token,state FROM attempts WHERE id=?",(ident,)).fetchone()
                if owned is None or owned['token']!=token or owned['state']!='leased':
                    return None
            return self._finish(db,ident,result)

    def attempt(self,ident):
        with self.connection() as db:
            row=db.execute('SELECT a.*,s.document_id,s.sha256,s.raw,s.message,s.metadata FROM attempts a JOIN sources s ON s.id=a.source_id WHERE a.id=?',(ident,)).fetchone()
            if row is None:raise ValueError(f'unknown attempt {ident}')
            item=dict(row);item['settings']=json.loads(item['settings']);item['result']=json.loads(item['result']) if item['result'] else None
            item['document']={'document_id':item['document_id'],'source_sha256':item['sha256'],'raw':item['raw'],'message':item['message'],'metadata':json.loads(item['metadata'])}
            return item

    def claim(self,ident,lease_seconds=120):
        token=uuid.uuid4().hex;now=time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute("UPDATE attempts SET state='leased',token=?,lease_until=?,tries=tries+1 WHERE id=? AND (state='pending' OR (state='leased' AND lease_until<?))",
                (token,now+lease_seconds,ident,now)).rowcount
            return token if changed else None

    def work(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT a.id AS attempt_id,s.document_id,s.sha256 AS source_sha256,a.state,a.lease_until FROM attempts a JOIN sources s ON s.id=a.source_id WHERE a.state IN ('pending','leased') ORDER BY a.sequence")]

    def recoverable(self):
        with self.connection() as db:
            db.execute("""UPDATE attempts SET state='superseded',token=NULL,lease_until=NULL WHERE
                (state='pending' OR (state='leased' AND lease_until<?)) AND sequence <
                (SELECT MAX(newer.sequence) FROM attempts newer JOIN sources ns ON ns.id=newer.source_id
                 WHERE ns.document_id=(SELECT document_id FROM sources WHERE id=attempts.source_id))""",(time.time(),))
        return [row['attempt_id'] for row in self.work() if row['state']=='pending' or (row['state']=='leased' and row['lease_until']<time.time())]

    def reserve_slot(self,rate,now):
        if not rate:return now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT next_at FROM throttle WHERE id=1').fetchone()
            slot=max(now,row[0] if row else now)
            db.execute('INSERT INTO throttle(id,next_at) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next_at=excluded.next_at',(slot+60/rate,))
            return slot

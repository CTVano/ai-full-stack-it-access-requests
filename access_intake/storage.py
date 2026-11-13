"""SQLite attempts/history are separate from one current pointer per document."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self,path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
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

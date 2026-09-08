"""Create-only UTC shards with indexed working state and append-only evidence."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,threading
from pathlib import Path
from .recorder_config import CONTRACT,RUNTIME

APPLICATION_ID=0x43535231
SCHEMA='''
CREATE TABLE collection_contracts(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract_name TEXT NOT NULL,data_contract TEXT NOT NULL,database_utc_date TEXT NOT NULL,runtime_job TEXT NOT NULL,schema_sha256 TEXT NOT NULL);
CREATE TABLE slot_claims(slot_utc TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,started_at TEXT NOT NULL,config_json TEXT NOT NULL);
CREATE TABLE claim_carryovers(slot_utc TEXT PRIMARY KEY,owner_run_id TEXT NOT NULL,source_shard TEXT NOT NULL,state_json TEXT NOT NULL,source_state_sha256 TEXT NOT NULL);
CREATE TABLE cycles(run_id TEXT PRIMARY KEY,slot_utc TEXT NOT NULL UNIQUE,reference_at TEXT NOT NULL,published_at TEXT NOT NULL,status TEXT NOT NULL,config_json TEXT NOT NULL,stats_json TEXT NOT NULL);
CREATE TABLE requests(attempt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,request_id TEXT NOT NULL,request_kind TEXT NOT NULL,started_at TEXT NOT NULL,received_at TEXT NOT NULL,status TEXT NOT NULL,http_status INTEGER,sha256 TEXT,raw_gzip BLOB,raw_complete INTEGER NOT NULL,receipt_json TEXT NOT NULL);
CREATE INDEX request_run_idx ON requests(run_id,request_id);
CREATE TABLE tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,first_run_id TEXT NOT NULL,state TEXT NOT NULL,slots_json TEXT NOT NULL,scheduled_start TEXT,end_anchor TEXT,end_basis TEXT,ever_live INTEGER NOT NULL,terminal_json TEXT,next_due TEXT NOT NULL,missing_count INTEGER NOT NULL,anchor_json TEXT NOT NULL);
CREATE INDEX tracked_due_idx ON tracked_events(state,next_due,event_id);
CREATE TABLE registry_carryovers(event_id TEXT PRIMARY KEY,source_shard TEXT NOT NULL,source_state_sha256 TEXT NOT NULL,state_json TEXT NOT NULL);
CREATE TABLE event_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family TEXT NOT NULL,league TEXT,season_phase TEXT,metadata_status TEXT NOT NULL,request_id TEXT,received_at TEXT,event_json TEXT,clock_json TEXT,slots_json TEXT NOT NULL,identity_valid INTEGER NOT NULL,window_status TEXT NOT NULL,lifecycle_state TEXT NOT NULL,scheduled_start TEXT,end_anchor TEXT,end_basis TEXT,terminal_json TEXT,reason TEXT NOT NULL,PRIMARY KEY(run_id,event_id));
CREATE TABLE book_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT NULL,condition_id TEXT,token_id TEXT,outcome TEXT,result_kind TEXT,verified_role TEXT,team_name TEXT,status TEXT NOT NULL,request_id TEXT,requested_at TEXT,received_at TEXT,book_sha256 TEXT,book_gzip BLOB,fee_json TEXT NOT NULL,point_in_time_valid INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(run_id,event_id,slot));
CREATE INDEX books_time_idx ON book_observations(received_at,event_id,token_id);
CREATE TABLE clock_observations(run_id TEXT NOT NULL,request_id TEXT NOT NULL,ordinal INTEGER NOT NULL,sha256 TEXT NOT NULL,received_at TEXT NOT NULL,raw_gzip BLOB NOT NULL,PRIMARY KEY(run_id,request_id,ordinal));
'''
PK={'collection_contracts':('singleton',),'slot_claims':('slot_utc',),'claim_carryovers':('slot_utc',),'cycles':('run_id',),'requests':('attempt_id',),'registry_carryovers':('event_id',),'event_observations':('run_id','event_id'),'book_observations':('run_id','event_id','slot'),'clock_observations':('run_id','request_id','ordinal')}
def schema_sql():
    sql=SCHEMA
    for table,keys in PK.items():
        for op in ('UPDATE','DELETE'):
            sql+=f"CREATE TRIGGER {table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END;"
        where=' AND '.join(f'{k}=NEW.{k}' for k in keys)
        if table=='slot_claims':where=f'({where}) OR run_id=NEW.run_id'
        if table=='cycles':where=f'({where}) OR slot_utc=NEW.slot_utc'
        sql+=f"CREATE TRIGGER {table}_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {where}) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END;"
    return sql

def fingerprint(c):
    rows=c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps([tuple(r) for r in rows],separators=(',',':')).encode()).hexdigest()

class RecorderStore:
    def __init__(self,path,day):
        self.path=Path(path);self.day=day;self.lock=threading.RLock()
        if datetime.fromisoformat(day).date().isoformat()!=day:raise ValueError('invalid UTC shard date')
        if self.path.name!='trades_sim.db' or self.path.is_symlink():raise ValueError('unsafe recorder database path')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        mem=sqlite3.connect(':memory:');mem.executescript(schema_sql());self.expected_schema=fingerprint(mem);mem.close()
        carry=[];source=None;source_sha=None;last_claim=None
        if self.path.exists():
            c=self._readonly(self.path);self._validate(c)
            stored=c.execute('SELECT database_utc_date FROM collection_contracts').fetchone()[0]
            if stored>day: c.close();raise ValueError('UTC shard clock reversed')
            if stored<day:
                carry=[dict(r) for r in c.execute("SELECT * FROM tracked_events WHERE state!='DONE'")]
                last_claim=c.execute('SELECT * FROM slot_claims ORDER BY slot_utc DESC LIMIT 1').fetchone()
                last_claim=dict(last_claim) if last_claim else None
                c.close();source=self.path.with_name('trades_sim_'+stored.replace('-','')+'.db')
                if source.exists():raise ValueError('UTC archive collision')
                os.replace(self.path,source)
            else:c.close()
        if not self.path.exists():
            # Recover an interrupted rollover from the immutable latest shard.
            # Only the small indexed working set is read, never observation history.
            if source is None:
                archives=sorted(self.path.parent.glob('trades_sim_????????.db'))
                if archives:
                    source=archives[-1]
                    if source.is_symlink():raise ValueError('unsafe archive')
                    prior=self._readonly(source);self._validate(prior)
                    stored=prior.execute('SELECT database_utc_date FROM collection_contracts').fetchone()[0]
                    if source.name!='trades_sim_'+stored.replace('-','')+'.db' or stored>=day:
                        prior.close();raise ValueError('archive day mismatch')
                    carry=[dict(r) for r in prior.execute("SELECT * FROM tracked_events WHERE state!='DONE'")]
                    last_claim=prior.execute('SELECT * FROM slot_claims ORDER BY slot_utc DESC LIMIT 1').fetchone()
                    last_claim=dict(last_claim) if last_claim else None
                    prior.close()
            self.path.open('xb').close();c=self._connect(self.path);c.executescript(schema_sql())
            c.execute(f'PRAGMA application_id={APPLICATION_ID}');c.execute('PRAGMA user_version=1')
            c.execute('INSERT INTO collection_contracts VALUES(1,?,?,?,?,?)',('research-full-v1',CONTRACT,day,RUNTIME,self.expected_schema))
            if last_claim:
                serialized=json.dumps(last_claim,sort_keys=True)
                self.insert(c,'claim_carryovers',{'slot_utc':last_claim['slot_utc'],'owner_run_id':last_claim['run_id'],'source_shard':source.name,'state_json':serialized,'source_state_sha256':hashlib.sha256(serialized.encode()).hexdigest()})
            for row in carry:
                self.insert(c,'tracked_events',row)
                self.insert(c,'registry_carryovers',{'event_id':row['event_id'],'source_shard':source.name,'source_state_sha256':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest(),'state_json':json.dumps(row,sort_keys=True)})
            c.commit();c.close()
        self.c=self._connect(self.path);self._validate(self.c)

    @staticmethod
    def _sha(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        return h.hexdigest()

    @staticmethod
    def _readonly(path):
        c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

    def _connect(self,path):
        c=sqlite3.connect(path,timeout=3,check_same_thread=False);c.row_factory=sqlite3.Row
        c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA journal_mode=DELETE');return c

    def _validate(self,c):
        row=c.execute('SELECT * FROM collection_contracts').fetchall()
        if row and datetime.fromisoformat(row[0]['database_utc_date']).date().isoformat()!=row[0]['database_utc_date']:
            raise ValueError('invalid stored UTC day')
        if (len(row)!=1 or row[0]['contract_name']!='research-full-v1' or row[0]['data_contract']!=CONTRACT
            or row[0]['runtime_job']!=RUNTIME or row[0]['schema_sha256']!=self.expected_schema
            or c.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID
            or fingerprint(c)!=self.expected_schema):raise ValueError('recorder schema/epoch mismatch')

    @staticmethod
    def insert(c,table,row):
        cols=list(row);c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(row[k] for k in cols))

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.c.execute('BEGIN IMMEDIATE');yield self.c;self.c.commit()
            except BaseException:self.c.rollback();raise

    def claim(self,slot,run_id,reference,config):
        with self.transaction() as c:
            row=c.execute('SELECT run_id FROM slot_claims WHERE slot_utc>=? ORDER BY slot_utc DESC LIMIT 1',(slot,)).fetchone()
            if row:return row[0]
            row=c.execute('SELECT owner_run_id FROM claim_carryovers WHERE slot_utc>=? ORDER BY slot_utc DESC LIMIT 1',(slot,)).fetchone()
            if row:return row[0]
            c.execute('INSERT INTO slot_claims VALUES(?,?,?,?)',(slot,run_id,reference,json.dumps(config,sort_keys=True)))
        return None

    def pending(self,now):
        return [dict(r) for r in self.c.execute("SELECT * FROM tracked_events INDEXED BY tracked_due_idx WHERE state IN ('SCHEDULED','WINDOW','WAIT_SETTLEMENT') AND next_due<=? ORDER BY next_due,event_id",(now,))]

    def close(self):self.c.close()

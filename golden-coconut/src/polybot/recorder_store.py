"""Create-only UTC shards with indexed working state and append-only evidence."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,os,sqlite3,tempfile,threading
from pathlib import Path
from .recorder_config import CONTRACT,RUNTIME
from polybot_observability import market_data_refs
from polybot_observability.market_data_refs import externalize_row
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_sqlite import connect as market_data_connect
from polybot_observability.market_data_raw_links import raw_layout_metadata, insert_raw_rows
from .recorder_shared_raw import RecorderSharedRaw

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
    def __init__(self,path,day,*,runtime_job=RUNTIME,references=None,raw_profile_id=None,raw_namespace=None):
        self.path=Path(path);self.day=day;self.runtime_job=runtime_job;self.lock=threading.RLock()
        self.references=references if references is not None else market_data_refs.configured_references()
        self._body_references=self.references if self.references.reader is not None or self.references.writer is not None else None
        self._shared_raw=RecorderSharedRaw(path,runtime_job,self.references,profile_id=raw_profile_id,namespace=raw_namespace)
        if datetime.fromisoformat(day).date().isoformat()!=day:raise ValueError('invalid UTC shard date')
        if self.path.name!='trades_sim.db' or self.path.is_symlink():raise ValueError('unsafe recorder database path')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        mem=sqlite3.connect(':memory:');mem.executescript(schema_sql());self.expected_schema=fingerprint(mem);mem.close()
        carry=[];source=None;source_sha=None;last_claim=None
        if self.path.exists():
            c=self._readonly(self.path)
            try:
                self._validate(c)
                self._shared_raw.check(c,write=True)
            except BaseException:
                c.close();raise
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
            self._create_shard(carry,last_claim,source)
        self.c=self._connect(self.path)
        try:
            self._validate(self.c)
            if self._shared_raw.enabled and raw_layout_metadata(self.c) is None:
                self.c.execute('BEGIN IMMEDIATE')
                self._shared_raw.initialize(self.c)
                self.c.commit()
        except BaseException:
            self.c.close();raise

    def _create_shard(self,carry,last_claim,source):
        # A failed public ACK or carry publication must not leave an empty or
        # half-initialized canonical shard that prevents the next cycle's retry.
        fd,name=tempfile.mkstemp(prefix='.recorder-init-',suffix='.db',dir=self.path.parent)
        os.close(fd);stage=Path(name);c=None
        try:
            c=self._connect(stage);c.executescript(schema_sql())
            c.execute(f'PRAGMA application_id={APPLICATION_ID}');c.execute('PRAGMA user_version=1')
            c.execute('INSERT INTO collection_contracts VALUES(1,?,?,?,?,?)',('research-full-v1',CONTRACT,self.day,self.runtime_job,self.expected_schema))
            self._shared_raw.initialize(c)
            if last_claim:
                serialized=json.dumps(last_claim,sort_keys=True)
                self.insert(c,'claim_carryovers',{'slot_utc':last_claim['slot_utc'],'owner_run_id':last_claim['run_id'],'source_shard':source.name,'state_json':serialized,'source_state_sha256':hashlib.sha256(serialized.encode()).hexdigest()})
            for row in carry:
                self.insert(c,'tracked_events',row)
                self.insert(c,'registry_carryovers',{'event_id':row['event_id'],'source_shard':source.name,'source_state_sha256':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest(),'state_json':json.dumps(row,sort_keys=True)})
            c.commit();c.close();c=None
            # Same-directory hard-link publication is atomic and refuses an
            # existing canonical path, unlike replace(). The staging link is
            # immediately removed below; the published DB has one link.
            os.link(stage,self.path)
        finally:
            if c is not None:c.close()
            stage.unlink(missing_ok=True)
            Path(str(stage)+'-journal').unlink(missing_ok=True)

    @staticmethod
    def _sha(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        return h.hexdigest()

    def _readonly(self,path):
        c=market_data_connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,references=self.references);c.row_factory=sqlite3.Row;return c

    def _connect(self,path):
        c=market_data_connect(path,timeout=3,check_same_thread=False,references=self.references);c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA journal_mode=DELETE');return c

    def _validate(self,c):
        metadata=raw_layout_metadata(c)
        if metadata is not None:self._shared_raw.check(c)
        row=c.execute('SELECT * FROM collection_contracts').fetchall()
        if row and datetime.fromisoformat(row[0]['database_utc_date']).date().isoformat()!=row[0]['database_utc_date']:
            raise ValueError('invalid stored UTC day')
        if (len(row)!=1 or row[0]['contract_name']!='research-full-v1' or row[0]['data_contract']!=CONTRACT
            or row[0]['runtime_job']!=self.runtime_job or row[0]['schema_sha256']!=self.expected_schema
            or c.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID
            or (metadata is None and fingerprint(c)!=self.expected_schema)):raise ValueError('recorder schema/epoch mismatch')

    def insert(self,c,table,row):
        context=self._receipt_context(c,table,row)
        namespace=self._shared_raw.namespace() if self._shared_raw.enabled else None
        if (self._shared_raw.enabled and table=='tracked_events'
                and c.execute('SELECT 1 FROM main.tracked_events WHERE event_id=?',(row['event_id'],)).fetchone()):
            raise sqlite3.IntegrityError('duplicate tracked event')
        if insert_raw_rows(c,'golden-coconut',table,[row],references=self.references,
                           namespace=namespace,receipt_context=context):return
        row=externalize_row('golden-coconut',table,row,references=self._body_references,receipt_context=context)
        cols=list(row);c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",tuple(row[k] for k in cols))

    def _receipt_context(self,c,table,row):
        if self.references.writer is None:return None
        # Source receipts exist only for actually retained source bodies. Fee
        # fragments or tracking-only updates do not invent observations.
        bodies={'requests':('raw_gzip',),'event_observations':('event_json','clock_json'),
                'book_observations':('book_gzip',),'clock_observations':('raw_gzip',)}
        if not any(row.get(column) is not None for column in bodies.get(table,())):return None
        if self._shared_raw.enabled:
            owner=json.loads(self._shared_raw.namespace())
            return ReceiptContext(owner['source'],self.runtime_job,owner['jenkins_job'],c)
        source=os.environ.get('PUBLIC_MARKET_DATA_SOURCE')
        if not source:raise ValueError('shared recorder requires PUBLIC_MARKET_DATA_SOURCE identity')
        return ReceiptContext(source,self.runtime_job,os.environ.get('JOB_NAME'),c)

    def update_tracked(self,c,row):
        if not c.execute('SELECT 1 FROM main.tracked_events WHERE event_id=?',(row['event_id'],)).fetchone():
            raise ValueError('recorder tracked event is absent')
        if self._shared_raw.enabled:
            insert_raw_rows(c,'golden-coconut','tracked_events',[row],references=self.references,
                            namespace=self._shared_raw.namespace())
        else:
            row=externalize_row('golden-coconut','tracked_events',row,references=self._body_references)
            keys=[key for key in row if key!='event_id']
            c.execute('UPDATE tracked_events SET '+','.join(key+'=?' for key in keys)+' WHERE event_id=?',
                      tuple(row[key] for key in keys)+(row['event_id'],))

    def demote_done(self,c,event_ids):
        # Only local lifecycle state changes; the already bound source and its
        # immutable receipt remain untouched, including in RAW storage.
        c.executemany("UPDATE main.tracked_events SET state='WAIT_SETTLEMENT' WHERE state='DONE' AND event_id=?",
                      ((event_id,) for event_id in event_ids))

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
        # Predicates remain on indexed private columns. INDEXED BY cannot name
        # the underlying table's index when this name is a logical RAW view.
        return [dict(r) for r in self.c.execute("SELECT * FROM tracked_events WHERE state IN ('SCHEDULED','WINDOW','WAIT_SETTLEMENT') AND next_due<=? ORDER BY next_due,event_id",(now,))]

    def close(self):self.c.close()

"""Reviewed independent Watermelon raw sidecar, separate from v401 parent.

Six original tables, two indexes, fifteen original append-only guards. Tracking
slots and terminal JSON use separate mixed public/private span contracts.
"""

SIDECAR_LOGICAL_SCHEMA_SHA256 = '590cc470ea312d0f586274aaee12578f8f877303afa9df7879c910d4c92965ed'


def validate_cycle_ownership(connection, namespace):
    """Deep immutable-source audit, never a per-row online metadata scan."""
    import json
    owner=json.loads(namespace)
    metadata=connection.execute('SELECT contract,parent_filename FROM main.raw_metadata').fetchone()
    expected=(*metadata,owner['runtime'])
    for row in connection.execute('SELECT DISTINCT contract,parent_filename,job_name FROM main.raw_cycles'):
        if tuple(row)!=expected:raise ValueError('RAW Watermelon sidecar cycle ownership differs')

SIDECAR_SCHEMA_OBJECTS = (('index',
  'raw_books_token_time_idx',
  'raw_books',
  'CREATE INDEX raw_books_token_time_idx ON raw_books(token_id,received_at)'),
 ('index',
  'raw_pending_idx',
  'raw_tracked_events',
  'CREATE INDEX raw_pending_idx ON raw_tracked_events(state,next_attempt_at,event_id)'),
 ('table',
  'raw_books',
  'raw_books',
  'CREATE TABLE raw_books(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT NULL,condition_id '
  'TEXT,token_id TEXT,outcome TEXT,status TEXT NOT NULL,request_id TEXT,requested_at TEXT,received_at '
  'TEXT,book_sha256 TEXT,source_kind TEXT NOT NULL,source_ref_json TEXT NOT '
  'NULL,point_in_time_identity_valid INTEGER NOT NULL,error_type TEXT,PRIMARY KEY(run_id,event_id,slot))'),
 ('table',
  'raw_cycles',
  'raw_cycles',
  'CREATE TABLE raw_cycles(run_id TEXT PRIMARY KEY,component_run_id TEXT NOT NULL UNIQUE,contract TEXT NOT '
  'NULL,parent_filename TEXT NOT NULL,config_hash TEXT NOT NULL,source_digest TEXT NOT NULL,job_name TEXT '
  'NOT NULL,reference_at TEXT NOT NULL,published_at TEXT NOT NULL,status TEXT NOT NULL,expected_events '
  'INTEGER NOT NULL,expected_tokens INTEGER NOT NULL,stats_json TEXT NOT NULL)'),
 ('table',
  'raw_events',
  'raw_events',
  'CREATE TABLE raw_events(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family TEXT NOT NULL,metadata_status '
  'TEXT NOT NULL,metadata_received_at TEXT,metadata_request_id TEXT,source_kind TEXT NOT '
  'NULL,source_ref_json TEXT NOT NULL,event_json TEXT,slots_json TEXT NOT NULL,identity_valid INTEGER NOT '
  'NULL,lifecycle_state TEXT NOT NULL,terminal_json TEXT,missing_count INTEGER NOT NULL,PRIMARY '
  'KEY(run_id,event_id))'),
 ('table',
  'raw_metadata',
  'raw_metadata',
  'CREATE TABLE raw_metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT '
  'NULL,schema_sha256 TEXT NOT NULL,parent_filename TEXT NOT NULL)'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads(payload_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,request_id '
  'TEXT,received_at TEXT,sha256 TEXT NOT NULL,payload_gzip BLOB NOT NULL)'),
 ('table',
  'raw_tracked_events',
  'raw_tracked_events',
  'CREATE TABLE raw_tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,state TEXT NOT '
  'NULL,slots_json TEXT NOT NULL,first_run_id TEXT NOT NULL,last_run_id TEXT NOT NULL,next_attempt_at TEXT '
  'NOT NULL,missing_count INTEGER NOT NULL,terminal_reason TEXT)'),
 ('trigger',
  'raw_books_no_delete',
  'raw_books',
  "CREATE TRIGGER raw_books_no_delete BEFORE DELETE ON raw_books BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_books_no_replace',
  'raw_books',
  'CREATE TRIGGER raw_books_no_replace BEFORE INSERT ON raw_books WHEN EXISTS(SELECT 1 FROM raw_books WHERE '
  "run_id=NEW.run_id AND event_id=NEW.event_id AND slot=NEW.slot) BEGIN SELECT RAISE(ABORT,'append-only "
  "duplicate'); END"),
 ('trigger',
  'raw_books_no_update',
  'raw_books',
  "CREATE TRIGGER raw_books_no_update BEFORE UPDATE ON raw_books BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_cycles_no_delete',
  'raw_cycles',
  "CREATE TRIGGER raw_cycles_no_delete BEFORE DELETE ON raw_cycles BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_cycles_no_replace',
  'raw_cycles',
  'CREATE TRIGGER raw_cycles_no_replace BEFORE INSERT ON raw_cycles WHEN EXISTS(SELECT 1 FROM raw_cycles '
  "WHERE (run_id=NEW.run_id) OR component_run_id=NEW.component_run_id) BEGIN SELECT RAISE(ABORT,'append-only "
  "duplicate'); END"),
 ('trigger',
  'raw_cycles_no_update',
  'raw_cycles',
  "CREATE TRIGGER raw_cycles_no_update BEFORE UPDATE ON raw_cycles BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_events_no_delete',
  'raw_events',
  "CREATE TRIGGER raw_events_no_delete BEFORE DELETE ON raw_events BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_events_no_replace',
  'raw_events',
  'CREATE TRIGGER raw_events_no_replace BEFORE INSERT ON raw_events WHEN EXISTS(SELECT 1 FROM raw_events '
  "WHERE run_id=NEW.run_id AND event_id=NEW.event_id) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); "
  'END'),
 ('trigger',
  'raw_events_no_update',
  'raw_events',
  "CREATE TRIGGER raw_events_no_update BEFORE UPDATE ON raw_events BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_metadata_no_delete',
  'raw_metadata',
  "CREATE TRIGGER raw_metadata_no_delete BEFORE DELETE ON raw_metadata BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_metadata_no_replace',
  'raw_metadata',
  'CREATE TRIGGER raw_metadata_no_replace BEFORE INSERT ON raw_metadata WHEN EXISTS(SELECT 1 FROM '
  "raw_metadata WHERE singleton=NEW.singleton) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'raw_metadata_no_update',
  'raw_metadata',
  "CREATE TRIGGER raw_metadata_no_update BEFORE UPDATE ON raw_metadata BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_payloads_no_delete',
  'raw_payloads',
  "CREATE TRIGGER raw_payloads_no_delete BEFORE DELETE ON raw_payloads BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"),
 ('trigger',
  'raw_payloads_no_replace',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_no_replace BEFORE INSERT ON raw_payloads WHEN EXISTS(SELECT 1 FROM '
  "raw_payloads WHERE payload_id=NEW.payload_id) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'raw_payloads_no_update',
  'raw_payloads',
  "CREATE TRIGGER raw_payloads_no_update BEFORE UPDATE ON raw_payloads BEGIN SELECT RAISE(ABORT,'append-only "
  "evidence'); END"))

SIDECAR_RAW_TABLES = {'raw_books': ('watermelon-sidecar-book-source-v1',
               'CREATE TABLE raw_books(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT '
               'NULL,condition_id TEXT,token_id TEXT,outcome TEXT,status TEXT NOT NULL,request_id '
               'TEXT,requested_at TEXT,received_at TEXT,book_sha256 TEXT,source_kind TEXT NOT '
               'NULL,source_ref_json TEXT NOT NULL,point_in_time_identity_valid INTEGER NOT NULL,error_type '
               'TEXT,PRIMARY KEY(run_id,event_id,slot))',
               ('event_id', 'condition_id', 'token_id', 'outcome'),
               ('event_id', 'condition_id', 'token_id'),
               'CREATE TABLE raw_books(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT '
               'NULL,condition_id TEXT,token_id TEXT,status TEXT NOT NULL,request_id TEXT,requested_at '
               'TEXT,received_at TEXT,book_sha256 TEXT,source_kind TEXT NOT NULL,source_ref_json TEXT NOT '
               'NULL,point_in_time_identity_valid INTEGER NOT NULL,error_type TEXT,_public_record_id INTEGER '
               'NOT NULL CHECK(_public_record_id>0),PRIMARY KEY(run_id,event_id,slot))')}

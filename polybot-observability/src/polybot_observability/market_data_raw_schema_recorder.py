"""Reviewed current Coconut recorder physical schema, separate from historical v6.

Ten original rowid tables, three explicit indexes and 27 original append-only
UPDATE/DELETE/duplicate guards. tracked_events is the sole mutable working cache.
Stored contract schema_sha256 remains the exact original native fingerprint.
"""

RECORDER_LOGICAL_SCHEMA_SHA256 = 'e4645288f42c6e87e556d54a01da9edc1d3be33d26fb08a111959a350be7c92b'

RECORDER_SCHEMA_OBJECTS = (('index',
  'books_time_idx',
  'book_observations',
  'CREATE INDEX books_time_idx ON book_observations(received_at,event_id,token_id)'),
 ('index', 'request_run_idx', 'requests', 'CREATE INDEX request_run_idx ON requests(run_id,request_id)'),
 ('index',
  'tracked_due_idx',
  'tracked_events',
  'CREATE INDEX tracked_due_idx ON tracked_events(state,next_due,event_id)'),
 ('table',
  'book_observations',
  'book_observations',
  'CREATE TABLE book_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT '
  'NULL,condition_id TEXT,token_id TEXT,outcome TEXT,result_kind TEXT,verified_role TEXT,team_name '
  'TEXT,status TEXT NOT NULL,request_id TEXT,requested_at TEXT,received_at TEXT,book_sha256 TEXT,book_gzip '
  'BLOB,fee_json TEXT NOT NULL,point_in_time_valid INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY '
  'KEY(run_id,event_id,slot))'),
 ('table',
  'claim_carryovers',
  'claim_carryovers',
  'CREATE TABLE claim_carryovers(slot_utc TEXT PRIMARY KEY,owner_run_id TEXT NOT NULL,source_shard TEXT NOT '
  'NULL,state_json TEXT NOT NULL,source_state_sha256 TEXT NOT NULL)'),
 ('table',
  'clock_observations',
  'clock_observations',
  'CREATE TABLE clock_observations(run_id TEXT NOT NULL,request_id TEXT NOT NULL,ordinal INTEGER NOT '
  'NULL,sha256 TEXT NOT NULL,received_at TEXT NOT NULL,raw_gzip BLOB NOT NULL,PRIMARY '
  'KEY(run_id,request_id,ordinal))'),
 ('table',
  'collection_contracts',
  'collection_contracts',
  'CREATE TABLE collection_contracts(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract_name TEXT NOT '
  'NULL,data_contract TEXT NOT NULL,database_utc_date TEXT NOT NULL,runtime_job TEXT NOT NULL,schema_sha256 '
  'TEXT NOT NULL)'),
 ('table',
  'cycles',
  'cycles',
  'CREATE TABLE cycles(run_id TEXT PRIMARY KEY,slot_utc TEXT NOT NULL UNIQUE,reference_at TEXT NOT '
  'NULL,published_at TEXT NOT NULL,status TEXT NOT NULL,config_json TEXT NOT NULL,stats_json TEXT NOT NULL)'),
 ('table',
  'event_observations',
  'event_observations',
  'CREATE TABLE event_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family TEXT NOT NULL,league '
  'TEXT,season_phase TEXT,metadata_status TEXT NOT NULL,request_id TEXT,received_at TEXT,event_json '
  'TEXT,clock_json TEXT,slots_json TEXT NOT NULL,identity_valid INTEGER NOT NULL,window_status TEXT NOT '
  'NULL,lifecycle_state TEXT NOT NULL,scheduled_start TEXT,end_anchor TEXT,end_basis TEXT,terminal_json '
  'TEXT,reason TEXT NOT NULL,PRIMARY KEY(run_id,event_id))'),
 ('table',
  'registry_carryovers',
  'registry_carryovers',
  'CREATE TABLE registry_carryovers(event_id TEXT PRIMARY KEY,source_shard TEXT NOT NULL,source_state_sha256 '
  'TEXT NOT NULL,state_json TEXT NOT NULL)'),
 ('table',
  'requests',
  'requests',
  'CREATE TABLE requests(attempt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,request_id TEXT NOT '
  'NULL,request_kind TEXT NOT NULL,started_at TEXT NOT NULL,received_at TEXT NOT NULL,status TEXT NOT '
  'NULL,http_status INTEGER,sha256 TEXT,raw_gzip BLOB,raw_complete INTEGER NOT NULL,receipt_json TEXT NOT '
  'NULL)'),
 ('table',
  'slot_claims',
  'slot_claims',
  'CREATE TABLE slot_claims(slot_utc TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,started_at TEXT NOT '
  'NULL,config_json TEXT NOT NULL)'),
 ('table',
  'tracked_events',
  'tracked_events',
  'CREATE TABLE tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,first_run_id TEXT NOT '
  'NULL,state TEXT NOT NULL,slots_json TEXT NOT NULL,scheduled_start TEXT,end_anchor TEXT,end_basis '
  'TEXT,ever_live INTEGER NOT NULL,terminal_json TEXT,next_due TEXT NOT NULL,missing_count INTEGER NOT '
  'NULL,anchor_json TEXT NOT NULL)'),
 ('trigger',
  'book_observations_delete',
  'book_observations',
  'CREATE TRIGGER book_observations_delete BEFORE DELETE ON book_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'book_observations_replace',
  'book_observations',
  'CREATE TRIGGER book_observations_replace BEFORE INSERT ON book_observations WHEN EXISTS(SELECT 1 FROM '
  'book_observations WHERE run_id=NEW.run_id AND event_id=NEW.event_id AND slot=NEW.slot) BEGIN SELECT '
  "RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'book_observations_update',
  'book_observations',
  'CREATE TRIGGER book_observations_update BEFORE UPDATE ON book_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'claim_carryovers_delete',
  'claim_carryovers',
  'CREATE TRIGGER claim_carryovers_delete BEFORE DELETE ON claim_carryovers BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'claim_carryovers_replace',
  'claim_carryovers',
  'CREATE TRIGGER claim_carryovers_replace BEFORE INSERT ON claim_carryovers WHEN EXISTS(SELECT 1 FROM '
  "claim_carryovers WHERE slot_utc=NEW.slot_utc) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'claim_carryovers_update',
  'claim_carryovers',
  'CREATE TRIGGER claim_carryovers_update BEFORE UPDATE ON claim_carryovers BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'clock_observations_delete',
  'clock_observations',
  'CREATE TRIGGER clock_observations_delete BEFORE DELETE ON clock_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'clock_observations_replace',
  'clock_observations',
  'CREATE TRIGGER clock_observations_replace BEFORE INSERT ON clock_observations WHEN EXISTS(SELECT 1 FROM '
  'clock_observations WHERE run_id=NEW.run_id AND request_id=NEW.request_id AND ordinal=NEW.ordinal) BEGIN '
  "SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'clock_observations_update',
  'clock_observations',
  'CREATE TRIGGER clock_observations_update BEFORE UPDATE ON clock_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'collection_contracts_delete',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_delete BEFORE DELETE ON collection_contracts BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'collection_contracts_replace',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_replace BEFORE INSERT ON collection_contracts WHEN EXISTS(SELECT 1 '
  "FROM collection_contracts WHERE singleton=NEW.singleton) BEGIN SELECT RAISE(ABORT,'append-only "
  "duplicate'); END"),
 ('trigger',
  'collection_contracts_update',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_update BEFORE UPDATE ON collection_contracts BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'cycles_delete',
  'cycles',
  "CREATE TRIGGER cycles_delete BEFORE DELETE ON cycles BEGIN SELECT RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'cycles_replace',
  'cycles',
  'CREATE TRIGGER cycles_replace BEFORE INSERT ON cycles WHEN EXISTS(SELECT 1 FROM cycles WHERE '
  "(run_id=NEW.run_id) OR slot_utc=NEW.slot_utc) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'cycles_update',
  'cycles',
  "CREATE TRIGGER cycles_update BEFORE UPDATE ON cycles BEGIN SELECT RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'event_observations_delete',
  'event_observations',
  'CREATE TRIGGER event_observations_delete BEFORE DELETE ON event_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'event_observations_replace',
  'event_observations',
  'CREATE TRIGGER event_observations_replace BEFORE INSERT ON event_observations WHEN EXISTS(SELECT 1 FROM '
  'event_observations WHERE run_id=NEW.run_id AND event_id=NEW.event_id) BEGIN SELECT '
  "RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'event_observations_update',
  'event_observations',
  'CREATE TRIGGER event_observations_update BEFORE UPDATE ON event_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'registry_carryovers_delete',
  'registry_carryovers',
  'CREATE TRIGGER registry_carryovers_delete BEFORE DELETE ON registry_carryovers BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'registry_carryovers_replace',
  'registry_carryovers',
  'CREATE TRIGGER registry_carryovers_replace BEFORE INSERT ON registry_carryovers WHEN EXISTS(SELECT 1 FROM '
  "registry_carryovers WHERE event_id=NEW.event_id) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'registry_carryovers_update',
  'registry_carryovers',
  'CREATE TRIGGER registry_carryovers_update BEFORE UPDATE ON registry_carryovers BEGIN SELECT '
  "RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'requests_delete',
  'requests',
  "CREATE TRIGGER requests_delete BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'requests_replace',
  'requests',
  'CREATE TRIGGER requests_replace BEFORE INSERT ON requests WHEN EXISTS(SELECT 1 FROM requests WHERE '
  "attempt_id=NEW.attempt_id) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END"),
 ('trigger',
  'requests_update',
  'requests',
  "CREATE TRIGGER requests_update BEFORE UPDATE ON requests BEGIN SELECT RAISE(ABORT,'append-only'); END"),
 ('trigger',
  'slot_claims_delete',
  'slot_claims',
  "CREATE TRIGGER slot_claims_delete BEFORE DELETE ON slot_claims BEGIN SELECT RAISE(ABORT,'append-only'); "
  'END'),
 ('trigger',
  'slot_claims_replace',
  'slot_claims',
  'CREATE TRIGGER slot_claims_replace BEFORE INSERT ON slot_claims WHEN EXISTS(SELECT 1 FROM slot_claims '
  "WHERE (slot_utc=NEW.slot_utc) OR run_id=NEW.run_id) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); "
  'END'),
 ('trigger',
  'slot_claims_update',
  'slot_claims',
  "CREATE TRIGGER slot_claims_update BEFORE UPDATE ON slot_claims BEGIN SELECT RAISE(ABORT,'append-only'); "
  'END'))

RECORDER_RAW_TABLES = {'tracked_events': ('coconut-recorder-tracked-events-v1',
                    'CREATE TABLE tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,first_run_id '
                    'TEXT NOT NULL,state TEXT NOT NULL,slots_json TEXT NOT NULL,scheduled_start '
                    'TEXT,end_anchor TEXT,end_basis TEXT,ever_live INTEGER NOT NULL,terminal_json '
                    'TEXT,next_due TEXT NOT NULL,missing_count INTEGER NOT NULL,anchor_json TEXT NOT NULL)',
                    ('event_id', 'scheduled_start'),
                    ('event_id',),
                    'CREATE TABLE tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,first_run_id '
                    'TEXT NOT NULL,state TEXT NOT NULL,slots_json TEXT NOT NULL,end_anchor TEXT,end_basis '
                    'TEXT,ever_live INTEGER NOT NULL,terminal_json TEXT,next_due TEXT NOT NULL,missing_count '
                    'INTEGER NOT NULL,anchor_json TEXT NOT NULL,_public_record_id INTEGER NOT NULL '
                    'CHECK(_public_record_id>0))'),
 'event_observations': ('coconut-recorder-event-observations-v1',
                        'CREATE TABLE event_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family '
                        'TEXT NOT NULL,league TEXT,season_phase TEXT,metadata_status TEXT NOT '
                        'NULL,request_id TEXT,received_at TEXT,event_json TEXT,clock_json TEXT,slots_json '
                        'TEXT NOT NULL,identity_valid INTEGER NOT NULL,window_status TEXT NOT '
                        'NULL,lifecycle_state TEXT NOT NULL,scheduled_start TEXT,end_anchor TEXT,end_basis '
                        'TEXT,terminal_json TEXT,reason TEXT NOT NULL,PRIMARY KEY(run_id,event_id))',
                        ('event_id', 'scheduled_start'),
                        ('event_id',),
                        'CREATE TABLE event_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family '
                        'TEXT NOT NULL,league TEXT,season_phase TEXT,metadata_status TEXT NOT '
                        'NULL,request_id TEXT,received_at TEXT,event_json TEXT,clock_json TEXT,slots_json '
                        'TEXT NOT NULL,identity_valid INTEGER NOT NULL,window_status TEXT NOT '
                        'NULL,lifecycle_state TEXT NOT NULL,end_anchor TEXT,end_basis TEXT,terminal_json '
                        'TEXT,reason TEXT NOT NULL,_public_record_id INTEGER NOT NULL '
                        'CHECK(_public_record_id>0),PRIMARY KEY(run_id,event_id))'),
 'book_observations': ('coconut-recorder-book-observations-v1',
                       'CREATE TABLE book_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT '
                       'NOT NULL,condition_id TEXT,token_id TEXT,outcome TEXT,result_kind TEXT,verified_role '
                       'TEXT,team_name TEXT,status TEXT NOT NULL,request_id TEXT,requested_at '
                       'TEXT,received_at TEXT,book_sha256 TEXT,book_gzip BLOB,fee_json TEXT NOT '
                       'NULL,point_in_time_valid INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY '
                       'KEY(run_id,event_id,slot))',
                       ('event_id', 'condition_id', 'token_id', 'outcome', 'team_name'),
                       ('event_id', 'condition_id', 'token_id'),
                       'CREATE TABLE book_observations(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT '
                       'NOT NULL,condition_id TEXT,token_id TEXT,result_kind TEXT,verified_role TEXT,status '
                       'TEXT NOT NULL,request_id TEXT,requested_at TEXT,received_at TEXT,book_sha256 '
                       'TEXT,book_gzip BLOB,fee_json TEXT NOT NULL,point_in_time_valid INTEGER NOT '
                       'NULL,reason TEXT NOT NULL,_public_record_id INTEGER NOT NULL '
                       'CHECK(_public_record_id>0),PRIMARY KEY(run_id,event_id,slot))')}

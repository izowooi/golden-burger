"""Reviewed Guava research v1 complete original schema and two RAW parents.

Frozen from the native producer's _SCHEMA and _triggers: 10 tables, 5 indexes,
33 original guards. The token array remains in public body CAS, not the scalar
record. Neither observation timestamp is a provider source clock.
"""

GUAVA_LOGICAL_SCHEMA_SHA256 = 'aa7e0b934c394aff8d16d550a3b4e89b56234016a417188076f5e8589bdcb43f'

GUAVA_SCHEMA_OBJECTS = (('index',
  'cycles_cohort_latest',
  'cycles',
  'CREATE INDEX cycles_cohort_latest ON cycles(cohort_key,observed_at DESC)'),
 ('index', 'cycles_latest', 'cycles', 'CREATE INDEX cycles_latest ON cycles(observed_at DESC)'),
 ('index',
  'run_audits_cohort_latest',
  'run_audits',
  'CREATE INDEX run_audits_cohort_latest ON run_audits(cohort_key,started_at DESC,run_id DESC)'),
 ('index',
  'run_audits_latest',
  'run_audits',
  'CREATE INDEX run_audits_latest ON run_audits(started_at DESC,run_id DESC)'),
 ('index',
  'run_events_terminal',
  'run_events',
  "CREATE UNIQUE INDEX run_events_terminal ON run_events(run_id) WHERE status!='STARTED'"),
 ('table',
  'book_attempts',
  'book_attempts',
  'CREATE TABLE book_attempts (\n'
  '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, token_id TEXT NOT NULL,\n'
  '        condition_id TEXT, result_kind TEXT, outcome_side TEXT,\n'
  '        observed_at TEXT NOT NULL, request_id TEXT, status TEXT NOT NULL,\n'
  '        book_json TEXT NOT NULL, raw_gzip BLOB, raw_sha256 TEXT,\n'
  '        fee_evidence_json TEXT NOT NULL, depth_metrics_json TEXT NOT NULL,\n'
  '        CHECK((raw_gzip IS NULL) = (raw_sha256 IS NULL)),\n'
  '        PRIMARY KEY(run_id,event_id,token_id),\n'
  '        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id),\n'
  '        FOREIGN KEY(run_id,request_id) REFERENCES source_requests(run_id,request_id)\n'
  '    )'),
 ('table',
  'collection_contracts',
  'collection_contracts',
  'CREATE TABLE collection_contracts (\n'
  '        singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n'
  '        schema_version INTEGER NOT NULL CHECK(schema_version=1),\n'
  "        contract_name TEXT NOT NULL CHECK(contract_name='guava-research-v1'),\n"
  "        data_contract TEXT NOT NULL CHECK(data_contract='guava-research-v1'),\n"
  '        database_utc_date TEXT CHECK(database_utc_date IS NULL),\n'
  "        strategy_name TEXT NOT NULL CHECK(strategy_name='golden-guava'),\n"
  "        job_name TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='sim'),\n"
  '        identity_json TEXT NOT NULL\n'
  '    )'),
 ('table',
  'cycles',
  'cycles',
  'CREATE TABLE cycles (\n'
  '        run_id TEXT PRIMARY KEY NOT NULL, cohort_key TEXT NOT NULL,\n'
  '        observed_at TEXT NOT NULL, summary_json TEXT NOT NULL,\n'
  '        UNIQUE(run_id,cohort_key),\n'
  '        FOREIGN KEY(run_id,cohort_key) REFERENCES run_audits(run_id,cohort_key)\n'
  '    )'),
 ('table',
  'events',
  'events',
  'CREATE TABLE events (\n'
  '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, cohort_key TEXT NOT NULL,\n'
  '        sport_family TEXT, league_code TEXT, observed_at TEXT NOT NULL,\n'
  '        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), exclusion_reason TEXT,\n'
  '        expected_token_ids_json TEXT NOT NULL, clock_json TEXT NOT NULL,\n'
  '        event_json TEXT NOT NULL, raw_gzip BLOB NOT NULL, raw_sha256 TEXT NOT NULL,\n'
  '        PRIMARY KEY(run_id,event_id), UNIQUE(run_id,event_id,cohort_key),\n'
  '        FOREIGN KEY(run_id,cohort_key) REFERENCES cycles(run_id,cohort_key)\n'
  '    )'),
 ('table',
  'features',
  'features',
  'CREATE TABLE features (\n'
  '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, hypothesis_id TEXT NOT NULL,\n'
  '        observed_at TEXT NOT NULL, applicable INTEGER NOT NULL CHECK(applicable IN (0,1)),\n'
  '        reason TEXT, metrics_json TEXT NOT NULL, feature_json TEXT NOT NULL,\n'
  '        PRIMARY KEY(run_id,event_id,hypothesis_id),\n'
  '        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id)\n'
  '    )'),
 ('table',
  'latest_event_state',
  'latest_event_state',
  'CREATE TABLE latest_event_state (\n'
  '        cohort_key TEXT NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL,\n'
  '        observed_at TEXT NOT NULL, PRIMARY KEY(cohort_key,event_id),\n'
  '        FOREIGN KEY(run_id,event_id,cohort_key) REFERENCES events(run_id,event_id,cohort_key)\n'
  '    )'),
 ('table',
  'run_audits',
  'run_audits',
  'CREATE TABLE run_audits (\n'
  '        run_id TEXT PRIMARY KEY NOT NULL, started_at TEXT NOT NULL,\n'
  '        strategy_name TEXT NOT NULL, job_name TEXT NOT NULL, mode TEXT NOT NULL '
  "CHECK(mode='sim'),\n"
  "        data_contract TEXT NOT NULL CHECK(data_contract='guava-research-v1'),\n"
  '        config_hash TEXT NOT NULL,\n'
  '        strategy_source_digest TEXT NOT NULL, cohort_key TEXT NOT NULL,\n'
  '        contract_json TEXT NOT NULL, UNIQUE(run_id,cohort_key),\n'
  '        FOREIGN KEY(config_hash,strategy_source_digest)\n'
  '            REFERENCES strategy_configs(config_hash,strategy_source_digest)\n'
  '    )'),
 ('table',
  'run_events',
  'run_events',
  'CREATE TABLE run_events (\n'
  '        run_id TEXT NOT NULL REFERENCES run_audits(run_id),\n'
  "        status TEXT NOT NULL CHECK(status IN ('STARTED','FAILED','SUCCEEDED')),\n"
  '        occurred_at TEXT NOT NULL, error_type TEXT, phase TEXT,\n'
  '        PRIMARY KEY(run_id,status)\n'
  '    )'),
 ('table',
  'source_requests',
  'source_requests',
  'CREATE TABLE source_requests (\n'
  '        run_id TEXT NOT NULL REFERENCES run_audits(run_id), request_id TEXT NOT NULL,\n'
  '        source TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,\n'
  '        params_json TEXT NOT NULL, started_at TEXT NOT NULL, received_at TEXT,\n'
  '        status, error_type TEXT, receipt_json TEXT NOT NULL,\n'
  '        payload_gzip BLOB, payload_sha256 TEXT,\n'
  '        CHECK((payload_gzip IS NULL) = (payload_sha256 IS NULL)),\n'
  '        PRIMARY KEY(run_id,request_id)\n'
  '    )'),
 ('table',
  'strategy_configs',
  'strategy_configs',
  'CREATE TABLE strategy_configs (\n'
  '        config_hash TEXT NOT NULL, strategy_source_digest TEXT NOT NULL,\n'
  '        config_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, first_observed_at TEXT NOT '
  'NULL,\n'
  '        PRIMARY KEY(config_hash,strategy_source_digest)\n'
  '    )'),
 ('trigger',
  'book_attempts_no_delete',
  'book_attempts',
  'CREATE TRIGGER book_attempts_no_delete\n'
  '                BEFORE DELETE ON book_attempts BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'book_attempts_no_replace',
  'book_attempts',
  'CREATE TRIGGER book_attempts_no_replace BEFORE INSERT ON book_attempts\n'
  '            WHEN EXISTS(SELECT 1 FROM book_attempts WHERE run_id=NEW.run_id AND '
  'event_id=NEW.event_id AND token_id=NEW.token_id) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'book_attempts_no_update',
  'book_attempts',
  'CREATE TRIGGER book_attempts_no_update\n'
  '                BEFORE UPDATE ON book_attempts BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'book_attempts_open_run',
  'book_attempts',
  'CREATE TRIGGER book_attempts_open_run BEFORE INSERT ON book_attempts\n'
  '            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status='STARTED')\n"
  '                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status!='STARTED')\n"
  "            BEGIN SELECT RAISE(ABORT,'run is not open'); END"),
 ('trigger',
  'collection_contracts_no_delete',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_no_delete\n'
  '                BEFORE DELETE ON collection_contracts BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'collection_contracts_no_replace',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_no_replace BEFORE INSERT ON collection_contracts\n'
  '            WHEN EXISTS(SELECT 1 FROM collection_contracts WHERE singleton=NEW.singleton) '
  'BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'collection_contracts_no_update',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_no_update\n'
  '                BEFORE UPDATE ON collection_contracts BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'cycles_no_delete',
  'cycles',
  'CREATE TRIGGER cycles_no_delete\n'
  '                BEFORE DELETE ON cycles BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'cycles_no_replace',
  'cycles',
  'CREATE TRIGGER cycles_no_replace BEFORE INSERT ON cycles\n'
  '            WHEN EXISTS(SELECT 1 FROM cycles WHERE run_id=NEW.run_id) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'cycles_no_update',
  'cycles',
  'CREATE TRIGGER cycles_no_update\n'
  '                BEFORE UPDATE ON cycles BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'cycles_open_run',
  'cycles',
  'CREATE TRIGGER cycles_open_run BEFORE INSERT ON cycles\n'
  '            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status='STARTED')\n"
  '                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status!='STARTED')\n"
  "            BEGIN SELECT RAISE(ABORT,'run is not open'); END"),
 ('trigger',
  'events_no_delete',
  'events',
  'CREATE TRIGGER events_no_delete\n'
  '                BEFORE DELETE ON events BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'events_no_replace',
  'events',
  'CREATE TRIGGER events_no_replace BEFORE INSERT ON events\n'
  '            WHEN EXISTS(SELECT 1 FROM events WHERE run_id=NEW.run_id AND event_id=NEW.event_id) '
  'BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'events_no_update',
  'events',
  'CREATE TRIGGER events_no_update\n'
  '                BEFORE UPDATE ON events BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'events_open_run',
  'events',
  'CREATE TRIGGER events_open_run BEFORE INSERT ON events\n'
  '            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status='STARTED')\n"
  '                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status!='STARTED')\n"
  "            BEGIN SELECT RAISE(ABORT,'run is not open'); END"),
 ('trigger',
  'features_no_delete',
  'features',
  'CREATE TRIGGER features_no_delete\n'
  '                BEFORE DELETE ON features BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'features_no_replace',
  'features',
  'CREATE TRIGGER features_no_replace BEFORE INSERT ON features\n'
  '            WHEN EXISTS(SELECT 1 FROM features WHERE run_id=NEW.run_id AND '
  'event_id=NEW.event_id AND hypothesis_id=NEW.hypothesis_id) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'features_no_update',
  'features',
  'CREATE TRIGGER features_no_update\n'
  '                BEFORE UPDATE ON features BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'features_open_run',
  'features',
  'CREATE TRIGGER features_open_run BEFORE INSERT ON features\n'
  '            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status='STARTED')\n"
  '                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status!='STARTED')\n"
  "            BEGIN SELECT RAISE(ABORT,'run is not open'); END"),
 ('trigger',
  'run_audits_no_delete',
  'run_audits',
  'CREATE TRIGGER run_audits_no_delete\n'
  '                BEFORE DELETE ON run_audits BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'run_audits_no_replace',
  'run_audits',
  'CREATE TRIGGER run_audits_no_replace BEFORE INSERT ON run_audits\n'
  '            WHEN EXISTS(SELECT 1 FROM run_audits WHERE run_id=NEW.run_id) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'run_audits_no_update',
  'run_audits',
  'CREATE TRIGGER run_audits_no_update\n'
  '                BEFORE UPDATE ON run_audits BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'run_events_lifecycle',
  'run_events',
  'CREATE TRIGGER run_events_lifecycle BEFORE INSERT ON run_events BEGIN\n'
  "        SELECT CASE WHEN NEW.status='STARTED' AND EXISTS(\n"
  '            SELECT 1 FROM run_events WHERE run_id=NEW.run_id\n'
  "        ) THEN RAISE(ABORT,'run already started') END;\n"
  "        SELECT CASE WHEN NEW.status!='STARTED' AND (\n"
  "            NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status='STARTED')\n"
  "            OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status!='STARTED')\n"
  "        ) THEN RAISE(ABORT,'run is not open') END;\n"
  "        SELECT CASE WHEN NEW.status='SUCCEEDED' AND NOT EXISTS(\n"
  '            SELECT 1 FROM cycles WHERE run_id=NEW.run_id\n'
  "        ) THEN RAISE(ABORT,'success requires a published cycle') END;\n"
  "        SELECT CASE WHEN NEW.status='FAILED' AND EXISTS(\n"
  '            SELECT 1 FROM cycles WHERE run_id=NEW.run_id\n'
  "        ) THEN RAISE(ABORT,'published cycle cannot fail') END;\n"
  '    END'),
 ('trigger',
  'run_events_no_delete',
  'run_events',
  'CREATE TRIGGER run_events_no_delete\n'
  '                BEFORE DELETE ON run_events BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'run_events_no_replace',
  'run_events',
  'CREATE TRIGGER run_events_no_replace BEFORE INSERT ON run_events\n'
  '            WHEN EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status=NEW.status) '
  'BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'run_events_no_update',
  'run_events',
  'CREATE TRIGGER run_events_no_update\n'
  '                BEFORE UPDATE ON run_events BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'source_requests_no_delete',
  'source_requests',
  'CREATE TRIGGER source_requests_no_delete\n'
  '                BEFORE DELETE ON source_requests BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'source_requests_no_replace',
  'source_requests',
  'CREATE TRIGGER source_requests_no_replace BEFORE INSERT ON source_requests\n'
  '            WHEN EXISTS(SELECT 1 FROM source_requests WHERE run_id=NEW.run_id AND '
  'request_id=NEW.request_id) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'source_requests_no_update',
  'source_requests',
  'CREATE TRIGGER source_requests_no_update\n'
  '                BEFORE UPDATE ON source_requests BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'source_requests_open_run',
  'source_requests',
  'CREATE TRIGGER source_requests_open_run BEFORE INSERT ON source_requests\n'
  '            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status='STARTED')\n"
  '                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND '
  "status!='STARTED')\n"
  "            BEGIN SELECT RAISE(ABORT,'run is not open'); END"),
 ('trigger',
  'strategy_configs_no_delete',
  'strategy_configs',
  'CREATE TRIGGER strategy_configs_no_delete\n'
  '                BEFORE DELETE ON strategy_configs BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"),
 ('trigger',
  'strategy_configs_no_replace',
  'strategy_configs',
  'CREATE TRIGGER strategy_configs_no_replace BEFORE INSERT ON strategy_configs\n'
  '            WHEN EXISTS(SELECT 1 FROM strategy_configs WHERE config_hash=NEW.config_hash AND '
  'strategy_source_digest=NEW.strategy_source_digest) BEGIN\n'
  "            SELECT RAISE(ABORT,'duplicate append-only evidence'); END"),
 ('trigger',
  'strategy_configs_no_update',
  'strategy_configs',
  'CREATE TRIGGER strategy_configs_no_update\n'
  '                BEFORE UPDATE ON strategy_configs BEGIN\n'
  "                SELECT RAISE(ABORT,'append-only evidence'); END"))

GUAVA_RAW_TABLES = {'events': ('guava-event-source-v1',
            'CREATE TABLE events (\n'
            '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, cohort_key TEXT NOT NULL,\n'
            '        sport_family TEXT, league_code TEXT, observed_at TEXT NOT NULL,\n'
            '        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), exclusion_reason TEXT,\n'
            '        expected_token_ids_json TEXT NOT NULL, clock_json TEXT NOT NULL,\n'
            '        event_json TEXT NOT NULL, raw_gzip BLOB NOT NULL, raw_sha256 TEXT NOT NULL,\n'
            '        PRIMARY KEY(run_id,event_id), UNIQUE(run_id,event_id,cohort_key),\n'
            '        FOREIGN KEY(run_id,cohort_key) REFERENCES cycles(run_id,cohort_key)\n'
            '    )',
            ('event_id', 'sport_family', 'league_code'),
            ('event_id',),
            'CREATE TABLE events (\n'
            '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, cohort_key TEXT NOT NULL,\n'
            '        observed_at TEXT NOT NULL,\n'
            '        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), exclusion_reason TEXT,\n'
            '        expected_token_ids_json TEXT NOT NULL, clock_json TEXT NOT NULL,\n'
            '        event_json TEXT NOT NULL, raw_gzip BLOB NOT NULL, raw_sha256 TEXT NOT NULL,\n'
            '        _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
            '        PRIMARY KEY(run_id,event_id), UNIQUE(run_id,event_id,cohort_key),\n'
            '        FOREIGN KEY(run_id,cohort_key) REFERENCES cycles(run_id,cohort_key)\n'
            '    )'),
 'book_attempts': ('guava-book-source-v1',
                   'CREATE TABLE book_attempts (\n'
                   '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, token_id TEXT NOT NULL,\n'
                   '        condition_id TEXT, result_kind TEXT, outcome_side TEXT,\n'
                   '        observed_at TEXT NOT NULL, request_id TEXT, status TEXT NOT NULL,\n'
                   '        book_json TEXT NOT NULL, raw_gzip BLOB, raw_sha256 TEXT,\n'
                   '        fee_evidence_json TEXT NOT NULL, depth_metrics_json TEXT NOT NULL,\n'
                   '        CHECK((raw_gzip IS NULL) = (raw_sha256 IS NULL)),\n'
                   '        PRIMARY KEY(run_id,event_id,token_id),\n'
                   '        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id),\n'
                   '        FOREIGN KEY(run_id,request_id) REFERENCES '
                   'source_requests(run_id,request_id)\n'
                   '    )',
                   ('event_id', 'token_id', 'condition_id'),
                   ('event_id', 'token_id', 'condition_id'),
                   'CREATE TABLE book_attempts (\n'
                   '        run_id TEXT NOT NULL, event_id TEXT NOT NULL, token_id TEXT NOT NULL,\n'
                   '        condition_id TEXT, result_kind TEXT, outcome_side TEXT,\n'
                   '        observed_at TEXT NOT NULL, request_id TEXT, status TEXT NOT NULL,\n'
                   '        book_json TEXT NOT NULL, raw_gzip BLOB, raw_sha256 TEXT,\n'
                   '        fee_evidence_json TEXT NOT NULL, depth_metrics_json TEXT NOT NULL,\n'
                   '        _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                   '        CHECK((raw_gzip IS NULL) = (raw_sha256 IS NULL)),\n'
                   '        PRIMARY KEY(run_id,event_id,token_id),\n'
                   '        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id),\n'
                   '        FOREIGN KEY(run_id,request_id) REFERENCES '
                   'source_requests(run_id,request_id)\n'
                   '    )')}

"""Reviewed Golden Black RAW source schema; no other collector is inferred."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from functools import lru_cache
import sqlite3
from types import MappingProxyType

from .market_data_projection_profiles import ProjectionField, ProjectionProfile


@dataclass(frozen=True)
class RawTableProfile:
    table: str
    kind: str
    source_sql: str
    public_columns: tuple[str, ...]
    identity_columns: tuple[str, ...]
    skeleton_source_sql: str | None = None
    condition_field: str | None = None
    token_field: str | None = None
    source_time_field: str | None = None

    @property
    def info(self):
        return source_info(self.source_sql, self.table)

    @property
    def columns(self):
        return tuple(row[1] for row in self.info)

    @property
    def private_columns(self):
        return tuple(name for name in self.columns if name not in self.public_columns or name in self.identity_columns)

    @property
    def primary_key_columns(self):
        return tuple(row[1] for row in sorted(self.info, key=lambda row:row[5]) if row[5])

    @property
    def primary_key(self):
        if len(self.primary_key_columns) != 1:
            raise ValueError("RAW table has a composite primary key")
        return self.primary_key_columns[0]


RAW_TABLES = MappingProxyType({
    'market_observations': RawTableProfile('market_observations', 'black-market-source-v1',
        'CREATE TABLE IF NOT EXISTS market_observations (\n    observation_id TEXT PRIMARY KEY,\n    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n    run_id TEXT NOT NULL,\n    event_id TEXT NOT NULL,\n    event_title TEXT,\n    condition_id TEXT,\n    market_id TEXT,\n    question TEXT,\n    observed_at TEXT NOT NULL,\n    end_date TEXT,\n    game_start_time TEXT,\n    hours_until_end REAL,\n    sports_phase TEXT NOT NULL,\n    liquidity REAL,\n    volume_total REAL,\n    active INTEGER,\n    closed INTEGER,\n    accepting_orders INTEGER,\n    enable_order_book INTEGER,\n    neg_risk INTEGER,\n    fee_rate REAL,\n    fee_schedule_json TEXT NOT NULL,\n    outcome_labels_json TEXT NOT NULL,\n    token_ids_json TEXT NOT NULL,\n    outcome_prices_json TEXT NOT NULL,\n    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n    exclusion_reason TEXT NOT NULL,\n    normalized_json TEXT NOT NULL,\n    UNIQUE (sweep_id, event_id, condition_id)\n)',
        ('event_id', 'condition_id', 'event_title', 'market_id', 'question', 'end_date', 'game_start_time', 'liquidity', 'volume_total', 'active', 'closed', 'accepting_orders', 'enable_order_book', 'neg_risk', 'fee_schedule_json', 'outcome_labels_json', 'token_ids_json', 'outcome_prices_json'), ('event_id', 'condition_id')),
    'outcome_observations': RawTableProfile('outcome_observations', 'black-outcome-source-v1',
        'CREATE TABLE IF NOT EXISTS outcome_observations (\n    outcome_observation_id TEXT PRIMARY KEY,\n    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),\n    sweep_id TEXT NOT NULL,\n    run_id TEXT NOT NULL,\n    condition_id TEXT NOT NULL,\n    event_id TEXT NOT NULL,\n    token_id TEXT NOT NULL,\n    outcome_index INTEGER NOT NULL,\n    outcome_label TEXT NOT NULL,\n    gamma_probability REAL,\n    observed_at TEXT NOT NULL,\n    UNIQUE (sweep_id, token_id)\n)',
        ('condition_id', 'event_id', 'token_id', 'outcome_index', 'outcome_label', 'gamma_probability'), ('condition_id', 'event_id', 'token_id')),
    'orderbook_snapshots': RawTableProfile('orderbook_snapshots', 'black-book-source-v1',
        'CREATE TABLE IF NOT EXISTS orderbook_snapshots (\n    snapshot_id TEXT PRIMARY KEY,\n    run_id TEXT NOT NULL,\n    token_id TEXT NOT NULL,\n    request_id TEXT NOT NULL,\n    observed_at TEXT NOT NULL,\n    raw_book_sha256 TEXT NOT NULL,\n    best_bid REAL,\n    best_ask REAL,\n    bid_level_count INTEGER NOT NULL,\n    ask_level_count INTEGER NOT NULL,\n    source_timestamp TEXT,\n    tick_size REAL,\n    min_order_size REAL,\n    UNIQUE (run_id, token_id)\n)',
        ('token_id', 'best_bid', 'best_ask', 'bid_level_count', 'ask_level_count', 'source_timestamp', 'tick_size', 'min_order_size'), ('token_id',)),
})


@lru_cache(maxsize=32)
def source_info(source_sql, table):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(source_sql)
        return tuple(db.execute('PRAGMA table_info("' + table + '")'))


def _public_profile(profile):
    by_name = {row[1]: row for row in profile.info}
    fields = tuple(ProjectionField(name, by_name[name][2], not bool(by_name[name][3]),
                                  64 << 20 if name.endswith("_json") or name in {"question", "event_title", "outcome_label"} else 4096)
                   for name in profile.public_columns)
    return ProjectionProfile(profile.kind, fields,
        condition_field=profile.condition_field or ("condition_id" if "condition_id" in profile.public_columns else None),
        token_field=profile.token_field or ("token_id" if "token_id" in profile.public_columns else None),
        source_time_field=profile.source_time_field or ("source_timestamp" if "source_timestamp" in profile.public_columns else None))


STRATEGY = "golden-black"
BLACK_PROFILE_ID = "black-research-paired-v1"
BLACK_FULL_PROFILE_ID = "black-research-full-v2"
WATERMELON_PROFILE_ID = "watermelon-research-v401"
COCONUT_PROFILE_ID = "coconut-historical-v6"
POMEGRANATE_PROFILE_ID = "pomegranate-research-full-v4"
RASPBERRY_PROFILE_ID = "raspberry-queue-echo-v3"
STRAWBERRY_V1_PROFILE_ID = "strawberry-last-mile-v1"
STRAWBERRY_FOLLOWUP_PROFILE_ID = "strawberry-followup-v2a-v4"
GUAVA_PROFILE_ID = "guava-research-v1"
COCONUT_RECORDER_PROFILE_ID = "coconut-recorder-v1"
CHERRY_SHADOW_PROFILE_ID = "cherry-shadow-resolution-v2"
WATERMELON_SIDECAR_PROFILE_ID = "watermelon-independent-raw-lifecycle-v1"


@dataclass(frozen=True)
class RawSchemaProfile:
    strategy: str
    profile_id: str
    version: int
    tables: object
    source_indexes: object
    application_id: int | None = None
    user_version: int | None = None
    source_objects: tuple = ()
    logical_schema_sha256: str | None = None
    authority_rows: tuple = ()
    level_tables: tuple[str, ...] = ()
    mutable_tables: tuple[str, ...] = ()

    def triggers_for(self, table):
        if self.source_objects:
            return {name:sql for kind,name,owner,sql in self.source_objects
                    if kind == 'trigger' and owner == table}
        from .market_data_raw_links import immutable_sql
        return {table+'_forbid_'+op.lower():immutable_sql(table,op) for op in ('UPDATE','DELETE')}

    def validate_authority(self, connection):
        for query, expected in self.authority_rows:
            if tuple(tuple(row) for row in connection.execute(query)) != expected:
                raise ValueError('RAW source table-row authority differs')
        if self.profile_id == BLACK_FULL_PROFILE_ID:
            if tuple(tuple(row) for row in connection.execute('SELECT data_contract FROM main.schema_metadata'))!=(('sports-resolution-paired-v1',),):
                raise ValueError('RAW Black full schema authority differs')
            for row in connection.execute('SELECT DISTINCT job_name,mode FROM main.research_config_versions'):
                if tuple(row)!=('black-shadow-paired','sim'):
                    raise ValueError('RAW Black full config authority differs')
        if self.profile_id == WATERMELON_SIDECAR_PROFILE_ID:
            from pathlib import Path
            rows=connection.execute('SELECT singleton,contract,schema_sha256,parent_filename FROM main.raw_metadata').fetchall()
            if len(rows)!=1:raise ValueError('RAW Watermelon sidecar metadata authority differs')
            singleton,contract,digest,parent=rows[0]
            if (singleton!=1 or contract!=WATERMELON_SIDECAR_PROFILE_ID or digest!=self.logical_schema_sha256
                    or not isinstance(parent,str) or Path(parent).name!=parent or parent in {'','.','..','shadow.db'}):
                raise ValueError('RAW Watermelon sidecar schema/parent authority differs')
        if self.profile_id == CHERRY_SHADOW_PROFILE_ID:
            rows=connection.execute('SELECT data_contract FROM main.shadow_schema_metadata').fetchall()
            if tuple(tuple(row) for row in rows)!=((CHERRY_SHADOW_PROFILE_ID,),):
                raise ValueError('RAW Cherry shadow collection authority differs')
            expected=(CHERRY_SHADOW_PROFILE_ID,CHERRY_SHADOW_PROFILE_ID,'shadow',
                'cherry-shadow-resolution-v2-prereg-2026-09-05',
                '72d87684fa9ec7145b64fb8614afee60c9a7391a518bd0a867df7d19c9f95ee7')
            for row in connection.execute('SELECT data_contract,runtime_job,mode,preregistration_id,preregistration_sha256 FROM main.shadow_config_versions'):
                if tuple(row)!=expected:raise ValueError('RAW Cherry shadow config authority differs')
        if self.profile_id == GUAVA_PROFILE_ID:
            # Canonical bytes, not merely a JSON object with equal values.
            import json
            row = connection.execute('SELECT strategy_name,job_name,mode,data_contract,identity_json '
                                     'FROM main.collection_contracts WHERE singleton=1').fetchone()
            identity = dict(zip(('strategy_name','job_name','mode','data_contract'),row[:4],strict=True))
            if row[4] != json.dumps(identity,sort_keys=True,ensure_ascii=False,separators=(',',':')):
                raise ValueError('RAW Guava canonical identity authority differs')
        if self.profile_id == COCONUT_RECORDER_PROFILE_ID:
            from datetime import date
            rows = connection.execute('SELECT singleton,contract_name,data_contract,database_utc_date,'
                                      'runtime_job,schema_sha256 FROM main.collection_contracts').fetchall()
            if len(rows) != 1:
                raise ValueError('RAW recorder requires one original collection contract')
            singleton, contract, data_contract, day, runtime, digest = rows[0]
            allowed = {
                'sports-price-recorder-1m-v1': {'coconut-sports-recorder-1m-v1', 'coconut-sports-recorder-silver-1m-v1'},
                'sports-price-recorder-1m-v2': {'coconut-sports-recorder-1m-v2', 'coconut-sports-recorder-silver-1m-v1'},
            }
            try:
                valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
            except ValueError:
                valid_day = False
            if (singleton != 1 or contract != 'research-full-v1' or not valid_day
                    or runtime not in allowed.get(data_contract, ())
                    or digest != self.logical_schema_sha256):
                raise ValueError('RAW recorder original schema/runtime/day authority differs')

    def validate_namespace(self, connection, namespace):
        if self.profile_id == BLACK_FULL_PROFILE_ID:
            import json
            owner=json.loads(namespace)
            if (owner.get('runtime'),owner.get('jenkins_job'))!=('black-shadow-paired','polybot-black'):
                raise ValueError('RAW Black full runtime/Jenkins namespace differs')
        if self.profile_id == WATERMELON_SIDECAR_PROFILE_ID:
            import json
            owner=json.loads(namespace)
            jobs={'watermelon-white-1m-v4b':'polybot-white','watermelon-grey-5m-v4b':'polybot-grey'}
            if owner.get('runtime') not in jobs or jobs[owner['runtime']]!=owner.get('jenkins_job'):
                raise ValueError('RAW Watermelon sidecar source/job/runtime namespace differs')
        if self.profile_id == CHERRY_SHADOW_PROFILE_ID:
            import json
            owner=json.loads(namespace)
            if (owner.get('runtime'),owner.get('jenkins_job'))!=(CHERRY_SHADOW_PROFILE_ID,'polybot-cherry-shadow'):
                raise ValueError('RAW Cherry shadow source/job/runtime namespace differs')
        if self.profile_id == COCONUT_RECORDER_PROFILE_ID:
            import json
            owner = json.loads(namespace)
            runtime = connection.execute('SELECT runtime_job FROM main.collection_contracts WHERE singleton=1').fetchone()[0]
            job = 'polybot-silver' if runtime == 'coconut-sports-recorder-silver-1m-v1' else 'polybot-white'
            if owner.get('runtime') != runtime or owner.get('jenkins_job') != job:
                raise ValueError('RAW recorder source/job/runtime namespace differs')

    def indexes_for(self, table):
        if self.source_objects:
            return {name:sql for kind,name,owner,sql in self.source_objects
                    if kind == 'index' and owner == table and name in self.source_indexes}
        # Explicit legacy Black ownership; index DDL is not parsed heuristically.
        owners={'market_condition_time_idx':'market_observations',
                'outcome_token_time_idx':'outcome_observations',
                'book_token_time_idx':'orderbook_snapshots'}
        if self.profile_id != BLACK_PROFILE_ID:raise ValueError('RAW profile has no reviewed index ownership')
        return {name:sql for name,sql in self.source_indexes.items() if owners[name] == table}


BLACK_INDEX_SQL = MappingProxyType({
    'market_condition_time_idx': 'CREATE INDEX market_condition_time_idx ON market_observations(condition_id, observed_at)',
    'outcome_token_time_idx': 'CREATE INDEX outcome_token_time_idx ON outcome_observations(token_id, observed_at)',
    'book_token_time_idx': 'CREATE INDEX book_token_time_idx ON orderbook_snapshots(token_id, observed_at)',
})
from .market_data_raw_schema_watermelon import WATERMELON_SCHEMA_OBJECTS, WATERMELON_RAW_TABLES
WATERMELON_TABLES = MappingProxyType({table:RawTableProfile(table,*values) for table,values in WATERMELON_RAW_TABLES.items()})
from .market_data_raw_schema_coconut import COCONUT_SCHEMA_OBJECTS, COCONUT_RAW_TABLES, COCONUT_LOGICAL_SCHEMA_SHA256
COCONUT_TABLES = MappingProxyType({table:RawTableProfile(table,*values) for table,values in COCONUT_RAW_TABLES.items()})
from .market_data_raw_schema_pomegranate import POMEGRANATE_SCHEMA_OBJECTS, POMEGRANATE_RAW_TABLES, POMEGRANATE_LOGICAL_SCHEMA_SHA256
POMEGRANATE_TABLES = MappingProxyType({table:RawTableProfile(table,*values,
    token_field='asset' if table == 'trade_observations' else None,
    source_time_field={'market_observations':'updated_at_source',
        'resolution_observations':'source_updated_at','trade_observations':'timestamp_raw'}.get(table))
    for table,values in POMEGRANATE_RAW_TABLES.items()})
from .market_data_raw_schema_raspberry import RASPBERRY_SCHEMA_OBJECTS, RASPBERRY_RAW_TABLES, RASPBERRY_LOGICAL_SCHEMA_SHA256
RASPBERRY_TABLES = MappingProxyType({table:RawTableProfile(table,*values,
    token_field='selected_token_id' if table == 'signal_decisions' else None)
    for table,values in RASPBERRY_RAW_TABLES.items()})
from .market_data_raw_schema_strawberry import (
    STRAWBERRY_V1_SCHEMA_OBJECTS, STRAWBERRY_V1_RAW_TABLES, STRAWBERRY_V1_LOGICAL_SCHEMA_SHA256,
    STRAWBERRY_FOLLOWUP_SCHEMA_OBJECTS, STRAWBERRY_FOLLOWUP_RAW_TABLES, STRAWBERRY_FOLLOWUP_LOGICAL_SCHEMA_SHA256,
)
STRAWBERRY_V1_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in STRAWBERRY_V1_RAW_TABLES.items()})
STRAWBERRY_FOLLOWUP_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in STRAWBERRY_FOLLOWUP_RAW_TABLES.items()})
from .market_data_raw_schema_guava import GUAVA_SCHEMA_OBJECTS, GUAVA_RAW_TABLES, GUAVA_LOGICAL_SCHEMA_SHA256
GUAVA_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in GUAVA_RAW_TABLES.items()})
from .market_data_raw_schema_recorder import RECORDER_SCHEMA_OBJECTS, RECORDER_RAW_TABLES, RECORDER_LOGICAL_SCHEMA_SHA256
RECORDER_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in RECORDER_RAW_TABLES.items()})
from .market_data_raw_schema_cherry import CHERRY_SCHEMA_OBJECTS, CHERRY_RAW_TABLES, CHERRY_LOGICAL_SCHEMA_SHA256
CHERRY_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in CHERRY_RAW_TABLES.items()})
from .market_data_raw_schema_watermelon_sidecar import SIDECAR_SCHEMA_OBJECTS, SIDECAR_RAW_TABLES, SIDECAR_LOGICAL_SCHEMA_SHA256
SIDECAR_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in SIDECAR_RAW_TABLES.items()})
from .market_data_raw_schema_black_full import BLACK_FULL_SCHEMA_OBJECTS,BLACK_FULL_RAW_TABLES,BLACK_FULL_LOGICAL_SCHEMA_SHA256
BLACK_FULL_TABLES = MappingProxyType({table:RawTableProfile(table,*values)
    for table,values in BLACK_FULL_RAW_TABLES.items()})
RAW_SCHEMA_PROFILES = MappingProxyType({
    BLACK_PROFILE_ID:RawSchemaProfile(STRATEGY, BLACK_PROFILE_ID, 1, RAW_TABLES, BLACK_INDEX_SQL,
        level_tables=('orderbook_levels',)),
    BLACK_FULL_PROFILE_ID:RawSchemaProfile('golden-black',BLACK_FULL_PROFILE_ID,1,BLACK_FULL_TABLES,
        MappingProxyType({name:sql for kind,name,table,sql in BLACK_FULL_SCHEMA_OBJECTS if kind=='index' and table in BLACK_FULL_TABLES}),
        0,0,BLACK_FULL_SCHEMA_OBJECTS,BLACK_FULL_LOGICAL_SCHEMA_SHA256,
        (("SELECT data_contract FROM main.schema_metadata",(('sports-resolution-paired-v1',),)),),
        level_tables=('orderbook_levels',)),
    WATERMELON_SIDECAR_PROFILE_ID:RawSchemaProfile('golden-watermelon',WATERMELON_SIDECAR_PROFILE_ID,1,
        SIDECAR_TABLES,MappingProxyType({name:sql for kind,name,table,sql in SIDECAR_SCHEMA_OBJECTS
            if kind=='index' and table in SIDECAR_TABLES}),0x57525231,1,SIDECAR_SCHEMA_OBJECTS,
        SIDECAR_LOGICAL_SCHEMA_SHA256),
    CHERRY_SHADOW_PROFILE_ID:RawSchemaProfile('golden-cherry',CHERRY_SHADOW_PROFILE_ID,1,
        CHERRY_TABLES,MappingProxyType({name:sql for kind,name,table,sql in CHERRY_SCHEMA_OBJECTS
            if kind=='index' and table in CHERRY_TABLES}),0,0,CHERRY_SCHEMA_OBJECTS,
        CHERRY_LOGICAL_SCHEMA_SHA256,
        (("SELECT data_contract FROM main.shadow_schema_metadata",((CHERRY_SHADOW_PROFILE_ID,),)),),
        level_tables=('shadow_book_levels',)),
    COCONUT_RECORDER_PROFILE_ID:RawSchemaProfile('golden-coconut', COCONUT_RECORDER_PROFILE_ID, 1,
        RECORDER_TABLES, MappingProxyType({name:sql for kind,name,table,sql in RECORDER_SCHEMA_OBJECTS
            if kind == 'index' and table in RECORDER_TABLES}), 0x43535231, 1, RECORDER_SCHEMA_OBJECTS,
        RECORDER_LOGICAL_SCHEMA_SHA256, mutable_tables=('tracked_events',)),
    GUAVA_PROFILE_ID:RawSchemaProfile('golden-guava',GUAVA_PROFILE_ID,1,
        GUAVA_TABLES,MappingProxyType({name:sql for kind,name,table,sql in GUAVA_SCHEMA_OBJECTS
            if kind == 'index' and table in GUAVA_TABLES}),0,0,GUAVA_SCHEMA_OBJECTS,
        GUAVA_LOGICAL_SCHEMA_SHA256,
        (("SELECT COUNT(*) FROM main.collection_contracts",((1,),)),
         ("SELECT COUNT(*) FROM main.collection_contracts WHERE singleton=1 AND schema_version=1 "
          "AND contract_name='guava-research-v1' AND data_contract='guava-research-v1' "
          "AND database_utc_date IS NULL AND strategy_name='golden-guava' AND mode='sim' "
          "AND typeof(job_name)='text' AND length(trim(job_name))>0",((1,),)))),
    WATERMELON_PROFILE_ID:RawSchemaProfile('golden-watermelon', WATERMELON_PROFILE_ID, 1,
        WATERMELON_TABLES, MappingProxyType({name:sql for kind,name,table,sql in WATERMELON_SCHEMA_OBJECTS
            if kind == 'index' and table in WATERMELON_TABLES}),1196903732,401,WATERMELON_SCHEMA_OBJECTS,
        '70baef885a69b0200bb11c8325530cc88a49be2f1b78e27fb046c097a1716e32'),
    COCONUT_PROFILE_ID:RawSchemaProfile('golden-coconut',COCONUT_PROFILE_ID,1,
        COCONUT_TABLES,MappingProxyType({name:sql for kind,name,table,sql in COCONUT_SCHEMA_OBJECTS
            if kind == 'index' and table in COCONUT_TABLES}),1195593521,6,COCONUT_SCHEMA_OBJECTS,
        COCONUT_LOGICAL_SCHEMA_SHA256),
    POMEGRANATE_PROFILE_ID:RawSchemaProfile('golden-pomegranate',POMEGRANATE_PROFILE_ID,1,
        POMEGRANATE_TABLES,MappingProxyType({name:sql for kind,name,table,sql in POMEGRANATE_SCHEMA_OBJECTS
            if kind == 'index' and table in POMEGRANATE_TABLES}),0,0,POMEGRANATE_SCHEMA_OBJECTS,
        POMEGRANATE_LOGICAL_SCHEMA_SHA256,
        (("SELECT contract_name,schema_version FROM main.collection_contracts ORDER BY contract_name",
          (('research-full-v1',4),)),),('orderbook_levels',)),
    RASPBERRY_PROFILE_ID:RawSchemaProfile('golden-raspberry',RASPBERRY_PROFILE_ID,1,
        RASPBERRY_TABLES,MappingProxyType({name:sql for kind,name,table,sql in RASPBERRY_SCHEMA_OBJECTS
            if kind == 'index' and table in RASPBERRY_TABLES}),0,0,RASPBERRY_SCHEMA_OBJECTS,
        RASPBERRY_LOGICAL_SCHEMA_SHA256,
        (("SELECT key,value FROM main.schema_metadata ORDER BY key",
          (('data_contract','queue-echo-v3'),('schema_profile','queue-echo-v3-sqlite-v3'),('schema_version','3'))),
         ("SELECT COUNT(*) FROM main.experiment_contracts",((1,),)),
         ("SELECT COUNT(*) FROM main.experiment_contracts WHERE strategy_name='golden-raspberry' "
          "AND data_contract='queue-echo-v3' AND schema_version=3 AND schema_profile='queue-echo-v3-sqlite-v3' "
          "AND shard_count=3 AND cadence_minutes=5 AND cadence_offset_minute=shard_index "
          "AND ((job_name='raspberry-do-v3-shard-0' AND shard_index=0) "
          "OR (job_name='raspberry-re-v3-shard-1' AND shard_index=1) "
          "OR (job_name='raspberry-mi-v3-shard-2' AND shard_index=2)) "
          "AND window_start='2026-08-23T20:00:00Z' AND window_end='2026-09-22T20:00:00Z' "
          "AND preregistration_sha256='f3614897346306e68f3b880c1d22c89d8046c2fc6c43589b88cc3d40d3f6130b' "
          "AND data_contract_sha256='bd59d171b50810b3ec263e85d478e5744f83c4b0fc15315e5d4d98c32f05961f'",((1,),))),
        ('orderbook_levels',)),
    STRAWBERRY_V1_PROFILE_ID:RawSchemaProfile('golden-strawberry',STRAWBERRY_V1_PROFILE_ID,1,
        STRAWBERRY_V1_TABLES,MappingProxyType({name:sql for kind,name,table,sql in STRAWBERRY_V1_SCHEMA_OBJECTS
            if kind == 'index' and table in STRAWBERRY_V1_TABLES}),0,0,STRAWBERRY_V1_SCHEMA_OBJECTS,
        STRAWBERRY_V1_LOGICAL_SCHEMA_SHA256,
        (("SELECT key,value FROM main.schema_metadata ORDER BY key",
          (('data_contract','last-mile-clob-v1'),('mutable_cache_table','latest_outcome_state'),('schema_version','1'))),
         ("SELECT COUNT(*) FROM main.experiment_contracts",((1,),)),
         ("SELECT COUNT(*) FROM main.experiment_contracts WHERE job_name='strawberry-shadow-one' "
          "AND strategy_name='golden-strawberry' AND data_contract='last-mile-clob-v1' "
          "AND lifecycle_mode='archive_only' AND cadence_minutes=10 AND cadence_offset_minute=7 "
          "AND entry_start='2026-08-15T04:00:00Z' AND entry_end='2026-08-22T04:00:00Z' "
          "AND followup_end='2026-09-21T04:00:00Z' "
          "AND preregistration_sha256='d42e1ff839e8fe03f88a4e653f5ced6d951ede061cd248b6a19424fb0af36ffd'",((1,),))),
        ('clob_levels',),('latest_outcome_state',)),
    STRAWBERRY_FOLLOWUP_PROFILE_ID:RawSchemaProfile('golden-strawberry',STRAWBERRY_FOLLOWUP_PROFILE_ID,1,
        STRAWBERRY_FOLLOWUP_TABLES,MappingProxyType({name:sql for kind,name,table,sql in STRAWBERRY_FOLLOWUP_SCHEMA_OBJECTS
            if kind == 'index' and table in STRAWBERRY_FOLLOWUP_TABLES}),0,0,STRAWBERRY_FOLLOWUP_SCHEMA_OBJECTS,
        STRAWBERRY_FOLLOWUP_LOGICAL_SCHEMA_SHA256,
        (("SELECT key,value FROM main.schema_metadata ORDER BY key",
          (('book_storage','canonical-gzip-one-row-per-token-cycle'),('data_contract','last-mile-clob-followup-v2a'),
           ('schema_version','4'),('v1_source_access','mode=ro'))),
         ("SELECT COUNT(*) FROM main.followup_contracts",((1,),)),
         ("SELECT COUNT(*) FROM main.followup_contracts WHERE job_name='strawberry-shadow-one-followup-v2a' "
          "AND entry_start='2026-08-15T04:00:00Z' AND entry_end='2026-08-22T04:00:00Z' "
          "AND followup_end='2026-09-21T04:00:00Z' "
          "AND preregistration_sha256='1abcf3f6b2c7e679b759755d587d776a2f6f8e8f10a6ef5e90d271cabad6659a'",((1,),)))),
})


def raw_profile(profile_id=BLACK_PROFILE_ID):
    try:return RAW_SCHEMA_PROFILES[profile_id]
    except (KeyError,TypeError) as error:raise ValueError('unreviewed RAW schema profile') from error


def _public_profiles():
    result = {}
    for schema in RAW_SCHEMA_PROFILES.values():
        for profile in schema.tables.values():
            public = _public_profile(profile)
            if profile.kind in result and result[profile.kind] != public:
                raise ValueError('shared RAW public kind schema differs across source profiles')
            result[profile.kind] = public
    return MappingProxyType(result)


RAW_PUBLIC_PROFILES = _public_profiles()


def profile_for_connection(connection, *, profile_id=None):
    """Resolve only an explicit profile or a validated persisted layout marker."""
    from .market_data_raw_links import profile_for_connection as resolve
    return resolve(connection, profile_id=profile_id)

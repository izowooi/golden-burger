"""Independent one-minute recorder epoch; the frozen v7 runtime is unchanged."""
from dataclasses import asdict, dataclass
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from .config import assert_safe_environment
from .recorder_registry_v2 import load_registry

PROJECT = Path(__file__).resolve().parents[2]
WHITE_RUNTIME = 'coconut-sports-recorder-1m-v2'
SILVER_RUNTIME = 'coconut-sports-recorder-silver-1m-v1'
RUNTIME = WHITE_RUNTIME
CONTRACT = 'sports-price-recorder-1m-v2'
EPOCH = 'research/frozen-2026-09-30-shared-projections-v8'
REGISTRY = 'research/frozen-2026-09-28-recorder-unl-v6/SPORTS_REGISTRY.json'
REGISTRY_SHA = '40bfec79a9ecff68004e5261d40ae640de60f55a202293eb195384e39804fd13'
SOURCES = ('pyproject.toml','uv.lock','src/polybot/__init__.py','src/polybot/api/__init__.py','scripts/sports_recorder.py','scripts/export_recorder.py',
 'src/polybot/recorder_config.py','src/polybot/recorder_registry_v2.py','src/polybot/recorder_classifier_v2.py','src/polybot/recorder_store.py','src/polybot/recorder_http.py',
 'src/polybot/recorder.py','src/polybot/recorder_export.py','src/polybot/recorder_workspace.py','src/polybot/recorder_shared_raw.py',
 'src/polybot/classifier.py','src/polybot/lifecycle.py','src/polybot/registry.py','src/polybot/config.py',
 'src/polybot/source_digest.py','src/polybot/api/transport.py','src/polybot/api/gamma_client.py',
 'src/polybot/api/sports_client.py','src/polybot/api/clob_client.py',REGISTRY,EPOCH+'/PREREGISTRATION.md',
 '../polybot-observability/src/polybot_observability/market_data_policy.py',
 '../polybot-observability/src/polybot_observability/market_data_apple.py',
 '../polybot-observability/src/polybot_observability/market_data_mixed.py',
 '../polybot-observability/src/polybot_observability/market_data_private_packets.py',
 '../polybot-observability/src/polybot_observability/market_data_scalars.py',
 '../polybot-observability/src/polybot_observability/market_data_scalar_store.py',
 '../polybot-observability/src/polybot_observability/market_data_scalar_links.py',
 '../polybot-observability/src/polybot_observability/market_data_projection_profiles.py',
 '../polybot-observability/src/polybot_observability/market_data_projections.py',
 '../polybot-observability/src/polybot_observability/market_data_projection_store.py',
 '../polybot-observability/src/polybot_observability/market_data_projection_links.py',
 '../polybot-observability/src/polybot_observability/market_data_catalog_profiles.py',
 '../polybot-observability/src/polybot_observability/market_data_catalog_links.py',
 '../polybot-observability/src/polybot_observability/market_data_catalog_sqlalchemy.py',
 '../polybot-observability/src/polybot_observability/market_data_orm_flush.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_profiles.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_black_full.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_watermelon.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_watermelon_sidecar.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_coconut.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_recorder.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_cherry.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_pomegranate.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_raspberry.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_strawberry.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_schema_guava.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_guava_reader.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_mutable.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_links.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_transition.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_readback.py',
 '../polybot-observability/src/polybot_observability/market_data_retirement.py',
 '../polybot-observability/src/polybot_observability/market_data_raw_migrate.py',
 '../polybot-observability/src/polybot_observability/market_data_projection_closure.py',
 '../polybot-observability/src/polybot_observability/market_data_bundle.py',
 '../polybot-observability/src/polybot_observability/market_data_migrate.py',
 '../polybot-observability/src/polybot_observability/market_data_refs.py',
 '../polybot-observability/src/polybot_observability/market_data_index.py',
 '../polybot-observability/src/polybot_observability/market_data_sqlite.py',
 '../polybot-observability/src/polybot_observability/market_data_sql_schema.py',
 '../polybot-observability/src/polybot_observability/market_data_levels.py',
 '../polybot-observability/src/polybot_observability/market_data_store.py',
 '../polybot-observability/src/polybot_observability/market_data_client.py')

@dataclass(frozen=True)
class RecorderConfig:
    job_name: str = WHITE_RUNTIME
    jenkins_job: str = 'polybot-white'
    cadence_seconds: int = 60
    slot_phase_seconds: int = 30
    pre_seconds: int = 600
    post_seconds: int = 600
    request_seconds: int = 42
    cycle_seconds: int = 50
    discovery_seconds: int = 300
    book_batch_limit: int = 240
    max_tokens_per_cycle: int = 600
    gamma_page_size: int = 100
    max_pages_per_family: int = 20
    max_response_bytes: int = 32*1024*1024
    settlement_poll_seconds: int = 300
    min_free_gib: int = 50
    max_used_ratio: float = .90

    @property
    def db_path(self): return PROJECT/'data'/self.job_name/'trades_sim.db'

    def snapshot(self,observation_mode='SCHEDULED'):
        if observation_mode not in ('SCHEDULED','PROBE'):raise ValueError('unknown recorder mode')
        values={**asdict(self),'observation_mode':observation_mode}
        return {**values,'strategy_name':'golden-coconut','mode':'sim','simulation_mode':True,'lifecycle_mode':'archive_only',
                'data_contract':CONTRACT,'registry_sha256':REGISTRY_SHA,
                'source_digest':source_digest(),
                'config_hash':hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()}


def source_digest():
    h=hashlib.sha256()
    for relative in SOURCES:
        p=PROJECT/relative
        if p.is_symlink() or not p.is_file():raise ValueError('recorder source absent or unsafe: '+relative)
        h.update(relative.encode()+b'\0'+p.read_bytes()+b'\0')
    return h.hexdigest()


def verify_manifest():
    path=PROJECT/EPOCH/'MANIFEST.sha256'
    seen=set()
    for line in path.read_text().splitlines():
        digest,relative=line.split('  ',1)
        if relative not in SOURCES or relative in seen:raise ValueError('invalid recorder manifest membership')
        if hashlib.sha256((PROJECT/relative).read_bytes()).hexdigest()!=digest:raise ValueError('recorder manifest mismatch: '+relative)
        seen.add(relative)
    if seen!=set(SOURCES):raise ValueError('recorder manifest incomplete')


RUNTIME_JENKINS_JOBS = {
    WHITE_RUNTIME: 'polybot-white',
    SILVER_RUNTIME: 'polybot-silver',
}


def load_config(*, job_name=RUNTIME, simulate=False, live=False):
    assert_safe_environment()
    if live or not simulate or job_name not in RUNTIME_JENKINS_JOBS:raise ValueError('explicit registered simulation recorder only')
    verify_manifest()
    return RecorderConfig(job_name=job_name,jenkins_job=RUNTIME_JENKINS_JOBS[job_name])


def registry():return load_registry(REGISTRY_SHA,PROJECT/REGISTRY)


def slot_start_utc(now,phase=30):
    if now.tzinfo is None:raise ValueError('UTC-aware reference required')
    return datetime.fromtimestamp(((now.timestamp()-phase)//60)*60+phase,timezone.utc)

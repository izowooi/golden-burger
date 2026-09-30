"""Bind shared runtime behavior into consumers that use explicit source lists."""
from pathlib import Path


MARKET_DATA_SOURCE_FILES = (
    'market_data_source_digest.py',
    'market_data_capture.py',
    'market_data_apple.py',
    'market_data_mixed.py',
    'market_data_private_packets.py',
    'market_data_reader.py',
    'market_data_scalars.py',
    'market_data_scalar_store.py',
    'market_data_scalar_links.py',
    'market_data_projection_profiles.py',
    'market_data_projections.py',
    'market_data_projection_store.py',
    'market_data_projection_links.py',
    'market_data_catalog_profiles.py',
    'market_data_catalog_links.py',
    'market_data_catalog_sqlalchemy.py',
    'market_data_orm_flush.py',
    'market_data_raw_profiles.py',
    'market_data_raw_schema_black_full.py',
    'market_data_raw_schema_watermelon.py',
    'market_data_raw_schema_watermelon_sidecar.py',
    'market_data_raw_schema_coconut.py',
    'market_data_raw_schema_recorder.py',
    'market_data_raw_schema_cherry.py',
    'market_data_raw_schema_pomegranate.py',
    'market_data_raw_schema_raspberry.py',
    'market_data_raw_schema_strawberry.py',
    'market_data_raw_schema_guava.py',
    'market_data_raw_guava_reader.py',
    'market_data_raw_mutable.py',
    'market_data_raw_links.py',
    'market_data_raw_transition.py',
    'market_data_raw_readback.py',
    'market_data_retirement.py',
    'market_data_raw_migrate.py',
    'market_data_projection_closure.py',
    'market_data_bundle.py',
    'market_data_migrate.py',
    'market_data_supervisor.py',
    'market_data_store.py',
    'market_data_service.py',
    'market_data_client.py',
    'market_data_policy.py',
    'market_data_refs.py',
    'market_data_sqlite.py',
    'market_data_sql_schema.py',
    'market_data_levels.py',
    'market_data_index.py',
    'market_data_sqlalchemy.py',
)


def update_digest(digest, shared_root: Path) -> None:
    root=Path(shared_root).resolve(strict=True)
    for name in MARKET_DATA_SOURCE_FILES:
        path=root/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('shared market-data runtime source is missing or symlinked: '+name)
        label=('shared-market-data/'+name).encode()
        body=path.read_bytes()
        digest.update(len(label).to_bytes(4,'big'))
        digest.update(label)
        digest.update(len(body).to_bytes(8,'big'))
        digest.update(body)

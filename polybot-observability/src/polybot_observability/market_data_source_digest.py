"""Bind shared runtime behavior into consumers that use explicit source lists."""
from pathlib import Path


MARKET_DATA_SOURCE_FILES = (
    'market_data_source_digest.py',
    'market_data_store.py',
    'market_data_service.py',
    'market_data_client.py',
    'market_data_policy.py',
    'market_data_refs.py',
    'market_data_sqlite.py',
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

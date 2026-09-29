"""Source identity independent of unrelated monorepo commits."""
import hashlib
from pathlib import Path

from polybot_observability.market_data_source_digest import update_digest as update_market_data_digest

ROOT=Path(__file__).resolve().parents[2]


def compute_strategy_source_digest(root=ROOT):
    root=Path(root)
    paths=sorted((root/'src/polybot').glob('**/*.py'))
    paths += [root/'config.yaml',root/'pyproject.toml',root/'STRATEGY.md']
    paths += sorted((root/'research').glob('**/*.md'))
    if (root/'uv.lock').exists():paths.append(root/'uv.lock')
    digest=hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(str(path.relative_to(root)).encode());digest.update(b'\0')
        digest.update(path.read_bytes());digest.update(b'\0')
    # The actual shared execution/config contract is part of provenance too.
    shared=root.parent/'polybot-observability/src/polybot_observability'
    for name in ('config_contract.py','execution_ledger.py','run_audit.py'):
        path=shared/name
        if not path.is_file():raise ValueError("shared observability source is unavailable")
        digest.update(('shared/'+name).encode());digest.update(path.read_bytes())
    update_market_data_digest(digest, shared)
    return digest.hexdigest()

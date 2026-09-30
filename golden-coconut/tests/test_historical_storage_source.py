"""Storage source epoch changes without relabeling the historical research cohort."""
import shutil

from polybot import source_digest
from polybot_observability.market_data_source_digest import MARKET_DATA_SOURCE_FILES


def test_historical_storage_epoch_keeps_v7_research_and_config_bytes():
    root = source_digest.PROJECT_ROOT
    source_digest.verify_frozen_manifest()
    assert source_digest.STORAGE_SOURCE_EPOCH == "coconut-historical-shared-raw-v9"
    assert source_digest.ACTIVE_MANIFEST != source_digest.PREVIOUS_MANIFEST
    assert source_digest.PREVIOUS_MANIFEST in source_digest.SOURCE_PATHS
    assert source_digest.sha256_file(root / source_digest.PREVIOUS_MANIFEST) == "cca46a5fbce654e7829b4ba06046a9c5692aaf604136b1ab52dba76027bf2d93"
    assert source_digest.sha256_file(root / "config.yaml") == "b3f60b0f847332446da22f758d15383d4658092c7903ca488a88e4a8f788b72d"
    assert source_digest.preregistration_sha256() == "a281139eb0e57d17c9c9d93cad14174070272ec12591d5eac013be31b8687152"


def test_historical_source_digest_binds_shared_adapter_bytes(tmp_path):
    project = tmp_path / "golden-coconut"
    for relative in source_digest.SOURCE_PATHS:
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_digest.PROJECT_ROOT / relative, destination)
    shared = tmp_path / "polybot-observability/src/polybot_observability"
    shared.mkdir(parents=True)
    original_shared = source_digest.PROJECT_ROOT.parent / "polybot-observability/src/polybot_observability"
    for name in MARKET_DATA_SOURCE_FILES:
        shutil.copyfile(original_shared / name, shared / name)
    before = source_digest.compute_strategy_source_digest(project)
    path = shared / "market_data_mixed.py"
    path.write_bytes(path.read_bytes() + b"\n# synthetic source change\n")
    assert source_digest.compute_strategy_source_digest(project) != before

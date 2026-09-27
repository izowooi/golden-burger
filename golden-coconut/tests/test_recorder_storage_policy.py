from types import SimpleNamespace
import pytest
from polybot.recorder_config import RecorderConfig, load_config, WHITE_RUNTIME
from polybot.recorder_workspace import verify_storage_usage

GIB = 1024**3


def test_active_storage_policy_reaches_ninety_instead_of_legacy_free_gate():
    config = load_config(job_name=WHITE_RUNTIME, simulate=True)
    assert config.max_used_ratio == .90
    assert config.min_free_gib == 50
    # 89% on a 1000 GiB volume leaves only110 GiB: legacy150 would stop early.
    usage = SimpleNamespace(total=1000*GIB, used=890*GIB, free=110*GIB)
    assert verify_storage_usage(config, usage)["used_ratio"] == .89


def test_ninety_percent_and_absolute_reserve_each_fail_closed():
    config = RecorderConfig()
    with pytest.raises(RuntimeError, match="used_ratio=0.9000"):
        verify_storage_usage(config, SimpleNamespace(total=1000*GIB, used=900*GIB, free=100*GIB))
    with pytest.raises(RuntimeError, match="free_gib=49"):
        verify_storage_usage(config, SimpleNamespace(total=200*GIB, used=151*GIB, free=49*GIB))

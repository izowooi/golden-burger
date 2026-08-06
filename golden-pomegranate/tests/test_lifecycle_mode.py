"""Golden Pomegranate has one lifecycle: full archive collection only."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from polybot.config import LIFECYCLE_MODES, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _path_with_lifecycle(tmp_path: Path, value) -> Path:
    payload = deepcopy(
        yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    )
    payload["trading"]["lifecycle_mode"] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_archive_only_is_the_only_lifecycle_and_collects_full_universe(tmp_path):
    assert LIFECYCLE_MODES == frozenset({"archive_only"})

    config = load_config(_path_with_lifecycle(tmp_path, "archive_only"), env={})

    assert config.trading.lifecycle_mode == "archive_only"
    assert config.trading.collects_full_universe is True
    assert config.trading.collects_resolution_only is False


@pytest.mark.parametrize(
    "value",
    ["active", "close_only", "archive-only", "ARCHIVE_ONLY", "", None, 1, True],
)
def test_every_noncanonical_lifecycle_value_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="(?i)archive_only|lifecycle"):
        load_config(_path_with_lifecycle(tmp_path, value), env={})


@pytest.mark.parametrize(
    "value", ["active", "close_only", "archive-only", "ARCHIVE_ONLY", "unknown"]
)
def test_environment_cannot_expand_the_lifecycle_surface(tmp_path, value):
    with pytest.raises(ValueError, match="(?i)archive_only|lifecycle"):
        load_config(
            _path_with_lifecycle(tmp_path, "archive_only"),
            env={"POLYBOT_LIFECYCLE_MODE": value},
        )

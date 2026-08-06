"""Strict configuration contract for the accountless collector."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from polybot.config import RESEARCH_DATA_CONTRACT, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _base_payload() -> dict[str, Any]:
    return yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))


def _write_config(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _mutated_config(tmp_path: Path, mutation: Callable[[dict[str, Any]], None]) -> Path:
    payload = deepcopy(_base_payload())
    mutation(payload)
    return _write_config(tmp_path, payload)


def test_default_config_is_archive_only_research_full_and_simulation(tmp_path):
    config = load_config(
        _write_config(tmp_path, _base_payload()),
        job_name="pomegranate-test",
        env={},
    )

    assert config.simulation_mode is True
    assert config.job_name == "pomegranate-test"
    assert config.db_path == tmp_path / "data" / "pomegranate-test" / "trades_sim.db"
    assert config.trading.lifecycle_mode == "archive_only"
    assert config.trading.collects_full_universe is True
    assert config.trading.collects_resolution_only is False
    assert config.trading.data_contract == RESEARCH_DATA_CONTRACT == "research-full-v1"
    assert config.trading.cadence_minutes == 15
    assert config.trading.gamma.min_liquidity == 10_000
    assert config.trading.gamma.min_total_volume == 2_000
    assert config.trading.gamma.max_end_horizon_days == 120
    assert config.trading.data_api.trade_limit == 10_000
    assert config.trading.data_api.safety_lag_seconds == 300
    assert config.trading.data_api.overlap_seconds == 1_800
    assert config.trading.data_api.initial_lookback_hours == 24
    assert config.trading.data_api.catchup_chunk_seconds == 3_600
    assert config.trading.data_api.max_request_attempts_per_cycle == 64
    assert config.trading.data_api.max_windows_per_cycle == 32
    assert config.trading.data_api.runtime_budget_seconds == 120
    assert config.trading.storage.raw_payload_every_cycles == 1
    assert config.trading.storage.min_free_gib >= 150
    assert len(config.trading.strategy_source_digest) == 64


def test_known_environment_overrides_are_strict_and_take_precedence(tmp_path):
    path = _write_config(tmp_path, _base_payload())

    config = load_config(
        path,
        env={
            "POLYBOT_CADENCE_MINUTES": "30",
            "POLYBOT_GAMMA_PAGE_SIZE": "50",
            "POLYBOT_GAMMA_MIN_LIQUIDITY": "25000",
            "POLYBOT_GAMMA_MIN_TOTAL_VOLUME": "5000",
            "POLYBOT_GAMMA_MAX_END_HORIZON_DAYS": "60",
            "POLYBOT_MIN_FREE_GIB": "200",
            "POLYBOT_LIFECYCLE_MODE": "archive_only",
            "POLYBOT_TRADE_CATCHUP_CHUNK_SECONDS": "1800",
            "POLYBOT_TRADE_MAX_REQUEST_ATTEMPTS_PER_CYCLE": "32",
            "POLYBOT_TRADE_MAX_WINDOWS_PER_CYCLE": "16",
            "POLYBOT_TRADE_RUNTIME_BUDGET_SECONDS": "60",
        },
    )

    assert config.trading.cadence_minutes == 30
    assert config.trading.gamma.page_size == 50
    assert config.trading.gamma.min_liquidity == 25_000
    assert config.trading.gamma.min_total_volume == 5_000
    assert config.trading.gamma.max_end_horizon_days == 60
    assert config.trading.storage.min_free_gib == 200
    assert config.trading.data_api.catchup_chunk_seconds == 1_800
    assert config.trading.data_api.max_request_attempts_per_cycle == 32
    assert config.trading.data_api.max_windows_per_cycle == 16
    assert config.trading.data_api.runtime_budget_seconds == 60


@pytest.mark.parametrize(
    "key",
    [
        "POLYBOT_GAMMA_BASE_URL",
        "POLYBOT_DATA_API_BASE_URL",
        "POLYBOT_CLOB_BASE_URL",
    ],
)
@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:must-never-appear@example.test",
        "https://user@example.test",
        "https://example.test/path",
        "https://example.test?api_key=must-never-appear",
        "https://example.test#must-never-appear",
        "http://example.test",
    ],
)
def test_public_base_urls_reject_credentials_and_non_origin_components(
    tmp_path, key, base_url
):
    with pytest.raises(ValueError) as captured:
        load_config(
            _write_config(tmp_path, _base_payload()),
            env={key: base_url},
        )

    message = str(captured.value)
    assert "must-never-appear" not in message
    assert (
        "https" in message.lower()
        or "credential" in message.lower()
        or "origin" in message.lower()
    )


def test_public_base_url_accepts_credential_free_https_origin_with_port(tmp_path):
    config = load_config(
        _write_config(tmp_path, _base_payload()),
        env={"POLYBOT_GAMMA_BASE_URL": "https://example.test:8443/"},
    )

    assert config.trading.gamma.base_url == "https://example.test:8443"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.__setitem__("unknown_root", 1),
        lambda payload: payload["trading"].__setitem__("unknown_trading", 1),
        lambda payload: payload["trading"]["gamma"].__setitem__("unknown_gamma", 1),
        lambda payload: payload["trading"]["data_api"].__setitem__(
            "unknown_data_api", 1
        ),
        lambda payload: payload["trading"]["orderbook"].__setitem__("unknown_book", 1),
        lambda payload: payload["trading"]["storage"].__setitem__("unknown_storage", 1),
    ],
)
def test_unknown_yaml_keys_are_rejected(tmp_path, mutation):
    with pytest.raises(
        (KeyError, TypeError, ValueError), match="unknown|Unknown|unsupported"
    ):
        load_config(_mutated_config(tmp_path, mutation), env={})


def test_unknown_polybot_environment_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown|unsupported|POLYBOT_TYPO"):
        load_config(
            _write_config(tmp_path, _base_payload()),
            env={"POLYBOT_TYPO": "15"},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.__setitem__("simulation_mode", 1), "boolean"),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__("page_size", True),
            "numeric|boolean",
        ),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__("page_size", 1.5),
            "integer",
        ),
        (
            lambda payload: payload["trading"]["storage"].__setitem__(
                "warn_used_ratio", "0.7"
            ),
            "numeric",
        ),
        (
            lambda payload: payload["trading"]["storage"].__setitem__(
                "min_free_gib", float("nan")
            ),
            "finite",
        ),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__(
                "read_timeout_seconds", float("inf")
            ),
            "finite",
        ),
    ],
)
def test_yaml_numeric_and_boolean_types_are_strict(tmp_path, mutation, message):
    with pytest.raises(ValueError, match=message):
        load_config(_mutated_config(tmp_path, mutation), env={})


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("POLYBOT_GAMMA_PAGE_SIZE", "1.5", "integer|valid int"),
        ("POLYBOT_GAMMA_PAGE_SIZE", "true", "integer|valid"),
        ("POLYBOT_GAMMA_READ_TIMEOUT_SECONDS", "nan", "finite"),
        ("POLYBOT_WARN_USED_RATIO", "inf", "finite"),
        ("POLYBOT_SIMULATION_MODE", "maybe", "boolean"),
    ],
)
def test_environment_numeric_and_boolean_types_are_strict(
    tmp_path, key, value, message
):
    with pytest.raises(ValueError, match=message):
        load_config(
            _write_config(tmp_path, _base_payload()),
            env={key: value},
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["trading"]["gamma"].update(
                retry_base_seconds=61, retry_max_seconds=60
            ),
            "retry_base_seconds",
        ),
        (
            lambda payload: payload["trading"]["storage"].update(
                warn_used_ratio=0.8, stop_used_ratio=0.8
            ),
            "warn.*stop",
        ),
        (
            lambda payload: payload["trading"]["resolution"].update(
                max_condition_lookups_per_cycle=10, batch_size=11
            ),
            "batch_size",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "trade_limit", 9_999
            ),
            "10000",
        ),
        (
            lambda payload: payload["trading"].__setitem__(
                "data_contract", "compact-v1"
            ),
            "compact-v1",
        ),
        (
            lambda payload: payload["trading"]["storage"].__setitem__(
                "min_free_gib", 149
            ),
            "150|min_free",
        ),
        (
            lambda payload: payload["trading"]["storage"].__setitem__(
                "raw_payload_every_cycles", 2
            ),
            "raw_payload|every",
        ),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__(
                "min_liquidity", 9_999
            ),
            "10,000|liquidity",
        ),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__(
                "min_total_volume", 1_999
            ),
            "2,000|volume",
        ),
        (
            lambda payload: payload["trading"]["gamma"].__setitem__(
                "max_end_horizon_days", 121
            ),
            "120|horizon",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "safety_lag_seconds", 299
            ),
            "300|safety_lag",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "overlap_seconds", 1_799
            ),
            "1800|overlap",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "initial_lookback_hours", 23
            ),
            "24|lookback",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "catchup_chunk_seconds", 3_601
            ),
            "catchup_chunk",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "max_request_attempts_per_cycle", 65
            ),
            "max_request_attempts",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "max_windows_per_cycle", 33
            ),
            "max_windows",
        ),
        (
            lambda payload: payload["trading"]["data_api"].__setitem__(
                "runtime_budget_seconds", 121
            ),
            "runtime_budget",
        ),
    ],
)
def test_cross_field_and_frozen_research_contracts_are_rejected(
    tmp_path, mutation, message
):
    with pytest.raises(ValueError, match=message):
        load_config(_mutated_config(tmp_path, mutation), env={})


@pytest.mark.parametrize("cadence", [0, 1, 5, 9, 10, 11, 20, 120])
def test_only_preregistered_cadences_are_allowed(tmp_path, cadence):
    path = _mutated_config(
        tmp_path,
        lambda payload: payload["trading"].__setitem__("cadence_minutes", cadence),
    )
    with pytest.raises(ValueError, match="cadence"):
        load_config(path, env={})


@pytest.mark.parametrize("cadence", [15, 30, 60])
def test_preregistered_cadences_are_valid(tmp_path, cadence):
    path = _mutated_config(
        tmp_path,
        lambda payload: payload["trading"].__setitem__("cadence_minutes", cadence),
    )
    assert load_config(path, env={}).trading.cadence_minutes == cadence


@pytest.mark.parametrize("job_name", ["../escape", "bad/name", "", "has space"])
def test_job_name_is_a_safe_single_path_component(tmp_path, job_name):
    with pytest.raises(ValueError, match="job_name"):
        load_config(
            _write_config(tmp_path, _base_payload()),
            job_name=job_name,
            env={},
        )

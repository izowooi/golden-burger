"""env > yaml > 기본값 병합 동작 테스트."""
import logging

import pytest

from polybot.config import load_config
from polybot.utils.logger import resolve_log_level

DUMMY_KEY = "0x" + "11" * 32
DUMMY_ADDR = "0x" + "00" * 19 + "01"

# 테스트 간 누수를 막기 위해 초기화할 env 목록
POLYBOT_ENV_KEYS = [
    "POLYBOT_BUY_AMOUNT", "POLYBOT_MIN_LIQUIDITY", "POLYBOT_MIN_VOLUME_24H",
    "POLYBOT_TAKE_PROFIT", "POLYBOT_STOP_LOSS", "POLYBOT_MAX_POSITIONS",
    "POLYBOT_REENTRY_COOLDOWN_HOURS", "POLYBOT_HISTORY_BACKFILL",
    "POLYBOT_EXCLUDED_CATEGORIES", "POLYBOT_YES_ONLY",
    "POLYBOT_LADDER_H1", "POLYBOT_LADDER_H2", "POLYBOT_LADDER_H3",
    "POLYBOT_BAND1_MIN", "POLYBOT_BAND1_MAX",
    "POLYBOT_BAND2_MIN", "POLYBOT_BAND2_MAX",
    "POLYBOT_BAND3_MIN", "POLYBOT_BAND3_MAX",
    "POLYBOT_ENTRY_HOURS_MIN", "POLYBOT_EXIT_HOURS",
    "POLYBOT_MOMENTUM_LOOKBACK_HOURS", "POLYBOT_MOMENTUM_MIN_CHANGE",
    "POLYBOT_TRAILING_STOP_ENABLED", "POLYBOT_TRAILING_STOP_PERCENT",
    "LOG_LEVEL",
]


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    """더미 API 키 + 깨끗한 env + tmp cwd (data/ 오염 방지)."""
    monkeypatch.chdir(tmp_path)
    for key in POLYBOT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_ADDR)
    return monkeypatch


class TestDefaults:
    def test_defaults_without_yaml(self, base_env):
        config = load_config("missing.yaml", "test")
        trading = config.trading

        assert trading.buy_amount_usdc == 5.0
        assert trading.min_liquidity == 15000.0
        assert trading.min_volume_24h == 5000.0
        assert trading.take_profit_percent == 0.12
        assert trading.stop_loss_percent == -0.08
        assert trading.max_positions == -1
        assert trading.reentry_cooldown_hours == 24.0
        assert trading.history_backfill is True
        assert trading.exit_hours == 2
        assert trading.excluded_categories == []
        assert trading.yes_only_mode is False

        ladder = trading.ladder
        assert ladder.entry_hours_min == 6
        assert ladder.rungs() == [
            (24.0, 0.80, 0.95),
            (72.0, 0.75, 0.92),
            (168.0, 0.70, 0.88),
        ]

        gate = trading.momentum_gate
        assert gate.lookback_hours == 6
        assert gate.min_change == -0.01

        assert trading.trailing_stop.enabled is True
        assert trading.trailing_stop.percent == 0.05

    def test_private_key_0x_prefix_stripped(self, base_env):
        config = load_config("missing.yaml", "test")
        assert not config.api.private_key.startswith("0x")
        assert config.api.signature_type == 1
        assert config.api.chain_id == 137

    def test_missing_credentials_raise(self, base_env):
        base_env.delenv("POLYMARKET_PRIVATE_KEY")
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
            load_config("missing.yaml", "test")


class TestEnvOverrides:
    def test_env_overrides_defaults(self, base_env):
        base_env.setenv("POLYBOT_BUY_AMOUNT", "1000")
        base_env.setenv("POLYBOT_TAKE_PROFIT", "0.2")
        base_env.setenv("POLYBOT_LADDER_H2", "96")
        base_env.setenv("POLYBOT_BAND1_MIN", "0.82")
        base_env.setenv("POLYBOT_MOMENTUM_MIN_CHANGE", "-0.02")
        base_env.setenv("POLYBOT_ENTRY_HOURS_MIN", "12")
        base_env.setenv("POLYBOT_EXIT_HOURS", "4")
        base_env.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")

        trading = load_config("missing.yaml", "test").trading

        assert trading.buy_amount_usdc == 1000.0
        assert trading.take_profit_percent == 0.2
        assert trading.ladder.h2 == 96
        assert trading.ladder.band1_min == 0.82
        assert trading.momentum_gate.min_change == -0.02
        assert trading.ladder.entry_hours_min == 12
        assert trading.exit_hours == 4
        assert trading.reentry_cooldown_hours == 48.0

    def test_env_overrides_yaml(self, base_env, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "trading:\n"
            "  min_liquidity: 99999\n"
            "  ladder:\n"
            "    h1: 12\n",
            encoding="utf-8",
        )
        base_env.setenv("POLYBOT_MIN_LIQUIDITY", "12345")

        trading = load_config(str(config_file), "test").trading

        assert trading.min_liquidity == 12345.0  # env > yaml
        assert trading.ladder.h1 == 12           # yaml > 기본값

    def test_bool_env_values(self, base_env):
        base_env.setenv("POLYBOT_HISTORY_BACKFILL", "false")
        base_env.setenv("POLYBOT_TRAILING_STOP_ENABLED", "0")
        base_env.setenv("POLYBOT_YES_ONLY", "true")

        trading = load_config("missing.yaml", "test").trading

        assert trading.history_backfill is False
        assert trading.trailing_stop.enabled is False
        assert trading.yes_only_mode is True

    def test_excluded_categories_env_csv(self, base_env):
        base_env.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NBA ,NFL")
        trading = load_config("missing.yaml", "test").trading
        assert trading.excluded_categories == ["Sports", "NBA", "NFL"]

    def test_excluded_categories_env_empty_disables(self, base_env, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "trading:\n  excluded_categories:\n    - Sports\n", encoding="utf-8"
        )
        base_env.setenv("POLYBOT_EXCLUDED_CATEGORIES", "")
        trading = load_config(str(config_file), "test").trading
        assert trading.excluded_categories == []  # env 빈 문자열 = 필터 비활성

    def test_cli_yes_only_beats_env(self, base_env):
        base_env.setenv("POLYBOT_YES_ONLY", "false")
        config = load_config("missing.yaml", "test", yes_only_mode=True)
        assert config.trading.yes_only_mode is True


class TestSimulationAndPaths:
    def test_simulation_db_path(self, base_env):
        config = load_config("missing.yaml", "myjob", simulation_mode=True)
        assert config.simulation_mode is True
        assert config.db_path.name == "trades_sim.db"
        assert config.db_path.parent.name == "myjob"

    def test_live_db_path(self, base_env):
        config = load_config("missing.yaml", "myjob")
        assert config.simulation_mode is False
        assert config.db_path.name == "trades.db"


class TestLogLevel:
    def test_default_info(self, base_env):
        assert resolve_log_level() == logging.INFO

    def test_env_log_level(self, base_env):
        base_env.setenv("LOG_LEVEL", "debug")
        assert resolve_log_level() == logging.DEBUG
        base_env.setenv("LOG_LEVEL", "WARNING")
        assert resolve_log_level() == logging.WARNING

    def test_verbose_beats_env(self, base_env):
        base_env.setenv("LOG_LEVEL", "ERROR")
        assert resolve_log_level(verbose=True) == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self, base_env):
        base_env.setenv("LOG_LEVEL", "BOGUS")
        assert resolve_log_level() == logging.INFO

"""env > yaml > 기본값 병합 동작 테스트."""
import logging

import pytest

from polybot.config import load_config
from polybot.utils.logger import resolve_log_level

DUMMY_KEY = "0x" + "11" * 32
DUMMY_ADDR = "0x" + "00" * 19 + "01"

# 테스트 간 누수를 막기 위해 초기화할 env 목록
POLYBOT_ENV_KEYS = [
    "POLYBOT_LIFECYCLE_MODE",
    "POLYBOT_BUY_AMOUNT", "POLYBOT_MIN_LIQUIDITY", "POLYBOT_MIN_VOLUME_24H",
    "POLYBOT_TAKE_PROFIT", "POLYBOT_STOP_LOSS", "POLYBOT_MAX_POSITIONS",
    "POLYBOT_REENTRY_COOLDOWN_HOURS", "POLYBOT_HISTORY_BACKFILL",
    "POLYBOT_EXCLUDED_CATEGORIES", "POLYBOT_YES_ONLY",
    "POLYBOT_YIELD_MIN", "POLYBOT_PROB_MIN", "POLYBOT_PROB_MAX",
    "POLYBOT_ENTRY_HOURS_MIN", "POLYBOT_ENTRY_HOURS_MAX", "POLYBOT_EXIT_HOURS",
    "POLYBOT_MOMENTUM_LOOKBACK_HOURS", "POLYBOT_MOMENTUM_MIN_CHANGE",
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

        assert trading.lifecycle_mode == "active"
        assert trading.buy_amount_usdc == 5.0
        assert trading.min_liquidity == 20000.0
        assert trading.min_volume_24h == 0.0
        assert trading.take_profit_percent == 9.99  # 목표가 0.99 캡만 작동
        assert trading.stop_loss_percent == -0.06
        assert trading.max_positions == -1
        assert trading.reentry_cooldown_hours == 24.0
        assert trading.history_backfill is True
        assert trading.exit_hours == 2
        assert trading.excluded_categories == []
        assert trading.yes_only_mode is False

        carry = trading.carry
        assert carry.yield_min == 2.0
        assert carry.prob_min == 0.85
        assert carry.prob_max == 0.985
        assert carry.entry_hours_min == 6
        assert carry.entry_hours_max == 336

        gate = trading.momentum_gate
        assert gate.lookback_hours == 6
        assert gate.min_change == -0.02

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
    def test_lifecycle_mode_env_is_normalized(self, base_env):
        base_env.setenv("POLYBOT_LIFECYCLE_MODE", "close-only")
        trading = load_config("missing.yaml", "test").trading
        assert trading.lifecycle_mode == "close_only"

    def test_lifecycle_mode_yaml_and_env_precedence(self, base_env, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "trading:\n  lifecycle_mode: archive_only\n",
            encoding="utf-8",
        )
        assert (
            load_config(str(config_file), "test").trading.lifecycle_mode
            == "archive_only"
        )

        base_env.setenv("POLYBOT_LIFECYCLE_MODE", "active")
        assert load_config(str(config_file), "test").trading.lifecycle_mode == "active"

    def test_env_overrides_defaults(self, base_env):
        base_env.setenv("POLYBOT_BUY_AMOUNT", "1000")
        base_env.setenv("POLYBOT_YIELD_MIN", "3.5")
        base_env.setenv("POLYBOT_PROB_MIN", "0.88")
        base_env.setenv("POLYBOT_PROB_MAX", "0.97")
        base_env.setenv("POLYBOT_ENTRY_HOURS_MIN", "12")
        base_env.setenv("POLYBOT_ENTRY_HOURS_MAX", "168")
        base_env.setenv("POLYBOT_MOMENTUM_MIN_CHANGE", "-0.01")
        base_env.setenv("POLYBOT_EXIT_HOURS", "4")
        base_env.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")
        base_env.setenv("POLYBOT_STOP_LOSS", "-0.10")

        trading = load_config("missing.yaml", "test").trading

        assert trading.buy_amount_usdc == 1000.0
        assert trading.carry.yield_min == 3.5
        assert trading.carry.prob_min == 0.88
        assert trading.carry.prob_max == 0.97
        assert trading.carry.entry_hours_min == 12
        assert trading.carry.entry_hours_max == 168
        assert trading.momentum_gate.min_change == -0.01
        assert trading.exit_hours == 4
        assert trading.reentry_cooldown_hours == 48.0
        assert trading.stop_loss_percent == -0.10

    def test_env_overrides_yaml(self, base_env, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "trading:\n"
            "  min_liquidity: 99999\n"
            "  carry:\n"
            "    yield_min: 5.0\n",
            encoding="utf-8",
        )
        base_env.setenv("POLYBOT_MIN_LIQUIDITY", "12345")

        trading = load_config(str(config_file), "test").trading

        assert trading.min_liquidity == 12345.0   # env > yaml
        assert trading.carry.yield_min == 5.0     # yaml > 기본값

    def test_bool_env_values(self, base_env):
        base_env.setenv("POLYBOT_HISTORY_BACKFILL", "false")
        base_env.setenv("POLYBOT_YES_ONLY", "true")

        trading = load_config("missing.yaml", "test").trading

        assert trading.history_backfill is False
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

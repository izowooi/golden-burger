"""config 로딩 및 env 오버라이드 동작 테스트 (env > yaml > 기본값)."""
import os

import pytest

from polybot.config import load_config

DUMMY_KEY = "0x" + "11" * 32
DUMMY_FUNDER = "0x" + "00" * 19 + "01"


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    """필수 env 설정 + 잔여 POLYBOT_* env 제거 + tmp cwd."""
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("POLYBOT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_FUNDER)
    return tmp_path


class TestRequiredEnv:
    def test_missing_private_key_raises(self, base_env, monkeypatch):
        monkeypatch.delenv("POLYMARKET_PRIVATE_KEY")
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
            load_config("nonexistent.yaml")

    def test_missing_funder_raises(self, base_env, monkeypatch):
        monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS")
        with pytest.raises(ValueError, match="POLYMARKET_FUNDER_ADDRESS"):
            load_config("nonexistent.yaml")

    def test_0x_prefix_stripped(self, base_env):
        config = load_config("nonexistent.yaml")
        assert not config.api.private_key.startswith("0x")


class TestDefaults:
    def test_strategy_defaults_without_yaml(self, base_env):
        config = load_config("nonexistent.yaml")
        strategy = config.trading.strategy
        assert strategy.yes_min == 0.05
        assert strategy.yes_max == 0.25
        assert strategy.yes_rise_block_24h == 0.02
        assert strategy.yes_spike_block_6h == 0.05
        assert config.trading.time_based.entry_hours_min == 24
        assert config.trading.time_based.entry_hours_max == 240
        assert config.trading.time_based.exit_hours == 2
        assert config.trading.take_profit_percent == 0.06
        assert config.trading.stop_loss_percent == -0.10
        assert config.trading.buy_amount_usdc == 5.0
        assert config.trading.lifecycle_mode == "active"
        assert config.trading.min_liquidity == 10000.0
        assert config.trading.max_positions == -1
        assert config.trading.reentry_cooldown_hours == 24.0
        assert config.trading.history_backfill is True
        assert config.trading.excluded_categories == []


class TestEnvOverrides:
    def test_lifecycle_env_normalizes_hyphen(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "CLOSE-ONLY")
        assert load_config("nonexistent.yaml").trading.lifecycle_mode == "close_only"

    def test_lifecycle_env_overrides_yaml(self, base_env, tmp_path, monkeypatch):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n  lifecycle_mode: archive_only\n", encoding="utf-8"
        )
        monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "active")
        assert load_config(str(yaml_path)).trading.lifecycle_mode == "active"

    def test_lifecycle_yaml_value(self, base_env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n  lifecycle_mode: archive-only\n", encoding="utf-8"
        )
        assert load_config(str(yaml_path)).trading.lifecycle_mode == "archive_only"

    def test_strategy_env_overrides(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_YES_MIN", "0.10")
        monkeypatch.setenv("POLYBOT_YES_MAX", "0.20")
        monkeypatch.setenv("POLYBOT_YES_RISE_BLOCK_24H", "0.03")
        monkeypatch.setenv("POLYBOT_YES_SPIKE_BLOCK_6H", "0.07")
        config = load_config("nonexistent.yaml")
        strategy = config.trading.strategy
        assert strategy.yes_min == 0.10
        assert strategy.yes_max == 0.20
        assert strategy.yes_rise_block_24h == 0.03
        assert strategy.yes_spike_block_6h == 0.07

    def test_trading_env_overrides(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "1000")
        monkeypatch.setenv("POLYBOT_MIN_LIQUIDITY", "30000")
        monkeypatch.setenv("POLYBOT_MIN_VOLUME_24H", "5000")
        monkeypatch.setenv("POLYBOT_TAKE_PROFIT", "0.08")
        monkeypatch.setenv("POLYBOT_STOP_LOSS", "-0.12")
        monkeypatch.setenv("POLYBOT_MAX_POSITIONS", "10")
        monkeypatch.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")
        config = load_config("nonexistent.yaml")
        assert config.trading.buy_amount_usdc == 1000.0
        assert config.trading.min_liquidity == 30000.0
        assert config.trading.min_volume_24h == 5000.0
        assert config.trading.take_profit_percent == 0.08
        assert config.trading.stop_loss_percent == -0.12
        assert config.trading.max_positions == 10
        assert config.trading.reentry_cooldown_hours == 48.0

    def test_time_based_env_overrides(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MIN", "48")
        monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MAX", "120")
        monkeypatch.setenv("POLYBOT_EXIT_HOURS", "4")
        config = load_config("nonexistent.yaml")
        assert config.trading.time_based.entry_hours_min == 48
        assert config.trading.time_based.entry_hours_max == 120
        assert config.trading.time_based.exit_hours == 4

    def test_history_backfill_env_off(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_HISTORY_BACKFILL", "false")
        config = load_config("nonexistent.yaml")
        assert config.trading.history_backfill is False

    def test_excluded_categories_env(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NFL,NBA")
        config = load_config("nonexistent.yaml")
        assert config.trading.excluded_categories == ["Sports", "NFL", "NBA"]

    def test_excluded_categories_env_empty_means_disabled(self, base_env, monkeypatch):
        monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "")
        config = load_config("nonexistent.yaml")
        assert config.trading.excluded_categories == []


class TestYamlAndPriority:
    def test_yaml_values_used_when_no_env(self, base_env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n"
            "  buy_amount_usdc: 7.5\n"
            "  strategy:\n"
            "    yes_max: 0.30\n"
            "  time_based:\n"
            "    exit_hours: 6\n",
            encoding="utf-8",
        )
        config = load_config(str(yaml_path))
        assert config.trading.buy_amount_usdc == 7.5
        assert config.trading.strategy.yes_max == 0.30
        assert config.trading.time_based.exit_hours == 6
        # yaml에 없는 값은 기본값
        assert config.trading.strategy.yes_min == 0.05

    def test_env_beats_yaml(self, base_env, tmp_path, monkeypatch):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n"
            "  buy_amount_usdc: 7.5\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "99")
        config = load_config(str(yaml_path))
        assert config.trading.buy_amount_usdc == 99.0


class TestDbPathAndSimulation:
    def test_simulation_db_separated(self, base_env):
        config = load_config("nonexistent.yaml", job_name="test", simulation_mode=True)
        assert config.db_path.name == "trades_sim.db"
        assert config.db_path.parent.name == "test"

    def test_live_db_path(self, base_env):
        config = load_config("nonexistent.yaml", job_name="prod")
        assert config.db_path.name == "trades.db"
        assert config.db_path.parent.name == "prod"

    def test_yaml_simulation_mode(self, base_env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("simulation_mode: true\n", encoding="utf-8")
        config = load_config(str(yaml_path))
        assert config.simulation_mode is True

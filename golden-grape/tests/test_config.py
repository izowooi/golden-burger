"""env > yaml > 기본값 병합 동작 테스트."""
import pytest

from polybot.config import load_config

DUMMY_KEY = "0x" + "11" * 32
DUMMY_FUNDER = "0x0000000000000000000000000000000000000001"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """필수 env 설정 + 임시 작업 디렉토리 (data/ 오염 방지)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_FUNDER)
    return monkeypatch


def load(config_path="nonexistent.yaml", **kwargs):
    """yaml 없이 로드 (코드 기본값 기준)."""
    return load_config(config_path, **kwargs)


class TestRequiredEnv:
    def test_missing_private_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_FUNDER)
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
            load()

    def test_missing_funder_raises(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
        monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
        with pytest.raises(ValueError, match="POLYMARKET_FUNDER_ADDRESS"):
            load()

    def test_0x_prefix_stripped(self, env):
        config = load()
        assert not config.api.private_key.startswith("0x")


class TestDefaults:
    def test_code_defaults_without_yaml(self, env):
        config = load()
        assert config.trading.buy_amount_usdc == 5.0
        assert config.trading.min_liquidity == 20000.0
        assert config.trading.min_volume_24h == 10000.0
        assert config.trading.take_profit_percent == 0.15
        assert config.trading.stop_loss_percent == -0.08
        assert config.trading.reentry_cooldown_hours == 24
        assert config.trading.history_backfill is True
        assert config.trading.entry_hours_min == 48
        assert config.trading.exit_hours == 24
        assert config.trading.trailing_stop.percent == 0.06
        assert config.trading.cascade.prob_min == 0.40
        assert config.trading.cascade.prob_max == 0.80
        assert config.trading.cascade.drift_min == 0.04
        assert config.trading.cascade.drift_max == 0.10
        assert config.trading.cascade.bucket_hours == 4
        assert config.trading.cascade.consistency_min == 0.70
        assert config.trading.cascade.vol_accel_min == 1.2
        assert config.trading.cascade.death_window_hours == 6
        assert config.trading.excluded_categories == []


class TestEnvOverrides:
    def test_env_overrides_defaults(self, env):
        env.setenv("POLYBOT_BUY_AMOUNT", "25.5")
        env.setenv("POLYBOT_DRIFT_MIN", "0.05")
        env.setenv("POLYBOT_DRIFT_MAX", "0.12")
        env.setenv("POLYBOT_CONSISTENCY_MIN", "0.8")
        env.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")
        env.setenv("POLYBOT_MAX_POSITIONS", "10")
        config = load()
        assert config.trading.buy_amount_usdc == 25.5
        assert config.trading.cascade.drift_min == 0.05
        assert config.trading.cascade.drift_max == 0.12
        assert config.trading.cascade.consistency_min == 0.8
        assert config.trading.reentry_cooldown_hours == 48
        assert config.trading.max_positions == 10

    def test_env_overrides_yaml(self, env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n"
            "  buy_amount_usdc: 7.0\n"
            "  cascade:\n"
            "    drift_min: 0.03\n",
            encoding="utf-8",
        )
        env.setenv("POLYBOT_DRIFT_MIN", "0.06")
        config = load(str(yaml_path))
        assert config.trading.cascade.drift_min == 0.06  # env > yaml
        assert config.trading.buy_amount_usdc == 7.0      # yaml > 기본값

    def test_bool_env_history_backfill(self, env):
        env.setenv("POLYBOT_HISTORY_BACKFILL", "false")
        config = load()
        assert config.trading.history_backfill is False

    def test_excluded_categories_env_comma_separated(self, env):
        env.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NBA ,NFL")
        config = load()
        assert config.trading.excluded_categories == ["Sports", "NBA", "NFL"]

    def test_excluded_categories_empty_env_disables(self, env):
        env.setenv("POLYBOT_EXCLUDED_CATEGORIES", "")
        config = load()
        assert config.trading.excluded_categories == []


class TestDbPath:
    def test_simulation_db_separated(self, env):
        config = load(simulation_mode=True, job_name="test")
        assert config.db_path.name == "trades_sim.db"
        assert config.job_name == "test"

    def test_live_db_path(self, env):
        config = load(job_name="live1")
        assert config.db_path.name == "trades.db"
        assert "live1" in str(config.db_path)

"""config 로드 및 env 오버라이드 동작 검증."""
import pytest

from polybot.config import load_config

DUMMY_KEY = "0x" + "1" * 64
DUMMY_ADDR = "0x" + "0" * 39 + "1"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """필수 env 설정 + data/ 디렉토리를 tmp로 격리."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_ADDR)
    # 워크스테이션 잔여 env가 테스트를 오염시키지 않도록 제거
    for key in [
        "POLYBOT_BUY_AMOUNT", "POLYBOT_MIN_LIQUIDITY", "POLYBOT_MIN_VOLUME_24H",
        "POLYBOT_TAKE_PROFIT", "POLYBOT_STOP_LOSS", "POLYBOT_MAX_POSITIONS",
        "POLYBOT_REENTRY_COOLDOWN_HOURS", "POLYBOT_HISTORY_BACKFILL",
        "POLYBOT_LIFECYCLE_MODE",
        "POLYBOT_EXCLUDED_CATEGORIES", "POLYBOT_REF_WINDOW_HOURS",
        "POLYBOT_REF_EXCLUDE_RECENT_HOURS", "POLYBOT_REF_MIN",
        "POLYBOT_DROP_MIN", "POLYBOT_CURRENT_MIN", "POLYBOT_CURRENT_MAX",
        "POLYBOT_STAB_WINDOW_MINUTES", "POLYBOT_STAB_MAX_STD",
        "POLYBOT_MAX_HOLDING_HOURS", "POLYBOT_ENTRY_HOURS_MIN",
        "POLYBOT_EXIT_HOURS",
    ]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestDefaults:
    def test_strategy_defaults(self, env):
        """yaml 없이 코드 기본값 로드 (스펙 §4.2 기본값)."""
        config = load_config(config_path="nonexistent.yaml")
        t = config.trading
        assert t.buy_amount_usdc == 5.0
        assert t.lifecycle_mode == "active"
        assert t.min_liquidity == 20000.0
        assert t.min_volume_24h == 10000.0
        assert t.take_profit_percent == 0.10
        assert t.stop_loss_percent == -0.10
        assert t.max_positions == -1
        assert t.reentry_cooldown_hours == 24.0
        assert t.history_backfill is True
        assert t.excluded_categories == []
        assert t.strategy.ref_window_hours == 48.0
        assert t.strategy.ref_exclude_recent_hours == 3.0
        assert t.strategy.ref_min == 0.70
        assert t.strategy.drop_min == 0.12
        assert t.strategy.current_min == 0.35
        assert t.strategy.current_max == 0.75
        assert t.strategy.stab_window_minutes == 45.0
        assert t.strategy.stab_max_std == 0.02
        assert t.strategy.max_holding_hours == 48.0
        assert t.time_based.entry_hours_min == 48
        assert t.time_based.exit_hours == 24

    def test_private_key_0x_prefix_stripped(self, env):
        config = load_config(config_path="nonexistent.yaml")
        assert not config.api.private_key.startswith("0x")


class TestRequiredEnv:
    def test_missing_private_key(self, env):
        env.delenv("POLYMARKET_PRIVATE_KEY")
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
            load_config(config_path="nonexistent.yaml")

    def test_missing_funder_address(self, env):
        env.delenv("POLYMARKET_FUNDER_ADDRESS")
        with pytest.raises(ValueError, match="POLYMARKET_FUNDER_ADDRESS"):
            load_config(config_path="nonexistent.yaml")


class TestEnvOverride:
    def test_lifecycle_env_normalizes_hyphen(self, env):
        env.setenv("POLYBOT_LIFECYCLE_MODE", "CLOSE-ONLY")
        assert load_config("nonexistent.yaml").trading.lifecycle_mode == "close_only"

    def test_lifecycle_env_overrides_yaml(self, env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n  lifecycle_mode: archive_only\n", encoding="utf-8"
        )
        env.setenv("POLYBOT_LIFECYCLE_MODE", "active")
        assert load_config(str(yaml_path)).trading.lifecycle_mode == "active"

    def test_lifecycle_yaml_value(self, env, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n  lifecycle_mode: archive-only\n", encoding="utf-8"
        )
        assert load_config(str(yaml_path)).trading.lifecycle_mode == "archive_only"

    def test_env_overrides_defaults(self, env):
        """env > yaml > 기본값 우선순위에서 env가 이긴다."""
        env.setenv("POLYBOT_BUY_AMOUNT", "1000")
        env.setenv("POLYBOT_MIN_LIQUIDITY", "30000")
        env.setenv("POLYBOT_TAKE_PROFIT", "0.15")
        env.setenv("POLYBOT_STOP_LOSS", "-0.05")
        env.setenv("POLYBOT_MAX_POSITIONS", "10")
        env.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "12")
        config = load_config(config_path="nonexistent.yaml")
        t = config.trading
        assert t.buy_amount_usdc == 1000.0
        assert t.min_liquidity == 30000.0
        assert t.take_profit_percent == 0.15
        assert t.stop_loss_percent == -0.05
        assert t.max_positions == 10
        assert t.reentry_cooldown_hours == 12.0

    def test_env_overrides_strategy_params(self, env):
        env.setenv("POLYBOT_REF_WINDOW_HOURS", "72")
        env.setenv("POLYBOT_REF_MIN", "0.65")
        env.setenv("POLYBOT_DROP_MIN", "0.15")
        env.setenv("POLYBOT_CURRENT_MIN", "0.40")
        env.setenv("POLYBOT_CURRENT_MAX", "0.70")
        env.setenv("POLYBOT_STAB_WINDOW_MINUTES", "60")
        env.setenv("POLYBOT_STAB_MAX_STD", "0.03")
        env.setenv("POLYBOT_MAX_HOLDING_HOURS", "24")
        env.setenv("POLYBOT_ENTRY_HOURS_MIN", "72")
        env.setenv("POLYBOT_EXIT_HOURS", "12")
        config = load_config(config_path="nonexistent.yaml")
        s = config.trading.strategy
        assert s.ref_window_hours == 72.0
        assert s.ref_min == 0.65
        assert s.drop_min == 0.15
        assert s.current_min == 0.40
        assert s.current_max == 0.70
        assert s.stab_window_minutes == 60.0
        assert s.stab_max_std == 0.03
        assert s.max_holding_hours == 24.0
        assert config.trading.time_based.entry_hours_min == 72
        assert config.trading.time_based.exit_hours == 12

    def test_env_overrides_yaml(self, env, tmp_path):
        """yaml 값이 있어도 env가 우선한다."""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n"
            "  buy_amount_usdc: 7.0\n"
            "  strategy:\n"
            "    drop_min: 0.20\n",
            encoding="utf-8",
        )
        env.setenv("POLYBOT_BUY_AMOUNT", "99")
        config = load_config(config_path=str(yaml_path))
        assert config.trading.buy_amount_usdc == 99.0
        # env 없는 값은 yaml이 적용
        assert config.trading.strategy.drop_min == 0.20

    def test_history_backfill_bool_env(self, env):
        env.setenv("POLYBOT_HISTORY_BACKFILL", "false")
        config = load_config(config_path="nonexistent.yaml")
        assert config.trading.history_backfill is False

    def test_excluded_categories_env_csv(self, env):
        """POLYBOT_EXCLUDED_CATEGORIES는 comma 구분 문자열."""
        env.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NBA ,NFL")
        config = load_config(config_path="nonexistent.yaml")
        assert config.trading.excluded_categories == ["Sports", "NBA", "NFL"]

    def test_excluded_categories_default_disabled(self, env):
        """기본은 빈 배열 = 필터 비활성 (SPORTS_KEYWORDS 과차단 회피)."""
        config = load_config(config_path="nonexistent.yaml")
        assert config.trading.excluded_categories == []


class TestSimulationMode:
    def test_simulation_db_separation(self, env):
        config = load_config(config_path="nonexistent.yaml", simulation_mode=True)
        assert config.simulation_mode is True
        assert config.db_path.name == "trades_sim.db"

    def test_live_db_default(self, env):
        config = load_config(config_path="nonexistent.yaml")
        assert config.simulation_mode is False
        assert config.db_path.name == "trades.db"

    def test_job_name_db_separation(self, env):
        config = load_config(config_path="nonexistent.yaml", job_name="test-job")
        assert "test-job" in str(config.db_path)

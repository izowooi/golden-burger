"""config.py 병합 로직 (env > yaml > 기본값) 유닛테스트."""
import os

import pytest

from polybot.config import load_config

DUMMY_KEY = "0x" + "11" * 32
DUMMY_FUNDER = "0x" + "00" * 19 + "01"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """POLYBOT_* env 오염 제거 + 필수 인증 env 주입 + tmp cwd (data/ 생성 격리)."""
    for key in list(os.environ):
        if key.startswith("POLYBOT_") or key in ("LOG_LEVEL",):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_FUNDER)
    monkeypatch.chdir(tmp_path)
    yield


def load_defaults(**kwargs):
    """yaml 없이 코드 기본값으로 로드."""
    return load_config(config_path="nonexistent.yaml", **kwargs)


class TestDefaults:
    def test_code_defaults(self):
        config = load_defaults()
        t = config.trading
        assert t.buy_amount_usdc == 5.0
        assert t.min_liquidity == 15000.0
        assert t.min_volume_24h == 0.0
        assert t.take_profit_percent == 0.06
        assert t.stop_loss_percent == -0.06
        assert t.max_positions == -1
        assert t.reentry_cooldown_hours == 24.0
        assert t.history_backfill is True
        assert t.excluded_categories == []
        assert t.quiet.hours_utc == "6-13"
        assert (t.quiet.start_hour, t.quiet.end_hour) == (6, 13)
        assert t.quiet.weekends is True
        assert t.signal.median_lookback_hours == 24
        assert t.signal.dev_min == 0.05
        assert t.signal.vol_spike_block == 1.5
        assert t.signal.entry_prob_min == 0.30
        assert t.signal.entry_prob_max == 0.90
        assert t.time_based.entry_hours_min == 24
        assert t.time_based.exit_hours == 12
        assert t.time_based.max_holding_hours == 24

    def test_private_key_0x_prefix_stripped(self):
        config = load_defaults()
        assert not config.api.private_key.startswith("0x")

    def test_missing_private_key_raises(self, monkeypatch):
        monkeypatch.delenv("POLYMARKET_PRIVATE_KEY")
        with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
            load_defaults()

    def test_missing_funder_raises(self, monkeypatch):
        monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS")
        with pytest.raises(ValueError, match="POLYMARKET_FUNDER_ADDRESS"):
            load_defaults()


class TestEnvOverrides:
    def test_common_env_overrides(self, monkeypatch):
        monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "1000")
        monkeypatch.setenv("POLYBOT_MIN_LIQUIDITY", "50000")
        monkeypatch.setenv("POLYBOT_MIN_VOLUME_24H", "5000")
        monkeypatch.setenv("POLYBOT_TAKE_PROFIT", "0.10")
        monkeypatch.setenv("POLYBOT_STOP_LOSS", "-0.08")
        monkeypatch.setenv("POLYBOT_MAX_POSITIONS", "10")
        monkeypatch.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")
        monkeypatch.setenv("POLYBOT_HISTORY_BACKFILL", "false")

        t = load_defaults().trading
        assert t.buy_amount_usdc == 1000.0
        assert t.min_volume_24h == 5000.0
        assert t.min_liquidity == 50000.0
        assert t.take_profit_percent == 0.10
        assert t.stop_loss_percent == -0.08
        assert t.max_positions == 10
        assert t.reentry_cooldown_hours == 48.0
        assert t.history_backfill is False

    def test_strategy_env_overrides(self, monkeypatch):
        monkeypatch.setenv("POLYBOT_QUIET_HOURS_UTC", "22-4")
        monkeypatch.setenv("POLYBOT_QUIET_WEEKENDS", "false")
        monkeypatch.setenv("POLYBOT_MEDIAN_LOOKBACK_HOURS", "48")
        monkeypatch.setenv("POLYBOT_DEV_MIN", "0.08")
        monkeypatch.setenv("POLYBOT_VOL_SPIKE_BLOCK", "2.0")
        monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", "0.40")
        monkeypatch.setenv("POLYBOT_ENTRY_PROB_MAX", "0.85")
        monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MIN", "48")
        monkeypatch.setenv("POLYBOT_MAX_HOLDING_HOURS", "12")
        monkeypatch.setenv("POLYBOT_EXIT_HOURS", "6")

        t = load_defaults().trading
        assert t.quiet.hours_utc == "22-4"
        assert (t.quiet.start_hour, t.quiet.end_hour) == (22, 4)
        assert t.quiet.weekends is False
        assert t.signal.median_lookback_hours == 48
        assert t.signal.dev_min == 0.08
        assert t.signal.vol_spike_block == 2.0
        assert t.signal.entry_prob_min == 0.40
        assert t.signal.entry_prob_max == 0.85
        assert t.time_based.entry_hours_min == 48
        assert t.time_based.max_holding_hours == 12
        assert t.time_based.exit_hours == 6

    def test_excluded_categories_env_csv(self, monkeypatch):
        """§3.7: comma 구분 env 오버라이드."""
        monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NFL ,NBA")
        t = load_defaults().trading
        assert t.excluded_categories == ["Sports", "NFL", "NBA"]

    def test_excluded_categories_env_empty_disables(self, monkeypatch, tmp_path):
        """env가 빈 문자열이면 yaml 값이 있어도 필터 비활성."""
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n  excluded_categories:\n    - Sports\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "")
        config = load_config(config_path=str(yaml_path))
        assert config.trading.excluded_categories == []

    def test_invalid_quiet_hours_raises(self, monkeypatch):
        monkeypatch.setenv("POLYBOT_QUIET_HOURS_UTC", "25-30")
        with pytest.raises(ValueError):
            load_defaults()


class TestYamlAndPriority:
    def test_env_beats_yaml(self, monkeypatch, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            "trading:\n"
            "  buy_amount_usdc: 7.0\n"
            "  signal:\n"
            "    dev_min: 0.10\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("POLYBOT_DEV_MIN", "0.03")

        config = load_config(config_path=str(yaml_path))
        # env가 yaml을 이긴다
        assert config.trading.signal.dev_min == 0.03
        # env가 없으면 yaml이 기본값을 이긴다
        assert config.trading.buy_amount_usdc == 7.0

    def test_simulation_db_path_separated(self):
        sim = load_defaults(job_name="testjob", simulation_mode=True)
        real = load_defaults(job_name="testjob", simulation_mode=False)
        assert sim.db_path.name == "trades_sim.db"
        assert real.db_path.name == "trades.db"
        assert sim.db_path.parent == real.db_path.parent

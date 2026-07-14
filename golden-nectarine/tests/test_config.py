"""env > yaml > 기본값 병합 동작 검증."""
import os

import pytest

from polybot.config import load_config

DUMMY_KEY = "0x" + "11" * 32
DUMMY_ADDR = "0x" + "00" * 19 + "01"


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    """더미 인증 env 설정 + POLYBOT_* 누수 제거 + tmp cwd (data/ 오염 방지)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_ADDR)
    for key in list(os.environ):
        if key.startswith("POLYBOT_"):
            monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_defaults_without_yaml(base_env):
    """yaml이 없으면 코드 기본값 사용."""
    config = load_config("nonexistent.yaml")

    assert config.trading.lifecycle_mode == "active"
    assert config.trading.buy_amount_usdc == 5.0
    assert config.trading.min_liquidity == 10000.0
    assert config.trading.min_volume_24h == 0.0
    assert config.trading.take_profit_percent == 0.30
    assert config.trading.stop_loss_percent == -0.30
    assert config.trading.max_positions == -1
    assert config.trading.reentry_cooldown_hours == 168.0  # 7일 (연속 재진입 방지)
    assert config.trading.history_backfill is True
    assert config.trading.strategy.lookback_days == 20.0
    assert config.trading.strategy.exclude_recent_hours == 24.0
    assert config.trading.strategy.hold_hours == 120.0
    assert config.trading.strategy.prob_min == 0.03
    assert config.trading.strategy.prob_max == 0.50
    assert config.trading.time_based.entry_hours_min == 720
    assert config.trading.time_based.exit_hours == 24
    assert config.trading.excluded_categories == []


def test_env_overrides_defaults(base_env, monkeypatch):
    """POLYBOT_* env가 기본값을 오버라이드한다."""
    monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "1000")
    monkeypatch.setenv("POLYBOT_MIN_LIQUIDITY", "50000")
    monkeypatch.setenv("POLYBOT_MIN_VOLUME_24H", "30000")
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT", "0.20")
    monkeypatch.setenv("POLYBOT_STOP_LOSS", "-0.05")
    monkeypatch.setenv("POLYBOT_MAX_POSITIONS", "3")
    monkeypatch.setenv("POLYBOT_REENTRY_COOLDOWN_HOURS", "48")
    monkeypatch.setenv("POLYBOT_HISTORY_BACKFILL", "false")
    monkeypatch.setenv("POLYBOT_LOOKBACK_DAYS", "10")
    monkeypatch.setenv("POLYBOT_EXCLUDE_RECENT_HOURS", "12")
    monkeypatch.setenv("POLYBOT_HOLD_HOURS", "72")
    monkeypatch.setenv("POLYBOT_PROB_MIN", "0.05")
    monkeypatch.setenv("POLYBOT_PROB_MAX", "0.40")
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MIN", "480")
    monkeypatch.setenv("POLYBOT_EXIT_HOURS", "48")

    config = load_config("nonexistent.yaml")

    assert config.trading.buy_amount_usdc == 1000.0
    assert config.trading.min_liquidity == 50000.0
    assert config.trading.min_volume_24h == 30000.0
    assert config.trading.take_profit_percent == 0.20
    assert config.trading.stop_loss_percent == -0.05
    assert config.trading.max_positions == 3
    assert config.trading.reentry_cooldown_hours == 48.0
    assert config.trading.history_backfill is False
    assert config.trading.strategy.lookback_days == 10.0
    assert config.trading.strategy.exclude_recent_hours == 12.0
    assert config.trading.strategy.hold_hours == 72.0
    assert config.trading.strategy.prob_min == 0.05
    assert config.trading.strategy.prob_max == 0.40
    assert config.trading.time_based.entry_hours_min == 480
    assert config.trading.time_based.exit_hours == 48


def test_lifecycle_mode_env_is_normalized(base_env, monkeypatch):
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "archive-only")
    config = load_config("nonexistent.yaml")
    assert config.trading.lifecycle_mode == "archive_only"


def test_lifecycle_mode_can_be_loaded_from_yaml(base_env, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "trading:\n  lifecycle_mode: close_only\n",
        encoding="utf-8",
    )
    config = load_config(str(config_file))
    assert config.trading.lifecycle_mode == "close_only"


def test_env_overrides_yaml(base_env, monkeypatch, tmp_path):
    """우선순위: env > yaml > 기본값."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "trading:\n"
        "  buy_amount_usdc: 7.0\n"
        "  strategy:\n"
        "    lookback_days: 30\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "9.0")

    config = load_config(str(config_file))

    assert config.trading.buy_amount_usdc == 9.0           # env가 yaml을 이김
    assert config.trading.strategy.lookback_days == 30.0   # env 없음 → yaml
    assert config.trading.strategy.hold_hours == 120.0     # 둘 다 없음 → 기본값


def test_excluded_categories_env_parsing(base_env, monkeypatch):
    """POLYBOT_EXCLUDED_CATEGORIES: comma 구분 파싱."""
    monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "Sports, NBA ,NFL")
    config = load_config("nonexistent.yaml")
    assert config.trading.excluded_categories == ["Sports", "NBA", "NFL"]


def test_excluded_categories_empty_env_disables_filter(base_env, monkeypatch):
    """빈 문자열 env = 필터 비활성 (기본 동작 유지)."""
    monkeypatch.setenv("POLYBOT_EXCLUDED_CATEGORIES", "")
    config = load_config("nonexistent.yaml")
    assert config.trading.excluded_categories == []


def test_signature_type_env_override(base_env, monkeypatch):
    """2026+ 신규 계정(POLY_1271 스마트 지갑)은 POLYMARKET_SIGNATURE_TYPE=3 으로 설정한다."""
    monkeypatch.setenv("POLYMARKET_SIGNATURE_TYPE", "3")
    config = load_config("nonexistent.yaml")
    assert config.api.signature_type == 3


def test_signature_type_defaults_to_poly_proxy(base_env, monkeypatch):
    """env 미설정 시 구형 계정 호환 기본값 1 유지."""
    monkeypatch.delenv("POLYMARKET_SIGNATURE_TYPE", raising=False)
    config = load_config("nonexistent.yaml")
    assert config.api.signature_type == 1


def test_missing_private_key_raises(base_env, monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY")
    with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
        load_config("nonexistent.yaml")


def test_missing_funder_address_raises(base_env, monkeypatch):
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS")
    with pytest.raises(ValueError, match="POLYMARKET_FUNDER_ADDRESS"):
        load_config("nonexistent.yaml")


def test_private_key_0x_prefix_stripped(base_env):
    config = load_config("nonexistent.yaml")
    assert not config.api.private_key.startswith("0x")
    assert config.api.private_key == "11" * 32
    assert config.api.signature_type == 1
    assert config.api.chain_id == 137


def test_simulation_mode_uses_separate_db(base_env):
    """시뮬레이션은 trades_sim.db로 분리 (실거래 기록과 격리)."""
    live = load_config("nonexistent.yaml", job_name="test")
    sim = load_config("nonexistent.yaml", job_name="test", simulation_mode=True)

    assert live.db_path.name == "trades.db"
    assert sim.db_path.name == "trades_sim.db"
    assert live.db_path.parent == sim.db_path.parent

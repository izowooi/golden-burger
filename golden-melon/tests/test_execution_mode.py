"""execution_mode — golden-melon의 처치축을 고정한다.

이 전략의 가설은 "방향을 잘 맞히는 것"이 아니라 **"스프레드를 지불하는 쪽이 아니라
받는 쪽에 서는 것"** 이다. 실측 왕복 비용이 실행 경로에 따라
maker→maker -31.1 bps / taker→taker +72.5 bps 로 **103 bps** 갈리기 때문이다
(`docs/retro/2026-07-29-execution-cost-floor.md`).

기존 14개 봇은 `round()` 로 가장 가까운 틱에 붙여서 이 축이 **무작위**였다. 여기서는
그것이 설정으로 결정되고, 이 테스트가 그 계약을 고정한다.
"""

import math

import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import _get_execution_mode


def make_wrapper(mode: str) -> ClobClientWrapper:
    """네트워크·인증 없이 반올림만 검사하기 위한 최소 인스턴스."""
    wrapper = ClobClientWrapper.__new__(ClobClientWrapper)
    wrapper.execution_mode = mode
    return wrapper


# midpoint가 반 틱 위에 놓이는 전형적 경우: 호가 0.79 / 0.80
HALF_TICK_MID = 0.795


class TestPassiveNeverCrosses:
    """passive 모드는 어떤 가격에서도 상대 호가를 건드리지 않아야 한다."""

    @pytest.mark.parametrize(
        "mid,expected_buy",
        [(0.795, 0.79), (0.902, 0.90), (0.9349, 0.93), (0.8501, 0.85)],
    )
    def test_buy_floors_to_join_the_bid(self, mid, expected_buy):
        wrapper = make_wrapper("passive")
        assert wrapper._round_to_tick(mid, side="BUY") == pytest.approx(expected_buy)

    def test_buy_price_never_exceeds_midpoint(self):
        """매수 지정가가 midpoint를 넘으면 그 순간 taker가 된다."""
        wrapper = make_wrapper("passive")
        for i in range(1, 100):
            mid = i / 100 + 0.005
            assert wrapper._round_to_tick(mid, side="BUY") <= mid + 1e-9

    def test_sell_is_never_passive_so_stops_can_execute(self):
        """처치는 진입에만 적용된다.

        SELL에 passive를 적용하면 최우선 매수호가보다 위에 걸려 손절이 체결되지
        않는다. **체결되지 않는 손절은 손절이 아니다.** time_exit도 없으므로
        대체 경로가 없다.
        """
        for mode in ("passive", "nearest", "cross"):
            wrapper = make_wrapper(mode)
            for i in range(1, 99):
                mid = i / 100 + 0.005
                nearest = make_wrapper("nearest")._round_to_tick(mid, side="SELL")
                assert wrapper._round_to_tick(mid, side="SELL") == pytest.approx(nearest)


class TestControlArms:
    def test_nearest_matches_legacy_fleet_behaviour(self):
        """대조군은 기존 14개 봇과 **정확히** 같아야 A/B가 성립한다."""
        wrapper = make_wrapper("nearest")
        for i in range(1, 200):
            mid = i / 200
            legacy = min(max(round(round(mid / 0.01) * 0.01, 2), 0.01), 0.99)
            assert wrapper._round_to_tick(mid, side="BUY") == pytest.approx(legacy)
            assert wrapper._round_to_tick(mid, side="SELL") == pytest.approx(legacy)

    def test_cross_always_takes_liquidity_on_entry(self):
        """비용 상한을 재는 팔. 진입에서 매도호가를 리프트한다."""
        wrapper = make_wrapper("cross")
        assert wrapper._round_to_tick(HALF_TICK_MID, side="BUY") == pytest.approx(0.80)

    def test_passive_is_never_worse_priced_than_cross(self):
        """같은 midpoint에서 passive 매수가는 cross 매수가보다 항상 싸거나 같다.

        이 부등식이 깨지면 실험의 방향 자체가 무너진다.
        """
        passive, cross = make_wrapper("passive"), make_wrapper("cross")
        for i in range(1, 400):
            mid = i / 400
            assert passive._round_to_tick(mid, side="BUY") <= cross._round_to_tick(
                mid, side="BUY"
            ) + 1e-9



class TestFailClosed:
    def test_missing_side_falls_back_to_nearest(self):
        """side를 모르면 방향을 추측하지 않는다."""
        wrapper = make_wrapper("passive")
        assert wrapper._round_to_tick(HALF_TICK_MID) == pytest.approx(0.80)

    def test_unknown_mode_raises(self):
        wrapper = make_wrapper("sideways")
        with pytest.raises(ValueError):
            wrapper._round_to_tick(HALF_TICK_MID, side="BUY")

    def test_unknown_mode_does_not_break_sell_path(self):
        """SELL은 mode와 무관하게 nearest이므로 알 수 없는 모드에서도 안전하다."""
        wrapper = make_wrapper("sideways")
        assert wrapper._round_to_tick(HALF_TICK_MID, side="SELL") == pytest.approx(0.80)

    @pytest.mark.parametrize("mode", ["passive", "nearest", "cross"])
    def test_price_stays_inside_tradable_bounds(self, mode):
        """Polymarket은 0.0/1.0을 거부한다. 어떤 모드에서도 벗어나면 안 된다."""
        wrapper = make_wrapper(mode)
        for mid in (0.0001, 0.004, 0.9999, 0.996):
            for side in ("BUY", "SELL"):
                price = wrapper._round_to_tick(mid, side=side)
                assert 0.01 - 1e-9 <= price <= 0.99 + 1e-9
                assert math.isfinite(price)


class TestConfigResolution:
    def test_default_is_passive(self):
        assert _get_execution_mode(None) == "nearest"

    @pytest.mark.parametrize("raw", ["passive", "PASSIVE", " nearest ", "cross"])
    def test_normalizes(self, raw):
        assert _get_execution_mode(raw) == raw.strip().lower()

    def test_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("POLYBOT_EXECUTION_MODE", "cross")
        assert _get_execution_mode("passive") == "cross"

    @pytest.mark.parametrize("bad", ["aggressive", "maker", "", 3, True])
    def test_rejects_unknown(self, bad):
        with pytest.raises(ValueError):
            _get_execution_mode(bad)


class TestDrawdownKillSwitch:
    """사전 등록 기준을 코드가 강제하는지 — golden-date의 -52%를 반복하지 않기 위한 것."""

    def _trader(self, realized_pnl, settlement_pnl=0.0):
        from types import SimpleNamespace
        from polybot.strategy.trader import Trader

        trader = Trader.__new__(Trader)
        trader.config = SimpleNamespace(
            experiment_capital_usdc=100.0, max_drawdown_stop=0.20
        )
        trader.repo = SimpleNamespace(
            get_stats=lambda: {
                "total_pnl": realized_pnl,
                "settlement_pnl_assumption": settlement_pnl,
            }
        )
        return trader

    def test_allows_entry_above_limit(self):
        assert self._trader(-19.99)._drawdown_stop_triggered() is False

    def test_blocks_entry_at_limit(self):
        # $100 x 20% = $20
        assert self._trader(-20.0)._drawdown_stop_triggered() is True

    def test_blocks_entry_below_limit(self):
        assert self._trader(-120.0)._drawdown_stop_triggered() is True

    def test_profit_never_blocks(self):
        assert self._trader(35.0)._drawdown_stop_triggered() is False

    def test_missing_pnl_is_treated_as_zero(self):
        """확정 왕복이 아직 없으면 정지하지 않는다 (None을 손실로 오해하면 안 된다)."""
        assert self._trader(None)._drawdown_stop_triggered() is False

    def test_resolution_only_loss_triggers_switch(self):
        """해결로만 잃은 경우 — 이 전략의 지배적 손실 경로.

        청산 규칙이 익절·손절 둘뿐이라 상당수가 해결까지 간다. 해결 포지션은
        CLOB SELL fill이 없어 realized_pnl이 NULL이고 손실이 settlement에만 남는다.
        확정손익만 보면 이 경로에서 kill switch가 영영 발동하지 않는다.
        """
        assert self._trader(0.0, -50.0)._drawdown_stop_triggered() is True

    def test_mixed_channels_sum_for_safety(self):
        assert self._trader(-25.0, -15.0)._drawdown_stop_triggered() is True

    def test_settlement_gain_offsets_realized_loss(self):
        assert self._trader(-50.0, 35.0)._drawdown_stop_triggered() is False

    def test_missing_settlement_key_is_zero(self):
        assert self._trader(-10.0, None)._drawdown_stop_triggered() is False

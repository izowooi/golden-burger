"""Micro-Cascade simulated execution, time exit, and resolution evidence."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import polybot.strategy.trader as trader_module
from polybot.api.clob_client import BuyBookDepth
from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.db.repository import DrawdownEvaluation
from polybot.strategy.trader import Trader


class FakeRepo:
    def __init__(self):
        self.created = None
        self.updated = None
        self.position_count = 0
        self.open_notional = 0.0
        self.condition_allowed = (True, "ok")
        self.event_allowed = (True, "ok")
        self.economic_pnl = 0.0
        self.drawdown_state = None
        self.holdings = []

    def get_stats(self):
        return {
            "research_economic_pnl": self.economic_pnl,
            "drawdown_kill_switch_tripped": self.drawdown_state is not None,
            "drawdown_kill_switch": self.drawdown_state,
        }

    def get_drawdown_kill_switch(self):
        return self.drawdown_state

    def strict_terminal_economic_path(
        self, *, current_run_id_value, loss_limit_usdc
    ):
        del current_run_id_value
        return DrawdownEvaluation(
            economic_pnl=self.economic_pnl,
            tripped=self.economic_pnl <= -loss_limit_usdc,
            trip_economic_pnl=(
                self.economic_pnl
                if self.economic_pnl <= -loss_limit_usdc
                else None
            ),
            source_terminal_run_id=(
                "terminal-success"
                if self.economic_pnl <= -loss_limit_usdc
                else None
            ),
            terminal_trade_count=1 if self.economic_pnl else 0,
        )

    def stage_drawdown_kill_switch(self, **kwargs):
        if self.drawdown_state is None:
            self.drawdown_state = {
                "schema_version": 1,
                "tripped": True,
                "tripped_at": "2026-07-30T00:00:00Z",
                "tripped_run_id": kwargs["source_terminal_run_id"],
                "economic_pnl": kwargs["economic_pnl"],
                "loss_limit_usdc": kwargs["loss_limit_usdc"],
                "experiment_capital_usdc": kwargs[
                    "experiment_capital_usdc"
                ],
                "max_drawdown_stop": kwargs["max_drawdown_stop"],
            }
        return {
            **self.drawdown_state,
            "source_terminal_run_id": self.drawdown_state["tripped_run_id"],
        }

    def can_reenter(self, *_args):
        return self.condition_allowed

    def can_enter_event(self, *_args):
        return self.event_allowed

    def get_position_count(self):
        return self.position_count

    def get_open_notional_usdc(self):
        return self.open_notional

    def create_trade(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(id=7, **kwargs)

    def update_trade(self, trade_id, **kwargs):
        self.updated = {"id": trade_id, **kwargs}
        return SimpleNamespace(id=trade_id, **kwargs)

    def save_market_catalog(self, *_args, **_kwargs):
        return None

    def get_holding_trades(self):
        return self.holdings


class FakeClob:
    simulation_mode = True

    def __init__(self, midpoint=0.42, best_bid=0.41, best_ask=0.42):
        self.midpoint = midpoint
        self.best_bid = best_bid
        self.best_ask = best_ask
        self.orders = []
        self.midpoint_calls = 0
        self.book_calls = 0
        self.depth = BuyBookDepth(
            best_bid=0.415,
            best_ask=0.425,
            spread=0.01,
            ask_depth_shares=100,
            ask_limit_price=0.43,
        )

    def get_midpoint(self, _token):
        self.midpoint_calls += 1
        if isinstance(self.midpoint, Exception):
            raise self.midpoint
        return self.midpoint

    def get_buy_book_depth(self, *_args, **_kwargs):
        self.book_calls += 1
        return self.depth

    def get_best_bid(self, _token):
        self.book_calls += 1
        if isinstance(self.best_bid, Exception):
            raise self.best_bid
        return self.best_bid

    def get_best_ask(self, _token):
        self.book_calls += 1
        if isinstance(self.best_ask, Exception):
            raise self.best_ask
        return self.best_ask

    def place_limit_order(self, token_id, price, size, side):
        self.orders.append((token_id, price, size, side))
        return {"success": True, "orderID": f"SIM_{side}"}


def candidate():
    now = datetime.now(timezone.utc)
    timestamps = [now - timedelta(minutes=value) for value in (15, 10, 5, 0)]
    return {
        "condition_id": "condition-1",
        "market_slug": "market-1",
        "question": "Will the staircase persist?",
        "event_id": "event-1",
        "event_slug": "event-1",
        "outcome": "Yes",
        "token_id": "yes-token",
        "trend_prices": [0.40, 0.407, 0.414, 0.42],
        "trend_gap_minutes": [5, 5, 5],
        "trend_snapshot_ids": [10, 11, 12, 13],
        "trend_snapshot_timestamps": timestamps,
        "trend_start_snapshot_id": 10,
        "prior_snapshot_id": 12,
        "entry_snapshot_id": 13,
        "liquidity": 30_000,
        "volume_24h": 12_000,
        "signal_best_bid": 0.415,
        "signal_best_ask": 0.425,
        "signal_spread": 0.01,
        "end_date": now + timedelta(hours=8),
        "hours_until_resolution": 8,
        "market_tags": "",
    }


@pytest.fixture(autouse=True)
def current_run(monkeypatch):
    monkeypatch.setattr(trader_module, "current_run_id", lambda: "run-success-pending")


def make_trader(repo=None, clob=None, gamma=None):
    repo = repo or FakeRepo()
    clob = clob or FakeClob()
    return Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=gamma,
        simulation_mode=True,
    ), repo, clob


def trade(*, minutes_ago=61, **overrides):
    values = {
        "id": 9,
        "condition_id": "condition-1",
        "token_id": "yes-token",
        "buy_price": 0.43,
        "buy_shares": 10.0,
        "buy_order_id": "SIM_BUY",
        "buy_timestamp": datetime.utcnow() - timedelta(minutes=minutes_ago),
        "hold_minutes_target_at_entry": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_live_trader_construction_is_hard_blocked():
    with pytest.raises(RuntimeError, match="research/simulation-only"):
        Trader(
            FakeRepo(),
            SimpleNamespace(simulation_mode=False),
            TradingConfig(),
            simulation_mode=False,
        )


def test_buy_revalidates_fresh_staircase_and_records_all_evidence():
    trader, repo, clob = make_trader()
    assert trader.execute_buy(candidate()) == 7
    assert clob.orders[0][3] == "BUY"
    created = repo.created
    assert created["status"] == TradeStatus.HOLDING
    assert created["mode"] == "sim"
    assert created["strategy_name"] == "kiwi"
    assert created["trend_start_snapshot_id_at_entry"] == 10
    assert created["prior_snapshot_id_at_entry"] == 12
    assert created["entry_snapshot_id"] == 13
    assert created["entry_run_id"] == "run-success-pending"
    assert created["trend_snapshot_ids_json"] == "[10, 11, 12, 13]"
    assert created["trend_persisted_prices_json"] == "[0.4, 0.407, 0.414, 0.42]"
    assert created["trend_decision_prices_json"] == "[0.4, 0.407, 0.414, 0.42]"
    assert created["trend_gap_minutes_json"] == "[5, 5, 5]"
    assert created["trend_decision_timestamps_json"]
    assert created["trend_decision_gap_minutes_json"]
    assert created["decision_observed_at_at_entry"] == created["buy_timestamp"]
    assert (
        created["decision_price_source_at_entry"]
        == "clob_single_order_book_midpoint"
    )
    assert created["confirmation_steps_at_entry"] == 3
    assert created["cumulative_move_at_entry"] == pytest.approx(0.02)
    assert created["min_snapshot_gap_minutes_at_entry"] == pytest.approx(5)
    assert created["max_snapshot_gap_minutes_at_entry"] == pytest.approx(5)
    assert created["signal_best_bid_at_entry"] == pytest.approx(0.415)
    assert created["signal_best_ask_at_entry"] == pytest.approx(0.425)
    assert created["signal_spread_at_entry"] == pytest.approx(0.01)
    assert created["hold_minutes_target_at_entry"] == pytest.approx(60)
    assert created["stop_price_at_entry"] is None
    assert created["take_profit_price_at_entry"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(trend_snapshot_ids=[10, 11, 13]),
        lambda row: row.update(trend_snapshot_ids=[10, 11, 13, 12]),
        lambda row: row.update(prior_snapshot_id=11),
        lambda row: row.update(event_id=""),
        lambda row: row.update(outcome="No"),
    ],
)
def test_invalid_identity_or_lineage_never_submits(mutation):
    row = candidate()
    mutation(row)
    trader, repo, clob = make_trader()
    assert trader.execute_buy(row) is None
    assert repo.created is None
    assert clob.orders == []


def test_single_fresh_book_that_breaks_last_positive_step_blocks_entry():
    clob = FakeClob(midpoint=0.99)
    clob.depth = BuyBookDepth(
        best_bid=0.405,
        best_ask=0.415,
        spread=0.01,
        ask_depth_shares=100,
        ask_limit_price=0.425,
    )
    trader, repo, clob = make_trader(clob=clob)
    assert trader.execute_buy(candidate()) is None
    assert repo.created is None
    assert clob.orders == []
    assert clob.midpoint_calls == 0
    assert clob.book_calls == 1


def test_event_cooldown_and_exposure_caps_are_enforced():
    repo = FakeRepo()
    repo.event_allowed = (False, "event_close_cooldown")
    trader, _, clob = make_trader(repo=repo)
    assert trader.execute_buy(candidate()) is None
    assert clob.orders == []

    repo.event_allowed = (True, "ok")
    repo.open_notional = 15
    assert trader.execute_buy(candidate()) is None
    assert clob.orders == []


@pytest.mark.parametrize("pnl", [-20.0, -50.0])
def test_drawdown_kill_switch_blocks_at_or_below_preregistered_limit(pnl):
    repo = FakeRepo()
    repo.economic_pnl = pnl
    trader, _, clob = make_trader(repo=repo)
    assert trader.evaluate_drawdown_stop() is True
    assert trader.execute_buy(candidate()) is None
    assert trader.buying_disabled is True
    assert clob.orders == []


def test_drawdown_just_above_limit_allows_entry():
    repo = FakeRepo()
    repo.economic_pnl = -19.99
    trader, _, _ = make_trader(repo=repo)
    assert trader.evaluate_drawdown_stop() is False
    assert trader.execute_buy(candidate()) == 7


def test_drawdown_latch_survives_new_trader_and_later_pnl_recovery():
    repo = FakeRepo()
    repo.economic_pnl = -20.0
    first, _, first_clob = make_trader(repo=repo)
    assert first.evaluate_drawdown_stop() is True
    assert first.execute_buy(candidate()) is None
    assert repo.drawdown_state["economic_pnl"] == -20.0
    assert first_clob.orders == []

    repo.economic_pnl = 10.0
    second, _, second_clob = make_trader(repo=repo)
    assert second.evaluate_drawdown_stop() is True
    assert second.execute_buy(candidate()) is None
    assert second.buying_disabled is True
    assert repo.drawdown_state["economic_pnl"] == -20.0
    assert second_clob.orders == []


def test_before_60_minutes_exit_does_not_even_read_clob():
    trader, repo, clob = make_trader()
    assert trader.execute_sell(trade(minutes_ago=59)) is False
    assert repo.updated is None
    assert clob.midpoint_calls == 0
    assert clob.book_calls == 0
    assert clob.orders == []


def test_first_cycle_after_60_minutes_exits_at_fresh_bid_and_records_delay():
    trader, repo, clob = make_trader(
        clob=FakeClob(
            midpoint=RuntimeError("midpoint endpoint unavailable"),
            best_ask=RuntimeError("ask endpoint unavailable"),
        )
    )
    assert trader.execute_sell(trade(minutes_ago=67)) is True
    assert clob.midpoint_calls == 0
    assert clob.book_calls == 1
    assert clob.orders[0][1] == pytest.approx(0.41)
    assert clob.orders[0][3] == "SELL"
    update = repo.updated
    assert update["status"] == TradeStatus.COMPLETED
    assert update["exit_reason"] == "time_exit_simulation_hypothetical"
    assert update["hold_minutes_observed_at_exit"] >= 67
    assert update["exit_delay_minutes"] >= 7
    assert update["promotion_eligible"] == 1
    assert update["promotion_exclusion_reason"] is None
    assert update["exit_run_id"] == "run-success-pending"
    assert update["realized_pnl"] is None
    assert update["hypothetical_pnl"] == pytest.approx(-0.2)
    assert update["best_ask_at_exit"] is None
    assert update["spread_at_exit"] is None


def test_exit_after_75_minutes_is_recorded_but_mechanically_censored():
    trader, repo, _clob = make_trader()
    assert trader.execute_sell(trade(minutes_ago=76)) is True
    assert repo.updated["status"] == TradeStatus.COMPLETED
    assert repo.updated["exit_delay_minutes"] >= 16
    assert repo.updated["promotion_eligible"] == 0
    assert (
        repo.updated["promotion_exclusion_reason"]
        == "exit_observed_after_75m_window"
    )


def resolved_market():
    return {
        "conditionId": "condition-1",
        "closed": True,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [1.0, 0.0],
        "clobTokenIds": ["yes-token", "no-token"],
        "negRisk": False,
        "umaResolutionStatus": "resolved",
    }


def test_resolution_is_recorded_separately_without_synthetic_sell():
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: resolved_market()
    )
    trader, repo, clob = make_trader(
        clob=FakeClob(best_bid=RuntimeError("no book")),
        gamma=gamma,
    )
    assert trader.execute_sell(trade(minutes_ago=61)) is False
    update = repo.updated
    assert update["status"] == TradeStatus.RESOLVED
    assert update["exit_reason"] == "resolved_with_payout_evidence"
    assert update["resolution_value"] == 1.0
    assert update["sell_order_id"] is None
    assert update["realized_pnl"] is None
    assert update["promotion_eligible"] == 0
    assert (
        update["promotion_exclusion_reason"]
        == "resolved_before_valid_60_75m_exit"
    )
    assert clob.orders == []


def test_check_and_sell_holdings_counts_only_completed_time_exits(monkeypatch):
    repo = FakeRepo()
    repo.holdings = [trade(minutes_ago=61), trade(minutes_ago=59, id=10)]
    trader, _, _ = make_trader(repo=repo)
    outcomes = iter([True, False])
    monkeypatch.setattr(trader, "execute_sell", lambda _trade: next(outcomes))
    assert trader.check_and_sell_holdings() == 1

"""Crown Momentum order-book execution and settlement-evidence contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader


class FakeClob:
    def __init__(
        self,
        *,
        midpoint=0.91,
        best_bid=0.90,
        best_ask=0.91,
        depth_shares=100.0,
        order_result=None,
        simulation_mode=False,
    ):
        self.midpoint = midpoint
        self.best_bid = best_bid
        self.best_ask = best_ask
        self.depth_shares = depth_shares
        self.order_result = order_result or {
            "success": True,
            "orderID": "TEST_ORDER",
        }
        self.simulation_mode = simulation_mode
        self.orders = []
        self.cancelled = []

    @staticmethod
    def _value(value):
        if isinstance(value, Exception):
            raise value
        return value

    def get_midpoint(self, _token_id):
        return self._value(self.midpoint)

    def get_best_bid(self, _token_id):
        return self._value(self.best_bid)

    def get_best_ask(self, _token_id):
        return self._value(self.best_ask)

    def get_buy_book_depth(
        self,
        _token_id,
        *,
        ask_limit_price,
        max_price_window,
    ):
        best_bid = float(self._value(self.best_bid))
        best_ask = float(self._value(self.best_ask))
        limit = min(float(ask_limit_price), best_ask + float(max_price_window))
        return SimpleNamespace(
            best_bid=best_bid,
            best_ask=best_ask,
            spread=best_ask - best_bid,
            ask_depth_shares=self._value(self.depth_shares),
            ask_limit_price=limit,
        )

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        if callable(self.order_result):
            return self.order_result(kwargs, len(self.orders))
        return dict(self.order_result)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}


class FakeRepo:
    def __init__(
        self,
        *,
        positions=0,
        event_positions=0,
        open_notional=0.0,
        can_reenter=True,
        fill_evidence=None,
        sell_fill_evidence=None,
    ):
        self.positions = positions
        self.event_positions = event_positions
        self.open_notional = open_notional
        self.can_reenter_result = (can_reenter, "ok" if can_reenter else "holding")
        self.updates = []
        self.created = []
        self.catalog = []
        self.holdings = []
        self.fill_evidence = fill_evidence or ExactFillEvidence(
            "confirmed",
            "0xBUY",
            order_status="MATCHED",
            side="BUY",
            requested_size=5.2,
            latest_size_matched=5.2,
            needs_reconciliation=False,
            reconciled_full_fill=True,
            confirmed_size=5.2,
            confirmed_vwap=0.955,
            confirmed_fee_usdc=0.0,
            fee_complete=True,
        )
        self.sell_fill_evidence = sell_fill_evidence or ExactFillEvidence(
            "confirmed",
            "0xSELL",
            order_status="MATCHED",
            side="SELL",
            requested_size=5.2,
            latest_size_matched=5.2,
            needs_reconciliation=False,
            reconciled_full_fill=True,
            confirmed_size=5.2,
            confirmed_vwap=0.885,
            confirmed_fee_usdc=0.01,
            fee_complete=True,
            matched_at="2026-07-14T00:01:00Z",
        )
        self.fill_calls = []
        self.sell_fill_calls = []

    def can_reenter(self, _condition_id, _cooldown_hours):
        return self.can_reenter_result

    def get_position_count(self):
        return self.positions

    def get_event_position_count(self, _event_id):
        return self.event_positions

    def get_open_notional_usdc(self):
        return self.open_notional

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=len(self.created), **kwargs)

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))

    def save_market_catalog(self, condition_id, market, *, commit=False):
        self.catalog.append((condition_id, market, commit))

    def get_exact_buy_fill_evidence(self, order_id):
        self.fill_calls.append(order_id)
        return self.fill_evidence

    def get_exact_sell_fill_evidence(self, order_id):
        self.sell_fill_calls.append(order_id)
        return self.sell_fill_evidence

    def get_holding_trades(self):
        return self.holdings


class FakeGamma:
    def __init__(self, market=None, error=None):
        self.market = market
        self.error = error
        self.calls = []

    def get_market_by_condition_id(self, condition_id):
        self.calls.append(condition_id)
        if self.error is not None:
            raise self.error
        return self.market


def make_candidate(**overrides):
    candidate = {
        "condition_id": "0xcondition",
        "market_slug": "crown-momentum-market",
        "question": "Will this resolve Yes?",
        "event_id": "event-1",
        "event_slug": "event-one",
        "outcome": "Yes",
        "token_id": "yes-token",
        "prior_yes_price": 0.899,
        "prior_snapshot_id": 16,
        "entry_snapshot_id": 17,
        "liquidity": 25_000.0,
        "volume_24h": 4_000.0,
        "market_tags": "economics",
        "end_date": datetime.now(timezone.utc) + timedelta(hours=12),
        "hours_until_resolution": 12.0,
        "is_sports": False,
        "game_start_time": None,
        "sports_market_type": None,
    }
    candidate.update(overrides)
    return candidate


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=7,
        condition_id="0xcondition",
        token_id="yes-token",
        outcome="Yes",
        question="Will this resolve Yes?",
        buy_price=0.915,
        buy_shares=5.2,
        buy_order_id="0xBUY",
        sell_order_id="0xSELL",
        stop_price_at_entry=0.85,
        take_profit_price_at_entry=0.98,
        exit_reason="absolute_stop_pending_confirmed_fill",
        market_end_date=datetime.utcnow() + timedelta(hours=48),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_trader(*, clob=None, repo=None, config=None, gamma=None, simulation=None):
    clob = clob or FakeClob()
    repo = repo or FakeRepo()
    trader = Trader(
        repo,
        clob,
        config or TradingConfig(),
        gamma_client=gamma,
        simulation_mode=simulation,
    )
    return trader, repo, clob


class TestEntryExecution:
    def test_buy_revalidates_crossing_and_executes_with_depth_capped_limit(self):
        clob = FakeClob(midpoint=0.911, best_bid=0.90, best_ask=0.91)
        trader, repo, _ = make_trader(clob=clob, simulation=False)

        trade_id = trader.execute_buy(make_candidate())

        assert trade_id == 1
        assert clob.orders == [
            {
                "token_id": "yes-token",
                "price": 0.92,
                "size": pytest.approx(5.0 / 0.92),
                "side": "BUY",
            }
        ]
        created = repo.created[0]
        assert created["outcome"] == "Yes"
        assert created["strategy_name"] == "queen"
        assert created["mode"] == "live"
        assert created["status"] == TradeStatus.PENDING_BUY
        assert created["event_id"] == "event-1"
        assert created["buy_price"] == 0.92
        assert created["buy_probability"] == 0.911
        assert created["prior_yes_price_at_entry"] == 0.899
        assert created["yes_price_at_buy"] == 0.911
        assert created["stop_price_at_entry"] == 0.85
        assert created["take_profit_price_at_entry"] == 0.98
        assert created["entry_prob_min_at_buy"] == 0.90
        assert created["entry_prob_max_at_buy"] == 0.94
        assert created["entry_hours_min_at_buy"] == 0.0
        assert created["entry_hours_max_at_buy"] == 24.0
        assert created["entry_time_reference"] == "end_date"
        assert created["sports_phase_at_buy"] == "not_sports"
        assert created["prior_snapshot_id_at_entry"] == 16
        assert created["entry_snapshot_id"] == 17
        assert created["best_bid_at_buy"] == 0.90
        assert created["best_ask_at_buy"] == 0.91
        assert created["spread_at_buy"] == pytest.approx(0.01)
        assert created["book_depth_shares_at_buy"] == 100.0
        assert created["depth_limit_price_at_buy"] == 0.92

    def test_simulation_mode_is_evidence_on_created_trade(self):
        trader, repo, _ = make_trader(
            clob=FakeClob(simulation_mode=True), simulation=None
        )
        assert trader.execute_buy(make_candidate()) == 1
        assert repo.created[0]["mode"] == "sim"
        assert repo.created[0]["status"] == TradeStatus.HOLDING

    def test_sports_without_game_start_remains_eligible_via_end_date_fallback(self):
        trader, repo, clob = make_trader()
        candidate = make_candidate(
            is_sports=True,
            market_tags="Sports",
            game_start_time=None,
            sports_market_type=None,
        )

        assert trader.execute_buy(candidate) == 1
        assert len(clob.orders) == 1
        created = repo.created[0]
        assert created["entry_time_reference"] == "end_date"
        assert created["sports_phase_at_buy"] == "scheduled"

    @pytest.mark.parametrize(
        ("candidate", "clob", "repo"),
        [
            (make_candidate(outcome="No"), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id=None), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id=0), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id=-1), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id=True), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id=17.0), FakeClob(), FakeRepo()),
            (make_candidate(entry_snapshot_id="17"), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=None), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=0), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=-1), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=True), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=16.0), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id="16"), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=17), FakeClob(), FakeRepo()),
            (make_candidate(prior_snapshot_id=18), FakeClob(), FakeRepo()),
            (make_candidate(prior_yes_price=0.90), FakeClob(), FakeRepo()),
            (make_candidate(), FakeClob(midpoint=0.941), FakeRepo()),
            (make_candidate(), FakeClob(best_ask=0.941), FakeRepo()),
            (make_candidate(), FakeClob(best_bid=0.92, best_ask=0.91), FakeRepo()),
            (
                make_candidate(),
                FakeClob(best_ask=RuntimeError("book down")),
                FakeRepo(),
            ),
            (make_candidate(), FakeClob(), FakeRepo(positions=20)),
            (make_candidate(), FakeClob(), FakeRepo(event_positions=1)),
            (make_candidate(), FakeClob(), FakeRepo(open_notional=50)),
            (make_candidate(), FakeClob(), FakeRepo(can_reenter=False)),
        ],
    )
    def test_entry_fails_closed_before_order(self, candidate, clob, repo):
        trader, _, _ = make_trader(clob=clob, repo=repo)
        assert trader.execute_buy(candidate) is None
        assert clob.orders == []
        assert repo.created == []

    def test_minimum_share_buffer_is_enforced_at_actual_ask(self):
        config = TradingConfig(min_order_buffer_shares=0.40)
        clob = FakeClob(midpoint=0.91, best_bid=0.92, best_ask=0.93)
        trader, repo, _ = make_trader(clob=clob, config=config)
        assert trader.execute_buy(make_candidate()) is None
        assert clob.orders == []
        assert repo.created == []

    @pytest.mark.parametrize(
        "clob",
        [
            FakeClob(best_bid=0.88, best_ask=0.91),
            FakeClob(depth_shares=1.0),
        ],
    )
    def test_spread_and_actual_ask_depth_fail_closed(self, clob):
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_buy(make_candidate()) is None
        assert clob.orders == []
        assert repo.created == []

    def test_balance_or_allowance_error_disables_remaining_cycle_buys(self):
        clob = FakeClob(
            order_result={
                "success": False,
                "error": "not enough balance / allowance: balance: 1, order amount: 5",
            }
        )
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_buy(make_candidate()) is None
        assert trader.execute_buy(make_candidate(condition_id="second")) is None
        assert len(clob.orders) == 1
        assert repo.created == []


class TestStopExecution:
    def test_absolute_stop_uses_yes_signal_and_executes_at_fresh_bid(self):
        clob = FakeClob(midpoint=0.85, best_bid=0.84, best_ask=0.86)
        trader, repo, _ = make_trader(clob=clob)

        assert trader.execute_sell(make_trade()) is False

        assert clob.orders == [
            {
                "token_id": "yes-token",
                "price": 0.84,
                "size": 5.2,
                "side": "SELL",
            }
        ]
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.PENDING_SELL
        assert update["exit_reason"] == "absolute_stop_pending_confirmed_fill"
        assert update["realized_pnl"] is None
        assert update["hypothetical_pnl"] is None
        assert update["yes_price_at_exit"] == 0.85
        assert update["sell_price"] == 0.84
        assert update["best_bid_at_exit"] == 0.84
        assert update["best_ask_at_exit"] == 0.86
        assert update["spread_at_exit"] == pytest.approx(0.02)

    def test_low_bid_alone_does_not_trigger_stop(self):
        clob = FakeClob(midpoint=0.851, best_bid=0.80, best_ask=0.86)
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_sell(make_trade()) is False
        assert clob.orders == []
        assert repo.updates == []

    @pytest.mark.parametrize("midpoint", [0.86, 0.97])
    def test_no_time_or_trailing_exit(self, midpoint):
        clob = FakeClob(midpoint=midpoint, best_bid=midpoint - 0.01, best_ask=midpoint)
        trader, repo, _ = make_trader(clob=clob)
        old = make_trade(market_end_date=datetime.utcnow() - timedelta(days=2))
        assert trader.execute_sell(old) is False
        assert clob.orders == []
        assert repo.updates == []

    def test_take_profit_submits_only_when_fresh_bid_reaches_target(self):
        clob = FakeClob(midpoint=0.99, best_bid=0.985, best_ask=0.995)
        trader, repo, _ = make_trader(clob=clob)

        assert trader.execute_sell(make_trade()) is False

        assert clob.orders[0]["price"] == 0.985
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.PENDING_SELL
        assert update["exit_reason"] == "take_profit_pending_confirmed_fill"

    def test_take_profit_waits_when_fresh_bid_is_below_target(self):
        clob = FakeClob(midpoint=0.985, best_bid=0.975, best_ask=0.985)
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_sell(make_trade()) is False
        assert clob.orders == []
        assert repo.updates == []

    def test_invalid_or_unavailable_book_never_submits_stop(self):
        clob = FakeClob(midpoint=0.84, best_bid=None, best_ask=0.86)
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_sell(make_trade()) is False
        assert clob.orders == []
        assert repo.updates == []

    def test_partial_token_balance_never_creates_an_unmodelled_partial_sell(self):
        def result(_order, attempt):
            if attempt == 1:
                return {
                    "success": False,
                    "error": (
                        "not enough balance / allowance: balance: 5100000, "
                        "order amount: 5200000"
                    ),
                }
            return {"success": True, "orderID": "RETRY"}

        clob = FakeClob(
            midpoint=0.84, best_bid=0.83, best_ask=0.85, order_result=result
        )
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_sell(make_trade()) is False
        assert [order["size"] for order in clob.orders] == [5.2]
        assert repo.updates == []

    def test_simulation_stop_is_hypothetical_not_realized(self):
        clob = FakeClob(
            midpoint=0.84,
            best_bid=0.83,
            best_ask=0.85,
            simulation_mode=True,
        )
        trader, repo, _ = make_trader(clob=clob, simulation=True)

        assert trader.execute_sell(make_trade()) is True

        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.COMPLETED
        assert update["realized_pnl"] is None
        assert update["hypothetical_pnl"] == pytest.approx((0.83 - 0.915) * 5.2)
        assert update["pnl_basis"] == ("simulation_hypothetical_best_bid_fees_excluded")

    def test_zero_balance_marks_proven_unfilled_and_cancels_live_buy(self):
        clob = FakeClob(
            midpoint=0.84,
            best_bid=0.83,
            best_ask=0.85,
            order_result={
                "success": False,
                "error": (
                    "not enough balance / allowance: balance: 0, order amount: 5200000"
                ),
            },
        )
        trader, repo, _ = make_trader(clob=clob)
        assert trader.execute_sell(make_trade()) is False
        assert clob.cancelled == ["0xBUY"]
        assert repo.updates[-1][1]["status"] == TradeStatus.UNFILLED
        assert repo.updates[-1][1]["realized_pnl"] is None


class TestPendingBuyReconciliation:
    def test_exact_full_buy_fill_activates_holding_with_actual_size_and_vwap(self):
        trader, repo, _ = make_trader()

        assert trader.reconcile_pending_buy(make_trade()) is True

        assert repo.fill_calls == ["0xBUY"]
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.HOLDING
        assert update["buy_price"] == 0.955
        assert update["buy_shares"] == 5.2
        assert update["buy_confirmed_size"] == 5.2
        assert update["buy_confirmed_vwap"] == 0.955

    def test_pending_or_partial_buy_never_becomes_holding(self):
        partial = ExactFillEvidence(
            "confirmed",
            "0xBUY",
            order_status="LIVE",
            side="BUY",
            requested_size=5.2,
            latest_size_matched=2.0,
            needs_reconciliation=True,
            reconciled_full_fill=False,
            confirmed_size=2.0,
            confirmed_vwap=0.91,
        )
        repo = FakeRepo(fill_evidence=partial)
        trader, _, _ = make_trader(repo=repo)

        assert trader.reconcile_pending_buy(make_trade()) is False
        assert repo.updates == []

    def test_terminal_zero_fill_buy_is_unfilled(self):
        zero = ExactFillEvidence(
            "terminal_zero_fill",
            "0xBUY",
            order_status="CANCELED",
            side="BUY",
            requested_size=5.2,
            latest_size_matched=0.0,
            needs_reconciliation=False,
            confirmed_size=0.0,
        )
        repo = FakeRepo(fill_evidence=zero)
        trader, _, _ = make_trader(repo=repo)

        assert trader.reconcile_pending_buy(make_trade()) is False
        assert repo.updates[-1][1]["status"] == TradeStatus.UNFILLED
        assert repo.updates[-1][1]["exit_reason"] == "buy_terminal_zero_fill"


class TestPendingSellReconciliation:
    def test_full_reconciled_buy_and_sell_fills_complete_with_actual_net_pnl(self):
        trader, repo, _ = make_trader()

        assert trader.reconcile_pending_sell(make_trade()) is True

        assert repo.sell_fill_calls == ["0xSELL"]
        assert repo.fill_calls == ["0xBUY"]
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.COMPLETED
        assert update["exit_reason"] == "absolute_stop_confirmed_fill"
        assert update["sell_price"] == 0.885
        assert update["sell_shares"] == 5.2
        assert update["buy_confirmed_size"] == 5.2
        assert update["sell_confirmed_size"] == 5.2
        assert update["buy_confirmed_vwap"] == 0.955
        assert update["sell_confirmed_vwap"] == 0.885
        assert update["buy_confirmed_fee_usdc"] == 0.0
        assert update["sell_confirmed_fee_usdc"] == 0.01
        assert update["realized_pnl"] == pytest.approx((0.885 - 0.955) * 5.2 - 0.01)
        assert update["hypothetical_pnl"] is None
        assert update["pnl_basis"] == (
            "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
        )

    def test_terminal_zero_fill_sell_returns_to_holding_without_pnl(self):
        zero = ExactFillEvidence(
            "terminal_zero_fill",
            "0xSELL",
            order_status="CANCELED",
            side="SELL",
            requested_size=5.2,
            latest_size_matched=0.0,
            needs_reconciliation=False,
            confirmed_size=0.0,
        )
        repo = FakeRepo(sell_fill_evidence=zero)
        trader, _, _ = make_trader(repo=repo)

        assert trader.reconcile_pending_sell(make_trade()) is False

        assert repo.fill_calls == []
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.HOLDING
        assert update["sell_order_id"] is None
        assert update["sell_price"] is None
        assert update["sell_shares"] is None
        assert update["realized_pnl"] is None
        assert update["hypothetical_pnl"] is None

    @pytest.mark.parametrize(
        "sell_evidence",
        [
            ExactFillEvidence(
                "pending",
                "0xSELL",
                order_status="LIVE",
                side="SELL",
                requested_size=5.2,
                latest_size_matched=0.0,
                needs_reconciliation=True,
            ),
            ExactFillEvidence(
                "confirmed",
                "0xSELL",
                order_status="LIVE",
                side="SELL",
                requested_size=5.2,
                latest_size_matched=2.0,
                needs_reconciliation=True,
                reconciled_full_fill=False,
                confirmed_size=2.0,
                confirmed_vwap=0.885,
                confirmed_fee_usdc=0.004,
                fee_complete=True,
            ),
            ExactFillEvidence(
                "confirmed",
                "0xSELL",
                order_status="MATCHED",
                side="SELL",
                requested_size=5.2,
                latest_size_matched=5.2,
                needs_reconciliation=False,
                reconciled_full_fill=True,
                confirmed_size=5.2,
                confirmed_vwap=0.885,
                confirmed_fee_usdc=None,
                fee_complete=False,
            ),
            ExactFillEvidence(
                "unavailable", "0xSELL", side="SELL", detail="ledger_gap"
            ),
        ],
    )
    def test_partial_pending_unknown_or_fee_gap_stays_pending(self, sell_evidence):
        repo = FakeRepo(sell_fill_evidence=sell_evidence)
        trader, _, _ = make_trader(repo=repo)

        assert trader.reconcile_pending_sell(make_trade()) is False

        assert repo.updates == []
        assert repo.fill_calls == []

    def test_buy_sell_size_mismatch_never_releases_position(self):
        sell = ExactFillEvidence(
            "confirmed",
            "0xSELL",
            order_status="MATCHED",
            side="SELL",
            requested_size=5.0,
            latest_size_matched=5.0,
            needs_reconciliation=False,
            reconciled_full_fill=True,
            confirmed_size=5.0,
            confirmed_vwap=0.885,
            confirmed_fee_usdc=0.01,
            fee_complete=True,
        )
        repo = FakeRepo(sell_fill_evidence=sell)
        trader, _, _ = make_trader(repo=repo)

        assert trader.reconcile_pending_sell(make_trade()) is False
        assert repo.updates == []


def resolved_market(yes_payout=1.0):
    return {
        "conditionId": "0xcondition",
        "closed": True,
        "negRisk": False,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [yes_payout, 1.0 - yes_payout],
        "clobTokenIds": ["yes-token", "no-token"],
        "umaResolutionStatus": "resolved",
        "updatedAt": "2026-07-14T01:02:03Z",
    }


class TestResolutionEvidence:
    @pytest.mark.parametrize(
        ("payout", "outcome"),
        [(1.0, "Yes"), (0.0, "No"), (0.5, "Ambiguous")],
    )
    def test_closed_final_gamma_payout_resolves_without_synthetic_sell(
        self, payout, outcome
    ):
        gamma = FakeGamma(resolved_market(payout))
        clob = FakeClob(midpoint=RuntimeError("no order book"))
        trader, repo, _ = make_trader(clob=clob, gamma=gamma)

        assert trader.execute_sell(make_trade()) is False

        assert gamma.calls == ["0xcondition"]
        assert repo.catalog == [("0xcondition", gamma.market, True)]
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.RESOLVED
        assert update["exit_reason"] == "resolved_with_payout_evidence"
        assert update["resolution_outcome"] == outcome
        assert update["resolution_value"] == payout
        assert update["yes_price_at_exit"] == payout
        assert update["resolution_evidence"] == (
            "gamma_closed_final_outcome_prices+execution_ledger_exact_confirmed_buy"
        )
        assert update["resolution_confirmed_buy_size"] == 5.2
        assert update["resolution_confirmed_buy_vwap"] == 0.955
        assert update["resolution_confirmed_buy_fee_usdc"] == 0.0
        assert update["settlement_pnl_assumption"] == pytest.approx(
            (payout - 0.955) * 5.2
        )
        assert update["settlement_assumption_basis"] == (
            "confirmed_buy_fill_net_known_buy_fee"
        )
        assert update["sell_price"] is None
        assert update["sell_shares"] is None
        assert update["sell_order_id"] is None
        assert update["sell_timestamp"] is None
        assert update["realized_pnl"] is None
        assert clob.orders == []

    def test_confirmed_fill_vwap_size_and_fee_drive_settlement_assumption(self):
        evidence = ExactFillEvidence(
            "confirmed",
            "0xBUY",
            order_status="MATCHED",
            confirmed_size=4.0,
            confirmed_vwap=0.96,
            confirmed_fee_usdc=0.012,
            fee_complete=True,
        )
        repo = FakeRepo(fill_evidence=evidence)
        trader, _, _ = make_trader(
            clob=FakeClob(midpoint=None),
            repo=repo,
            gamma=FakeGamma(resolved_market(1.0)),
        )

        assert trader.execute_sell(make_trade()) is False

        update = repo.updates[-1][1]
        assert update["resolution_confirmed_buy_size"] == 4.0
        assert update["resolution_confirmed_buy_vwap"] == 0.96
        assert update["resolution_confirmed_buy_fee_usdc"] == 0.012
        assert update["settlement_pnl_assumption"] == pytest.approx(
            (1.0 - 0.96) * 4.0 - 0.012
        )
        assert update["realized_pnl"] is None

    @pytest.mark.parametrize("state", ["pending", "unavailable"])
    def test_accepted_or_unknown_live_order_is_intent_not_a_fill(self, state):
        evidence = ExactFillEvidence(
            state,
            "0xBUY",
            order_status="LIVE" if state == "pending" else None,
            detail="no_exact_confirmed_fill",
        )
        repo = FakeRepo(fill_evidence=evidence)
        trader, _, clob = make_trader(
            clob=FakeClob(midpoint=None),
            repo=repo,
            gamma=FakeGamma(resolved_market(1.0)),
        )

        assert trader.execute_sell(make_trade()) is False

        assert repo.fill_calls == ["0xBUY"]
        assert repo.catalog == []
        assert repo.updates == []
        assert clob.orders == []

    def test_terminal_zero_fill_resolution_marks_unfilled_not_resolved(self):
        evidence = ExactFillEvidence(
            "terminal_zero_fill",
            "0xBUY",
            order_status="CANCELED",
            confirmed_size=0.0,
        )
        repo = FakeRepo(fill_evidence=evidence)
        trader, _, _ = make_trader(
            clob=FakeClob(midpoint=None),
            repo=repo,
            gamma=FakeGamma(resolved_market(1.0)),
        )

        assert trader.execute_sell(make_trade()) is False

        assert repo.catalog == []
        update = repo.updates[-1][1]
        assert update == {
            "status": TradeStatus.UNFILLED,
            "exit_reason": "resolution_terminal_zero_fill",
            "realized_pnl": None,
        }

    def test_sim_resolution_is_explicit_requested_order_assumption(self):
        repo = FakeRepo()
        trader, _, _ = make_trader(
            clob=FakeClob(midpoint=None, simulation_mode=True),
            repo=repo,
            gamma=FakeGamma(resolved_market(1.0)),
            simulation=None,
        )

        assert trader.execute_sell(make_trade(buy_order_id="SIM_BUY_token")) is False

        assert repo.fill_calls == []
        update = repo.updates[-1][1]
        assert update["status"] == TradeStatus.RESOLVED
        assert update["resolution_evidence"].endswith("+simulation_order")
        assert update["settlement_assumption_basis"] == (
            "simulation_requested_order_assumption"
        )
        assert update["realized_pnl"] is None

    @pytest.mark.parametrize(
        "market",
        [
            None,
            {**resolved_market(), "closed": False},
            {**resolved_market(), "outcomePrices": [0.99, 0.01]},
            {**resolved_market(), "negRisk": True},
            {**resolved_market(), "outcomes": ["No", "Yes"]},
        ],
    )
    def test_unproven_resolution_never_finalizes_position(self, market):
        gamma = FakeGamma(market)
        trader, repo, clob = make_trader(clob=FakeClob(midpoint=None), gamma=gamma)
        assert trader.execute_sell(make_trade()) is False
        assert repo.catalog == []
        assert repo.updates == []
        assert clob.orders == []

    def test_no_gamma_client_means_no_inferred_resolution(self):
        trader, repo, clob = make_trader(clob=FakeClob(midpoint=0.0))
        assert trader.execute_sell(make_trade()) is False
        assert repo.updates == []
        assert clob.orders == []


def test_check_and_sell_holdings_counts_only_submitted_sells(monkeypatch):
    repo = FakeRepo()
    repo.holdings = [make_trade(id=1), make_trade(id=2)]
    trader, _, _ = make_trader(repo=repo)
    outcomes = iter([True, False])
    monkeypatch.setattr(trader, "execute_sell", lambda _trade: next(outcomes))
    assert trader.check_and_sell_holdings() == 1

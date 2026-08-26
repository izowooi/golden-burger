from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import polybot.strategy.trader as trader_module
from polybot.api.clob_client import (
    BuyBookWalk,
    SellBookWalk,
    _normalize_clob_resolution,
)
from polybot.config import TradingConfig
from polybot.db.models import TradeStatus
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader


NOW = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)


class _Repo:
    def __init__(self):
        self.created = []
        self.updated = []
        self.linked = []
        self.resolution_observations = []

    def can_reenter(self, *_args):
        return True, "ok"

    def get_position_count(self):
        return 0

    def get_entry_capacity_state(self):
        return {
            "open_positions": 0,
            "untracked_buy_reservations": 0,
            "total_reserved": 0,
        }

    def get_event_position_count(self, _event_id):
        return 0

    def create_trade(self, **values):
        episode_id = values.pop("entry_episode_id", None)
        self.created.append(values)
        if episode_id is not None:
            self.linked.append((episode_id, 7))
        return SimpleNamespace(id=7)

    def link_entry_episode_trade(self, episode_id, trade_id):
        self.linked.append((episode_id, trade_id))

    def save_market_catalog(self, *_args, **_kwargs):
        return None

    def update_trade(self, trade_id, **values):
        self.updated.append((trade_id, values))

    def stage_clob_resolution_observation(self, **values):
        self.resolution_observations.append(values)

    def get_exact_buy_fill_evidence(self, _order_id):
        return ExactFillEvidence(
            "confirmed",
            "buy-1",
            order_status="MATCHED",
            side="BUY",
            requested_size=5 / 0.985,
            latest_size_matched=5 / 0.985,
            needs_reconciliation=False,
            reconciled_full_fill=True,
            confirmed_size=5 / 0.985,
            confirmed_vwap=0.985,
            confirmed_fee_usdc=0.01,
            fee_complete=True,
        )


class _Clob:
    simulation_mode = False

    def __init__(
        self,
        vwap=0.985,
        best_bid=0.98,
        best_ask=0.985,
        sell_vwap=None,
        sell_limit=None,
    ):
        self.vwap = vwap
        self.best_bid = best_bid
        self.best_ask = best_ask
        self.sell_vwap = best_bid if sell_vwap is None else sell_vwap
        self.sell_limit = best_bid if sell_limit is None else sell_limit
        self.orders = []
        self.resolution = None

    def get_buy_book_walk(self, token_id, *, notional_usdc):
        return BuyBookWalk(
            token_id,
            self.best_bid,
            self.best_ask,
            self.best_ask - self.best_bid,
            self.vwap,
            5 / self.vwap,
            5,
            0.99,
            2,
        )

    def place_fok_buy(self, **order):
        self.orders.append(order)
        return {
            "success": True,
            "orderID": "buy-1",
            "requested_size": 5 / self.vwap,
        }

    def place_limit_order(self, **order):
        self.orders.append(order)
        return {"success": True, "orderID": "sell-1"}

    def get_sell_book_walk(self, token_id, *, shares):
        return SellBookWalk(
            token_id,
            self.best_bid,
            self.best_ask,
            self.best_ask - self.best_bid,
            self.sell_vwap,
            shares,
            self.sell_vwap * shares,
            self.sell_limit,
            2 if self.sell_limit != self.best_bid else 1,
        )

    def get_best_bid(self, _token_id):
        return self.best_bid

    def get_best_ask(self, _token_id):
        return self.best_ask

    def get_market_resolution(self, _condition_id):
        if self.resolution is None:
            raise AssertionError("unexpected CLOB resolution lookup")
        return self.resolution


def _candidate():
    return {
        "condition_id": "condition-1",
        "market_slug": "market",
        "question": "Will the away team win?",
        "event_id": "event-1",
        "event_slug": "event",
        "outcome": "Yes",
        "result_kind": "AWAY",
        "token_id": "away-yes-token",
        "entry_snapshot_id": 11,
        "entry_episode_id": 3,
        "yes_probability": 0.985,
        "game_start_time": NOW - timedelta(hours=1),
        "end_date": NOW - timedelta(hours=1),
        "liquidity": 20_000,
        "volume_24h": 1_000,
        "market_tags": "Sports",
    }


def test_buy_revalidates_exact_five_and_submits_fok(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob()
    config = TradingConfig()
    trader = Trader(repo, clob, config, simulation_mode=False)

    assert trader.execute_buy(_candidate()) == 7
    assert clob.orders == [
        {
            "token_id": "away-yes-token",
            "amount_usdc": 5,
            "limit_price": 0.99,
        }
    ]
    created = repo.created[0]
    assert created["outcome"] == "Yes"
    assert created["buy_amount"] == 5
    assert created["buy_price"] == 0.985
    assert created["buy_shares"] == pytest.approx(5 / 0.985)
    assert created["status"] is TradeStatus.PENDING_BUY
    assert created["yes_price_at_buy"] == 0.985
    assert created["stop_price_at_entry"] == 0.70
    assert repo.linked == [(3, 7)]


def test_uncertain_buy_disables_remaining_cycle_entries(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob()
    submissions = []

    def uncertain_buy(**order):
        submissions.append(order)
        return {
            "success": False,
            "submission_outcome_unknown": True,
            "quarantined": True,
        }

    clob.place_fok_buy = uncertain_buy
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.execute_buy(_candidate()) is None
    assert trader.last_entry_outcome_reason == "buy_submission_outcome_unknown"
    assert trader.local_untracked_buy_reservations == 1
    assert trader.buying_disabled is True
    assert trader.execute_buy({**_candidate(), "condition_id": "condition-2"}) is None
    assert trader.last_entry_outcome_reason == "cycle_buying_disabled"
    assert len(submissions) == 1


def test_pending_buy_waits_for_complete_terminal_fee_evidence() -> None:
    repo, clob = _Repo(), _Clob()
    repo.get_exact_buy_fill_evidence = lambda _order_id: ExactFillEvidence(
        "confirmed",
        "buy-1",
        order_status="MATCHED",
        side="BUY",
        requested_size=5.102,
        latest_size_matched=5.102,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=5.102,
        confirmed_vwap=0.98,
        confirmed_fee_usdc=None,
        fee_complete=False,
    )
    trade = SimpleNamespace(
        id=9,
        buy_order_id="buy-1",
        buy_timestamp=NOW.replace(tzinfo=None),
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_buy(trade, now=NOW.replace(tzinfo=None)) is False
    assert repo.updated == []


def test_owned_holding_above_stop_remains_untouched(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.71, best_ask=0.72)
    config = TradingConfig()
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="own-db-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.1,
        buy_price=0.985,
    )
    trader = Trader(repo, clob, config, simulation_mode=False)

    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    assert repo.updated == []


def test_stop_walk_uses_sdk_sellable_size_and_records_residual_dust(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.69, best_ask=0.70)
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="own-db-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.execute_sell(trade) is False
    assert clob.orders[0]["size"] == pytest.approx(5.10)
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.PENDING_SELL
    assert update["sell_shares"] == pytest.approx(5.10)
    assert update["sell_residual_shares"] == pytest.approx(0.002)


def test_orphan_catalog_identity_requires_yes_token_event_and_snapshot_alignment():
    episode = SimpleNamespace(
        condition_id="condition-1",
        event_id="event-1",
        outcome="Yes",
        entry_snapshot_id=11,
    )
    snapshot = SimpleNamespace(
        id=11,
        condition_id="condition-1",
        token_id="yes-token",
        outcome="Yes",
    )
    catalog = SimpleNamespace(
        condition_id="condition-1",
        event_id="event-1",
        outcomes_json='["Yes","No"]',
        outcome_prices_json='["0.98","0.02"]',
        token_ids_json='["yes-token","no-token"]',
        neg_risk=1,
    )

    assert trader_module._orphan_catalog_identity_matches(
        token_id="yes-token",
        episode=episode,
        snapshot=snapshot,
        catalog=catalog,
    )
    catalog.token_ids_json = '["no-token","yes-token"]'
    assert not trader_module._orphan_catalog_identity_matches(
        token_id="yes-token",
        episode=episode,
        snapshot=snapshot,
        catalog=catalog,
    )


def test_yes_resolution_uses_selected_payout_without_synthetic_sell() -> None:
    repo, clob = _Repo(), _Clob()
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: {
            "conditionId": "condition-1",
            "closed": True,
            "outcomes": ["Yes", "No"],
            "outcomePrices": [1, 0],
            "clobTokenIds": ["away-yes-token", "away-no-token"],
            "negRisk": True,
        }
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_order_id="buy-1",
        buy_shares=5 / 0.985,
        buy_price=0.985,
    )
    trader = Trader(repo, clob, TradingConfig(), gamma_client=gamma, simulation_mode=False)

    assert trader._handle_midpoint_unavailable(trade, "closed") is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.RESOLVED
    assert update["resolution_value"] == 1.0
    assert update["yes_price_at_exit"] == 1.0
    assert update["realized_pnl"] is None
    assert update["settlement_pnl_assumption"] == pytest.approx(
        (1 - 0.985) * (5 / 0.985) - 0.01
    )
    assert clob.orders == []


def test_resolution_waits_for_complete_buy_fee_evidence() -> None:
    repo, clob = _Repo(), _Clob()
    repo.get_exact_buy_fill_evidence = lambda _order_id: ExactFillEvidence(
        "confirmed",
        "buy-1",
        order_status="MATCHED",
        side="BUY",
        requested_size=5.102,
        latest_size_matched=5.102,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=5.102,
        confirmed_vwap=0.98,
        confirmed_fee_usdc=None,
        fee_complete=False,
    )
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: {
            "conditionId": "condition-1",
            "closed": True,
            "outcomes": ["Yes", "No"],
            "outcomePrices": [1, 0],
            "clobTokenIds": ["away-yes-token", "away-no-token"],
            "negRisk": True,
        }
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_order_id="buy-1",
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(
        repo, clob, TradingConfig(), gamma_client=gamma, simulation_mode=False
    )

    assert trader._handle_midpoint_unavailable(trade, "closed") is False
    assert repo.updated == []


def test_gamma_resolution_requires_exact_condition_and_token_identity() -> None:
    repo, clob = _Repo(), _Clob()
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: {
            "conditionId": "wrong-condition",
            "closed": True,
            "outcomes": ["Yes", "No"],
            "outcomePrices": [1, 0],
            "clobTokenIds": ["away-yes-token", "away-no-token"],
            "negRisk": True,
        }
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_order_id="buy-1",
        buy_shares=5 / 0.985,
        buy_price=0.985,
    )
    trader = Trader(
        repo, clob, TradingConfig(), gamma_client=gamma, simulation_mode=False
    )

    assert trader._handle_midpoint_unavailable(trade, "closed") is False
    assert repo.updated == []


def test_clob_one_hot_resolution_fallback_settles_confirmed_own_trade() -> None:
    repo, clob = _Repo(), _Clob()
    clob.resolution = _normalize_clob_resolution(
        "condition-1",
        {
            "closed": True,
            "tokens": [
                {
                    "outcome": "Yes",
                    "price": 1,
                    "token_id": "away-yes-token",
                    "winner": True,
                },
                {
                    "outcome": "No",
                    "price": 0,
                    "token_id": "away-no-token",
                    "winner": False,
                },
            ],
        },
        observed_at="2026-08-21T11:00:00Z",
    )
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda _condition: {
            "conditionId": "condition-1",
            "closed": True,
            "outcomes": ["Yes", "No"],
            "outcomePrices": [0.999, 0.001],
            "clobTokenIds": ["away-yes-token", "away-no-token"],
            "negRisk": True,
            "umaResolutionStatus": "proposed",
        }
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_order_id="buy-1",
        buy_shares=5 / 0.985,
        buy_price=0.985,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=gamma,
        simulation_mode=False,
    )

    assert trader._handle_midpoint_unavailable(trade, "closed") is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.RESOLVED
    assert update["resolution_value"] == 1.0
    assert update["resolution_outcome"] == "Yes"
    assert update["resolution_status"] == "clob_closed_unique_winner"
    assert update["resolution_evidence"].startswith(
        "clob_closed_unique_winner_sha256:"
    )
    assert len(repo.resolution_observations) == 1
    observation = repo.resolution_observations[0]
    assert observation["winner_index"] == 0
    assert observation["selected_payout"] == 1
    assert clob.orders == []


def test_stop_uses_fresh_bid_and_submits_fok_sell() -> None:
    repo = _Repo()
    clob = _Clob(
        best_bid=0.27,
        best_ask=0.29,
        sell_vwap=0.25,
        sell_limit=0.23,
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        token_id="away-yes-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.076142,
        buy_price=0.985,
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.execute_sell(trade) is False
    assert clob.orders == [
        {
            "token_id": "away-yes-token",
            "price": 0.23,
            "size": 5.07,
            "side": "SELL",
            "order_type": "FOK",
        }
    ]
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.PENDING_SELL
    assert update["exit_reason"] == "absolute_stop_pending_confirmed_fill"
    assert update["sell_price"] == 0.25
    assert update["sell_residual_shares"] == pytest.approx(0.006142)

from datetime import datetime, timedelta, timezone
import json
import math
from types import SimpleNamespace

import pytest

import polybot.strategy.trader as trader_module
from polybot.api.clob_client import (
    BuyBookWalk,
    PreSubmissionContractError,
    SellBookWalk,
    _normalize_clob_resolution,
)
from polybot.config import TradingConfig
from polybot.db.models import (
    BUY_RECONCILIATION_QUARANTINE_REASON,
    STOP_SELL_LEDGER_QUARANTINE_REASON,
    STOP_SELL_QUARANTINE_REASON,
    TradeStatus,
)
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader
from polybot_observability import SubmissionEvidenceError


NOW = datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc)


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
        self.episode_execution = []
        self.resolution_observations = []
        self.reentry = (True, "ok")

    def can_reenter(self, *_args, **_kwargs):
        return self.reentry

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

    def mark_entry_episode_execution(self, episode_id, *, state, reason=None):
        self.episode_execution.append((episode_id, state, reason))

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
        self.resolution = _normalize_clob_resolution(
            "condition-1",
            {"condition_id": "condition-1", "closed": False},
        )

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

    def get_buy_book_walks(self, token_ids, *, notional_usdc):
        assert notional_usdc == 5
        other_prices = {
            "home-yes-token": 0.55,
            "home-no-token": 0.45,
            "draw-yes-token": 0.30,
            "draw-no-token": 0.70,
            "away-no-token": 0.40,
        }
        walks = {}
        for token_id in token_ids:
            if token_id == "away-yes-token":
                bid = self.best_bid
                ask = self.best_ask
                vwap = self.vwap
            else:
                vwap = other_prices[token_id]
                bid = max(0.001, vwap - 0.01)
                ask = vwap
            walks[token_id] = BuyBookWalk(
                token_id,
                bid,
                ask,
                ask - bid,
                vwap,
                5 / vwap,
                5,
                ask,
                1,
            )
        return walks

    def get_cached_book_evidence(self, token_id):
        return json.dumps(
            {
                "schema_version": 1,
                "token_id": token_id,
                "bids": [{"price": self.best_bid, "size": 10_000}],
                "asks": [{"price": self.best_ask, "size": 10_000}],
            },
            sort_keys=True,
            separators=(",", ":"),
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


def _active_gamma():
    return SimpleNamespace(
        get_market_by_condition_id=lambda condition_id: {
            "conditionId": condition_id,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "acceptingOrders": True,
        },
        get_event_by_id=lambda event_id: {
            "id": event_id,
            "active": True,
            "closed": False,
            "live": True,
            "ended": False,
            "elapsed": "30",
            "period": "1H",
        },
    )


def _candidate():
    return {
        "condition_id": "condition-1",
        "market_slug": "market",
        "question": "Will the away team win?",
        "event_id": "event-1",
        "event_slug": "event",
        "outcome": "Yes",
        "result_kind": "AWAY",
        "outcome_side": "YES",
        "token_id": "away-yes-token",
        "candidate_kind": "YES_AWAY",
        "event_token_ids": [
            "home-yes-token",
            "home-no-token",
            "draw-yes-token",
            "draw-no-token",
            "away-yes-token",
            "away-no-token",
        ],
        "entry_snapshot_id": 11,
        "entry_episode_id": 3,
        "yes_probability": 0.80,
        "game_start_time": NOW - timedelta(minutes=5),
        "end_date": NOW - timedelta(minutes=5),
        "liquidity": 20_000,
        "volume_24h": 1_000,
        "market_tags": "Sports",
    }


def _set_kickoff_cycle(trader: Trader) -> None:
    trader.set_cycle_markets(
        [
            {
                "conditionId": "condition-1",
                "events": [
                    {
                        "id": "event-1",
                        "elapsed": "5",
                        "period": "1H",
                        "active": True,
                        "closed": False,
                        "live": True,
                        "ended": False,
                    }
                ],
            }
        ]
    )


def test_buy_revalidates_exact_five_and_submits_fok(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(vwap=0.80, best_bid=0.79, best_ask=0.80)
    config = TradingConfig()
    trader = Trader(repo, clob, config, simulation_mode=False)
    _set_kickoff_cycle(trader)

    assert trader.execute_buy(_candidate()) == 7
    assert clob.orders == [
        {
            "token_id": "away-yes-token",
            "amount_usdc": 5,
            "limit_price": 0.80,
            "max_limit_price": 0.94,
        }
    ]
    created = repo.created[0]
    assert created["outcome"] == "Yes"
    assert created["buy_amount"] == 5
    assert created["buy_price"] == 0.80
    assert created["buy_shares"] == pytest.approx(5 / 0.80)
    assert created["status"] is TradeStatus.PENDING_BUY
    assert created["yes_price_at_buy"] == 0.80
    assert created["stop_price_at_entry"] == pytest.approx(0.70)
    assert repo.linked == [(3, 7)]
    assert repo.episode_execution == [
        (3, "SUBMISSION_IN_PROGRESS", "fresh_book_validated_before_submission_wrapper")
    ]
    assert trader.last_entry_may_have_reached_venue is True


def test_buy_refuses_any_prior_event_trade(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(vwap=0.80, best_bid=0.79, best_ask=0.80)
    repo.reentry = (False, "event_already_traded")
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    _set_kickoff_cycle(trader)
    assert trader.execute_buy(_candidate()) is None
    assert repo.created == []
    assert clob.orders == []


def test_pre_submission_contract_error_is_proven_no_post(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(vwap=0.80, best_bid=0.79, best_ask=0.80)

    def reject_before_post(**_order):
        raise PreSubmissionContractError("signed amount precision")

    clob.place_fok_buy = reject_before_post
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)
    _set_kickoff_cycle(trader)

    with pytest.raises(PreSubmissionContractError):
        trader.execute_buy(_candidate())

    assert trader.last_entry_may_have_reached_venue is False
    assert repo.created == []
    assert repo.episode_execution == [
        (3, "SUBMISSION_IN_PROGRESS", "fresh_book_validated_before_submission_wrapper")
    ]


def test_uncertain_buy_reserves_capacity_without_disabling_unrelated_entries(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(vwap=0.80, best_bid=0.79, best_ask=0.80)
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
    _set_kickoff_cycle(trader)

    assert trader.execute_buy(_candidate()) is None
    assert trader.last_entry_outcome_reason == "buy_submission_outcome_unknown"
    assert trader.local_untracked_buy_reservations == 1
    assert trader.buying_disabled is False
    assert trader.last_entry_may_have_reached_venue is True
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


def test_pending_buy_is_event_locally_quarantined_after_three_hours() -> None:
    repo, clob = _Repo(), _Clob()
    repo.get_exact_buy_fill_evidence = lambda _order_id: ExactFillEvidence(
        "unavailable",
        "",
        side="BUY",
        detail="no exact order identity",
    )
    trade = SimpleNamespace(
        id=10,
        status=TradeStatus.PENDING_BUY,
        exit_reason=None,
        buy_order_id=None,
        buy_timestamp=(NOW - timedelta(minutes=181)).replace(tzinfo=None),
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_buy(
        trade, now=NOW.replace(tzinfo=None)
    ) is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.QUARANTINED
    assert update["exit_reason"] == BUY_RECONCILIATION_QUARANTINE_REASON


def test_isolated_buy_returns_to_holding_on_late_exact_fill() -> None:
    repo, clob = _Repo(), _Clob()
    repo.get_exact_buy_fill_evidence = lambda _order_id: ExactFillEvidence(
        "confirmed",
        "buy-late",
        order_status="MATCHED",
        side="BUY",
        requested_size=6.25,
        latest_size_matched=6.25,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=6.25,
        confirmed_vwap=0.80,
        confirmed_fee_usdc=0.0,
        fee_complete=True,
    )
    trade = SimpleNamespace(
        id=11,
        status=TradeStatus.QUARANTINED,
        exit_reason=BUY_RECONCILIATION_QUARANTINE_REASON,
        buy_order_id="buy-late",
        buy_timestamp=(NOW - timedelta(minutes=181)).replace(tzinfo=None),
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_buy(
        trade, now=NOW.replace(tzinfo=None)
    ) is True
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.HOLDING
    assert update["exit_reason"] is None
    assert update["buy_confirmed_size"] == pytest.approx(6.25)


def test_confirmed_buy_freezes_entry_relative_stop_from_actual_vwap() -> None:
    repo, clob = _Repo(), _Clob()
    repo.get_exact_buy_fill_evidence = lambda _order_id: ExactFillEvidence(
        "confirmed",
        "buy-1",
        order_status="MATCHED",
        side="BUY",
        requested_size=5.0505,
        latest_size_matched=5.0505,
        needs_reconciliation=False,
        reconciled_full_fill=True,
        confirmed_size=5.0505,
        confirmed_vwap=0.99,
        confirmed_fee_usdc=0.0,
        fee_complete=True,
    )
    trade = SimpleNamespace(
        id=9,
        buy_order_id="buy-1",
        buy_timestamp=NOW.replace(tzinfo=None),
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_buy(trade, now=NOW.replace(tzinfo=None)) is True
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.HOLDING
    assert update["buy_confirmed_vwap"] == pytest.approx(0.99)
    assert update["stop_price_at_entry"] == pytest.approx(0.89)


def test_owned_holding_above_stop_remains_untouched(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.94, best_ask=0.95)
    config = TradingConfig()
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
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


def test_entry_relative_stop_uses_frozen_ten_point_reversal() -> None:
    repo = _Repo()
    clob = _Clob(
        best_bid=0.89,
        best_ask=0.90,
        sell_vwap=0.888,
        sell_limit=0.885,
    )
    # The frozen Peach stop is ten percentage points below confirmed entry.
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="away-yes-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_confirmed_vwap=0.99,
        buy_shares=5.0505,
        buy_price=0.99,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert Trader.effective_stop_price(trade, TradingConfig()) == pytest.approx(0.89)
    assert trader.execute_sell(trade) is False
    assert len(clob.orders) == 1
    assert clob.orders[0]["side"] == "SELL"
    assert repo.updated[-1][1]["status"] is TradeStatus.PENDING_SELL


def test_continuous_stop_failure_is_quarantined_after_three_hours(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.88, best_ask=0.89)
    trade = SimpleNamespace(
        id=91,
        status=TradeStatus.HOLDING,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        exit_reason="exit_sell_failure_retrying:absolute_stop",
        sell_timestamp=(NOW - timedelta(minutes=181)).replace(tzinfo=None),
        stop_price_at_entry=0.94,
        buy_confirmed_vwap=0.99,
        buy_shares=5.05,
        buy_price=0.99,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    assert repo.updated[-1][1]["status"] is TradeStatus.QUARANTINED
    assert repo.updated[-1][1]["exit_reason"] == STOP_SELL_QUARANTINE_REASON


def test_rejected_stop_starts_failure_timer_without_aborting_cycle(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(
        best_bid=0.88,
        best_ask=0.89,
        sell_vwap=0.88,
        sell_limit=0.88,
    )
    clob.place_limit_order = lambda **order: {
        "success": False,
        "error": "temporary venue rejection",
    }
    trade = SimpleNamespace(
        id=92,
        status=TradeStatus.HOLDING,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        exit_reason=None,
        sell_timestamp=None,
        stop_price_at_entry=0.94,
        buy_confirmed_vwap=0.99,
        buy_shares=5.05,
        buy_price=0.99,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.HOLDING
    assert update["exit_reason"] == "exit_sell_failure_retrying:absolute_stop"
    assert update["sell_timestamp"] == NOW.replace(tzinfo=None)


def test_sell_ledger_failure_is_immediately_isolated_without_raising(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(
        best_bid=0.88,
        best_ask=0.89,
        sell_vwap=0.88,
        sell_limit=0.88,
    )

    def fail_ledger(**_order):
        raise SubmissionEvidenceError("durable ledger bind failed")

    clob.place_limit_order = fail_ledger
    trade = SimpleNamespace(
        id=93,
        status=TradeStatus.HOLDING,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        exit_reason=None,
        sell_timestamp=None,
        stop_price_at_entry=0.94,
        buy_confirmed_vwap=0.99,
        buy_shares=5.05,
        buy_price=0.99,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.QUARANTINED
    assert update["exit_reason"] == STOP_SELL_LEDGER_QUARANTINE_REASON


def test_recovered_stop_clears_continuous_failure_timer(monkeypatch) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.95, best_ask=0.96)
    trade = SimpleNamespace(
        id=94,
        status=TradeStatus.HOLDING,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        exit_reason="exit_sell_failure_retrying:absolute_stop",
        sell_timestamp=(NOW - timedelta(minutes=10)).replace(tzinfo=None),
        stop_price_at_entry=0.94,
        buy_confirmed_vwap=0.99,
        buy_shares=5.05,
        buy_price=0.99,
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.execute_sell(trade) is False
    update = repo.updated[-1][1]
    assert update["exit_reason"] is None
    assert update["sell_timestamp"] is None


def test_stop_uses_fresh_bid_and_submits_fok_sell(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trader_module, "datetime", _FixedDatetime)
    repo, clob = _Repo(), _Clob(best_bid=0.69, best_ask=0.70)
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert clob.orders[0]["size"] == pytest.approx(5.10)
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.PENDING_SELL
    assert update["sell_shares"] == pytest.approx(5.10)
    assert update["sell_residual_shares"] == pytest.approx(0.002)


def test_stop_walk_uses_sdk_sellable_size_and_records_residual_dust(
    monkeypatch,
) -> None:
    # Keep the historical execution-contract name explicit: the fresh-stop
    # test above also proves SDK sizing and residual-dust persistence.
    test_stop_uses_fresh_bid_and_submits_fok_sell(monkeypatch)


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
        outcome_side="YES",
        result_kind="HOME",
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


def test_missing_source_clock_cannot_create_a_late_stop() -> None:
    repo = _Repo()
    clob = _Clob(best_bid=0.60, best_ask=0.61)
    trade = SimpleNamespace(
        id=11,
        condition_id="condition-1",
        event_id="event-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_confirmed_vwap=0.80,
        buy_shares=6.25,
        buy_price=0.80,
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.execute_sell(trade) is False
    assert clob.orders == []


def test_minute_eighty_allows_half_target_but_disables_new_stop() -> None:
    repo = _Repo()
    clob = _Clob(best_bid=0.815, best_ask=0.82, sell_vwap=0.815)
    trade = SimpleNamespace(
        id=12,
        condition_id="condition-1",
        event_id="event-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_confirmed_vwap=0.80,
        buy_shares=6.25,
        buy_price=0.80,
        take_profit_delta_at_buy=0.03,
        stop_loss_delta_at_buy=0.10,
        late_exit_minute_at_buy=80,
    )
    gamma = _active_gamma()
    gamma.get_event_by_id = lambda event_id: {
        "id": event_id,
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "elapsed": "80",
        "period": "2H",
    }
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=gamma,
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert len(clob.orders) == 1
    assert clob.orders[0]["side"] == "SELL"
    assert repo.updated[-1][1]["exit_reason"] == (
        "late_half_target_pending_confirmed_fill"
    )

    losing_repo = _Repo()
    losing_clob = _Clob(best_bid=0.60, best_ask=0.61, sell_vwap=0.60)
    losing = Trader(
        losing_repo,
        losing_clob,
        TradingConfig(),
        gamma_client=gamma,
        simulation_mode=False,
    )
    assert losing.execute_sell(trade) is False
    assert losing_clob.orders == []


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
        event_id="event-1",
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


def test_live_gap_beyond_normal_stop_limit_uses_first_full_depth_book() -> None:
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
        event_id="event-1",
        token_id="away-yes-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.076142,
        buy_price=0.985,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert len(clob.orders) == 1
    assert clob.orders[0]["side"] == "SELL"
    assert repo.updated[-1][1]["status"] is TradeStatus.PENDING_SELL


def test_post_game_cleanup_bid_cannot_trigger_stop() -> None:
    repo = _Repo()
    clob = _Clob(best_bid=0.001, best_ask=1.0)
    gamma = SimpleNamespace(
        get_market_by_condition_id=lambda condition_id: {
            "conditionId": condition_id,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "acceptingOrders": True,
        },
        get_event_by_id=lambda event_id: {
            "id": event_id,
            "active": True,
            "closed": False,
            "live": False,
            "ended": True,
        },
    )
    clob.resolution = _normalize_clob_resolution(
        "condition-1",
        {"condition_id": "condition-1", "closed": False},
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="away-yes-token",
        outcome="Yes",
        buy_order_id="buy-1",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=gamma,
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    assert repo.updated == []


def test_clob_closed_market_blocks_stop_even_when_gamma_still_says_live() -> None:
    repo = _Repo()
    clob = _Clob(best_bid=0.69, best_ask=0.70)
    clob.resolution = _normalize_clob_resolution(
        "condition-1",
        {
            "condition_id": "condition-1",
            "closed": True,
            "tokens": [
                {
                    "outcome": "Yes",
                    "token_id": "own-db-token",
                    "price": 1,
                    "winner": True,
                },
                {
                    "outcome": "No",
                    "token_id": "no-token",
                    "price": 0,
                    "winner": False,
                },
            ],
        },
    )
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        buy_order_id="buy-1",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    assert repo.updated[-1][1]["status"] is TradeStatus.RESOLVED


def test_unrelated_event_exits_are_not_blocked_by_first_sell() -> None:
    repo, clob = _Repo(), _Clob(best_bid=0.69, best_ask=0.70)
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )
    first = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    second = SimpleNamespace(
        id=10,
        condition_id="condition-2",
        event_id="event-2",
        token_id="second-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )

    assert trader.execute_sell(first) is False
    assert trader.execute_sell(second) is False
    assert len(clob.orders) == 2
    assert trader.emergency_sell_submissions == 2
    assert trader.emergency_sell_guard_blocks == 0


def test_sdk_sell_submission_nudge_survives_binary_float_double_floor() -> None:
    sellable = trader_module._sdk_sellable_shares(5.102)
    submission = trader_module._sdk_sell_submission_shares(sellable)

    assert sellable == 5.10
    assert submission > sellable
    assert math.floor(submission * 100) / 100 == 5.10


def test_accepted_sell_with_unsafe_signed_size_is_never_orphaned() -> None:
    repo = _Repo()
    clob = _Clob(best_bid=0.69, best_ask=0.70)

    def post_with_unsafe_signed_size(**order):
        clob.orders.append(order)
        return {
            "success": True,
            "orderID": "sell-unsafe",
            "requested_size": 5.09,
        }

    clob.place_limit_order = post_with_unsafe_signed_size
    trade = SimpleNamespace(
        id=9,
        condition_id="condition-1",
        event_id="event-1",
        token_id="own-db-token",
        outcome="Yes",
        stop_price_at_entry=0.70,
        buy_shares=5.102,
        buy_price=0.98,
    )
    trader = Trader(
        repo,
        clob,
        TradingConfig(),
        gamma_client=_active_gamma(),
        simulation_mode=False,
    )

    assert trader.execute_sell(trade) is False
    update = repo.updated[-1][1]
    assert update["status"] is TradeStatus.PENDING_SELL
    assert update["sell_order_id"] == "sell-unsafe"
    assert update["sell_shares"] == pytest.approx(5.09)
    assert update["exit_reason"] == "signed_sell_size_drift_unsafe"

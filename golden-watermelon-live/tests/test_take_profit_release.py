"""Mock-only tests for the disabled Cat/Dog take-profit release proposal."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import polybot.strategy.trader as trader_module
from polybot.config import TakeProfitConfig, TradingConfig, load_config, profile_environment
from polybot.db.models import TradeStatus
from polybot.db.models import TAKE_PROFIT_QUARANTINE_REASON, TAKE_PROFIT_LEDGER_QUARANTINE_REASON
from polybot.db.repository import ExactFillEvidence
from polybot.strategy.trader import Trader
from polybot.api.clob_client import _walk_sell_book
from polybot.source_digest import preregistration_sha256
from tests.test_trader import _Clob, _Repo, _active_gamma
from polybot_observability import SubmissionEvidenceError


NOW = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)


def setup_tp():
    repo = _Repo()
    clob = _Clob(best_bid=0.99, best_ask=0.999, sell_vwap=0.99, sell_limit=0.99)
    clob.get_sell_fee_quote = lambda *a, **kw: 0.001
    config = TradingConfig(take_profit=TakeProfitConfig(
        enabled=True, effective_from_utc="2026-09-07T00:00:00Z",
    ))
    trade = SimpleNamespace(
        id=7, status=TradeStatus.HOLDING, token_id="own-db-token",
        event_id="event-1", condition_id="condition-1", buy_order_id="buy-1",
        buy_timestamp=NOW, buy_price=0.985, buy_shares=5 / 0.985,
        buy_confirmed_vwap=0.985, exit_reason=None, sell_timestamp=None,
    )
    trader = Trader(repo, clob, config, gamma_client=_active_gamma(), simulation_mode=False)
    return trader, trade, repo, clob


def test_default_does_not_take_profit_or_change_registered_bands():
    trader, trade, repo, clob = setup_tp()
    trader.config.take_profit = TakeProfitConfig()
    assert trader.execute_sell(trade) is False
    assert clob.orders == []
    env = profile_environment("watermelon-live-cat-96-1m-v2h", {})
    assert env["POLYBOT_ENTRY_PROB_MIN"] == "0.96"
    assert env["POLYBOT_ENTRY_PROB_MAX"] == "0.999"


def test_tp_uses_fresh_full_holding_fok_and_waits_for_confirmation():
    trader, trade, repo, clob = setup_tp()
    walks = []
    original = clob.get_sell_book_walk
    clob.get_sell_book_walk = lambda *a, **kw: (walks.append(kw) or original(*a, **kw))
    assert trader.execute_sell(trade) is False
    assert len(walks) == 2  # trigger and a new post-lifecycle book
    assert len(clob.orders) == 1
    assert clob.orders[0]["order_type"] == "FOK"
    assert clob.orders[0]["price"] == 0.99
    pending = repo.updated[-1][1]
    assert pending["status"] == TradeStatus.PENDING_SELL
    assert pending["exit_reason"] == "take_profit_pending_confirmed_fill"
    assert pending["realized_pnl"] is None
    assert 0 < pending["sell_residual_shares"] < 0.01
    trade.__dict__.update(pending)
    repo.get_exact_sell_fill_evidence = lambda _: ExactFillEvidence(
        "confirmed", "sell-1", side="SELL", order_status="MATCHED",
        requested_size=trade.sell_shares, latest_size_matched=trade.sell_shares,
        needs_reconciliation=False, reconciled_full_fill=True,
        confirmed_size=trade.sell_shares, confirmed_vwap=0.99,
        confirmed_fee_usdc=0.001, fee_complete=True,
    )
    assert trader.reconcile_pending_sell(trade) is True
    closed = repo.updated[-1][1]
    assert closed["exit_reason"] == "take_profit_confirmed_fill_with_recorded_sdk_dust"
    assert closed["realized_pnl"] > 0
    assert closed["sell_residual_shares"] > 0


@pytest.mark.parametrize("case", ["missing_buy_fee", "loss_after_fee", "insufficient_depth",
    "limit_below_target", "stale", "closed", "carry_in", "manual", "pending", "size_mismatch"])
def test_tp_fails_closed_without_executable_positive_net(case, monkeypatch):
    trader, trade, repo, clob = setup_tp()
    if case == "missing_buy_fee":
        original = repo.get_exact_buy_fill_evidence("buy-1")
        repo.get_exact_buy_fill_evidence = lambda _: replace(original, fee_complete=False, confirmed_fee_usdc=None)
    elif case == "loss_after_fee":
        clob.get_sell_fee_quote = lambda *a, **kw: 1
    elif case == "insufficient_depth":
        original = clob.get_sell_book_walk
        first = original(trade.token_id, shares=trader.signable_sell_shares(trade))
        walks = iter([first, None])
        clob.get_sell_book_walk = lambda *a, **kw: next(walks)
    elif case == "limit_below_target":
        clob.sell_limit = 0.989
    elif case == "stale":
        ticks = iter([100.0, 104.0])
        monkeypatch.setattr(trader_module.time, "monotonic", lambda: next(ticks))
    elif case == "closed":
        trader._stop_execution_is_explicitly_live = lambda _: False
    elif case == "carry_in":
        trade.buy_timestamp = NOW - timedelta(days=1)
    elif case == "manual":
        trade.buy_order_id = None
    elif case == "pending":
        trade.status = TradeStatus.PENDING_SELL
    elif case == "size_mismatch":
        trade.buy_shares = 10
    assert trader.execute_sell(trade) is False
    assert clob.orders == []


def test_tp_refresh_rechecks_price_instead_of_using_trigger_book():
    trader, trade, repo, clob = setup_tp()
    original = clob.get_sell_book_walk
    calls = []
    def get_walk(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            clob.sell_limit = clob.sell_vwap = clob.best_bid = 0.98
        return original(*a, **kw)
    clob.get_sell_book_walk = get_walk
    assert trader.execute_sell(trade) is False
    assert clob.orders == []


def test_tp_accepts_a_canonical_empty_ask_side_with_complete_profitable_bids():
    trader, trade, repo, clob = setup_tp()
    clob.get_sell_book_walk = lambda token, **kw: _walk_sell_book(
        {"bids":[{"price":"0.99","size":"20"}],"asks":[]}, token, kw["shares"])
    assert trader.execute_sell(trade) is False
    assert len(clob.orders) == 1
    assert clob.orders[0]["price"] == .99
    assert repo.updated[-1][1]["status"] == TradeStatus.PENDING_SELL
    assert repo.updated[-1][1]["best_ask_at_exit"] is None


@pytest.mark.parametrize("asks", [None, {}, ["bad"], [{"price":float('nan'),"size":20}], [{"price":.995,"size":-1}]])
def test_tp_never_treats_malformed_asks_as_a_valid_empty_side(asks):
    trader, trade, repo, clob = setup_tp()
    original = clob.get_sell_book_walk
    seen = []
    def get_walk(token, **kw):
        seen.append(1)
        if len(seen) == 1:
            return original(token, **kw)
        return _walk_sell_book({"bids":[{"price":.99,"size":20}],"asks":asks}, token, kw["shares"])
    clob.get_sell_book_walk = get_walk
    assert trader.execute_sell(trade) is False
    assert clob.orders == []


def test_tp_rejects_sold_portion_profit_when_zero_value_dust_makes_whole_buy_negative():
    trader, trade, repo, clob = setup_tp()
    clob.get_sell_fee_quote = lambda *a, **kw: .01
    buy = repo.get_exact_buy_fill_evidence(trade.buy_order_id)
    sellable = trader.signable_sell_shares(trade)
    sold_portion_net = ((.99-buy.confirmed_vwap)*sellable
        - buy.confirmed_fee_usdc*sellable/buy.confirmed_size - .01 - .001)
    whole_position_floor = (.99*sellable - buy.confirmed_vwap*buy.confirmed_size
        - buy.confirmed_fee_usdc - .01 - .001)
    assert sold_portion_net > 0
    assert whole_position_floor <= 0
    assert trader.execute_sell(trade) is False
    assert clob.orders == []


def test_unknown_tp_submission_stays_pending_and_consumes_sell_budget():
    trader, trade, repo, clob = setup_tp()
    clob.place_limit_order = lambda **kw: {"submission_outcome_unknown": True}
    assert trader.execute_sell(trade) is False
    pending = repo.updated[-1][1]
    assert pending["status"] == TradeStatus.PENDING_SELL
    assert pending["exit_reason"] == "take_profit_sell_submission_outcome_unknown"
    assert pending["realized_pnl"] is None
    assert trader.emergency_sell_submissions == 1
    assert trader.execute_sell(trade) is False  # same cycle cannot duplicate


@pytest.mark.parametrize("account,minimum", [("cat", "0.95"), ("dog", "0.97")])
def test_explicit_release_config_loads_new_band_with_default_release_off(account, minimum, monkeypatch, tmp_path):
    runtime = f"watermelon-live-{account}-{'96' if account == 'cat' else '99'}-1m-v2h"
    env = profile_environment(runtime, {"POLYBOT_TAKE_PROFIT_ENABLED": "true"})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_EFFECTIVE_FROM_UTC", "2026-09-07T00:00:00Z")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "1" * 64)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "2" * 40)
    monkeypatch.setenv("JOB_NAME", f"polybot-{account}")
    monkeypatch.chdir(tmp_path)
    config = load_config("missing-config.yaml", runtime, simulation_mode=False)
    assert config.trading.entry.prob_min == float(minimum)
    assert config.trading.entry.prob_max == 0.989
    assert config.trading.take_profit.enabled
    assert not config.trading.take_profit.include_existing_holdings
    assert config.trading.preregistration_sha256 == preregistration_sha256(take_profit_profile=True)
    assert config.trading.preregistration_sha256 != preregistration_sha256()


def test_flag_without_explicit_new_band_is_rejected_before_db_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_ENABLED", "true")
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_EFFECTIVE_FROM_UTC", "2026-09-07T00:00:00Z")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "1" * 64)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "2" * 40)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="entry band"):
        load_config("missing.yaml", "watermelon-live-cat-96-1m-v2h", simulation_mode=False)
    assert not (tmp_path / "data").exists()


def mutable_repo(repo, trades):
    """Model the real SQLAlchemy identity-map updates for multi-phase tests."""
    original = repo.update_trade
    def update(trade_id, **fields):
        original(trade_id, **fields)
        trades[trade_id].__dict__.update(fields)
    repo.update_trade = update
    repo.get_by_id = trades.get


def test_take_profit_rejection_preserves_first_failure_until_180_minute_quarantine(monkeypatch):
    trader, trade, repo, clob = setup_tp()
    mutable_repo(repo, {trade.id: trade})
    now = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz else now[0].replace(tzinfo=None)
        @classmethod
        def utcnow(cls):
            return now[0].replace(tzinfo=None)
    monkeypatch.setattr(trader_module, "datetime", Clock)
    attempts = []
    def reject(**order):
        attempts.append(order)
        return {"success": False, "error": "proven rejected order"}
    clob.place_limit_order = reject
    assert trader.execute_sell(trade) is False
    first = trade.sell_timestamp
    assert trade.exit_reason == "take_profit_sell_failure_retrying"
    now[0] += timedelta(minutes=1)
    assert trader.execute_sell(trade) is False
    assert trade.sell_timestamp == first
    now[0] += timedelta(minutes=180)
    assert trader.execute_sell(trade) is False
    assert len(attempts) == 2
    assert trade.status == TradeStatus.QUARANTINED
    assert trade.realized_pnl is None


def test_enabled_dispatcher_prioritizes_later_emergency_stop_over_earlier_tp():
    trader, profit, repo, clob = setup_tp()
    stopped = SimpleNamespace(**profit.__dict__)
    stopped.id = 8
    stopped.token_id = "emergency-stop-token"
    stopped.event_id = "event-stop"
    stopped.condition_id = "condition-stop"
    mutable_repo(repo, {profit.id: profit, stopped.id: stopped})
    tp_clob = clob
    stop_clob = _Clob(best_bid=.69, best_ask=.695, sell_vwap=.69, sell_limit=.69)
    tp_walk = tp_clob.get_sell_book_walk
    clob.get_sell_book_walk = lambda token, **kw: (stop_clob.get_sell_book_walk(token, **kw)
        if token == stopped.token_id else tp_walk(token, **kw))
    # Deliberately put the profitable holding first, reproducing the starvation case.
    assert trader.execute_holding_exits([profit, stopped]) == []
    assert len(clob.orders) == 1
    assert clob.orders[0]["token_id"] == stopped.token_id
    assert stopped.status == TradeStatus.PENDING_SELL
    assert profit.status == TradeStatus.HOLDING
    assert trader.emergency_sell_submissions == 1


def test_enabled_dispatcher_still_submits_tp_after_every_stop_check():
    trader, trade, repo, clob = setup_tp()
    mutable_repo(repo, {trade.id: trade})
    assert trader.execute_holding_exits([trade]) == []
    assert len(clob.orders) == 1
    assert trade.status == TradeStatus.PENDING_SELL
    assert trade.exit_reason == "take_profit_pending_confirmed_fill"


@pytest.mark.parametrize("ledger_failure", [False, True])
def test_late_tp_fill_after_quarantine_keeps_tp_identity_and_denies_reversal(ledger_failure, tmp_path):
    trader, trade, repo, clob = setup_tp()
    mutable_repo(repo, {trade.id: trade})
    if ledger_failure:
        def fail(**kw):
            raise SubmissionEvidenceError("mock durable ledger failure")
        clob.place_limit_order = fail
    assert trader.execute_sell(trade) is False
    if ledger_failure:
        assert trade.exit_reason == TAKE_PROFIT_LEDGER_QUARANTINE_REASON
        # Exact delayed ledger binding is provided before reconciliation.
        trade.sell_order_id = "sell-1"
        trade.sell_shares = trader.signable_sell_shares(trade)
    else:
        assert trader._quarantine_stop_sell_if_due(trade,
            now=trade.sell_timestamp+timedelta(minutes=181), detail="test timeout")
        assert trade.exit_reason == TAKE_PROFIT_QUARANTINE_REASON
    repo.get_exact_sell_fill_evidence = lambda _: ExactFillEvidence(
        "confirmed", "sell-1", side="SELL", order_status="MATCHED",
        requested_size=trade.sell_shares, latest_size_matched=trade.sell_shares,
        needs_reconciliation=False, reconciled_full_fill=True,
        confirmed_size=trade.sell_shares, confirmed_vwap=.99,
        confirmed_fee_usdc=.001, fee_complete=True,
    )
    assert trader.reconcile_pending_sell(trade) is True
    assert trade.exit_reason.startswith("take_profit_confirmed_fill")
    # Use the real repository policy, not a copied startswith assertion.
    from tests.test_reentry_policy import _repository, _trade, NOW as REENTRY_NOW
    session, actual_repo = _repository(tmp_path, "no-tp-reversal.db")
    _trade(actual_repo, exit_reason=trade.exit_reason)
    assert actual_repo.can_reenter("condition-b", 720, REENTRY_NOW,
        event_id="event-1", token_id="token-b") == (False, "event_close_not_reversible")
    session.close()


def test_future_release_cannot_buy_new_band_before_tp_is_effective(monkeypatch, tmp_path):
    runtime = "watermelon-live-cat-96-1m-v2h"
    for key, value in profile_environment(runtime, {"POLYBOT_TAKE_PROFIT_ENABLED":"true"}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_EFFECTIVE_FROM_UTC", "2100-01-01T00:00:00Z")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "1" * 64)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "2" * 40)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="future"):
        load_config("missing.yaml", runtime, simulation_mode=False)
    assert not (tmp_path/"data").exists()

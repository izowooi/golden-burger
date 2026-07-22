"""Golden Cherry의 시간 경계와 주문 실패 방어 로직 테스트."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)
from polybot.config import GameStartConfig, TimeBasedConfig, TradingConfig
from polybot.api.clob_client import ClobClientWrapper
from polybot.db.models import TradeStatus, init_database
from polybot.strategy.scanner import (
    MarketScanner,
    evaluate_game_start,
    is_valid_time_entry,
)
from polybot.strategy.trader import Trader


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeRepo:
    def __init__(self, *, position_count=0, open_notional=0.0):
        self.updates = []
        self.created = []
        self.position_count = position_count
        self.open_notional = open_notional

    def is_already_traded(self, condition_id):
        return False

    def get_position_count(self):
        return self.position_count

    def get_open_notional_usdc(self):
        return self.open_notional

    def mark_as_skipped(self, condition_id, reason):
        return None

    def create_trade(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)

    def update_trade(self, trade_id, **kwargs):
        self.updates.append((trade_id, kwargs))


class FakeClob:
    def __init__(self, midpoint=0.85, results=None, token_balance=None):
        self.midpoint = midpoint
        self.results = list(results or [{"success": True, "orderID": "ORDER"}])
        self.token_balance = token_balance
        self.orders = []
        self.cancelled = []

    def get_midpoint(self, token_id):
        return self.midpoint

    def get_conditional_token_balance(self, token_id):
        return self.token_balance

    def place_limit_order(self, **kwargs):
        self.orders.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return {"success": True, "orderID": "ORDER"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}


def make_trade(**overrides):
    trade = SimpleNamespace(
        id=1,
        condition_id="0xcondition",
        token_id="TOKEN",
        outcome="Yes",
        question="Will this market resolve?",
        buy_price=0.85,
        buy_shares=6.0,
        buy_order_id="BUY_ORDER",
        max_price=0.85,
        market_end_date=utcnow_naive() + timedelta(hours=100),
    )
    for key, value in overrides.items():
        setattr(trade, key, value)
    return trade


def make_candidate(condition_id="0xbuy"):
    return {
        "condition_id": condition_id,
        "token_id": "YES_TOKEN",
        "probability": 0.80,
        "outcome": "Yes",
        "question": "Will this market resolve?",
        "market_slug": "will-this-market-resolve",
        "liquidity": 100_000.0,
        "entry_reason": "time_based_24h",
        "end_date": utcnow_naive() + timedelta(hours=24),
        "hours_until_resolution": 24.0,
    }


def test_zero_to_120_hour_window_accepts_only_future_markets():
    now = datetime.now(timezone.utc)

    valid, _, _ = is_valid_time_entry(
        now + timedelta(minutes=1), 120, 0
    )
    too_early, reason, _ = is_valid_time_entry(
        now + timedelta(hours=121), 120, 0
    )
    resolved, resolved_reason, _ = is_valid_time_entry(
        now - timedelta(minutes=1), 120, 0
    )

    assert valid is True
    assert too_early is False and reason.startswith("too_early")
    assert resolved is False and resolved_reason == "already_resolved"


def test_active_time_exit_window_is_not_opened_as_a_new_position():
    valid, reason, _ = is_valid_time_entry(
        datetime.now(timezone.utc) + timedelta(hours=6),
        120,
        0,
        12,
    )

    assert valid is False
    assert reason.startswith("inside_exit_window")


def test_entry_window_change_does_not_sell_an_existing_position():
    config = TradingConfig(
        time_based=TimeBasedConfig(
            enabled=True,
            entry_hours_min=0,
            entry_hours_max=120,
            exit_hours=0,
        )
    )
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.85)
    trader = Trader(repo, clob, config)

    sold = trader.execute_sell(
        make_trade(market_end_date=utcnow_naive() + timedelta(hours=300))
    )

    assert sold is False
    assert clob.orders == []


def test_past_end_date_does_not_trigger_time_exit():
    config = TradingConfig(
        time_based=TimeBasedConfig(
            enabled=True,
            entry_hours_min=0,
            entry_hours_max=120,
            exit_hours=12,
        )
    )
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.85)
    trader = Trader(repo, clob, config)

    sold = trader.execute_sell(
        make_trade(market_end_date=utcnow_naive() - timedelta(hours=1))
    )

    assert sold is False
    assert clob.orders == []


def test_open_notional_limit_blocks_buy_without_touching_clob():
    config = TradingConfig(
        buy_amount_usdc=100,
        max_buy_amount_usdc=100,
        max_open_notional_usdc=5000,
    )
    repo = FakeRepo(open_notional=4950)
    clob = FakeClob(midpoint=0.80)
    trader = Trader(repo, clob, config)

    assert trader.execute_buy(make_candidate()) is None
    assert clob.orders == []


def test_per_cycle_position_limit_stops_buy_burst():
    config = TradingConfig(max_new_positions_per_cycle=1)
    repo = FakeRepo()
    clob = FakeClob(
        midpoint=0.80,
        results=[{"success": True, "orderID": "FIRST"}],
    )
    trader = Trader(repo, clob, config)

    assert trader.execute_buy(make_candidate("0xfirst")) == 1
    assert trader.execute_buy(make_candidate("0xsecond")) is None
    assert len(clob.orders) == 1


def test_order_preflight_rejects_sport_inside_game_start_buffer():
    game_start = datetime.now(timezone.utc) + timedelta(minutes=4)
    candidate = make_candidate()
    candidate.update({
        "entry_time_reference": "game_start_time",
        "game_start_time": game_start,
        "sports_market_type": "moneyline",
        "is_sports_timed": True,
    })
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.80)
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_buy(candidate) is None
    assert clob.orders == []


def test_order_preflight_rejects_non_sport_after_end_date():
    candidate = make_candidate()
    candidate["end_date"] = utcnow_naive() - timedelta(seconds=1)
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.80)
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_buy(candidate) is None
    assert clob.orders == []


def test_live_token_balance_clamps_sell_before_order():
    repo = FakeRepo()
    clob = FakeClob(
        midpoint=0.70,
        token_balance=9248.547141,
        results=[{"success": True, "orderID": "CLAMPED_SELL"}],
    )
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade(buy_shares=9248.5549)) is True
    assert [order["size"] for order in clob.orders] == [9248.547141]
    assert repo.updates[-1][1]["sell_shares"] == 9248.547141
    assert repo.updates[-1][1]["status"] == TradeStatus.COMPLETED


def test_clob_conditional_balance_is_scaled_from_micro_shares():
    wrapper = object.__new__(ClobClientWrapper)
    wrapper.simulation_mode = False
    wrapper._initialized = True
    wrapper._client = SimpleNamespace(
        get_balance_allowance=lambda params: {"balance": "9248547141"}
    )

    assert wrapper.get_conditional_token_balance("TOKEN") == 9248.547141


def test_existing_trade_database_gets_game_start_evidence_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, market_tags TEXT)"
        )

    init_database(str(db_path))

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
    assert {
        "market_game_start_time",
        "minutes_until_game_start_at_buy",
        "entry_time_reference",
        "hours_until_entry_deadline_at_buy",
        "sports_market_type",
    } <= columns


def test_reported_token_balance_retries_one_micro_share_below_balance():
    repo = FakeRepo()
    clob = FakeClob(
        midpoint=0.70,
        results=[
            {
                "success": False,
                "error": (
                    "not enough balance / allowance: the balance is not enough "
                    "-> balance: 5500000, order amount: 6000000"
                ),
            },
            {"success": True, "orderID": "PARTIAL_SELL"},
        ],
    )
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade()) is True
    assert [order["size"] for order in clob.orders] == [6.0, 5.499999]
    assert repo.updates[-1][1]["sell_shares"] == 5.499999
    assert repo.updates[-1][1]["status"] == TradeStatus.COMPLETED


def test_generic_large_partial_sell_keeps_sellable_remainder_open():
    repo = FakeRepo()
    clob = FakeClob(
        midpoint=0.70,
        results=[
            {"success": False, "error": "not enough balance / allowance"},
            {"success": True, "orderID": "PARTIAL_SELL"},
        ],
    )
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade(buy_shares=1000.0)) is False
    assert [order["size"] for order in clob.orders] == [1000.0, 990.0]
    assert repo.updates[-1][1]["buy_shares"] == 10.0
    assert repo.updates[-1][1]["status"] == TradeStatus.HOLDING


def test_game_start_filter_uses_start_not_late_end_date():
    now = datetime.now(timezone.utc)

    class FakeGamma:
        def get_all_tradable_markets(self, min_liquidity):
            assert min_liquidity == 50_000
            return [{
                "conditionId": "0xsport",
                "slug": "tennis-match",
                "question": "Will Player A win?",
                "outcomePrices": ["0.80", "0.20"],
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["YES", "NO"],
                "liquidity": "100000",
                "endDate": (now + timedelta(days=7)).isoformat(),
                "gameStartTime": (now + timedelta(hours=2)).isoformat(),
                "sportsMarketType": "moneyline",
            }]

    config = TradingConfig(
        excluded_categories=[],
        time_based=TimeBasedConfig(
            enabled=True,
            entry_hours_min=0,
            entry_hours_max=120,
            exit_hours=0,
        ),
    )
    candidates = MarketScanner(FakeGamma(), config).scan_buy_candidates()

    assert len(candidates) == 1
    assert candidates[0]["entry_time_reference"] == "game_start_time"
    assert 1.9 < candidates[0]["hours_until_entry_deadline"] <= 2
    assert candidates[0]["hours_until_resolution"] > 160


def test_game_start_filter_rejects_started_buffer_and_missing_start():
    now = datetime.now(timezone.utc)
    config = GameStartConfig(
        enabled=True,
        entry_buffer_minutes=5,
        reject_sports_without_game_start=True,
    )

    inside = evaluate_game_start(
        {
            "gameStartTime": (now + timedelta(minutes=4)).isoformat(),
            "sportsMarketType": "moneyline",
        },
        config,
        now=now,
    )
    missing = evaluate_game_start(
        {"sportsMarketType": "spread"},
        config,
        now=now,
    )

    assert inside.valid is False
    assert inside.reason.startswith("game_started_or_inside_buffer")
    assert missing.valid is False
    assert missing.reason == "sports_missing_game_start"


def test_unknown_submission_result_does_not_create_or_complete_trade():
    unknown = {
        "success": False,
        "error": "주문 POST 결과가 불확실하여 동일 token/side를 격리했습니다",
        "submission_outcome_unknown": True,
        "quarantined": True,
    }
    buy_repo = FakeRepo()
    buy_trader = Trader(buy_repo, FakeClob(midpoint=0.80, results=[unknown]), TradingConfig())
    sell_repo = FakeRepo()
    sell_trader = Trader(sell_repo, FakeClob(midpoint=0.70, results=[unknown]), TradingConfig())

    assert buy_trader.execute_buy(make_candidate()) is None
    assert sell_trader.execute_sell(make_trade()) is False
    assert buy_repo.created == []
    assert sell_repo.updates == []


def test_unavailable_zero_balance_buy_order_is_quarantined():
    zero_balance = {
        "success": False,
        "error": (
            "not enough balance / allowance: the balance is not enough "
            "-> balance: 0, order amount: 6000000"
        ),
    }
    repo = FakeRepo()
    clob = FakeClob(midpoint=0.70, results=[zero_balance])

    def unavailable_cancel(order_id):
        clob.cancelled.append(order_id)
        try:
            raise ClobResponseUnavailableError("aged out")
        except ClobResponseUnavailableError as cause:
            raise SubmissionEvidenceError("unavailable") from cause

    clob.cancel_order = unavailable_cancel
    trader = Trader(repo, clob, TradingConfig())

    assert trader.execute_sell(make_trade()) is False
    assert clob.cancelled == ["BUY_ORDER"]
    assert repo.updates[-1][1]["status"] == TradeStatus.QUARANTINED
    assert repo.updates[-1][1]["realized_pnl"] is None

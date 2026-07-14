"""Final Five replay catalog, broad archive, and atomic sweep evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.config import ArchiveConfig, TradingConfig
from polybot.db.models import (
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    TradeStatus,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner, _snapshot_values
from polybot.strategy.trader import Trader


NOW = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)


def market(
    condition_id: str,
    *,
    yes_price: float = 0.85,
    hours_left: float = 48,
    liquidity: float = 20_000,
    neg_risk=False,
    outcomes=("Yes", "No"),
    tokens=("yes-token", "no-token"),
    best_bid=0.84,
    best_ask=0.86,
    spread=0.02,
):
    return {
        "id": f"gamma-{condition_id}",
        "conditionId": condition_id,
        "slug": f"slug-{condition_id}",
        "question": f"Question for {condition_id}?",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": str(liquidity),
        "volume": "4000",
        "volume24hr": "1200",
        "outcomePrices": json.dumps([yes_price, 1.0 - yes_price]),
        "outcomes": json.dumps(list(outcomes)),
        "clobTokenIds": json.dumps(list(tokens)),
        "negRisk": neg_risk,
        "bestBid": best_bid,
        "bestAsk": best_ask,
        "spread": spread,
        "endDate": (NOW + timedelta(hours=hours_left)).isoformat(),
        "events": [
            {
                "id": "event-cluster",
                "slug": "event-cluster-slug",
                "title": "Event cluster",
            }
        ],
        "tags": [{"id": "7", "slug": "economics", "label": "Economics"}],
        "feesEnabled": True,
        "feeSchedule": {"rate": 0.01},
        "updatedAt": "2026-07-14T00:00:00Z",
    }


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


class _KeysetSession:
    def __init__(self, markets):
        self.markets = markets
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return _Response({"markets": self.markets})


def build_scanner(tmp_path, markets, *, archive_floor=0.85):
    Session = init_database(str(tmp_path / "papaya-evidence.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = GammaClient()
    gamma.session = _KeysetSession(markets)
    config = TradingConfig(
        archive=ArchiveConfig(prob_min=archive_floor, hours_max=168, retention_days=60)
    )
    return session, repo, gamma, MarketScanner(gamma, config, repo=repo)


def snapshot_cycle(gamma, repo, config, raw_market, observed_at):
    gamma.session.markets = [raw_market]
    scanner = MarketScanner(gamma, config, repo=repo)
    qualified = scanner.fetch_markets()
    scanner.save_market_snapshots(qualified, now=observed_at)
    return scanner, qualified


def test_complete_sweep_catalogs_all_qualified_but_snapshots_only_archive_set(
    tmp_path,
):
    raw = [
        market("archive-floor", yes_price=0.85, hours_left=168),
        market("below-floor", yes_price=0.849),
        market("neg-risk", yes_price=0.95, neg_risk=True),
        market("not-binary", yes_price=0.95, outcomes=("Up", "Down")),
        market("too-early", yes_price=0.95, hours_left=169),
        market(
            "invalid-book",
            yes_price=0.95,
            best_bid=0.96,
            best_ask=0.94,
            spread=0.02,
        ),
        market("low-liquidity", liquidity=10),
    ]
    session, repo, gamma, scanner = build_scanner(tmp_path, raw)

    qualified = scanner.fetch_markets()
    saved = scanner.save_market_snapshots(qualified, now=NOW)

    assert saved == 1
    assert {row.condition_id for row in session.query(MarketCatalog).all()} == {
        "archive-floor",
        "below-floor",
        "neg-risk",
        "not-binary",
        "too-early",
        "invalid-book",
    }
    assert {
        row.condition_id for row in session.query(MarketSnapshot).all()
    } == {"archive-floor"}

    catalog = session.get(MarketCatalog, "archive-floor")
    assert catalog.event_id == "event-cluster"
    assert catalog.event_slug == "event-cluster-slug"
    assert json.loads(catalog.outcomes_json) == ["Yes", "No"]
    assert json.loads(catalog.token_ids_json) == ["yes-token", "no-token"]
    assert json.loads(catalog.tags_json)[0]["slug"] == "economics"
    assert catalog.neg_risk == 0
    assert catalog.fees_enabled == 1
    assert catalog.fee_rate == 0.01

    snapshot = session.query(MarketSnapshot).one()
    assert snapshot.probability == 0.85
    assert snapshot.liquidity == 20_000
    assert snapshot.volume_24h == 1_200
    assert snapshot.best_bid == 0.84
    assert snapshot.best_ask == 0.86
    assert snapshot.spread == 0.02
    assert snapshot.source_updated_at == "2026-07-14T00:00:00Z"

    sweep = session.query(MarketSweep).one()
    assert sweep.cursor_complete == 1
    assert sweep.raw_market_count == 7
    assert sweep.unique_condition_count == 7
    assert sweep.qualified_market_count == 6
    assert sweep.excluded_condition_count == 1
    assert json.loads(sweep.exclusion_counts_json) == {"below_min_liquidity": 1}
    assert sweep.snapshot_eligible_count == 2
    assert sweep.snapshotted_market_count == 1
    memberships = {
        row.condition_id: row
        for row in session.query(MarketSweepMembership).all()
    }
    assert set(memberships) == {
        "archive-floor",
        "below-floor",
        "neg-risk",
        "not-binary",
        "too-early",
        "invalid-book",
    }
    assert memberships["archive-floor"].snapshot_reason == "snapshot_saved"
    assert memberships["archive-floor"].snapshotted == 1
    assert memberships["below-floor"].snapshot_reason == "archive_price_below_0.849"
    assert memberships["neg-risk"].snapshot_reason == "neg_risk_or_unknown"
    assert memberships["not-binary"].snapshot_reason == "not_standard_yes_no"
    assert memberships["too-early"].snapshot_reason == "archive_too_early_169.0h"
    assert memberships["invalid-book"].snapshot_eligible == 1
    assert memberships["invalid-book"].snapshotted == 0
    assert memberships["invalid-book"].snapshot_reason == "invalid_order_book"
    assert gamma.last_sweep_attestation["snapshot_eligible_count"] == 2
    assert gamma.last_sweep_attestation["snapshotted_market_count"] == 1
    session.close()


@pytest.mark.parametrize(
    ("yes_price", "overrides", "expected_reason"),
    [
        (float("inf"), {}, "invalid_yes_price"),
        (1.1, {}, "invalid_yes_price"),
        (0.85, {"liquidity": -1}, "invalid_liquidity"),
        (0.85, {"volume24hr": "nan"}, "invalid_volume_24h"),
        (0.85, {"bestBid": 1.1}, "invalid_best_bid"),
        (0.85, {"bestAsk": -0.1}, "invalid_best_ask"),
        (0.85, {"spread": -0.01}, "invalid_spread"),
        (
            0.85,
            {"bestBid": 0.90, "bestAsk": 0.89, "spread": 0.01},
            "invalid_order_book",
        ),
        (
            0.85,
            {"bestBid": 0.84, "bestAsk": 0.86, "spread": 0.03},
            "invalid_spread_consistency",
        ),
    ],
)
def test_snapshot_numeric_and_book_domain_is_fail_closed(
    yes_price, overrides, expected_reason
):
    raw = {
        "liquidity": 20_000,
        "volume24hr": 1_200,
        "bestBid": 0.84,
        "bestAsk": 0.86,
        "spread": 0.02,
        **overrides,
    }
    values, reason = _snapshot_values(raw, yes_price)
    assert values is None
    assert reason == expected_reason


def test_snapshot_accepts_valid_zero_domain_for_research_evidence():
    values, reason = _snapshot_values(
        {
            "liquidity": 0,
            "volume24hr": 0,
            "bestBid": 0,
            "bestAsk": 0,
            "spread": 0,
        },
        0,
    )
    assert reason == "snapshot_valid"
    assert values == {
        "liquidity": 0.0,
        "volume_24h": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
    }


def test_tampered_sweep_digest_rolls_back_catalog_snapshot_and_sweep(tmp_path):
    session, repo, gamma, scanner = build_scanner(
        tmp_path, [market("archive-floor", yes_price=0.85)]
    )
    qualified = scanner.fetch_markets()
    gamma.last_sweep_attestation["membership_digest_sha256"] = "tampered"

    with pytest.raises(ValueError, match="digest"):
        scanner.save_market_snapshots(qualified, now=NOW)

    assert session.query(MarketCatalog).count() == 0
    assert session.query(MarketSnapshot).count() == 0
    assert session.query(MarketSweep).count() == 0
    assert session.query(MarketSweepMembership).count() == 0
    session.close()


def test_archive_requires_completed_sweep_attestation(tmp_path):
    session, _repo, _gamma, scanner = build_scanner(
        tmp_path, [market("archive-floor")]
    )
    with pytest.raises(RuntimeError, match="attestation"):
        scanner.save_market_snapshots([], now=NOW)
    session.close()


@pytest.mark.parametrize(
    ("config", "first_crossing_overrides", "first_rejection"),
    [
        (
            TradingConfig(min_liquidity=5_000),
            {"liquidity": 2_000},
            "low_liquidity",
        ),
        (
            TradingConfig(min_volume_24h=5_000),
            {"volume24hr": "2000"},
            "low_volume",
        ),
    ],
)
def test_rejected_first_crossing_is_durable_and_later_recross_fails_closed(
    tmp_path,
    caplog,
    config,
    first_crossing_overrides,
    first_rejection,
):
    Session = init_database(str(tmp_path / f"durable-{first_rejection}.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = GammaClient()
    gamma.session = _KeysetSession([])

    below = market("durable", yes_price=0.94, hours_left=48)
    snapshot_cycle(gamma, repo, config, below, NOW)

    first_crossing = market("durable", yes_price=0.95, hours_left=48)
    first_crossing.update(first_crossing_overrides)
    first_scanner, first_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        first_crossing,
        NOW + timedelta(minutes=5),
    )
    with caplog.at_level("INFO"):
        assert first_scanner.scan_buy_candidates(
            first_markets, now=NOW + timedelta(minutes=5)
        ) == []
    assert f"{first_rejection}: 1" in caplog.text

    dip = market("durable", yes_price=0.94, hours_left=48)
    snapshot_cycle(gamma, repo, config, dip, NOW + timedelta(minutes=10))

    recross = market("durable", yes_price=0.95, hours_left=48)
    recross["liquidity"] = "6000"
    recross["volume24hr"] = "6000"
    recross_scanner, recross_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        recross,
        NOW + timedelta(minutes=15),
    )
    caplog.clear()
    with caplog.at_level("INFO"):
        assert recross_scanner.scan_buy_candidates(
            recross_markets, now=NOW + timedelta(minutes=15)
        ) == []
    assert "first_crossing_already_observed: 1" in caplog.text
    session.close()


def test_window_rejected_first_crossing_is_durable_after_entry_window_opens(
    tmp_path,
    caplog,
):
    Session = init_database(str(tmp_path / "durable-window.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = GammaClient()
    gamma.session = _KeysetSession([])
    config = TradingConfig()

    # Keep one fixed resolution time.  The first crossing is outside the
    # 72-hour entry window, but remains inside the 168-hour archive envelope.
    below = market("window", yes_price=0.94, hours_left=80)
    snapshot_cycle(gamma, repo, config, below, NOW)
    first_crossing = market("window", yes_price=0.95, hours_left=80)
    first_scanner, first_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        first_crossing,
        NOW + timedelta(minutes=5),
    )
    with caplog.at_level("INFO"):
        assert first_scanner.scan_buy_candidates(
            first_markets, now=NOW + timedelta(minutes=5)
        ) == []
    assert "too_early: 1" in caplog.text

    # Preserve an uninterrupted <=30-minute lineage until the entry window
    # opens.  These observations are below the threshold, so the later 0.95 is
    # a mathematical re-crossing but not a new first-observed crossing.
    for offset_minutes in range(25, 8 * 60, 20):
        snapshot_cycle(
            gamma,
            repo,
            config,
            below,
            NOW + timedelta(minutes=offset_minutes),
        )

    recross_time = NOW + timedelta(hours=8, minutes=5)
    recross = market("window", yes_price=0.95, hours_left=80)
    recross_scanner, recross_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        recross,
        recross_time,
    )
    caplog.clear()
    with caplog.at_level("INFO"):
        assert recross_scanner.scan_buy_candidates(
            recross_markets, now=recross_time
        ) == []
    assert "first_crossing_already_observed: 1" in caplog.text
    session.close()


def test_fresh_ask_rejected_first_crossing_is_durable_and_not_retried(
    tmp_path,
    caplog,
):
    class AskAboveCapClob:
        simulation_mode = True

        def __init__(self):
            self.orders = []

        def get_midpoint(self, _token_id):
            return 0.95

        def get_best_bid(self, _token_id):
            return 0.94

        def get_best_ask(self, _token_id):
            return 0.98

        def place_limit_order(self, **kwargs):
            self.orders.append(kwargs)
            return {"success": True, "orderID": "must-not-submit"}

    Session = init_database(str(tmp_path / "durable-ask.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = GammaClient()
    gamma.session = _KeysetSession([])
    config = TradingConfig()

    below = market("ask", yes_price=0.94, hours_left=48)
    snapshot_cycle(gamma, repo, config, below, NOW)
    first_crossing = market(
        "ask",
        yes_price=0.95,
        hours_left=48,
        best_bid=0.94,
        best_ask=0.96,
        spread=0.02,
    )
    first_scanner, first_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        first_crossing,
        NOW + timedelta(minutes=5),
    )
    first_candidates = first_scanner.scan_buy_candidates(
        first_markets, now=NOW + timedelta(minutes=5)
    )
    assert len(first_candidates) == 1

    clob = AskAboveCapClob()
    # Trader uses wall-clock time for final revalidation; keep only that field
    # fresh while preserving the scanner/archive lineage under test.
    first_candidates[0]["end_date"] = datetime.now(timezone.utc) + timedelta(
        hours=48
    )
    trader = Trader(repo, clob, config, simulation_mode=True)
    with caplog.at_level("INFO"):
        assert trader.execute_buy(first_candidates[0]) is None
    assert clob.orders == []
    assert "fresh ask 상한 초과" in caplog.text

    dip = market("ask", yes_price=0.94, hours_left=48)
    snapshot_cycle(gamma, repo, config, dip, NOW + timedelta(minutes=10))
    recross = market("ask", yes_price=0.95, hours_left=48)
    recross_scanner, recross_markets = snapshot_cycle(
        gamma,
        repo,
        config,
        recross,
        NOW + timedelta(minutes=15),
    )
    caplog.clear()
    with caplog.at_level("INFO"):
        assert recross_scanner.scan_buy_candidates(
            recross_markets, now=NOW + timedelta(minutes=15)
        ) == []
    assert "first_crossing_already_observed: 1" in caplog.text
    session.close()


def test_retention_removes_old_snapshots_and_sweeps_but_not_catalog(tmp_path):
    session, repo, _gamma, scanner = build_scanner(
        tmp_path, [market("archive-floor")]
    )
    qualified = scanner.fetch_markets()
    scanner.save_market_snapshots(qualified, now=NOW)
    old = datetime.utcnow() - timedelta(days=61)
    session.query(MarketSnapshot).one().timestamp = old
    session.query(MarketSweep).one().completed_at = old
    session.commit()

    assert repo.cleanup_old_snapshots(days=60) == 1
    assert session.query(MarketSnapshot).count() == 0
    assert session.query(MarketSweepMembership).count() == 0
    assert session.query(MarketSweep).count() == 0
    assert session.query(MarketCatalog).count() == 1
    session.close()


def test_repository_event_cap_counts_open_sibling_conditions_only(tmp_path):
    Session = init_database(str(tmp_path / "event-cap.db"))
    session = Session()
    repo = TradeRepository(session)
    for condition_id, status in [
        ("condition-a", TradeStatus.HOLDING),
        ("condition-b", TradeStatus.PENDING_BUY),
        ("condition-e", TradeStatus.PENDING_SELL),
        ("condition-c", TradeStatus.COMPLETED),
    ]:
        repo.create_trade(
            condition_id=condition_id,
            event_id="same-event",
            token_id=f"token-{condition_id}",
            outcome="Yes",
            status=status,
        )
    repo.create_trade(
        condition_id="condition-d",
        event_id="other-event",
        token_id="token-d",
        outcome="Yes",
        status=TradeStatus.HOLDING,
    )

    assert repo.get_event_position_count("same-event") == 3
    assert repo.get_event_position_count("other-event") == 1
    assert repo.get_event_position_count(None) == 0
    session.close()

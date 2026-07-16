"""Final Five scanner crossing and event-risk identity contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


class PriorRepo:
    def __init__(
        self,
        probability=0.949,
        *,
        gap_minutes=5,
        history=None,
    ):
        self.prior = SimpleNamespace(
            id=1,
            condition_id="condition-1",
            probability=probability,
            timestamp=(NOW - timedelta(minutes=gap_minutes)).replace(tzinfo=None),
        )
        self.current = SimpleNamespace(
            id=2,
            condition_id="condition-1",
            probability=0.95,
            timestamp=NOW.replace(tzinfo=None),
            liquidity=20_000.0,
            volume_24h=4_000.0,
            best_bid=0.949,
            best_ask=0.951,
            spread=0.002,
        )
        self.history = history or [self.prior, self.current]

    def get_latest_snapshot_before_run(self, _condition_id, run_id=None):
        return self.prior

    def get_snapshots_since(self, _condition_id, _since):
        return list(self.history)


def scanner_with_lineage(*, repo=None, config=None):
    repo = repo or PriorRepo()
    scanner = MarketScanner(
        gamma_client=SimpleNamespace(),
        config=config or TradingConfig(),
        repo=repo,
    )
    scanner._current_snapshot_ids["condition-1"] = repo.current.id
    scanner._current_snapshots["condition-1"] = repo.current
    scanner._prior_snapshots["condition-1"] = repo.prior
    return scanner


def crossed_market(*, event=True):
    raw = {
        "conditionId": "condition-1",
        "slug": "final-five",
        "question": "Will this resolve Yes?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": [0.95, 0.05],
        "clobTokenIds": ["yes-token", "no-token"],
        "negRisk": False,
        "liquidity": 20_000,
        "volume24hr": 4_000,
        "bestBid": 0.949,
        "bestAsk": 0.951,
        "spread": 0.002,
        "endDate": (NOW + timedelta(hours=48)).isoformat(),
        "tags": [{"slug": "economics", "label": "Economics"}],
    }
    if event:
        raw["events"] = [{"id": "event-1", "slug": "event-one"}]
    return raw


def test_crossed_strict_binary_market_preserves_gamma_event_identity():
    scanner = scanner_with_lineage()

    candidates = scanner.scan_buy_candidates([crossed_market()], now=NOW)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["condition_id"] == "condition-1"
    assert candidate["event_id"] == "event-1"
    assert candidate["event_slug"] == "event-one"
    assert candidate["outcome"] == "Yes"
    assert candidate["prior_yes_price"] == 0.949
    assert candidate["yes_price"] == 0.95
    assert candidate["entry_snapshot_id"] == 2


def test_crossed_market_without_event_id_fails_closed_and_is_counted(caplog):
    scanner = scanner_with_lineage()

    with caplog.at_level("INFO"):
        candidates = scanner.scan_buy_candidates(
            [crossed_market(event=False)], now=NOW
        )

    assert candidates == []
    assert "missing_event_id: 1" in caplog.text
    assert "event_id_fallback" not in caplog.text


def test_candidate_without_current_persisted_snapshot_fails_closed(caplog):
    scanner = MarketScanner(
        gamma_client=SimpleNamespace(),
        config=TradingConfig(),
        repo=PriorRepo(),
    )

    with caplog.at_level("INFO"):
        assert scanner.scan_buy_candidates([crossed_market()], now=NOW) == []

    assert "current_snapshot_missing: 1" in caplog.text


def test_non_positive_current_snapshot_id_fails_closed(caplog):
    repo = PriorRepo()
    repo.current.id = 0
    scanner = scanner_with_lineage(repo=repo)

    with caplog.at_level("INFO"):
        assert scanner.scan_buy_candidates([crossed_market()], now=NOW) == []

    assert "current_snapshot_id_invalid: 1" in caplog.text


def test_stale_prior_snapshot_fails_closed_at_explicit_gap(caplog):
    scanner = scanner_with_lineage(repo=PriorRepo(gap_minutes=30.01))

    with caplog.at_level("INFO"):
        assert scanner.scan_buy_candidates([crossed_market()], now=NOW) == []

    assert "prior_snapshot_stale: 1" in caplog.text


def test_exact_max_snapshot_gap_is_inclusive():
    scanner = scanner_with_lineage(repo=PriorRepo(gap_minutes=30))
    assert len(scanner.scan_buy_candidates([crossed_market()], now=NOW)) == 1


def test_any_earlier_threshold_observation_permanently_blocks_recross(caplog):
    first_below = SimpleNamespace(
        id=1,
        condition_id="condition-1",
        probability=0.94,
        timestamp=(NOW - timedelta(minutes=15)).replace(tzinfo=None),
    )
    first_crossing = SimpleNamespace(
        id=2,
        condition_id="condition-1",
        probability=0.95,
        timestamp=(NOW - timedelta(minutes=10)).replace(tzinfo=None),
    )
    latest_below = SimpleNamespace(
        id=3,
        condition_id="condition-1",
        probability=0.94,
        timestamp=(NOW - timedelta(minutes=5)).replace(tzinfo=None),
    )
    current_recross = SimpleNamespace(
        id=4,
        condition_id="condition-1",
        probability=0.95,
        timestamp=NOW.replace(tzinfo=None),
        liquidity=20_000.0,
        volume_24h=4_000.0,
        best_bid=0.949,
        best_ask=0.951,
        spread=0.002,
    )
    repo = PriorRepo(
        history=[first_below, first_crossing, latest_below, current_recross]
    )
    repo.prior = latest_below
    repo.current = current_recross
    scanner = scanner_with_lineage(repo=repo)

    with caplog.at_level("INFO"):
        assert scanner.scan_buy_candidates([crossed_market()], now=NOW) == []

    assert "first_crossing_already_observed: 1" in caplog.text


def test_archive_fetch_keeps_low_liquidity_baseline_for_higher_entry_cohort():
    gamma = SimpleNamespace(get_all_tradable_markets=MagicMock(return_value=[]))
    scanner = MarketScanner(
        gamma,
        TradingConfig(min_liquidity=10_000, min_volume_24h=5_000),
    )

    assert scanner.fetch_markets() == []
    gamma.get_all_tradable_markets.assert_called_once_with(
        min_liquidity=1_000.0,
        min_volume=0,
    )

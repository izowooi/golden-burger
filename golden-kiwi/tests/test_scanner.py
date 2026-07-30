"""Persisted Micro-Cascade lineage and per-event ranking contracts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import polybot.strategy.scanner as scanner_module
from polybot.config import (
    ExperimentCollectionConfig,
    MicroCascadeEntryConfig,
    TradingConfig,
)
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
CURRENT_RUN_ID = "current-run"


@pytest.fixture(autouse=True)
def current_run(monkeypatch):
    monkeypatch.setattr(scanner_module, "current_run_id", lambda: CURRENT_RUN_ID)


def market(condition="c1", event="e1", liquidity=30_000, tags=None):
    return {
        "conditionId": condition,
        "slug": condition,
        "question": condition,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [0.42, 0.58],
        "clobTokenIds": [f"{condition}-yes", f"{condition}-no"],
        "negRisk": False,
        "liquidity": liquidity,
        "volume24hr": 12_000,
        "endDate": (NOW + timedelta(hours=8)).isoformat(),
        "events": [{"id": event, "slug": event}],
        "tags": tags or [],
    }


def snapshots(condition="c1", *, steps=3, liquidity=30_000, gaps=None):
    probabilities = (
        [0.40, 0.407, 0.414, 0.42]
        if steps == 3
        else [0.40, 0.404, 0.408, 0.412, 0.416, 0.42]
    )
    gaps = gaps or [5] * steps
    timestamps = [NOW - timedelta(minutes=sum(gaps))]
    for gap in gaps:
        timestamps.append(timestamps[-1] + timedelta(minutes=gap))
    return [
        SimpleNamespace(
            id=index + 1,
            condition_id=condition,
            probability=probability,
            liquidity=liquidity,
            volume_24h=12_000,
            best_bid=probability - 0.005,
            best_ask=probability + 0.005,
            spread=0.01,
            run_id=(
                CURRENT_RUN_ID
                if index == len(probabilities) - 1
                else f"successful-prior-{index}"
            ),
            timestamp=timestamp.replace(tzinfo=None),
        )
        for index, (probability, timestamp) in enumerate(
            zip(probabilities, timestamps)
        )
    ]


class Repo:
    def __init__(self, rows_by_condition):
        self.rows = rows_by_condition

    def get_entry_lineage_snapshots(self, condition_id, _since, run_id):
        assert run_id == CURRENT_RUN_ID
        return list(self.rows.get(condition_id, []))


def scanner_for(
    rows_by_condition,
    *,
    steps=3,
    minimum=0.02,
    experiment=None,
):
    config = TradingConfig(
        entry=MicroCascadeEntryConfig(
            confirmation_steps=steps,
            min_cumulative_move=minimum,
        )
    )
    scanner = MarketScanner(
        SimpleNamespace(),
        config,
        Repo(rows_by_condition),
        experiment=experiment,
        job_name="kiwi-sim-b-3x2",
    )
    for condition, rows in rows_by_condition.items():
        scanner._current_snapshot_ids[condition] = rows[-1].id
        scanner._current_snapshots[condition] = rows[-1]
    return scanner


def test_three_step_candidate_carries_complete_trend_evidence():
    rows = snapshots()
    candidate = scanner_for({"c1": rows}).scan_buy_candidates(
        [market()], now=NOW
    )[0]
    assert candidate["trend_snapshot_ids"] == [1, 2, 3, 4]
    assert candidate["trend_start_snapshot_id"] == 1
    assert candidate["prior_snapshot_id"] == 3
    assert candidate["entry_snapshot_id"] == 4
    assert candidate["confirmation_steps"] == 3
    assert candidate["cumulative_move"] == pytest.approx(0.02)
    assert candidate["min_gap_minutes"] == pytest.approx(5)
    assert candidate["max_gap_minutes"] == pytest.approx(5)
    assert candidate["signal_best_bid"] == pytest.approx(0.415)
    assert candidate["signal_best_ask"] == pytest.approx(0.425)
    assert candidate["signal_spread"] == pytest.approx(0.01)


def test_five_step_arm_requires_six_persisted_observations():
    rows = snapshots(steps=5)
    candidates = scanner_for(
        {"c1": rows}, steps=5, minimum=0.02
    ).scan_buy_candidates([market()], now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["trend_snapshot_ids"] == [1, 2, 3, 4, 5, 6]


def test_no_current_run_snapshot_means_no_candidate():
    rows = snapshots()
    scanner = scanner_for({"c1": rows})
    scanner._current_snapshot_ids.clear()
    scanner._current_snapshots.clear()
    assert scanner.scan_buy_candidates([market()], now=NOW) == []


def test_gap_outside_three_to_ten_minutes_fails_closed():
    rows = snapshots(gaps=[2.9, 5, 5])
    assert scanner_for({"c1": rows}).scan_buy_candidates([market()], now=NOW) == []


@pytest.mark.parametrize(
    "tag",
    [
        "sports",
        "games",
        "esports",
        "crypto-prices",
        "up-or-down",
        "multi-strikes",
        "5m",
        "15m",
        "1h",
    ],
)
def test_each_exact_frozen_tag_is_excluded(tag):
    rows = snapshots()
    tagged = market(tags=[{"slug": tag}])
    assert scanner_for({"c1": rows}).scan_buy_candidates([tagged], now=NOW) == []


def test_similar_but_not_exact_tag_is_not_excluded():
    rows = snapshots()
    tagged = market(tags=[{"slug": "sports-politics"}])
    assert len(scanner_for({"c1": rows}).scan_buy_candidates([tagged], now=NOW)) == 1


def test_one_candidate_per_event_uses_liquidity_then_condition_id():
    first = snapshots("c1", liquidity=40_000)
    second = snapshots("c2", liquidity=50_000)
    scanner = scanner_for({"c1": first, "c2": second})
    result = scanner.scan_buy_candidates(
        [
            market("c1", "shared", liquidity=40_000),
            market("c2", "shared", liquidity=50_000),
        ],
        now=NOW,
    )
    assert [candidate["condition_id"] for candidate in result] == ["c2"]
    by_condition = {
        row["condition_id"]: row for row in scanner.last_signal_funnel
    }
    assert set(by_condition) == {"c1", "c2"}
    assert by_condition["c1"]["event_sibling_count"] == 2
    assert by_condition["c1"]["event_rank"] == 2
    assert by_condition["c1"]["event_selected"] == 0
    assert by_condition["c1"]["raw_selected"] == 0
    assert by_condition["c2"]["event_rank"] == 1
    assert by_condition["c2"]["event_selected"] == 1
    assert by_condition["c2"]["global_rank"] == 1
    assert by_condition["c2"]["raw_selected"] == 1
    assert by_condition["c2"]["position_count"] == 0
    assert by_condition["c2"]["open_notional_usdc"] == 0


def test_collection_window_marks_only_in_window_raw_signal_evidence():
    experiment = ExperimentCollectionConfig(
        enabled=True,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW + timedelta(hours=1),
        expected_offset_minute=0,
    )
    scanner = scanner_for(
        {"c1": snapshots()},
        experiment=experiment,
    )
    scanner.scan_buy_candidates([market()], now=NOW)
    assert scanner.last_signal_funnel[0]["collection_eligible"] == 1
    assert scanner.last_signal_funnel[0]["canonical_job"] == "kiwi-sim-b-3x2"
    assert scanner.last_signal_funnel[0]["arm"] == "B"


def test_resolution_less_than_six_hours_away_is_rejected():
    rows = snapshots()
    too_close = market()
    too_close["endDate"] = (NOW + timedelta(hours=5.9)).isoformat()
    assert scanner_for({"c1": rows}).scan_buy_candidates([too_close], now=NOW) == []

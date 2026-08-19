from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "evaluate_late_underdog.py"
SPEC = importlib.util.spec_from_file_location("pomegranate_late_underdog_analysis", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)


def observation(
    received_at: datetime,
    *,
    end_at: datetime,
    prices: tuple[float, float] = (0.85, 0.15),
) -> analysis.Observation:
    return analysis.Observation(
        condition_id="condition",
        event_id="event",
        question="Will the underdog win?",
        received_at=received_at,
        end_at=end_at,
        prices=prices,
        tokens=("favorite", "underdog"),
        best_bid=0.85,
        best_ask=0.86,
        spread=0.01,
        run_id="run",
        cycle_number=7,
    )


def test_sports_tag_and_binary_price_contract() -> None:
    assert analysis.has_sports_tag('[{"slug":"sports"}]')
    assert not analysis.has_sports_tag('[{"slug":"politics"}]')
    assert analysis.parse_pair('["0.25", "0.75"]') == (0.25, 0.75)
    assert analysis.parse_pair('["0.25", "0.50"]') is None
    assert analysis.parse_pair('["0.25", "0.25", "0.50"]') is None


def test_price_arm_boundaries_are_half_open() -> None:
    assert analysis.arm_for_price(0.10) == "primary_10_20"
    assert analysis.arm_for_price(0.199999) == "primary_10_20"
    assert analysis.arm_for_price(0.20) == "control_20_30"
    assert analysis.arm_for_price(0.299999) == "control_20_30"
    assert analysis.arm_for_price(0.30) is None


def test_discovery_requires_forward_six_hour_crossing(monkeypatch) -> None:
    end = datetime(2026, 8, 20, 12, tzinfo=UTC)
    prior = observation(end - timedelta(hours=6, minutes=15), end_at=end)
    current = observation(end - timedelta(hours=6), end_at=end)
    monkeypatch.setattr(
        analysis,
        "iter_observations",
        lambda _path: iter((prior, current)),
    )

    signals, counters = analysis.discover_signals(
        [Path("unused.db")],
        {("run", 7, "underdog"): 0.16},
    )

    assert len(signals) == 1
    assert signals[0].arm == "primary_10_20"
    assert signals[0].underdog_index == 1
    assert signals[0].proxy_ask == pytest.approx(0.15)
    assert signals[0].exact_clob_ask == 0.16
    assert counters["six_hour_crossings"] == 1


def test_end_date_change_cannot_create_crossing(monkeypatch) -> None:
    end = datetime(2026, 8, 20, 12, tzinfo=UTC)
    prior = observation(end - timedelta(hours=6, minutes=15), end_at=end)
    current = observation(
        end - timedelta(hours=6),
        end_at=end + timedelta(minutes=5),
    )
    monkeypatch.setattr(
        analysis,
        "iter_observations",
        lambda _path: iter((prior, current)),
    )

    signals, counters = analysis.discover_signals([Path("unused.db")], {})

    assert signals == []
    assert counters["end_date_changed"] == 1

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "evaluate_sports_favorite_grid.py"
SPEC = importlib.util.spec_from_file_location("pomegranate_sports_favorite_grid", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)


def observation(
    received_at: datetime,
    *,
    end_at: datetime,
    best_bid: float,
    best_ask: float,
    cycle: int,
) -> analysis.Observation:
    quotes = analysis.executable_quotes(best_bid, best_ask)
    assert quotes is not None
    return analysis.Observation(
        condition_id="condition",
        event_id="event",
        question="Will the favorite win?",
        received_at=received_at,
        end_at=end_at,
        game_start_at=end_at,
        outcome_prices=(best_ask, 1.0 - best_ask),
        tokens=("favorite", "other"),
        bids=quotes[0],
        asks=quotes[1],
        spread=best_ask - best_bid,
        fees_enabled=True,
        market_type="moneyline",
        run_id="run",
        cycle_number=cycle,
    )


def test_executable_quotes_include_complement_token() -> None:
    quotes = analysis.executable_quotes(0.79, 0.81)

    assert quotes == ((0.79, pytest.approx(0.19)), (0.81, pytest.approx(0.21)))
    assert analysis.executable_quotes(None, 0.81) is None
    assert analysis.executable_quotes(0.82, 0.81) is None


def test_discovery_requires_six_hour_boundary_and_records_later_target(
    monkeypatch,
) -> None:
    end = datetime(2026, 8, 20, 12, tzinfo=UTC)
    rows = (
        observation(
            end - timedelta(hours=6, minutes=15),
            end_at=end,
            best_bid=0.78,
            best_ask=0.79,
            cycle=1,
        ),
        observation(
            end - timedelta(hours=6),
            end_at=end,
            best_bid=0.795,
            best_ask=0.805,
            cycle=2,
        ),
        observation(
            end - timedelta(hours=5, minutes=45),
            end_at=end,
            best_bid=0.90,
            best_ask=0.91,
            cycle=3,
        ),
    )
    monkeypatch.setattr(analysis, "iter_observations", lambda _path: iter(rows))

    signals, counters = analysis.discover_signals(
        [Path("unused.db")],
        {
            ("run", 2, "favorite"): analysis.ExactBook(bid=0.79, ask=0.81),
            ("run", 3, "favorite"): analysis.ExactBook(bid=0.89, ask=0.91),
        },
    )

    entry_80 = [signal for signal in signals if signal.entry_cents == 80]
    assert len(entry_80) == 1
    assert entry_80[0].proxy_ask == pytest.approx(0.805)
    assert entry_80[0].exact_ask == pytest.approx(0.81)
    assert entry_80[0].target_hits[90].proxy_bid == pytest.approx(0.90)
    assert entry_80[0].target_hits[90].exact_bid == pytest.approx(0.89)
    assert counters["six_hour_boundaries"] == 1


def make_signal(entry_ask: float = 0.80) -> analysis.Signal:
    entered = datetime(2026, 8, 14, tzinfo=UTC)
    return analysis.Signal(
        condition_id="condition",
        event_id="event",
        question="Will the favorite win?",
        outcome_index=0,
        token_id="favorite",
        entry_cents=80,
        signal_at=entered,
        end_at=entered + timedelta(hours=6),
        game_start_at=entered + timedelta(hours=6),
        proxy_ask=entry_ask,
        exact_ask=0.81,
        spread=0.01,
        fees_enabled=True,
        market_type="moneyline",
        run_id="run",
        cycle_number=1,
    )


def test_target_exit_precedes_resolution_and_applies_both_taker_fees() -> None:
    signal = make_signal()
    signal.target_hits[90] = analysis.ExitHit(
        observed_at=signal.signal_at + timedelta(hours=1),
        proxy_bid=0.91,
        exact_bid=0.90,
    )
    labels = {
        "condition": [
            analysis.Label(
                observed_at=signal.signal_at + timedelta(hours=8), winner_index=1
            )
        ]
    }

    result = analysis.evaluate_signal(
        signal,
        target_cents=90,
        cutoff=signal.signal_at + timedelta(days=1),
        labels=labels,
    )

    assert result is not None
    assert result.exit_kind == "target"
    expected = analysis.trade_roi(
        entry_price=0.80,
        exit_value=0.91,
        rate=0.03,
        resolution_exit=False,
    )
    assert result.roi == pytest.approx(expected)
    assert result.exact_roi == pytest.approx(
        analysis.trade_roi(
            entry_price=0.81,
            exit_value=0.90,
            rate=0.03,
            resolution_exit=False,
        )
    )


def test_resolution_only_is_right_censored_at_period_cutoff() -> None:
    signal = make_signal()
    labels = {
        "condition": [
            analysis.Label(
                observed_at=signal.signal_at + timedelta(days=2), winner_index=0
            )
        ]
    }

    assert (
        analysis.evaluate_signal(
            signal,
            target_cents=None,
            cutoff=signal.signal_at + timedelta(days=1),
            labels=labels,
        )
        is None
    )
    resolved = analysis.evaluate_signal(
        signal,
        target_cents=None,
        cutoff=signal.signal_at + timedelta(days=3),
        labels=labels,
    )
    assert resolved is not None
    assert resolved.exit_kind == "resolution"
    assert resolved.resolution_win == 1
    assert resolved.exit_value == 1.0


def test_fee_disabled_market_has_no_synthetic_fee() -> None:
    signal = make_signal()
    signal.fees_enabled = False

    assert analysis.fee_rate(signal) == 0.0
    assert analysis.trade_roi(
        entry_price=0.80,
        exit_value=1.0,
        rate=analysis.fee_rate(signal),
        resolution_exit=True,
    ) == pytest.approx(0.25)


def test_wilson_interval_does_not_treat_all_wins_as_certain() -> None:
    lower, upper = analysis.wilson_interval(60, 60)

    assert lower is not None and lower < 0.95
    assert upper == pytest.approx(1.0)

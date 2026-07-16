"""Deterministic, offline Papaya research backtest contract."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "backtest.py"
SPEC = importlib.util.spec_from_file_location("papaya_backtest", SCRIPT)
assert SPEC and SPEC.loader
backtest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backtest
SPEC.loader.exec_module(backtest)


FIELDS = [
    "event_id",
    "condition_id",
    "timestamp",
    "hours_left",
    "yes_price",
    "best_ask",
    "best_bid",
    "liquidity",
    "volume_24h",
    "resolution_payout",
    "outcomes",
    "token_ids",
    "yes_token_id",
    "neg_risk",
]


def _write_research(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _row(condition: str, timestamp: str, hours: float, price: float, **overrides):
    return {
        "event_id": overrides.pop("event_id", f"event-{condition}"),
        "condition_id": condition,
        "timestamp": timestamp,
        "hours_left": hours,
        "yes_price": price,
        "best_ask": overrides.pop("best_ask", price + 0.001),
        "best_bid": overrides.pop("best_bid", price - 0.001),
        "liquidity": overrides.pop("liquidity", 25_000),
        "volume_24h": overrides.pop("volume_24h", 10_000),
        "resolution_payout": overrides.pop("resolution_payout", 1),
        "outcomes": overrides.pop("outcomes", '["Yes", "No"]'),
        "token_ids": overrides.pop(
            "token_ids", f'["yes-{condition}", "no-{condition}"]'
        ),
        "yes_token_id": overrides.pop("yes_token_id", f"yes-{condition}"),
        "neg_risk": overrides.pop("neg_risk", "false"),
        **overrides,
    }


def _params(
    entry=0.95,
    entry_upper=0.97,
    stop=0.90,
    liquidity=1_000,
    volume=0,
    hours_max=72,
):
    return backtest.Parameters(
        entry, entry_upper, stop, liquidity, volume, hours_max
    )


@pytest.fixture
def research_csv(tmp_path):
    path = tmp_path / "research.csv"
    rows = [
        # train: first 0.95 crossing enters at executable ask, then stops at bid.
        _row("train-stop", "2026-01-01T00:00:00Z", 48, 0.93),
        _row("train-stop", "2026-01-01T00:10:00Z", 47, 0.951, best_ask=0.952),
        _row("train-stop", "2026-01-01T00:20:00Z", 46, 0.89, best_bid=0.89),
        # test: crosses once and settles; later second crossing must not re-enter.
        _row("test-resolve", "2026-02-01T00:00:00Z", 10, 0.94),
        _row("test-resolve", "2026-02-01T00:10:00Z", 9, 0.956, best_ask=0.957),
        _row("test-resolve", "2026-02-01T00:20:00Z", 8, 0.94, best_bid=0.94),
        _row("test-resolve", "2026-02-01T00:30:00Z", 7, 0.96),
        # third market makes the chronological split deterministic.
        _row("test-loss", "2026-03-01T00:00:00Z", 6, 0.93, resolution_payout=0),
        _row(
            "test-loss",
            "2026-03-01T00:10:00Z",
            5,
            0.952,
            best_ask=0.953,
            resolution_payout=0,
        ),
    ]
    _write_research(path, rows)
    return path


def test_replay_uses_first_crossing_executable_ask_and_bid(research_csv):
    grouped = backtest.read_research_csv(research_csv)
    time_split = backtest.build_time_split(grouped, 0.5)
    params = _params()

    stopped = backtest.replay_market(grouped["train-stop"], params, time_split)
    resolved = backtest.replay_market(grouped["test-resolve"], params, time_split)

    assert stopped.entry_ask == 0.952
    assert stopped.exit_value == 0.89
    assert stopped.exit_reason == "stop_loss"
    assert stopped.pnl_per_share == pytest.approx(0.89 - 0.952)
    assert stopped.observed_book_pnl_per_share == stopped.pnl_per_share
    assert stopped.midpoint_pnl_per_share == pytest.approx(0.89 - 0.951)
    assert stopped.confirmed_fill_pnl_per_share is None
    assert stopped.execution_evidence == "hypothetical_observed_book"
    assert resolved.entry_timestamp == "2026-02-01T00:10:00Z"
    assert resolved.exit_reason == "resolution"
    assert resolved.exit_value == 1.0
    assert resolved.pnl_per_share == pytest.approx(1.0 - 0.957)
    assert resolved.pnl_after_fee_10bps < resolved.pnl_per_share
    assert resolved.pnl_after_fee_50bps < resolved.pnl_after_fee_10bps
    assert resolved.pnl_after_slippage_10bps < resolved.pnl_per_share


def test_no_entry_without_a_crossing_even_if_first_row_is_above_threshold(tmp_path):
    path = tmp_path / "already-high.csv"
    _write_research(
        path,
        [
            _row("high", "2026-01-01T00:00:00Z", 10, 0.96),
            _row("high", "2026-01-01T00:10:00Z", 9, 0.965),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)

    assert (
        backtest.replay_market(
            grouped["high"], _params(liquidity=0), time_split
        )
        is None
    )


def test_positive_sub_two_hour_crossing_is_inside_entry_window(tmp_path):
    path = tmp_path / "sub-two-hours.csv"
    _write_research(
        path,
        [
            _row("sports", "2026-01-01T00:00:00Z", 1.6, 0.94),
            _row("sports", "2026-01-01T00:05:00Z", 1.5, 0.951),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)

    result = backtest.replay_market(grouped["sports"], _params(), time_split)

    assert result is not None
    assert result.condition_id == "sports"


@pytest.mark.parametrize(
    ("hours", "ask", "liquidity", "volume"),
    [
        (0.0, 0.951, 25_000, 10_000),
        (72.01, 0.951, 25_000, 10_000),
        (24, 0.971, 25_000, 10_000),
        (24, 0.951, 999, 10_000),
        (24, 0.951, 25_000, 499),
    ],
)
def test_entry_requires_window_executable_ask_liquidity_and_volume(
    tmp_path, hours, ask, liquidity, volume
):
    path = tmp_path / "filters.csv"
    _write_research(
        path,
        [
            _row("market", "2026-01-01T00:00:00Z", hours + 1, 0.94),
            _row(
                "market",
                "2026-01-01T00:10:00Z",
                hours,
                0.951,
                best_ask=ask,
                best_bid=min(ask, 0.95),
                liquidity=liquidity,
                volume_24h=volume,
            ),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)
    params = _params(volume=500)
    assert backtest.replay_market(grouped["market"], params, time_split) is None


def test_declared_grid_is_complete():
    params = list(backtest.parameter_grid())
    assert len(params) == 3 * 3 * 3 * 4 * 4 * 3
    assert sorted({item.entry_probability for item in params}) == [0.94, 0.95, 0.96]
    assert sorted({item.entry_price_max for item in params}) == [0.96, 0.97, 0.98]
    assert sorted({item.stop_probability for item in params}) == [0.85, 0.90, 0.93]
    assert sorted({item.min_liquidity for item in params}) == [1000, 5000, 10000, 20000]
    assert sorted({item.min_volume_24h for item in params}) == [0, 500, 2000, 5000]
    assert sorted({item.entry_hours_max for item in params}) == [24, 48, 72]


def test_run_writes_hashed_csv_json_manifest_and_no_database(
    research_csv, tmp_path, monkeypatch
):
    work = tmp_path / "isolated-work"
    work.mkdir()
    monkeypatch.chdir(work)
    output = tmp_path / "artifacts"

    manifest_path = backtest.run(
        research_csv,
        output,
        train_fraction=0.5,
        review_start="2026-01-01",
        review_end="2026-03-31",
    )

    assert manifest_path == output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["engine_version"] == "papaya-offline-v3"
    assert manifest["safety"] == {
        "database_writes": False,
        "network_calls": False,
        "live_order_submission": False,
    }
    assert manifest["evidence_scope"] == {
        "research_only": True,
        "hypothetical_fills": True,
        "live_order_fill_claims": False,
        "live_fill_requirement": "exact order_fills.status=CONFIRMED",
        "strict_standard_binary_required": True,
    }
    assert manifest["review_window"] == {
        "start_utc": "2026-01-01T00:00:00Z",
        "end_exclusive_utc": "2026-04-01T00:00:00Z",
        "entry_cohort": "first_observed_crossing_timestamp",
    }
    assert manifest["grid"] == {
        "entry": [0.94, 0.95, 0.96],
        "entry_upper": [0.96, 0.97, 0.98],
        "stop": [0.85, 0.9, 0.93],
        "liquidity": [1000.0, 5000.0, 10000.0, 20000.0],
        "volume_24h": [0.0, 500.0, 2000.0, 5000.0],
        "hours_max": [24.0, 48.0, 72.0],
    }
    assert manifest["rows"]["grid"] == 1296
    assert manifest["rows"]["monthly_grid"] > 0
    for name in (
        "trades.csv",
        "trades.json",
        "grid.csv",
        "grid.json",
        "monthly_grid.csv",
        "monthly_grid.json",
    ):
        artifact = output / name
        assert artifact.is_file()
        assert manifest["artifacts"][name]["sha256"] == backtest.sha256_file(artifact)
    assert not list(tmp_path.rglob("*.db"))
    assert not list(work.iterdir())


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("yes_price", "nan", "finite"),
        ("best_ask", "1.1", "between 0 and 1"),
        ("liquidity", "-1", "must be >= 0"),
        ("resolution_payout", "0.25", "must be 0, 0.5, or 1"),
    ],
)
def test_invalid_research_evidence_fails_closed(tmp_path, field, value, match):
    path = tmp_path / "invalid.csv"
    row = _row("bad", "2026-01-01T00:00:00Z", 10, 0.94)
    row[field] = value
    _write_research(path, [row])

    with pytest.raises(ValueError, match=match):
        backtest.read_research_csv(path)


def test_related_conditions_share_one_event_split(tmp_path):
    path = tmp_path / "events.csv"
    _write_research(
        path,
        [
            _row("a-yes", "2026-01-01T00:00:00Z", 10, 0.94, event_id="event-a"),
            _row("a-no", "2026-01-03T00:00:00Z", 10, 0.94, event_id="event-a"),
            _row("b", "2026-02-01T00:00:00Z", 10, 0.94, event_id="event-b"),
            _row("c", "2026-03-01T00:00:00Z", 10, 0.94, event_id="event-c"),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)

    assert set(time_split.event_assignments) == {"event-a", "event-b", "event-c"}
    assert time_split.event_assignments["event-a"] in {"train", "test"}
    # Assignment is keyed once per event, so its two conditions cannot leak.
    assert grouped["a-yes"][0].event_id == grouped["a-no"][0].event_id


def test_parameter_replay_allows_same_event_only_after_prior_exit(tmp_path):
    path = tmp_path / "event-cap.csv"
    _write_research(
        path,
        [
            # Intentionally insert the later condition first; selection must be
            # chronological rather than dependent on CSV/dict insertion order.
            _row(
                "later",
                "2026-01-02T00:00:00Z",
                10,
                0.94,
                event_id="same-event",
            ),
            _row(
                "later",
                "2026-01-02T00:10:00Z",
                9,
                0.951,
                event_id="same-event",
            ),
            _row(
                "earlier",
                "2026-01-01T00:00:00Z",
                10,
                0.94,
                event_id="same-event",
            ),
            _row(
                "earlier",
                "2026-01-01T00:10:00Z",
                9,
                0.951,
                event_id="same-event",
            ),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)

    trades = backtest.replay_parameter(
        grouped,
        _params(liquidity=0),
        time_split,
    )

    assert [trade.condition_id for trade in trades] == ["earlier", "later"]
    assert [trade.event_id for trade in trades] == ["same-event", "same-event"]


def test_ambiguous_official_half_payout_is_accepted(tmp_path):
    path = tmp_path / "half.csv"
    _write_research(
        path,
        [
            _row("half", "2026-01-01T00:00:00Z", 10, 0.94, resolution_payout=0.5),
            _row("half", "2026-01-01T00:10:00Z", 9, 0.951, resolution_payout=0.5),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)
    result = backtest.replay_market(
        grouped["half"], _params(liquidity=0), time_split
    )
    assert result.exit_value == 0.5
    assert result.exit_reason == "resolution"


def test_first_crossing_is_consumed_when_a_later_gate_fails(tmp_path):
    path = tmp_path / "first-crossing.csv"
    _write_research(
        path,
        [
            _row("market", "2026-01-01T00:00:00Z", 30, 0.94),
            _row(
                "market",
                "2026-01-01T00:10:00Z",
                29,
                0.951,
                liquidity=999,
            ),
            _row("market", "2026-01-01T00:20:00Z", 28, 0.94),
            _row("market", "2026-01-01T00:30:00Z", 27, 0.952),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    split = backtest.build_time_split(grouped, 0.5)

    assert backtest.replay_market(grouped["market"], _params(), split) is None


def test_crossing_requires_current_signal_below_upper_and_fresh_lineage(tmp_path):
    path = tmp_path / "lineage.csv"
    _write_research(
        path,
        [
            _row("gap", "2026-01-01T00:00:00Z", 30, 0.94),
            _row("gap", "2026-01-01T00:31:00Z", 29, 0.951),
            _row("jump", "2026-01-02T00:00:00Z", 30, 0.94),
            _row(
                "jump",
                "2026-01-02T00:10:00Z",
                29,
                0.98,
                best_ask=0.96,
                best_bid=0.95,
            ),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    split = backtest.build_time_split(grouped, 0.5)

    assert backtest.replay_market(grouped["gap"], _params(), split) is None
    assert backtest.replay_market(grouped["jump"], _params(), split) is None


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"outcomes": '["No", "Yes"]'}, "outcomes must be exactly"),
        ({"token_ids": '["same", "same"]', "yes_token_id": "same"}, "distinct"),
        ({"yes_token_id": "wrong"}, "Yes outcome token"),
        ({"neg_risk": "true"}, "explicitly be false"),
    ],
)
def test_research_csv_requires_strict_standard_binary_yes_identity(
    tmp_path, override, match
):
    path = tmp_path / "universe.csv"
    _write_research(
        path,
        [_row("market", "2026-01-01T00:00:00Z", 10, 0.94, **override)],
    )

    with pytest.raises(ValueError, match=match):
        backtest.read_research_csv(path)


def test_review_window_is_entry_cohort_and_does_not_relabel_old_crossing(tmp_path):
    path = tmp_path / "review.csv"
    _write_research(
        path,
        [
            _row("old", "2026-01-31T23:40:00Z", 30, 0.94),
            _row("old", "2026-01-31T23:50:00Z", 29, 0.951),
            _row("old", "2026-02-01T00:00:00Z", 28, 0.94),
            _row("old", "2026-02-01T00:10:00Z", 27, 0.952),
            _row("inside", "2026-02-02T00:00:00Z", 30, 0.94),
            _row("inside", "2026-02-02T00:10:00Z", 29, 0.951),
            _row("other", "2026-03-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    split = backtest.build_time_split(grouped, 0.5)
    window = backtest.build_review_window("2026-02-01", "2026-02-28")

    assert backtest.replay_market(grouped["old"], _params(), split, window) is None
    inside = backtest.replay_market(grouped["inside"], _params(), split, window)
    assert inside is not None
    assert inside.entry_timestamp.startswith("2026-02-02")


def test_simultaneous_same_event_candidate_is_skipped(tmp_path):
    path = tmp_path / "overlap.csv"
    _write_research(
        path,
        [
            _row("first", "2026-01-01T00:00:00Z", 30, 0.94, event_id="same"),
            _row("first", "2026-01-01T00:10:00Z", 29, 0.951, event_id="same"),
            _row(
                "first",
                "2026-01-01T00:30:00Z",
                28,
                0.89,
                event_id="same",
                best_bid=0.89,
            ),
            _row("second", "2026-01-01T00:05:00Z", 30, 0.94, event_id="same"),
            _row("second", "2026-01-01T00:20:00Z", 29, 0.951, event_id="same"),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.90),
        ],
    )
    grouped = backtest.read_research_csv(path)
    split = backtest.build_time_split(grouped, 0.5)

    trades = backtest.replay_parameter(grouped, _params(liquidity=0), split)
    assert [trade.condition_id for trade in trades] == ["first"]


def test_review_dates_must_be_supplied_together(research_csv, tmp_path):
    with pytest.raises(ValueError, match="supplied together"):
        backtest.run(
            research_csv,
            tmp_path / "output",
            review_start="2026-01-01",
        )

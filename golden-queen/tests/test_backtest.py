"""Deterministic, offline Queen research backtest contract."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "backtest.py"
SPEC = importlib.util.spec_from_file_location("queen_backtest", SCRIPT)
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
        "liquidity": overrides.pop("liquidity", 250_000),
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
    entry=0.90,
    entry_upper=0.94,
    stop=0.85,
    take_profit=0.98,
    liquidity=10_000,
    volume=2_000,
    hours_max=24,
):
    return backtest.Parameters(
        entry,
        entry_upper,
        stop,
        take_profit,
        liquidity,
        volume,
        hours_max,
    )


@pytest.fixture
def research_csv(tmp_path):
    path = tmp_path / "research.csv"
    rows = [
        # train: first 0.90 crossing enters at executable ask, then stops at bid.
        _row("train-stop", "2026-01-01T00:00:00Z", 20, 0.89),
        _row("train-stop", "2026-01-01T00:10:00Z", 19, 0.901, best_ask=0.902),
        _row("train-stop", "2026-01-01T00:20:00Z", 18, 0.84, best_bid=0.84),
        # test: crosses once and settles; later second crossing must not re-enter.
        _row("test-resolve", "2026-02-01T00:00:00Z", 10, 0.89),
        _row("test-resolve", "2026-02-01T00:10:00Z", 9, 0.906, best_ask=0.907),
        _row("test-resolve", "2026-02-01T00:20:00Z", 8, 0.89, best_bid=0.89),
        _row("test-resolve", "2026-02-01T00:30:00Z", 7, 0.91),
        # third market makes the chronological split deterministic.
        _row("test-loss", "2026-03-01T00:00:00Z", 6, 0.89, resolution_payout=0),
        _row(
            "test-loss",
            "2026-03-01T00:10:00Z",
            5,
            0.902,
            best_ask=0.903,
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

    assert stopped.entry_ask == 0.902
    assert stopped.exit_value == 0.84
    assert stopped.exit_reason == "stop_loss"
    assert stopped.pnl_per_share == pytest.approx(0.84 - 0.902)
    assert stopped.observed_book_pnl_per_share == stopped.pnl_per_share
    assert stopped.midpoint_pnl_per_share == pytest.approx(0.84 - 0.901)
    assert stopped.confirmed_fill_pnl_per_share is None
    assert stopped.execution_evidence == "hypothetical_observed_book"
    assert resolved.entry_timestamp == "2026-02-01T00:10:00Z"
    assert resolved.exit_reason == "resolution"
    assert resolved.exit_value == 1.0
    assert resolved.pnl_per_share == pytest.approx(1.0 - 0.907)
    assert resolved.pnl_after_fee_10bps < resolved.pnl_per_share
    assert resolved.pnl_after_fee_50bps < resolved.pnl_after_fee_10bps
    assert resolved.pnl_after_slippage_10bps < resolved.pnl_per_share


def test_no_entry_without_a_crossing_even_if_first_row_is_above_threshold(tmp_path):
    path = tmp_path / "already-high.csv"
    _write_research(
        path,
        [
            _row("high", "2026-01-01T00:00:00Z", 10, 0.91),
            _row("high", "2026-01-01T00:10:00Z", 9, 0.915),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.89),
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
            _row("sports", "2026-01-01T00:00:00Z", 1.6, 0.89),
            _row("sports", "2026-01-01T00:05:00Z", 1.5, 0.901),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.89),
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
        (0.0, 0.901, 25_000, 10_000),
        (24.01, 0.901, 25_000, 10_000),
        (12, 0.941, 25_000, 10_000),
        (12, 0.901, 9_999, 10_000),
        (12, 0.901, 25_000, 1_999),
    ],
)
def test_entry_requires_window_executable_ask_liquidity_and_volume(
    tmp_path, hours, ask, liquidity, volume
):
    path = tmp_path / "filters.csv"
    _write_research(
        path,
        [
            _row("market", "2026-01-01T00:00:00Z", hours + 1, 0.89),
            _row(
                "market",
                "2026-01-01T00:10:00Z",
                hours,
                0.901,
                best_ask=ask,
                best_bid=min(ask, 0.90),
                liquidity=liquidity,
                volume_24h=volume,
            ),
            _row("other", "2026-02-01T00:00:00Z", 10, 0.89),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)
    params = _params()
    assert backtest.replay_market(grouped["market"], params, time_split) is None


def test_declared_grid_is_complete():
    params = list(backtest.parameter_grid())
    assert len(params) == 2
    assert {item.entry_probability for item in params} == {0.90}
    assert {item.entry_price_max for item in params} == {0.94}
    assert {item.stop_probability for item in params} == {0.85}
    assert {item.take_profit_probability for item in params} == {0.98}
    assert {item.min_liquidity for item in params} == {100_000}
    assert {item.min_volume_24h for item in params} == {5_000}
    assert sorted({item.entry_hours_max for item in params}) == [12, 24]


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
    assert manifest["engine_version"] == "queen-offline-v5"
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
        "order_notional_usdc": 100.0,
        "entry": [0.9],
        "entry_upper": [0.94],
        "stop": [0.85],
        "take_profit": [0.98],
        "liquidity": [100000.0],
        "volume_24h": [5000.0],
        "hours_max": [12.0, 24.0],
    }
    assert manifest["rows"]["grid"] == 2
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
                0.89,
                event_id="same-event",
            ),
            _row(
                "later",
                "2026-01-02T00:10:00Z",
                9,
                0.901,
                event_id="same-event",
            ),
            _row(
                "earlier",
                "2026-01-01T00:00:00Z",
                10,
                0.89,
                event_id="same-event",
            ),
            _row(
                "earlier",
                "2026-01-01T00:10:00Z",
                9,
                0.901,
                event_id="same-event",
            ),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
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
            _row("half", "2026-01-01T00:00:00Z", 10, 0.89, resolution_payout=0.5),
            _row("half", "2026-01-01T00:10:00Z", 9, 0.901, resolution_payout=0.5),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
        ],
    )
    grouped = backtest.read_research_csv(path)
    time_split = backtest.build_time_split(grouped, 0.5)
    result = backtest.replay_market(
        grouped["half"], _params(liquidity=0), time_split
    )
    assert result.exit_value == 0.5
    assert result.exit_reason == "resolution"


def test_take_profit_requires_signal_and_executable_bid(tmp_path):
    path = tmp_path / "target.csv"
    _write_research(
        path,
        [
            _row("target", "2026-01-01T00:00:00Z", 10, 0.89),
            _row("target", "2026-01-01T00:10:00Z", 9, 0.901),
            _row(
                "target",
                "2026-01-01T00:20:00Z",
                8,
                0.985,
                best_ask=0.986,
                best_bid=0.975,
            ),
            _row(
                "target",
                "2026-01-01T00:30:00Z",
                7,
                0.986,
                best_ask=0.989,
                best_bid=0.981,
            ),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
        ],
    )
    grouped = backtest.read_research_csv(path)
    split = backtest.build_time_split(grouped, 0.5)

    result = backtest.replay_market(grouped["target"], _params(), split)

    assert result is not None
    assert result.exit_reason == "take_profit"
    assert result.exit_timestamp == "2026-01-01T00:30:00Z"
    assert result.exit_value == 0.981


def test_first_crossing_is_consumed_when_a_later_gate_fails(tmp_path):
    path = tmp_path / "first-crossing.csv"
    _write_research(
        path,
        [
            _row("market", "2026-01-01T00:00:00Z", 20, 0.89),
            _row(
                "market",
                "2026-01-01T00:10:00Z",
                19,
                0.901,
                liquidity=9_999,
            ),
            _row("market", "2026-01-01T00:20:00Z", 18, 0.89),
            _row("market", "2026-01-01T00:30:00Z", 17, 0.902),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
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
            _row("gap", "2026-01-01T00:00:00Z", 20, 0.89),
            _row("gap", "2026-01-01T00:16:00Z", 19, 0.901),
            _row("jump", "2026-01-02T00:00:00Z", 20, 0.89),
            _row(
                "jump",
                "2026-01-02T00:10:00Z",
                19,
                0.95,
                best_ask=0.93,
                best_bid=0.92,
            ),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
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
            _row("old", "2026-01-31T23:40:00Z", 20, 0.89),
            _row("old", "2026-01-31T23:50:00Z", 19, 0.901),
            _row("old", "2026-02-01T00:00:00Z", 18, 0.89),
            _row("old", "2026-02-01T00:10:00Z", 17, 0.902),
            _row("inside", "2026-02-02T00:00:00Z", 20, 0.89),
            _row("inside", "2026-02-02T00:10:00Z", 19, 0.901),
            _row("other", "2026-03-01T00:00:00Z", 8, 0.89),
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
            _row("first", "2026-01-01T00:00:00Z", 20, 0.89, event_id="same"),
            _row("first", "2026-01-01T00:10:00Z", 19, 0.901, event_id="same"),
            _row(
                "first",
                "2026-01-01T00:30:00Z",
                18,
                0.84,
                event_id="same",
                best_bid=0.84,
            ),
            _row("second", "2026-01-01T00:05:00Z", 20, 0.89, event_id="same"),
            _row("second", "2026-01-01T00:20:00Z", 19, 0.901, event_id="same"),
            _row("other", "2026-02-01T00:00:00Z", 8, 0.89),
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

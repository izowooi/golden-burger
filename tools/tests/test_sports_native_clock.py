"""Pure format tests plus hash-anchored public historical clock records."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sports_native_clock", TOOLS / "sports_native_clock.py")
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


@pytest.mark.parametrize("period,remaining,quarter,seconds,elapsed", [
    ("Q1", "15:00", 1, 900, 0),
    ("Q1", "14:23", 1, 863, 37),
    ("Q2", "6:03", 2, 363, 1437),
    ("Q3", "00:00", 3, 0, 2700),
    ("Q4", "2:00", 4, 120, 3480),
    ("Q4", "00:00", 4, 0, 3600),
    (" q2 ", " 14:54 ", 2, 894, 906),
])
def test_regulation_quarter_remaining_clock(period, remaining, quarter, seconds, elapsed):
    result = clock.decode_nfl_clock(period, remaining)
    assert result["status"] == "DECODED"
    assert (result["quarter"], result["quarter_remaining_seconds"], result["regulation_elapsed_seconds"]) == (quarter, seconds, elapsed)
    assert result["raw_period"] == period and result["raw_clock"] == remaining
    assert result["format_only"] and not result["terminal_proven"]
    assert not result["live_entry_ready"] and not result["season_inferred"]


@pytest.mark.parametrize("remaining", [None, True, 14.5, "", "14", "14.5", "1:60", "15:01", "16:00", "-1:00", "1:2", "01:02.5", "100:00", "1:00:00"])
def test_invalid_remaining_clock_never_becomes_a_minute(remaining):
    result = clock.decode_nfl_clock("Q1", remaining)
    assert result["status"] == "INVALID"
    assert result["regulation_elapsed_seconds"] is None
    assert result["raw_clock"] == remaining


@pytest.mark.parametrize("period,state", [
    ("HT", "HALFTIME_REPORTED"),
    ("End Q1", "QUARTER_END_REPORTED"),
    ("End Q2", "QUARTER_END_REPORTED"),
    ("End Q3", "QUARTER_END_REPORTED"),
    ("End Q4", "QUARTER_END_REPORTED"),
    ("FT", "FULL_TIME_REPORTED"),
    ("VFT", "VFT_UNINTERPRETED"),
    ("NS", "NOT_STARTED_REPORTED"),
])
def test_non_numeric_state_preserved_without_finality(period, state):
    result = clock.decode_nfl_clock(period, None)
    assert result["status"] == "STATE_ONLY" and result["state"] == state
    assert result["quarter_remaining_seconds"] is None
    assert result["regulation_elapsed_seconds"] is None
    assert not result["terminal_proven"]


@pytest.mark.parametrize("period", ["OT", "OT1", "1OT", "Overtime", "Q5", "1H", "Second Quarter"])
def test_overtime_and_other_sport_periods_are_not_converted(period):
    result = clock.decode_nfl_clock(period, "10:00")
    assert result["status"] == "UNSUPPORTED"
    assert result["regulation_elapsed_seconds"] is None


@pytest.mark.parametrize("period", [None, "", True, 1, {"quarter": 1}])
def test_missing_or_non_text_period_is_invalid(period):
    original = deepcopy(period)
    result = clock.decode_nfl_clock(period, "10:00")
    assert result["status"] == "INVALID" and result["raw_period"] == original
    assert period == original


def test_non_json_python_inputs_rejected_without_stringifying():
    for value in (float("nan"), object()):
        with pytest.raises(TypeError, match="finite JSON"):
            clock.decode_nfl_clock("Q1", value)


def test_regression_is_reported_without_changing_source_readings():
    previous = clock.decode_nfl_clock("Q2", "6:03")
    current = clock.decode_nfl_clock("Q2", "7:00")
    before = deepcopy((previous, current))
    result = clock.compare_nfl_clocks(previous, current)
    assert result["comparison"] == "REGRESSED"
    assert result["delta_regulation_elapsed_seconds"] == -57
    assert result["clock_regression"] is True
    assert (previous, current) == before
    assert not result["event_source_and_receipt_order_verified"]


def test_quarter_transition_and_stopped_clock_are_distinct():
    a = clock.decode_nfl_clock("Q1", "00:00")
    b = clock.decode_nfl_clock("Q2", "15:00")
    assert clock.compare_nfl_clocks(a, b)["comparison"] == "UNCHANGED"
    c = clock.decode_nfl_clock("Q2", "14:54")
    assert clock.compare_nfl_clocks(b, c)["delta_regulation_elapsed_seconds"] == 6
    assert clock.compare_nfl_clocks(c, clock.decode_nfl_clock("HT", None))["comparison"] == "UNAVAILABLE"
    altered = dict(c, regulation_elapsed_seconds=0)
    assert clock.compare_nfl_clocks(c, altered)["comparison"] == "UNAVAILABLE"


def test_historical_public_raw_fixture_hashes_and_formats():
    fixture = json.loads((TOOLS / "tests/fixtures/nfl_clock_coconut_20260829.json").read_text())
    assert fixture["source_cadence_minutes"] == 5
    assert fixture["season_phase_recorded"] == "UNKNOWN"
    kinds = set()
    for row in fixture["records"]:
        assert hashlib.sha256(row["raw_json"].encode()).hexdigest() == row["raw_sha256"]
        raw = json.loads(row["raw_json"])
        assert raw["period"] == row["period_raw"]
        assert raw.get("elapsed", raw.get("clock")) == row["clock_raw"]
        result = clock.decode_nfl_clock(row["period_raw"], row["clock_raw"])
        assert result["status"] in {"DECODED", "STATE_ONLY"}
        kinds.add(row["source_kind"])
    assert kinds == {"GAMMA_FALLBACK", "SPORTS_WSS"}

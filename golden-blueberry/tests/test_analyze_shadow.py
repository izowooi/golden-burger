"""Shadow diagnostics must never silently deduplicate economics."""

from __future__ import annotations

import json
import sqlite3

from scripts.analyze_shadow import analyze_shadow


START = "2026-08-31T00:00:00Z"
END = "2026-09-05T00:00:00Z"


def _run_table(connection):
    connection.execute(
        """
        CREATE TABLE run_audits (
            strategy_name TEXT, mode TEXT, status TEXT,
            started_at TEXT, finished_at TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO run_audits VALUES (?, ?, ?, ?, ?)",
        (
            "golden-blueberry",
            "sim",
            "FAILED",
            "2026-09-01T00:00:00+00:00",
            "2026-09-01T00:02:00+00:00",
        ),
    )


def test_historical_duplicate_or_missing_cohort_suppresses_closed_summary(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        _run_table(connection)
        connection.execute(
            """
            CREATE TABLE shadow_signals (
                id INTEGER PRIMARY KEY, condition_id TEXT, min_surge REAL,
                horizon_hours REAL, status TEXT, classification TEXT,
                hypothetical_gross_pnl REAL, first_observed_at TEXT
            )
            """
        )
        for identifier in (865, 866):
            connection.execute(
                "INSERT INTO shadow_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    "condition-duplicate",
                    0.02,
                    72.0,
                    "CLOSED",
                    "ENTERED_PROFIT",
                    1.0,
                    "2026-09-01 00:01:00.000000",
                ),
            )

    output = analyze_shadow(database, tmp_path / "report", START, END)
    payload = json.loads(output.read_text())
    assert payload["status"] == "NOT_EVALUABLE_EVIDENCE_CONTRACT"
    assert "shadow_cohort_columns_missing" in payload["issues"]
    assert "duplicate_shadow_treatment_rows" in payload["issues"]
    assert payload["rows"]["exact_duplicate_rows"] == 1
    assert payload["closed_summary"] is None


def test_complete_unique_cohort_grid_is_summarized_by_full_cell(tmp_path):
    database = tmp_path / "prospective.db"
    with sqlite3.connect(database) as connection:
        _run_table(connection)
        connection.execute(
            """
            CREATE TABLE shadow_signals (
                id INTEGER PRIMARY KEY, condition_id TEXT, min_surge REAL,
                horizon_hours REAL, status TEXT, classification TEXT,
                hypothetical_gross_pnl REAL, first_observed_at TEXT,
                config_hash TEXT, strategy_source_digest TEXT,
                mode TEXT, job_name TEXT
            )
            """
        )
        for identifier, (surge, horizon) in enumerate(
            ((0.02, 72), (0.02, 168), (0.05, 72), (0.05, 168)), start=1
        ):
            connection.execute(
                "INSERT INTO shadow_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    "condition-one",
                    surge,
                    horizon,
                    "CLOSED",
                    "ENTERED_PROFIT",
                    0.25,
                    "2026-09-01 00:01:00.000000",
                    "a" * 64,
                    "b" * 64,
                    "sim",
                    "blueberry-shadow-research",
                ),
            )

    output = analyze_shadow(database, tmp_path / "report", START, END)
    payload = json.loads(output.read_text())
    assert payload["issues"] == []
    assert payload["rows"]["raw"] == 4
    assert payload["rows"]["unique_conditions"] == 1
    assert set(payload["closed_summary"]) == {
        "0.02x72h",
        "0.02x168h",
        "0.05x72h",
        "0.05x168h",
    }

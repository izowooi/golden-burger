from __future__ import annotations

from datetime import datetime, timezone

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository

from scripts.analyze_experiment import _primary_gate, _summarize_outcomes, analyze_db


def test_analyzer_is_read_only_and_reports_empty_health(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    path = tmp_path / "trades_sim.db"
    repository = ResearchRepository(path)
    repository.initialize(config)
    result = analyze_db(
        "DO",
        path,
        datetime(2026, 8, 13, tzinfo=timezone.utc),
        datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert result["quick_check"] == "ok"
    assert result["contract"]["shard_index"] == 0
    assert result["health_pass"] is False


def test_primary_gate_uses_one_fleet_cluster_bootstrap_and_day_union():
    rows = []
    for index in range(60):
        day = index % 30 + 1
        common = {
            "namespace": f"shard-{index % 3}",
            "arm": "MI",
            "condition_id": f"condition-{index}",
            "event_id": f"event-{index % 30}",
            "cluster_key": f"shard-{index % 3}:event-{index % 30}",
            "evaluated_at": f"2026-08-{day:02d}T01:00:00Z",
            "matched_pair_id": f"pair-{index}",
            "pair_key": f"shard-{index % 3}:pair-{index}",
            "case_id": f"case-{index}",
        }
        rows.extend(
            [
                {
                    **common,
                    "case_kind": "SIGNAL",
                    "executable_return_bps": 100.0,
                    "base_stressed_return_bps": 89.6,
                    "severe_stressed_return_bps": 27.5,
                },
                {
                    **common,
                    "arm": "DO",
                    "evaluated_at": f"2026-08-{day:02d}T00:50:00Z",
                    "case_kind": "SIGNAL",
                    "executable_return_bps": 10.0,
                    "base_stressed_return_bps": -0.4,
                    "severe_stressed_return_bps": -62.5,
                },
                {
                    **common,
                    "case_kind": "CONTROL",
                    "executable_return_bps": 0.0,
                    "base_stressed_return_bps": -10.4,
                    "severe_stressed_return_bps": -72.5,
                },
                {
                    **common,
                    "case_kind": "OPPOSITE",
                    "executable_return_bps": -50.0,
                    "base_stressed_return_bps": -60.4,
                    "severe_stressed_return_bps": -122.5,
                },
            ]
        )

    fleet = _summarize_outcomes(rows)
    gate = _primary_gate([{"health_pass": True}] * 3, fleet, 30)

    assert fleet["MI"]["distinct_utc_days"] == 30
    assert fleet["MI"]["event_clusters"] == 30
    assert gate["totals"]["distinct_utc_days"] == 30
    assert gate["checks"]["mi_fleet_severe_lower_positive"] is True
    assert gate["checks"]["mi_minus_do_severe_lower_positive"] is True
    assert gate["pass"] is True

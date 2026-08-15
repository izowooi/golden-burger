from __future__ import annotations

from polybot.analyzer import analyze_database, parse_utc, write_analysis
from tests.integration_support import build_three_cycle_evidence


def test_analyzer_is_immutable_reports_grid_and_conservative_same_poll(
    config, tmp_path
):
    repository, _ = build_three_cycle_evidence(config)
    result = analyze_database(
        repository.db_path,
        start=parse_utc("2026-08-15T02:00:00Z"),
        end=parse_utc("2026-08-15T02:30:00Z"),
    )
    assert result["database"]["opened_read_only_immutable"] is True
    assert result["database"]["quick_check"] == "ok"
    assert len(result["policy_grid"]) == 45
    primary = result["frozen_primary_policy"]["summary"]
    assert primary["policy_role"] == "PRIMARY"
    assert primary["entry_threshold"] == 0.95
    assert primary["stop_threshold"] == 0.85
    assert primary["target_threshold"] is None
    assert primary["exit_reason_counts"] == {"STOP": 1}
    assert primary["same_poll_stop_resolution_ambiguous_count"] == 1
    gross = primary["gross_counterfactual_bps"]["mean"]
    assert gross < 0
    assert primary["round_trip_cost_stress_bps"]["10.4"] == gross - 10.4
    assert primary["round_trip_cost_stress_bps"]["72.5"] == gross - 72.5
    assert result["interpretation"]["target_0_99_is_resolution"] is False
    assert result["interpretation"]["same_poll_stop_resolution_ordering"].endswith(
        "STOP_FIRST"
    )
    assert result["interpretation"]["verdict"] == "HEALTH_ONLY"


def test_analyzer_reports_path_resolution_censoring_and_strata(config):
    repository, _ = build_three_cycle_evidence(config)
    result = analyze_database(
        repository.db_path,
        start=parse_utc("2026-08-15T02:00:00Z"),
        end=parse_utc("2026-08-15T02:30:00Z"),
    )
    coverage = result["crossing_episode_resolution_coverage"]
    assert coverage["crossing_clob_coverage"] == 1.0
    assert coverage["episode_path_coverage"] == 1.0
    assert coverage["resolution_coverage"] == 1.0
    assert coverage["resolved_independent_event_clusters"] == 1
    assert result["crossing_censoring"]["left_censored"] == 2
    strata = result["stratified_counts"]
    assert strata["sports"] == {"NON_SPORTS": 2}
    assert strata["outcome_type"] == {"BINARY": 2}
    assert strata["neg_risk"] == {"STANDARD": 2}
    assert strata["crossing_source_metadata"]["fee_rate_bps_present"] == 2


def test_analyzer_writes_json_to_explicit_absolute_output(config, tmp_path):
    repository, _ = build_three_cycle_evidence(config)
    output = tmp_path / "analysis.json"
    result = write_analysis(
        repository.db_path,
        start=parse_utc("2026-08-15T02:00:00Z"),
        end=parse_utc("2026-08-15T02:30:00Z"),
        output=output,
    )
    assert output.is_file()
    assert '"schema": "golden-strawberry-analysis-v1"' in output.read_text(
        encoding="utf-8"
    )
    assert result["interpretation"]["parameter_winner_selection_allowed"] is False

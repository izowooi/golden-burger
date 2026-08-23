from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polybot.db.followup_repository as followup_repository_module
import polybot.followup_collector as followup_collector_module
import polybot.followup_run_audit as followup_run_audit_module
import tests.followup_support as followup_support_module
from polybot.followup_analyzer import analyze_followup, write_followup_analysis
from polybot.utils.retry import iso_utc as real_iso_utc
from polybot.v1_source import V1SourceReader
from tests.followup_support import build_followup_evidence, build_v1_handoff


def test_combined_analyzer_is_health_only_and_skips_large_v1_quick_check(
    config, followup_config, tmp_path
):
    build_v1_handoff(config)
    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()
    evidence = build_followup_evidence(followup_config, snapshot)
    now = datetime.now(timezone.utc)
    result = analyze_followup(
        config.db_path,
        followup_config.db_path,
        start=now - timedelta(minutes=15),
        end=now + timedelta(minutes=15),
    )
    assert result["schema"] == "golden-strawberry-followup-health-v2a"
    assert result["v1"]["database"]["quick_check"] == "not_run_large_v1"
    assert result["v1"]["anchor"]["semantic_match"] is True
    assert result["v2a"]["database"]["quick_check"] == "ok"
    assert result["v2a"]["seed_integrity"]["healthy"] is True
    assert result["v2a"]["compact_book_integrity"]["healthy"] is True
    assert result["v2a"]["request_lineage"][
        "forbidden_sampling_or_candidate_metadata_requests"
    ] == 0
    assert result["interpretation"]["profitability_claim_allowed"] is False
    assert result["interpretation"]["parameter_selection_allowed"] is False

    output = (tmp_path / "followup-health.json").resolve()
    written = write_followup_analysis(
        config.db_path,
        followup_config.db_path,
        start=now - timedelta(minutes=15),
        end=now + timedelta(minutes=15),
        output=output,
    )
    assert output.is_file()
    assert written["schema"] == result["schema"]


def test_full_seed_is_maintenance_and_rollout_starts_at_first_natural_pinned_slot(
    config, followup_config, monkeypatch
):
    build_v1_handoff(config)
    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()
    clock = {"value": "2026-08-24T12:02:00Z"}

    def frozen_iso(value=None):
        return real_iso_utc(value) if value is not None else clock["value"]

    for module in (
        followup_repository_module,
        followup_collector_module,
        followup_run_audit_module,
        followup_support_module,
    ):
        monkeypatch.setattr(module, "iso_utc", frozen_iso)

    cycle_clocks = ("2026-08-24T12:02:00Z", "2026-08-24T12:07:05Z")
    evidence = build_followup_evidence(
        followup_config,
        snapshot,
        cycles=2,
        validation_modes=("FULL_SEED", "PINNED_FAST"),
        anchor_elapsed_seconds=(900.0, 0.01),
        before_cycle=lambda index: clock.update(value=cycle_clocks[index]),
    )
    result = analyze_followup(
        config.db_path,
        evidence.repository.db_path,
        start=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
    )

    cadence = result["v2a"]["cadence"]
    runtime = result["v2a"]["runtime_sla"]
    assert cadence["rollout_health_start"] == "2026-08-24T12:07:00Z"
    assert cadence["full_seed_runs_excluded"] == 1
    assert cadence["off_slot_runs"] == 0
    assert runtime["recurring_pinned_fast"]["count"] == 1
    assert runtime["recurring_pinned_fast"]["max"] < 480
    assert runtime["full_seed_maintenance"]["count"] == 1
    assert runtime["full_seed_maintenance"]["max"] >= 900
    assert runtime["full_seed_counts_as_recurring_sla_violation"] is False

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polybot.followup_analyzer import analyze_followup, write_followup_analysis
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
    assert result["schema"] == "golden-strawberry-followup-health-v2"
    assert result["v1"]["database"]["quick_check"] == "not_run_large_v1"
    assert result["v1"]["anchor"]["semantic_match"] is True
    assert result["v2"]["database"]["quick_check"] == "ok"
    assert result["v2"]["seed_integrity"]["healthy"] is True
    assert result["v2"]["compact_book_integrity"]["healthy"] is True
    assert result["v2"]["request_lineage"][
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

"""Keep the operator guide and preregistration aligned with safety invariants."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_names_the_research_profile_rotation_and_live_block():
    source = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for token in (
        "research-full-v1",
        "trades_sim.db",
        "trades_sim_20260806.db",
        "H/15",
        "--simulate",
        "--live",
        "compact-v1",
        "ExecutionLedger",
        "volume24hr",
        "possible_gap",
        "/trades",
        "10,000",
        "24h",
        "polling",
        "tick 전수",
    ):
        assert token in source

    assert "120일 whole-shard retention" in source
    assert "free space `<150 GiB`" in source
    assert "filesystem 사용률 `>=80%`" in source
    assert "Use custom workspace" in source
    assert "/Volumes/t7/jenkins/workspace/${JOB_NAME}" in source
    assert "Credentials Binding" in source
    assert "summary console log를 120일" in source
    assert "Jenkins mount identity 검사를 대신하지 않는다" in source
    freestyle = source[source.index("### Jenkins Freestyle job") :]
    config_at = freestyle.index("polybot config --simulate")
    first_health_at = freestyle.index("polybot health --simulate", config_at)
    run_at = freestyle.index("polybot run --simulate", first_health_at)
    status_at = freestyle.index("polybot status --simulate", run_at)
    second_health_at = freestyle.index("polybot health --simulate", status_at)
    assert config_at < first_health_at < run_at < status_at < second_health_at


def test_preregistration_is_dated_and_has_health_falsification_gates():
    path = PROJECT_ROOT / "research" / "2026-08-06-preregistration.md"
    source = path.read_text(encoding="utf-8")

    assert source.startswith("# 2026-08-06")
    assert "672" in source
    assert "p95 `<8분`" in source
    assert "NOT_EVALUABLE_FAIL_CLOSED" in source
    assert "P&L" in source
    assert "resolution / redeemable" in source
    assert "CLOB은 sample" in source


def test_trade_tape_docs_fix_taker_only_envelope_and_analysis_denominator():
    paths = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "STRATEGY.md",
        PROJECT_ROOT / "research" / "2026-08-06-preregistration.md",
    )

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "/trades?takerOnly=true" in source
        assert "taker-side" in source
        assert "economic-event" in source
        assert "proxyWallet" in source
        assert "maker counterparty" in source
        assert "maker-side" in source
        assert "participant" in source
        assert "clock" in source
        assert "regression" in source
        assert "watermark" in source
        assert "EMPTY" in source
        assert "[start,end]" in source
        assert "offset=0" in source


def test_sampling_docs_keep_wall_clock_rotation_across_daily_shards():
    for path in (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "STRATEGY.md",
        PROJECT_ROOT / "research" / "2026-08-06-preregistration.md",
    ):
        source = path.read_text(encoding="utf-8")
        assert "sampler_slot" in source
        assert "bucket_visit_index" in source
        assert "rotation_offset" in source
        assert "wall-clock" in source
        assert "daily shard" in source

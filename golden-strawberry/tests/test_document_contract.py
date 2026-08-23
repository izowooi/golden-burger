from __future__ import annotations


def test_capacity_probe_is_dated_estimate_not_source_contract(project_root):
    documents = "\n".join(
        (project_root / name).read_text(encoding="utf-8")
        for name in (
            "README.md",
            "STRATEGY.md",
            "OPERATIONS.md",
            "research/frozen-2026-08-15-clob/PREREGISTRATION.md",
        )
    )
    assert "12,555" in documents
    assert "25,110" in documents
    assert "13 pages" in documents
    assert "31.66MB" in documents
    assert "6.46MB" in documents
    assert "not" in documents.lower() and "contract" in documents.lower()
    source_and_config = "\n".join(
        [
            (project_root / "config.yaml").read_text(encoding="utf-8"),
            *[
                path.read_text(encoding="utf-8")
                for path in (project_root / "src").rglob("*.py")
            ],
        ]
    )
    assert "12,555" not in source_and_config
    assert "12555" not in source_and_config


def test_primary_and_sensitivity_are_unambiguous_in_docs(project_root):
    prereg = (
        project_root / "research/frozen-2026-08-15-clob/PREREGISTRATION.md"
    ).read_text(encoding="utf-8")
    assert "entry `0.95`" in prereg
    assert "stop `0.85`" in prereg
    assert "otherwise hold to proven terminal resolution" in prereg
    assert "Sensitivity dimensions" in prereg
    assert "sampled target, not" in prereg and "resolution" in prereg
    assert "stop-before-resolution" in prereg


def test_external_preflight_reuses_existing_pins(project_root):
    text = (project_root / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "/Volumes/t7/jenkins/polybot-shadow-one" in text
    assert "/Volumes/t7/.golden-raspberry-volume" in text
    assert "/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid" in text
    assert "Do not" in text


def test_retro_invokes_evidence_contract_and_primary_research_analyzer(project_root):
    retro = (project_root.parent / "docs/retro/golden-strawberry.md").read_text(
        encoding="utf-8"
    )
    assert "EVIDENCE_CONTRACT.md" in retro
    assert "REVIEW_START" in retro and "REVIEW_END" in retro
    assert "immutable" in retro
    assert "Primary" in retro
    assert "secondary" in retro


def test_active_followup_docs_point_only_to_v2a_identity(project_root):
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    operations = (project_root / "OPERATIONS.md").read_text(encoding="utf-8")
    agents = (project_root / "AGENTS.md").read_text(encoding="utf-8")
    active = "research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md"
    assert f"]({active})" in readme
    assert active in operations
    assert active in agents
    for text in (readme, operations, agents):
        assert "strawberry-shadow-one-followup-v2a" in text
        assert "last-mile-clob-followup-v2a" in text
    assert "first successful natural `PINNED_FAST`" in readme
    assert "FULL_SEED" in readme and "recurring 480-second" in readme

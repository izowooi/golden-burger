from __future__ import annotations


def test_capacity_probe_is_dated_estimate_not_source_contract(project_root):
    documents = "\n".join(
        (project_root / name).read_text(encoding="utf-8")
        for name in ("README.md", "STRATEGY.md", "OPERATIONS.md")
    )
    assert "32,132" in documents
    assert "322 pages" in documents
    assert "121.39" in documents
    assert "16.7 MiB" in documents
    assert "16.5 GiB" in documents
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
    assert "32,132" not in source_and_config
    assert "32132" not in source_and_config


def test_primary_and_sensitivity_are_unambiguous_in_docs(project_root):
    prereg = (project_root / "research/frozen-2026-08-15/PREREGISTRATION.md").read_text(
        encoding="utf-8"
    )
    assert "entry threshold `0.95`" in prereg
    assert "stop threshold `0.85`" in prereg
    assert "no price target" in prereg
    assert "sensitivity-grid" in prereg
    assert "not terminal resolution" in prereg
    assert "stop-before-resolution" in prereg


def test_external_preflight_reuses_existing_pins(project_root):
    for name in ("README.md", "OPERATIONS.md"):
        text = (project_root / name).read_text(encoding="utf-8")
        assert "/Volumes/t7/jenkins/polybot-shadow-one" in text
        assert "/Volumes/t7/.golden-raspberry-volume" in text
        assert "/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid" in text
        assert "do not" in text.lower()


def test_retro_invokes_evidence_contract_and_primary_research_analyzer(project_root):
    retro = (project_root.parent / "docs/retro/golden-strawberry.md").read_text(
        encoding="utf-8"
    )
    assert "EVIDENCE_CONTRACT.md" in retro
    assert "REVIEW_START" in retro and "REVIEW_END" in retro
    assert "immutable" in retro
    assert "Primary" in retro
    assert "secondary" in retro

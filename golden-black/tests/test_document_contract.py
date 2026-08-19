from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documents_preserve_frozen_contract() -> None:
    combined = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "STRATEGY.md", "OPERATIONS.md", "AGENTS.md"))
    for token in ("sports-resolution-paired-v1", "0.92", "0.94", "archive_only", "--live", "H/5 * * * *", "daily-rsync"):
        assert token in combined


def test_runtime_data_is_ignored() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "/golden-black/data/" in gitignore

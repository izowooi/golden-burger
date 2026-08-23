from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documents_preserve_frozen_contract() -> None:
    combined = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "STRATEGY.md", "OPERATIONS.md", "AGENTS.md"))
    for token in (
        "soccer-inplay-major-league-match-winner-v1", "0.95", "0.97", "0.99", "0.80", "0.70",
        "HOLD_TO_RESOLUTION", "FAST_1M", "CONTROL_5M", "moneyline",
        "child_moneyline", "archive_only", "--live", "* * * * *", "daily-rsync",
        "epl", "bun", "fl1", "lal", "mls", "e-sports",
    ):
        assert token in combined


def test_runtime_data_is_ignored() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "/golden-watermelon/data/" in gitignore

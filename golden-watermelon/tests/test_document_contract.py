from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documents_preserve_frozen_contract() -> None:
    combined = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "STRATEGY.md", "OPERATIONS.md", "AGENTS.md"))
    for token in (
        "watermelon-soccer-mlb-nhl-inplay-match-winner-v5",
        "watermelon-white-1m-v4a", "watermelon-grey-5m-v4a",
        "100350", "100381", "899", "related_tags=false",
        "World Series", "Stanley Cup Final", "MiLB", "AHL", "NCAA",
        "GWM4", "401", "migration", "ALTER TABLE",
        "0.95", "0.97", "0.99", "0.80", "0.70",
        "HOLD_TO_RESOLUTION", "FAST_1M", "CONTROL_5M", "moneyline",
        "archive_only", "--live", "* * * * *", "daily-rsync",
        "epl", "bun", "fl1", "lal", "mls", "sea", "Serie A", "e-sports",
        "ucl", "uel", "UEFA Champions League", "UEFA Europa League",
        "75/80/85", "$1000", "displayed", "actual fill", "immutable",
    ):
        assert token in combined


def test_runtime_data_is_ignored() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "/golden-watermelon/data/" in gitignore

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documents_preserve_frozen_contract() -> None:
    combined = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "STRATEGY.md", "OPERATIONS.md", "AGENTS.md"))
    for token in (
        "soccer-inplay-elite-competition-match-winner-v3",
        "soccer-inplay-major-league-match-winner-v2",
        "soccer-inplay-major-league-match-winner-v1",
        "watermelon-white-1m-v3c", "watermelon-grey-5m-v3c",
        "watermelon-white-1m-v3b", "watermelon-grey-5m-v3b",
        "tag_id=100350", "related_tags=false", "primaryTagId",
        "event_observations", "DRIFT", "application", "user version",
        "macro", "null", "migration", "ALTER TABLE",
        "0.95", "0.97", "0.99", "0.80", "0.70",
        "HOLD_TO_RESOLUTION", "FAST_1M", "CONTROL_5M", "moneyline",
        "child_moneyline", "archive_only", "--live", "* * * * *", "daily-rsync",
        "epl", "bun", "fl1", "lal", "mls", "sea", "Serie A", "e-sports",
        "ucl", "uel", "UEFA Champions League", "UEFA Europa League",
        "SPORTS_CLOCK_UPDATE", "75/80/85", "$500",
        "stoppage time", "extra time", "penalty",
    ):
        assert token in combined


def test_runtime_data_is_ignored() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "/golden-watermelon/data/" in gitignore

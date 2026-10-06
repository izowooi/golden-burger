import pytest


@pytest.fixture
def unfrozen_ladder(monkeypatch):
    """Lift the 5 USDC stake freeze (owner 2026-10-06 `stake:freeze-5`) so the ladder's promotion mechanics, kept for a
    later scale-up study, stay tested."""
    from polylab.risk import ladder
    monkeypatch.setattr(ladder, "STAKE_FREEZE_USDC", None)

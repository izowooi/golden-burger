from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def clean_polybot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("POLYBOT_") or key.startswith("POLYMARKET_") or key.startswith("CLOB_"):
            monkeypatch.delenv(key, raising=False)

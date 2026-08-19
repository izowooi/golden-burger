from __future__ import annotations

import sqlite3

from polybot.analyzer import analyze_database
from polybot.db.repository import ResearchRepository


def test_analyzer_uses_fee_and_resolution(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "trades_sim.db", busy_timeout_ms=1000, data_contract="sports-resolution-paired-v1")
    with sqlite3.connect(repository.path) as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO hypothetical_episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "e","d","r","c","event","Event","Question","token",0,"Yes",0.94,
            "2026-08-20T01:00:00Z","2026-08-20T02:00:00Z",None,"PRE_GAME",10000,5000,0.05,0.94,0.94,5/0.94,5,
        ))
        c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?,?,?,?,?)", (
            "z","r2","c","2026-08-20T03:00:00Z",0,"request","a"*64,"{}",
        ))
    result = analyze_database(repository.path)
    arm = result["arms"]["0.94"]["all"]
    assert arm["resolved"] == 1
    assert arm["wins"] == 1
    assert arm["event_equal_fee_net_roi_pct"] > 0
    assert arm["event_equal_fee_plus_1c_roi_bootstrap_95ci_pct"][0] > 0

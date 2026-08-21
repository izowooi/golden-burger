from __future__ import annotations

import sqlite3

import pytest

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
        shares = 5 / 0.94
        gross = shares * 0.78
        exit_fee = shares * 0.05 * 0.78 * 0.22
        c.execute("INSERT INTO counterfactual_exit_policies VALUES(?,?,?,?,?,?)", (
            "hold","e","r","HOLD_TO_RESOLUTION",None,"2026-08-20T01:00:00Z",
        ))
        c.execute("INSERT INTO counterfactual_exit_policies VALUES(?,?,?,?,?,?)", (
            "stop","e","r","STOP_0.80",0.80,"2026-08-20T01:00:00Z",
        ))
        c.execute("INSERT INTO stop_execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "attempt","stop","e","r-stop",None,"2026-08-20T01:05:00Z",0.80,
            0.93,0.79,shares,shares,0,0.78,gross,0.05,exit_fee,gross-exit_fee,2,
            "FULL_EXIT",0.02,0.14,
        ))
        c.execute("INSERT INTO counterfactual_stop_exits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "exit","stop","e","r-stop","attempt","2026-08-20T01:05:00Z",
            "2026-08-20T01:05:00Z",0.80,0.79,0.78,shares,shares,gross,exit_fee,
            gross-exit_fee,1,0.02,
        ))
    result = analyze_database(repository.path)
    assert result["database_checks"] == []
    arm = result["arms"]["0.94"]["all"]
    assert arm["resolved"] == 1
    assert arm["wins"] == 1
    assert arm["event_equal_fee_net_roi_pct"] > 0
    assert arm["event_equal_fee_plus_1c_roi_bootstrap_95ci_pct"][0] > 0
    policies = result["stop_policy_comparison"]["0.94"]
    assert policies["HOLD_TO_RESOLUTION"]["all"]["event_equal_fee_net_roi_pct"] > 0
    assert policies["STOP_0.80"]["all"]["event_equal_fee_net_roi_pct"] < 0
    assert policies["STOP_0.80"]["all"]["gap_below_stop_p50"] == pytest.approx(0.02)

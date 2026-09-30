"""stream daemon handlers (no sockets): raw gzip files, ws_last bars, game_states on change, plist render."""

from __future__ import annotations

import gzip
import json
import plistlib
from pathlib import Path

import pytest

from polylab import db as dbmod
from polylab.collector import common as C
from polylab.collector import stream
from polylab.settings import Paths

FX = Path(__file__).parent / "fixtures" / "polymarket_api"


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    p = Paths(tmp_path).ensure()
    conn = dbmod.core(p)
    now = C.now()
    C.upsert_game(conn, {"game_key": "g1", "sport": "soccer", "title": "Seychelles vs. Sri Lanka", "start_time": now - 3600,
                         "polymarket_game_id": "90123081", "status": "live", "source": "discover"})
    C.upsert_market(conn, {"condition_id": "0xc", "game_key": "g1", "market_type": "moneyline"})
    C.upsert_tokens(conn, "0xc", [{"token_id": "tokA", "outcome_index": 0, "outcome_label": "Yes", "side": "home"},
                                  {"token_id": "tokB", "outcome_index": 1, "outcome_label": "No", "side": "no"}])
    conn.commit()
    conn.close()
    return p


def test_raw_writer_multi_member_gzip(tmp_path):
    w = stream.RawWriter(tmp_path, "market")
    w.add(1, '{"a":1}')
    w.flush(1790780000)
    w.add(2, '[{"b":2}]')
    w.flush(1790780010)
    f = w.path_for(1790780000)
    assert f.name == "market-14.jsonl.gz" and f.parent.name == "2026-09-30"
    lines = [json.loads(x) for x in gzip.decompress(f.read_bytes()).splitlines()]
    assert lines == [{"received_at": 1, "raw": {"a": 1}}, {"received_at": 2, "raw": [{"b": 2}]}]


def test_market_messages_to_bars_and_raw(paths):
    col = stream.Collector(paths)
    assert col.desired_assets() == {"tokA", "tokB"}
    t = C.now() * 1000
    col.on_market(json.dumps({"event_type": "last_trade_price", "asset_id": "tokA", "price": "0.52", "timestamp": str(t)}), t)
    col.on_market(json.dumps({"event_type": "last_trade_price", "asset_id": "tokA", "price": "0.55", "timestamp": str(t + 1)}), t)
    col.on_market(json.dumps({"event_type": "last_trade_price", "asset_id": "other", "price": "0.1", "timestamp": str(t)}), t)
    col.on_market(json.dumps({"event_type": "new_market", "id": "x"}), t)
    col.on_market(json.dumps({"event_type": "best_bid_ask", "asset_id": "tokA", "timestamp": str(t - 60000)}), t)
    col.on_market(json.dumps({"event_type": "best_bid_ask", "asset_id": "tokA", "timestamp": str(t - 120000)}), t)
    col.on_market("PONG", t)
    col.flush()
    conn = dbmod.core(paths)
    assert [tuple(r) for r in conn.execute("SELECT token_id, source, price FROM price_bars")] == [("tokA", "ws_last", 0.55)]
    assert [r[0] for r in conn.execute("SELECT kind FROM quality_events")] == ["ws_ts_regression"]
    raw = list((paths.raw_dir).rglob("market-*.jsonl.gz"))
    lines = gzip.decompress(raw[0].read_bytes()).splitlines()
    assert len(lines) == 5                                  # new_market and PONG not stored


def test_sports_frames_state_changes_only(paths):
    col = stream.Collector(paths)
    base = {"gameId": 90123081, "leagueAbbreviation": "fif", "homeTeam": "Seychelles", "awayTeam": "Sri Lanka",
            "status": "InProgress", "score": "0-1", "period": "2H", "live": True, "ended": False, "elapsed": "81"}
    t = C.now() * 1000
    col.on_sports(json.dumps(base), t)
    col.on_sports(json.dumps(base), t + 1000)                                   # duplicate -> ignored
    col.on_sports(json.dumps(dict(base, score="0-2", elapsed="84")), t + 2000)
    col.on_sports(json.dumps(dict(base, gameId=1, leagueAbbreviation="cs2")), t)  # untracked game
    col.on_sports(json.dumps(dict(base, score="0-2", elapsed="90+3", period="FT", live=False, ended=True,
                                  status="Final")), t + 3000)
    col.flush()
    conn = dbmod.core(paths)
    rows = conn.execute("SELECT game_minute, home_score, away_score, status FROM game_states ORDER BY received_at, ts").fetchall()
    assert [tuple(r) for r in rows][:2] == [(81.0, 0, 1, "live"), (84.0, 0, 2, "live")]
    assert len(rows) == 3 and rows[2]["status"] == "ended"
    g = conn.execute("SELECT status, home_score, away_score, ended_at FROM games").fetchone()
    assert (g["status"], g["home_score"], g["away_score"]) == ("ended", 0, 2) and g["ended_at"]
    assert len(list(paths.raw_dir.rglob("sports-*.jsonl.gz"))) == 1


def test_heartbeat_written(paths):
    col = stream.Collector(paths)
    col.heartbeat()
    age, hb = stream.heartbeat_info(paths)
    assert age is not None and age < 5 and hb["assets"] == 0 and "market_connected" in hb


def test_render_plist():
    data = plistlib.loads(stream.render_plist(env={"POLYLAB_ROOT": "/x"}))
    assert data["Label"] == "uk.zowoo.polylab.stream"
    assert data["ProgramArguments"] == ["/Users/jongwoopark/.local/bin/uv", "run", "--frozen", "--project",
                                        "/Volumes/t7/polylab/repo", "polylab", "stream", "--run"]
    assert data["KeepAlive"] is True and data["WorkingDirectory"] == "/Volumes/t7/polylab/repo"
    assert data["StandardErrorPath"].endswith("Library/Logs/polylab/stream.err.log")   # launchd can't open /Volumes
    assert data["EnvironmentVariables"]["POLYLAB_ROOT"] == "/x"


def test_single_instance_lock(paths):
    first = stream.acquire_lock(paths, wait=False)
    assert first is not None
    assert stream.acquire_lock(paths, wait=False) is None
    first.close()
    again = stream.acquire_lock(paths, wait=False)
    assert again is not None
    again.close()

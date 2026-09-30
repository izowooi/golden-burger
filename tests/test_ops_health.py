import json

from test_reports_build import NOW, make_world

from polylab.ops import health


def test_checks_flag_critical_conditions():
    h = {"now": NOW, "disk_free_gb": 30.0, "kill_switch": True,
         "collector": {"live_games": 3, "last_poll_at": NOW - 900, "stream_heartbeat_at": NOW - 60},
         "jobs": [{"name": "polylab-tick", "status": "failing", "fail_streak": 3},
                  {"name": "polylab-backfill", "status": "failing", "fail_streak": 1, "detail": "FAILURE"}],
         "loss_stops": [{"variant_id": "v", "pnl_today": -21.0, "stop": 20.0}],
         "retro_last": {"ok": False, "kind": "daily", "at": "x", "error": "codex: timeout"}}
    keys = {p["key"]: p["level"] for p in health.checks(h)}
    assert keys == {"disk": "warn", "poll_stale": "critical", "job:polylab-tick": "critical",
                    "job:polylab-backfill": "warn", "kill_switch": "critical", "loss_stop:v": "warn",
                    "retro_failed": "warn"}


def test_no_poll_alert_without_live_games():
    h = {"now": NOW, "disk_free_gb": 500.0, "collector": {"live_games": 0, "last_poll_at": None}, "jobs": []}
    assert health.checks(h) == []


def test_alert_dedupe_and_recovery():
    p = [{"key": "disk", "level": "warn", "message": "disk low"}]
    msgs, st = health.diff_alerts({}, p, [], NOW)
    assert msgs == ["[경고] disk low"]
    msgs, st = health.diff_alerts(st, p, [], NOW + 300)
    assert msgs == []  # unchanged -> silent
    msgs, st = health.diff_alerts(st, [], [], NOW + 900)
    assert len(msgs) == 1 and msgs[0].startswith("[복구]")
    ev = [{"variant_id": "v", "id": 7, "from_usdc": 5, "to_usdc": 10, "from_mode": "live", "to_mode": "live",
           "reason": "ladder"}]
    msgs, st = health.diff_alerts(st, [], ev, NOW + 1000)
    assert msgs == ["[stake 증액] v: 5→10 USDC (ladder)"]
    assert health.diff_alerts(st, [], ev, NOW + 1100)[0] == []


def test_gather_and_alert_state_file(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    h, problems = health.evaluate(paths, NOW, use_jenkins=False)
    assert h["collector"]["live_games"] == 1 and h["collector"]["last_poll_at"] is not None
    assert not any(p["key"] == "poll_stale" for p in problems)
    posted = []
    problems = [{"key": "disk", "level": "warn", "message": "disk low"}]
    health.alert(paths, h, problems, poster=lambda t: posted.append(t) or True)
    health.alert(paths, h, problems, poster=lambda t: posted.append(t) or True)
    assert len(posted) == 1 and "disk low" in posted[0]
    state = json.loads((paths.state / "alerts.json").read_text())
    assert "disk" in state["active"]


def test_failed_post_keeps_state_for_retry(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    h = {"now": NOW}
    problems = [{"key": "disk", "level": "warn", "message": "disk low"}]
    health.alert(paths, h, problems, poster=lambda t: False)
    posted = []
    health.alert(paths, h, problems, poster=lambda t: posted.append(t) or True)
    assert posted


def test_merged_jobs_status():
    jobs = health.merged_jobs({"polylab-tick": {"last_run_at": NOW - 30, "last_ok_at": NOW - 30, "fail_streak": 0}},
                              {"polylab-publish": {"last_run_at": NOW - 5000, "last_ok_at": NOW - 5000,
                                                   "fail_streak": 0}}, NOW)
    by = {j["name"]: j["status"] for j in jobs}
    assert by["polylab-tick"] == "ok" and by["polylab-publish"] == "stale"


def test_live_games_from_time_and_heartbeat_file(tmp_path, monkeypatch):
    import os

    from polylab import db
    paths, _ = make_world(tmp_path, monkeypatch)
    core = db.core(paths)
    core.execute("UPDATE games SET status='scheduled'")  # stale status must not hide a started game
    core.execute("INSERT INTO games(game_key, sport, start_time, status, first_seen, updated_at, source)"
                 " VALUES('old','soccer',?, 'live', 0, 0, 'discover')", (NOW - 10 * 3600,))  # too old to be live
    core.commit()
    hb = paths.state / "stream.heartbeat"
    hb.write_text("{}")
    os.utime(hb, (NOW - 30, NOW - 30))
    h = health.gather(paths, NOW, use_jenkins=False)
    assert h["collector"]["live_games"] == 1 and h["collector"]["stream_heartbeat_at"] == NOW - 30
    assert not any(p["key"] == "stream_stale" for p in health.checks(h))

"""`polylab health [--json] [--alert] [--dry-run]` — freshness / disk / job checks.

Exit code: 0 ok, 1 warnings only, 2 critical. `--alert` posts to Slack only when the set of
active problems changes (new problem, level change, recovery), deduplicated through
<state>/alerts.json, plus one-shot notices for stake changes and retro failures.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import requests

from polylab import registry, settings
from polylab.analysis import _common as C

JENKINS_URL = os.environ.get("POLYLAB_JENKINS_URL", "http://127.0.0.1:8080")
JOB_CADENCE_S = {"polylab-tick": 60, "polylab-stream": 300, "polylab-backfill": 3600,
                 "polylab-publish": 300, "polylab-retro": 12 * 3600}
DISK_WARN_GB = 50.0
DISK_CRIT_GB = 20.0
POLL_STALE_S = 5 * 60
STREAM_STALE_S = 10 * 60
TICK_FAIL_STREAK = 3
IN_PLAY_HOURS = {"soccer": 4.0, "mlb": 8.0, "nba": 5.0, "nfl": 6.0, "nhl": 5.0}  # = marketview defaults


# ------------------------------------------------------------------ gathering

def _file_mb(path: Path) -> float | None:
    if not path.exists():
        return None
    total = path.stat().st_size
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            total += side.stat().st_size
    return round(total / 1e6, 1)


def _dir_mb(path: Path) -> float | None:
    if not path.exists():
        return None
    return round(sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1e6, 1)


def stream_heartbeat(paths) -> int | None:
    """mtime of <state>/stream.heartbeat, written every 10 s by the stream daemon."""
    try:
        return int((Path(paths.state) / "stream.heartbeat").stat().st_mtime)
    except OSError:
        return None


def collector_status(conn: sqlite3.Connection | None, now: int) -> dict:
    out = {"last_poll_at": None, "last_ws_price_at": None, "last_history_at": None, "ws_last_message_at": None,
           "stream_heartbeat_at": None, "last_game_state_at": None, "live_games": None, "tracked_markets": None,
           "backfill_progress": {"games_done": None, "games_total": None}, "quality_24h": {}, "checkpoints": {}}
    if conn is None:
        return out
    q = lambda sql, p=(): conn.execute(sql, p).fetchone()  # noqa: E731
    try:
        for source, ts in conn.execute("SELECT source, MAX(ts) FROM price_bars WHERE ts >= ? GROUP BY source",
                                       (now - 3 * 86400,)):
            key = {"poll_mid": "last_poll_at", "ws_last": "last_ws_price_at", "history": "last_history_at"}.get(source)
            if key:
                out[key] = ts
        out["last_game_state_at"] = q("SELECT MAX(received_at) FROM game_states WHERE ts >= ?", (now - 3 * 86400,))[0]
        ws_state = q("SELECT MAX(received_at) FROM game_states WHERE source='ws_sports' AND ts >= ?",
                     (now - 3 * 86400,))[0]
        # "live" from time, like marketview (games.status is only the collector's latest knowledge)
        live = 0
        for sport, hours in IN_PLAY_HOURS.items():
            live += q("SELECT COUNT(*) FROM games WHERE sport=? AND start_time <= ? AND start_time >= ? "
                      "AND ended_at IS NULL AND COALESCE(status,'') NOT IN ('ended','cancelled','final','closed')",
                      (sport, now, now - int(hours * 3600)))[0]
        out["live_games"] = live
        out["tracked_markets"] = q("SELECT COUNT(*) FROM markets m JOIN games g ON g.game_key=m.game_key "
                                   "WHERE m.closed=0 AND g.start_time BETWEEN ? AND ?", (now - 12 * 3600, now + 86400))[0]
        out["backfill_progress"] = {
            "games_done": q("SELECT COUNT(DISTINCT m.game_key) FROM backfill_status b JOIN markets m "
                            "ON m.condition_id=b.condition_id WHERE b.kind='prices' AND b.status IN ('done','empty')")[0],
            "games_total": q("SELECT COUNT(*) FROM games WHERE ended_at IS NOT NULL OR status='ended'")[0]}
        out["quality_24h"] = {k: n for k, n in conn.execute(
            "SELECT kind, COUNT(*) FROM quality_events WHERE ts >= ? GROUP BY kind ORDER BY 2 DESC", (now - 86400,))}
        cps = {name: updated for name, updated in conn.execute("SELECT name, updated_at FROM checkpoints")}
        out["checkpoints"] = {k: C.iso(v) for k, v in cps.items()}
        if cps.get("poll.last_run"):  # poll ran even when no live bar was written
            out["last_poll_at"] = max(x for x in (out["last_poll_at"], cps["poll.last_run"]) if x)
        ws_candidates = [x for x in (ws_state, out["last_ws_price_at"]) if x]
        out["ws_last_message_at"] = max(ws_candidates) if ws_candidates else None
    except sqlite3.OperationalError as exc:
        out["error"] = str(exc)[:200]
    return out


def job_runs(conn: sqlite3.Connection | None, now: int) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    if conn is None:
        return jobs
    try:
        names = [r[0] for r in conn.execute("SELECT DISTINCT job FROM job_runs WHERE started_at >= ?",
                                            (now - 7 * 86400,))]
        for name in names:
            rows = conn.execute("SELECT started_at, finished_at, ok, summary FROM job_runs WHERE job=? "
                                "ORDER BY started_at DESC LIMIT 50", (name,)).fetchall()
            streak = 0
            for r in rows:
                if r["ok"] == 0:
                    streak += 1
                elif r["ok"] == 1:
                    break
            last_ok = conn.execute("SELECT MAX(started_at) FROM job_runs WHERE job=? AND ok=1", (name,)).fetchone()[0]
            jobs[name] = {"last_run_at": rows[0]["started_at"] if rows else None, "last_ok_at": last_ok,
                          "fail_streak": streak, "detail": (rows[0]["summary"] or "")[:200] if rows else None}
    except sqlite3.OperationalError:
        pass
    return jobs


def jenkins_jobs(timeout: float = 2.0) -> dict[str, dict] | None:
    """Anonymous Jenkins JSON API (Mac mini only). None when unreachable."""
    tree = "jobs[name,color,lastBuild[number,timestamp,result,building],lastSuccessfulBuild[number,timestamp]]"
    try:
        r = requests.get(f"{JENKINS_URL}/api/json", params={"tree": tree}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    out = {}
    for j in data.get("jobs", []):
        name = j.get("name", "")
        if not name.startswith("polylab-"):
            continue
        last, ok = j.get("lastBuild") or {}, j.get("lastSuccessfulBuild") or {}
        streak = 0
        if last and last.get("result") not in (None, "SUCCESS") and not last.get("building"):
            streak = (last.get("number") or 0) - (ok.get("number") or 0)
        out[name] = {"last_run_at": int(last["timestamp"] / 1000) if last.get("timestamp") else None,
                     "last_ok_at": int(ok["timestamp"] / 1000) if ok.get("timestamp") else None,
                     "fail_streak": streak, "detail": last.get("result") or ("building" if last.get("building") else None)}
    return out


def merged_jobs(core_jobs: dict, jenkins: dict | None, now: int) -> list[dict]:
    names = sorted(set(core_jobs) | set(jenkins or {}) | set(JOB_CADENCE_S))
    out = []
    for name in names:
        a, b = core_jobs.get(name, {}), (jenkins or {}).get(name, {})
        last_run = max([x for x in (a.get("last_run_at"), b.get("last_run_at")) if x], default=None)
        last_ok = max([x for x in (a.get("last_ok_at"), b.get("last_ok_at")) if x], default=None)
        streak = max(a.get("fail_streak", 0), b.get("fail_streak", 0))
        cadence = JOB_CADENCE_S.get(name)
        if last_run is None:
            status = "stale"
        elif streak > 0:
            status = "failing"
        elif cadence and now - last_run > 3 * cadence + 60:
            status = "stale"
        else:
            status = "ok"
        out.append({"name": name, "last_run_at": C.iso(last_run), "last_ok_at": C.iso(last_ok), "status": status,
                    "fail_streak": streak, "detail": b.get("detail") or a.get("detail")})
    return out


def kill_switch(paths, conn: sqlite3.Connection | None = None) -> bool | None:
    """Kill switch owned by polylab.risk.caps (state/KILL file or POLYLAB_KILL env)."""
    try:
        from polylab.risk.caps import kill_switch_active  # noqa: PLC0415
    except Exception:
        return None
    return bool(kill_switch_active(Path(paths.state)))


def gather(paths, now: int | None = None, use_jenkins: bool = True) -> dict:
    now = now or int(time.time())
    conn = C.open_ro(paths.core_db)
    try:
        coll = collector_status(conn, now)
        core_jobs = job_runs(conn, now)
        ks = kill_switch(paths, conn)
    finally:
        if conn is not None:
            conn.close()
    coll["stream_heartbeat_at"] = stream_heartbeat(paths)
    try:
        disk_free_gb = round(shutil.disk_usage(paths.root).free / 1e9, 1)
    except OSError:
        disk_free_gb = None
    jenkins = jenkins_jobs() if use_jenkins else None
    return {"now": now, "collector": coll, "jobs": merged_jobs(core_jobs, jenkins, now),
            "jenkins_reachable": jenkins is not None, "kill_switch": ks,
            "core_db_mb": _file_mb(paths.core_db), "books_db_mb": _dir_mb(paths.books_dir),
            "strategies_db_mb": _dir_mb(paths.strategies_dir), "disk_free_gb": disk_free_gb}


# ------------------------------------------------------------------ checks

def _age(now: int, ts: int | None) -> float | None:
    return None if ts is None else now - ts


def variant_loss_stops(paths, now: int) -> list[dict]:
    """Variants whose realised live PnL today (UTC day, same as the engine's cap) reached daily_loss_stop_usdc."""
    from polylab.analysis import performance  # noqa: PLC0415
    out = []
    try:
        variants = registry.load_all()
    except Exception:
        return out
    for v in variants:
        stop = (v.limits or {}).get("daily_loss_stop_usdc")
        if not stop:
            continue
        s = performance.settled(performance.load_variant_positions(paths.strategy_db(v.id)), "live")
        today = s[s["closed_at"].astype(int) >= now - now % 86400] if not s.empty else s  # UTC day, as risk.caps
        pnl = float(today["realized_pnl"].sum()) if not today.empty else 0.0
        if pnl <= -float(stop):
            out.append({"variant_id": v.id, "pnl_today": round(pnl, 2), "stop": float(stop)})
    return out


def checks(h: dict, paths=None) -> list[dict]:
    """Active problems: [{key, level: warn|critical, message}]."""
    now = h["now"]
    coll = h["collector"]
    problems = []

    def add(key, level, message):
        problems.append({"key": key, "level": level, "message": message})

    if h.get("storage_error"):
        add("storage", "critical", h["storage_error"])
        return problems
    free = h.get("disk_free_gb")
    if free is not None and free < DISK_CRIT_GB:
        add("disk", "critical", f"외장 디스크 여유 {free}GB (< {DISK_CRIT_GB:g}GB)")
    elif free is not None and free < DISK_WARN_GB:
        add("disk", "warn", f"외장 디스크 여유 {free}GB (< {DISK_WARN_GB:g}GB)")
    live = coll.get("live_games") or 0
    poll_age = _age(now, coll.get("last_poll_at"))
    if live > 0 and (poll_age is None or poll_age > POLL_STALE_S):
        mins = "없음" if poll_age is None else f"{poll_age / 60:.0f}분 전"
        add("poll_stale", "critical", f"라이브 경기 {live}개인데 마지막 poll {mins}")
    beat = coll.get("stream_heartbeat_at")
    beat_age = _age(now, beat)
    if live > 0 and (beat_age is None or beat_age > STREAM_STALE_S):
        mins = "없음" if beat_age is None else f"{beat_age / 60:.0f}분 전"
        add("stream_stale", "warn", f"WS stream heartbeat {mins}")
    for job in h.get("jobs", []):
        if job["name"] == "polylab-tick" and job.get("fail_streak", 0) >= TICK_FAIL_STREAK:
            add("job:polylab-tick", "critical", f"polylab-tick {job['fail_streak']}회 연속 실패")
        elif job["status"] == "failing" and job["name"] != "polylab-tick":
            add(f"job:{job['name']}", "warn", f"{job['name']} 실패 ({job.get('detail') or '-'})")
    if h.get("kill_switch"):
        add("kill_switch", "critical", "kill switch 활성 — 신규 진입 중단")
    for stop in h.get("loss_stops", []):
        add(f"loss_stop:{stop['variant_id']}", "warn",
            f"{stop['variant_id']} 일일 손실 한도 도달 ({stop['pnl_today']:+.2f} / -{stop['stop']:g} USDC)")
    retro = h.get("retro_last") or {}
    if retro and retro.get("ok") is False:
        add("retro_failed", "warn", f"AI 회고 실패 ({retro.get('kind')}, {retro.get('at')}): {(retro.get('error') or '')[:120]}")
    return problems


def retro_last(paths) -> dict | None:
    p = Path(paths.state) / "retro" / "last.json"
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def recent_stake_events(paths, since: int) -> list[dict]:
    out = []
    try:
        variants = registry.load_all(include_off=True)
    except Exception:
        return out
    for v in variants:
        conn = C.open_ro(paths.strategy_db(v.id))
        if conn is None:
            continue
        try:
            for r in conn.execute("SELECT id, ts, from_usdc, to_usdc, from_mode, to_mode, reason FROM stake_events "
                                  "WHERE ts >= ? ORDER BY id", (since,)):
                out.append({"variant_id": v.id, **dict(r)})
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    return out


# ------------------------------------------------------------------ alerting

def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": {}, "seen_stake_events": [], "stake_events_since": None}


def diff_alerts(state: dict, problems: list[dict], stake_events: list[dict], now: int) -> tuple[list[str], dict]:
    """Return (messages to post, new state). Pure: no IO."""
    messages = []
    active = dict(state.get("active", {}))
    current = {p["key"]: p for p in problems}
    for key, p in current.items():
        prev = active.get(key)
        if prev is None or prev.get("level") != p["level"]:
            tag = "[장애]" if p["level"] == "critical" else "[경고]"
            messages.append(f"{tag} {p['message']}")
            active[key] = {"level": p["level"], "since": now, "message": p["message"]}
    for key in list(active):
        if key not in current:
            mins = (now - active[key].get("since", now)) / 60
            messages.append(f"[복구] {active[key].get('message', key)} — 해소 ({mins:.0f}분 지속)")
            del active[key]
    seen = set(state.get("seen_stake_events", []))
    for ev in stake_events:
        key = f"{ev['variant_id']}:{ev['id']}"
        if key in seen:
            continue
        seen.add(key)
        direction = "증액" if (ev.get("to_usdc") or 0) > (ev.get("from_usdc") or 0) else "감액/변경"
        mode = f", mode {ev.get('from_mode')}→{ev.get('to_mode')}" if ev.get("to_mode") != ev.get("from_mode") else ""
        messages.append(f"[stake {direction}] {ev['variant_id']}: {ev.get('from_usdc')}→{ev.get('to_usdc')} USDC"
                        f"{mode} ({ev.get('reason')})")
    return messages, {"active": active, "seen_stake_events": sorted(seen)[-500:], "updated_at": now}


FALLBACK_STATE_DIR = Path.home() / ".cache" / "polylab"  # used only when the external disk is missing


def alert(paths, h: dict, problems: list[dict], dry_run: bool = False, poster=None) -> list[str]:
    state_dir = Path(paths.state) if paths is not None else FALLBACK_STATE_DIR
    state_path = state_dir / "alerts.json"
    state = _load_state(state_path)
    first_run = not state_path.exists()
    events = recent_stake_events(paths, h["now"] - 2 * 86400) if paths is not None else []
    messages, new_state = diff_alerts(state, problems, events, h["now"])
    if first_run:  # don't replay history on the very first run
        messages = [m for m in messages if not m.startswith("[stake")]
    if messages and not dry_run:
        from polylab.reports import slack  # noqa: PLC0415
        text = "polylab 알림\n" + "\n".join(messages) + f"\n{slack.DASHBOARD_URL}"
        ok = (poster or slack.post_text)(text)
        if not ok:
            return messages  # keep old state so the alert is retried next run
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=1))
    return messages


def evaluate(paths=None, now: int | None = None, use_jenkins: bool = True) -> tuple[dict, list[dict]]:
    now = now or int(time.time())
    if paths is None:
        try:
            paths = settings.paths()
        except settings.StorageUnavailable as exc:
            h = {"now": now, "storage_error": str(exc), "collector": {}, "jobs": []}
            return h, checks(h)
    h = gather(paths, now, use_jenkins)
    h["loss_stops"] = variant_loss_stops(paths, now)
    h["retro_last"] = retro_last(paths)
    return h, checks(h, paths)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab health")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--alert", action="store_true", help="post state changes to Slack (dedup via state file)")
    ap.add_argument("--dry-run", action="store_true", help="with --alert: print instead of posting")
    ap.add_argument("--no-jenkins", action="store_true")
    args = ap.parse_args(argv)
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        paths = None
        h, problems = {"now": int(time.time()), "storage_error": str(exc), "collector": {}, "jobs": []}, []
        problems = checks(h)
    else:
        h, problems = evaluate(paths, use_jenkins=not args.no_jenkins)
    if args.alert:
        sent = alert(paths, h, problems, dry_run=args.dry_run)
        for m in sent:
            print(("DRY " if args.dry_run else "SENT ") + m)
    if args.json:
        print(json.dumps({"health": h, "problems": problems}, ensure_ascii=False, indent=1, default=str))
    else:
        c = h.get("collector", {})
        print(f"disk_free_gb={h.get('disk_free_gb')} core_db_mb={h.get('core_db_mb')} books_db_mb={h.get('books_db_mb')}")
        print(f"last_poll={C.iso(c.get('last_poll_at'))} ws={C.iso(c.get('ws_last_message_at'))} "
              f"live_games={c.get('live_games')} backfill={c.get('backfill_progress')}")
        for p in problems:
            print(f"{p['level'].upper():8s} {p['message']}")
        if not problems:
            print("OK")
    if any(p["level"] == "critical" for p in problems):
        return 2
    return 1 if problems else 0

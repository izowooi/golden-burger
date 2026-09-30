"""`polylab stream [--run|--ensure|--status]` — WebSocket collector daemon (market + sports channels).

--run     long-running process (launchd keeps it alive):
          * CLOB market WS for every token of live / starting-within-15-min games (set refreshed every 60 s
            from core.db; PING every 10 s; reconnect with exponential backoff).
          * sports WS (all active games are pushed; we keep the ones joined via games.polymarket_game_id).
          * raw frames -> paths.raw_dir/YYYY-MM-DD/{market,sports}-HH.jsonl.gz, one line per frame
            {"received_at": <ms>, "raw": <frame json>}, appended as a new gzip member every ~15 s
            (a crash loses at most the unflushed buffer; the files stay valid multi-member gzip).
            `new_market` broadcasts (unrelated markets) are not stored.
          * price_bars source='ws_last': last trade price per token per minute (exchange timestamp minute).
          * game_states source='ws_sports' whenever status/period/elapsed/score/live/ended changes;
            games.status/score/ended_at kept current.
          * heartbeat JSON at paths.state/stream.heartbeat every 10 s (written even when idle).
--ensure  install/refresh the launchd LaunchAgent (ops/launchd/uk.zowoo.polylab.stream.plist template) and
          kickstart it if the heartbeat is older than 3 minutes; if still no heartbeat, spawn a detached daemon
          (see the TCC note below); exit 1 if still unhealthy. Fails closed when /Volumes/t7 is not mounted.
--status  print heartbeat; exit 0 when fresh (< 3 min) else 1.

game_minute mapping (common.game_minute): soccer = minutes incl. stoppage; NBA/NFL/NHL = elapsed regulation
minutes from period + countdown clock; MLB = innings completed (top n -> n-1, mid/bottom n -> n-0.5).
Sports-WS score strings are assumed to follow Gamma's title order (soccer home first, US sports away first).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

from polylab import db as dbmod
from polylab.api import ws_market, ws_sports
from polylab.collector import common as C
from polylab.settings import REPO_ROOT, StorageUnavailable

log = logging.getLogger("polylab.stream")

LABEL = "uk.zowoo.polylab.stream"
TEMPLATE = REPO_ROOT / "ops" / "launchd" / f"{LABEL}.plist"
PROD_ROOT = Path("/Volumes/t7/polylab")
UV = "/Users/jongwoopark/.local/bin/uv"
HEARTBEAT_STALE_S = 180
FLUSH_EVERY_S = 15
HEARTBEAT_EVERY_S = 10
WS_GAP_S = 180


class RawWriter:
    """Buffered hourly gzip writer: each flush appends one gzip member."""

    def __init__(self, raw_dir: Path, channel: str):
        self.raw_dir = raw_dir
        self.channel = channel
        self.buf: list[bytes] = []
        self.bytes_written = 0
        self.lines = 0

    def add(self, received_ms: int, text: str) -> None:
        self.buf.append(b'{"received_at":%d,"raw":%s}\n' % (received_ms, text.encode("utf-8")))

    def path_for(self, ts: float) -> Path:
        t = time.gmtime(ts)
        return self.raw_dir / time.strftime("%Y-%m-%d", t) / f"{self.channel}-{time.strftime('%H', t)}.jsonl.gz"

    def flush(self, ts: float | None = None) -> None:
        if not self.buf:
            return
        path = self.path_for(ts or time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"".join(self.buf)
        with open(path, "ab") as fh:
            fh.write(gzip.compress(data, compresslevel=6))
        self.bytes_written += len(data)
        self.lines += len(self.buf)
        self.buf.clear()


class Collector:
    def __init__(self, paths, cfg: C.CollectorConfig | None = None):
        self.paths = paths
        self.cfg = cfg or C.load_config()
        self.raw = {"market": RawWriter(paths.raw_dir, "market"), "sports": RawWriter(paths.raw_dir, "sports")}
        self.bars: dict[tuple[str, int], tuple[float, int]] = {}
        self.states: list[tuple] = []
        self.game_updates: dict[str, dict] = {}
        self.last_state: dict[str, tuple] = {}
        self.last_ts: dict[tuple[str, str], int] = {}
        self.q = C.QualityLog()
        self.assets: set[str] = set()
        self.games_by_gid: dict[str, dict] = {}
        self.counts = {"market_msgs": 0, "sports_msgs": 0, "bars": 0, "states": 0}
        self.started = time.time()
        self.market: ws_market.MarketFeed | None = None
        self.sports: ws_sports.SportsFeed | None = None
        self.ws_down_since: float | None = None
        self._refresh_games(force=True)

    # ------------------------------------------------------------- db lookups
    def _ro(self):
        return dbmod.core(self.paths)

    def _refresh_games(self, force: bool = False) -> None:
        conn = self._ro()
        try:
            rows = conn.execute(
                "SELECT game_key, sport, title, polymarket_game_id, status FROM games "
                "WHERE polymarket_game_id IS NOT NULL AND COALESCE(status,'') NOT IN ('cancelled') "
                "AND start_time >= ? AND start_time <= ?", (C.now() - 2 * 86400, C.now() + 86400)).fetchall()
        finally:
            conn.close()
        self.games_by_gid = {r["polymarket_game_id"]: dict(r) for r in rows}

    def desired_assets(self) -> set[str]:
        conn = self._ro()
        try:
            toks = C.active_tokens(conn, C.now(), self.cfg.poll_lead_s)
        finally:
            conn.close()
        self.assets = {r["token_id"] for r in toks}
        self._refresh_games()
        return self.assets

    # ------------------------------------------------------------- handlers
    def on_market(self, text: str, received_ms: int) -> None:
        events = ws_market.parse_frame(text)
        if not events:
            return
        keep = False
        for ev in events:
            et = ev.get("event_type")
            if et == "new_market":
                continue
            keep = True
            self.counts["market_msgs"] += 1
            try:
                ets = int(ev.get("timestamp") or 0)
            except (TypeError, ValueError):
                ets = 0
            for asset in ws_market.event_assets(ev):
                key = (asset, et or "")
                prev = self.last_ts.get(key)
                if ets and prev and ets < prev - 1000:
                    self.q.add("ws_ts_regression", asset, (prev - ets) / 1000.0, event_type=et)
                if ets:
                    self.last_ts[key] = max(ets, prev or 0)
            if et == "last_trade_price" and ev.get("asset_id") in self.assets:
                try:
                    price = float(ev["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                t = (ets / 1000.0) if ets else received_ms / 1000.0
                self.bars[(ev["asset_id"], C.minute(t))] = (price, received_ms // 1000)
            elif et == "book" and ev.get("asset_id") in self.assets:
                m = C.book_metrics(ev)
                if m["crossed"]:
                    self.q.add("crossed_book_ws", ev["asset_id"], (m["best_bid"] or 0) - (m["best_ask"] or 0))
        if keep:
            self.raw["market"].add(received_ms, text)

    def on_sports(self, text: str, received_ms: int) -> None:
        frames = ws_sports.parse_frame(text)
        if not frames:
            return
        self.raw["sports"].add(received_ms, text)
        for fr in frames:
            self.counts["sports_msgs"] += 1
            gid = ws_sports.game_id_of(fr)
            game = self.games_by_gid.get(gid) if gid else None
            if not game:
                continue
            sport = game["sport"]
            first_is_home = C.TITLE_FIRST.get(sport, "home") == "home"
            hs, as_ = C.parse_score(fr.get("score"), first_is_home)
            status = "ended" if fr.get("ended") else ("live" if fr.get("live") else (fr.get("status") or None))
            state = (fr.get("status"), _s(fr.get("period")), _s(fr.get("elapsed")), fr.get("score"),
                     fr.get("live"), fr.get("ended"))
            gk = game["game_key"]
            if self.last_state.get(gk) == state:
                continue
            self.last_state[gk] = state
            ts = received_ms // 1000
            self.states.append((gk, ts, ts, "ws_sports", status, _b(fr.get("live")), _b(fr.get("ended")), state[1],
                                state[2], C.game_minute(sport, fr.get("period"), fr.get("elapsed"), status),
                                hs, as_, text[:2000]))
            upd = self.game_updates.setdefault(gk, {})
            if status in ("live", "ended"):
                upd["status"] = status
            if hs is not None:
                upd["home_score"], upd["away_score"] = hs, as_
            if status == "ended":
                upd["ended_at"] = ts

    # ------------------------------------------------------------- periodic
    def flush(self) -> None:
        now = time.time()
        for w in self.raw.values():
            w.flush(now)
        if self.market is not None:
            if self.assets and not self.market.connected:
                self.ws_down_since = self.ws_down_since or now
                if now - self.ws_down_since > WS_GAP_S:
                    self.q.add("ws_gap", "market", now - self.ws_down_since, assets=len(self.assets))
                    self.ws_down_since = now
            else:
                self.ws_down_since = None
        if not (self.bars or self.states or self.game_updates or len(self.q)):
            return
        bars = [(tok, t, "ws_last", p, recv) for (tok, t), (p, recv) in self.bars.items()]
        states, updates = self.states, self.game_updates
        self.bars, self.states, self.game_updates = {}, [], {}
        conn = dbmod.core(self.paths)
        try:
            with dbmod.tx(conn):
                C.write_price_bars(conn, bars)
                conn.executemany(
                    "INSERT OR REPLACE INTO game_states(game_key, ts, received_at, source, status, live, ended, period, "
                    "elapsed, game_minute, home_score, away_score, raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", states)
                for gk, u in updates.items():
                    conn.execute(
                        """UPDATE games SET
                             status=CASE WHEN status IN ('ended','cancelled') THEN status ELSE COALESCE(?, status) END,
                             home_score=COALESCE(?, home_score), away_score=COALESCE(?, away_score),
                             ended_at=COALESCE(ended_at, ?), updated_at=? WHERE game_key=?""",
                        (u.get("status"), u.get("home_score"), u.get("away_score"), u.get("ended_at"), C.now(), gk))
                self.q.flush(conn)
        finally:
            conn.close()
        self.counts["bars"] += len(bars)
        self.counts["states"] += len(states)

    def heartbeat(self) -> None:
        hb = {
            "ts": C.now(), "pid": os.getpid(), "uptime_s": int(time.time() - self.started),
            "assets": len(self.assets), "tracked_games": len(self.games_by_gid),
            "market_connected": bool(self.market and self.market.connected),
            "sports_connected": bool(self.sports and self.sports.connected),
            "market_reconnects": self.market.reconnects if self.market else 0,
            "sports_reconnects": self.sports.reconnects if self.sports else 0,
            "raw_bytes": {k: w.bytes_written for k, w in self.raw.items()},
            **self.counts,
        }
        path = self.paths.state / "stream.heartbeat"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hb))
        tmp.replace(path)

    async def run(self, duration: float | None = None) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        self.market = ws_market.MarketFeed(self.desired_assets, self.on_market)
        self.sports = ws_sports.SportsFeed(self.on_sports)
        tasks = [asyncio.create_task(self.market.run(stop)), asyncio.create_task(self.sports.run(stop))]
        deadline = time.time() + duration if duration else None
        next_flush = time.time() + FLUSH_EVERY_S
        self.heartbeat()
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_EVERY_S)
                except asyncio.TimeoutError:
                    pass
                if deadline and time.time() >= deadline:
                    stop.set()
                if time.time() >= next_flush or stop.is_set():
                    try:
                        await asyncio.to_thread(self.flush)
                    except Exception:
                        log.exception("flush failed")
                    next_flush = time.time() + FLUSH_EVERY_S
                self.heartbeat()
                if any(t.done() for t in tasks):
                    for t in tasks:
                        if t.done() and t.exception():
                            raise t.exception()  # type: ignore[misc]
        finally:
            stop.set()
            # Feeds blocked in recv() may never observe `stop`; give them a moment, then cancel.
            _, pending = await asyncio.wait(tasks, timeout=20)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.flush()
            self.heartbeat()


def _s(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _b(v) -> int | None:
    return None if v is None else (1 if v else 0)


# ------------------------------------------------------------------ launchd / supervision
#
# Observed on the Mac mini (2026-09-30): a LaunchAgent cannot use /Volumes/t7 until the binary is granted
# macOS "Removable Volumes"/Full Disk Access — launchd fails to open StandardOut/ErrPath there (exit 78
# EX_CONFIG) and the spawned uv blocks/gets EPERM listing the volume. So the agent logs to the internal disk
# (~/Library/Logs/polylab, tiny), the daemon itself logs to paths.logs/stream.log, and `--ensure` falls back to
# a detached daemon started from the caller (Jenkins has volume access; JENKINS_NODE_COOKIE=dontKillMe keeps
# it alive after the build). A flock on paths.state/stream.lock guarantees a single running collector.

LAUNCHD_LOGS = Path.home() / "Library" / "Logs" / "polylab"


def render_plist(label: str = LABEL, repo: Path = PROD_ROOT / "repo", logs: Path = LAUNCHD_LOGS,
                 uv: str = UV, env: dict[str, str] | None = None, template: Path = TEMPLATE) -> bytes:
    """Fill the template; returns plist bytes (validated with plistlib)."""
    text = template.read_text()
    for k, v in {"__LABEL__": label, "__UV__": uv, "__REPO__": str(repo), "__LOGS__": str(logs)}.items():
        text = text.replace(k, v)
    data = plistlib.loads(text.encode())
    if env:
        data.setdefault("EnvironmentVariables", {}).update(env)
    return plistlib.dumps(data)


def launchd_domain() -> str:
    uid = os.getuid()
    r = subprocess.run(["launchctl", "print", f"gui/{uid}"], capture_output=True, text=True)
    return f"gui/{uid}" if r.returncode == 0 else f"user/{uid}"


def heartbeat_info(paths) -> tuple[float | None, dict]:
    path = paths.state / "stream.heartbeat"
    try:
        age = time.time() - path.stat().st_mtime
        return age, json.loads(path.read_text() or "{}")
    except (OSError, ValueError):
        return None, {}


def acquire_lock(paths, wait: bool = True, max_wait_s: float | None = None):
    """Exclusive flock so only one collector runs; returns the open file (keep it referenced).

    With max_wait_s the caller gives up (returns None) instead of queueing forever behind a live daemon.
    """
    import fcntl

    fh = open(paths.state / "stream.lock", "a+")
    give_up = time.time() + max_wait_s if max_wait_s is not None else None
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if not wait or (give_up is not None and time.time() >= give_up):
                fh.close()
                return None
            log.info("another stream daemon holds %s; waiting", paths.state / "stream.lock")
            time.sleep(15)


def spawn_detached(paths) -> int:
    """Start `polylab stream --run` detached from the caller (survives a Jenkins build)."""
    paths.logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JENKINS_NODE_COOKIE="dontKillMe", BUILD_ID="dontKillMe", PYTHONUNBUFFERED="1")
    out = open(paths.logs / "stream.detached.log", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "polylab.cli", "stream", "--run"], cwd=str(REPO_ROOT), env=env,
                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return proc.pid


def _wait_fresh(paths, wait_s: float) -> tuple[float | None, dict]:
    deadline = time.time() + wait_s
    age, hb = heartbeat_info(paths)
    while time.time() < deadline:
        if age is not None and age < 30:
            break
        time.sleep(5)
        age, hb = heartbeat_info(paths)
    return age, hb


def ensure(paths, *, label: str = LABEL, repo: Path | None = None, dry_run: bool = False, wait_s: int = 45,
           fallback: bool = True) -> int:
    if not os.environ.get("POLYLAB_ROOT") and not Path("/Volumes/t7").is_mount():
        print("FAIL: /Volumes/t7 is not mounted; refusing to start the stream daemon", file=sys.stderr)
        return 2
    repo = repo or PROD_ROOT / "repo"
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    if os.environ.get("POLYLAB_ROOT"):
        env["POLYLAB_ROOT"] = os.environ["POLYLAB_ROOT"]
    plist = render_plist(label=label, repo=repo, env=env)
    target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    domain = launchd_domain()
    changed = not target.exists() or target.read_bytes() != plist
    loaded = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True).returncode == 0
    age, hb = heartbeat_info(paths)
    stale = age is None or age > HEARTBEAT_STALE_S
    actions = []
    if changed:
        actions.append(f"write {target}")
        if loaded:
            actions.append(f"launchctl bootout {domain}/{label}")
    if changed or not loaded:
        actions.append(f"launchctl bootstrap {domain} {target}")
    elif stale:
        actions.append(f"launchctl kickstart -k {domain}/{label}")
    if dry_run:
        print(json.dumps({"domain": domain, "target": str(target), "changed": changed, "loaded": loaded,
                          "heartbeat_age_s": age, "actions": actions, "fallback": fallback}, indent=2))
        sys.stdout.write(plist.decode())
        return 0
    for a in actions:
        if a.startswith("write "):
            target.parent.mkdir(parents=True, exist_ok=True)
            LAUNCHD_LOGS.mkdir(parents=True, exist_ok=True)
            target.write_bytes(plist)
        else:
            r = subprocess.run(a.split(), capture_output=True, text=True)
            if r.returncode != 0 and "bootout" not in a:
                print(f"{a} -> rc={r.returncode} {r.stderr.strip()}", file=sys.stderr)
    if not stale:
        print(json.dumps({"healthy": True, "actions": actions, "heartbeat_age_s": round(age or 0, 1), **hb}))
        return 0
    age, hb = _wait_fresh(paths, wait_s)
    if (age is None or age >= 30) and fallback:
        pid = spawn_detached(paths)
        actions.append(f"spawn detached pid={pid}")
        age, hb = _wait_fresh(paths, wait_s)
    healthy = age is not None and age < 30
    print(json.dumps({"healthy": healthy, "actions": actions, "heartbeat_age_s": age, **hb}),
          file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


def _file_logging(paths) -> None:
    from logging.handlers import RotatingFileHandler

    try:
        paths.logs.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(paths.logs / "stream.log", maxBytes=10 * 2**20, backupCount=5)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(h)
    except OSError as exc:
        log.warning("file logging unavailable: %r", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab stream", description="WebSocket collector daemon")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--run", action="store_true", help="run the daemon in the foreground")
    g.add_argument("--ensure", action="store_true", help="install/refresh the launchd agent; restart if stale")
    g.add_argument("--status", action="store_true", help="print heartbeat; rc 0 if fresh")
    ap.add_argument("--duration", type=float, help="--run: stop after N seconds (testing)")
    ap.add_argument("--label", default=LABEL, help="--ensure: launchd label override (testing)")
    ap.add_argument("--repo", type=Path, help="--ensure: repo checkout the agent runs from")
    ap.add_argument("--dry-run", action="store_true", help="--ensure: print plist and planned actions only")
    ap.add_argument("--no-fallback", action="store_true", help="--ensure: never spawn a detached daemon")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from polylab.settings import paths
    try:
        p = paths()
    except StorageUnavailable as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if args.ensure:
        return ensure(p, label=args.label, repo=args.repo, dry_run=args.dry_run, fallback=not args.no_fallback)
    if args.run:
        _file_logging(p)
        lock = acquire_lock(p, wait=True, max_wait_s=55)
        if lock is None:
            print(json.dumps({"skipped": "another stream daemon is running"}))
            return 0
        if args.duration:
            # Last-resort exit so a wedged shutdown can never outlive its Jenkins build (orphans behind ssh).
            import threading
            killer = threading.Timer(args.duration + 120, lambda: os._exit(3))
            killer.daemon = True
            killer.start()
        try:
            col = Collector(p)
            asyncio.run(col.run(args.duration))
        finally:
            lock.close()
        print(json.dumps({"counts": col.counts, "raw_bytes": {k: w.bytes_written for k, w in col.raw.items()},
                          "raw_lines": {k: w.lines for k, w in col.raw.items()}}))
        return 0
    age, hb = heartbeat_info(p)
    fresh = age is not None and age <= HEARTBEAT_STALE_S
    print(json.dumps({"fresh": fresh, "heartbeat_age_s": None if age is None else round(age, 1), **hb}))
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())

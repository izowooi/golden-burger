"""Data growth budget (owner decision 2026-10-05 `storage:budget`: <= 50 GB/month, hard max 100 GB/month).

`polylab storage [report]`          sizes per data area, 7-day growth, 30-day projection (JSON)
`polylab storage compact [--apply]` VACUUM closed-month shards (books/, ou05/, general/) — never deletes anything

Growth per area (core.db, books/, raw/, ou05/, general/, strategies/, manual/, research/), steady state first
(2026-10-06: the first-week projection of 82–93 GB/month was one-time backfills plus raw days before the lean switch):
- raw/: per UTC day from the dated directories raw/YYYY-MM-DD (exact bytes);
- every other area: per UTC day from the size samples in <state>/storage_samples.json (at most one per hour, 60 days,
  written by `polylab health`): the day's growth from the last sample before the day (or its first) to its last
  sample, scaled to 24 h; a day needs >= 6 h of coverage. Shard names are never used to date growth (backfills write
  into old-month shards).
- steady state (`gb_per_day`, `gb_30d`, `projected_30d_gb`, the budget level) = median daily rate over the last 7 days
  (today's partial day included), excluding days with a known one-time event of that area (`ONE_TIME_EVENTS` and
  <state>/storage_events.json: backfills) and days up to an area's last collection config change (`CONFIG_CHANGES`,
  e.g. raw lean mode). Excluded backfill days' growth above the steady rate is reported as `one_time_gb`. The old
  7-day mean stays available as `naive_*` (additive).
Budget status on the steady 30-day projection of the summed known areas: warn > 50 GB, critical > 100 GB.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

BUDGET_WARN_GB = 50.0
BUDGET_CRIT_GB = 100.0
SAMPLE_EVERY_S = 3600
SAMPLE_KEEP_S = 60 * 86400
WINDOW_S = 7 * 86400
MIN_SPAN_S = 86400
DAY = 86400
SHARD_RE = re.compile(r"^(\d{4}-\d{2})\.db$")
SHARD_AREAS = ("books", "ou05", "general")
MIN_DAY_COVERAGE_S = 6 * 3600
STEADY_DAYS = 7
# Known one-time growth (area, UTC day, note). More can be listed at runtime in <state>/storage_events.json
# ([{"area", "day": "YYYY-MM-DD", "kind": "backfill", "note"}]); a backfill tool may append there.
ONE_TIME_EVENTS: tuple[dict, ...] = (
    {"area": "general", "day": "2026-10-05", "kind": "backfill", "note": "general 전 카테고리 과거 가격 백필(약 1.1GB)"},
    {"area": "core", "day": "2026-10-05", "kind": "backfill", "note": "NBA·NHL·NFL 2024~ moneyline 과거 가격 백필(약 0.64GB)"},
)
# Collection config changes: days up to and including this UTC day are not steady state for the area.
CONFIG_CHANGES: dict[str, tuple[str, str]] = {
    "raw": ("2026-10-05", "raw WebSocket 보관 lean 전환(price_change 제외)"),
}


def areas(paths) -> dict[str, Path]:
    d = Path(paths.data)
    return {"core": Path(paths.core_db), "books": d / "books", "raw": d / "raw", "ou05": d / "ou05",
            "general": d / "general", "strategies": d / "strategies", "manual": d / "manual",
            "research": d / "research"}


def size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        total = path.stat().st_size
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                total += side.stat().st_size
        return total
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def measure(paths) -> dict[str, int]:
    return {name: size_bytes(p) for name, p in areas(paths).items()}


def raw_daily_bytes(paths, now: int, days: int = 7) -> tuple[float | None, int]:
    """Mean bytes/day of the last `days` complete UTC days of raw/YYYY-MM-DD (None when none exist)."""
    raw = areas(paths)["raw"]
    today = now - now % DAY
    sizes = []
    for i in range(1, days + 1):
        day = time.strftime("%Y-%m-%d", time.gmtime(today - i * DAY))
        p = raw / day
        if p.exists():
            sizes.append(size_bytes(p))
    return (sum(sizes) / len(sizes) if sizes else None), len(sizes)


def samples_path(paths) -> Path:
    return Path(paths.state) / "storage_samples.json"


def load_samples(paths) -> list[dict]:
    try:
        data = json.loads(samples_path(paths).read_text())
        return [s for s in data if isinstance(s, dict) and "ts" in s and "sizes" in s]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def record(paths, now: int, sizes: dict[str, int]) -> list[dict]:
    samples = load_samples(paths)
    if not samples or now - int(samples[-1]["ts"]) >= SAMPLE_EVERY_S:
        samples.append({"ts": now, "sizes": sizes})
        samples = [s for s in samples if now - int(s["ts"]) <= SAMPLE_KEEP_S]
        try:
            p = samples_path(paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(samples, separators=(",", ":")))
            tmp.replace(p)
        except OSError:
            pass
    return samples


def _day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def load_events(paths) -> list[dict]:
    """ONE_TIME_EVENTS plus runtime-recorded ones (<state>/storage_events.json)."""
    out = list(ONE_TIME_EVENTS)
    try:
        extra = json.loads((Path(paths.state) / "storage_events.json").read_text())
        out += [e for e in extra if isinstance(e, dict) and e.get("area") and e.get("day")]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return out


def sample_days(samples: list[dict], name: str, now: int, days: int = STEADY_DAYS) -> list[dict]:
    """Per UTC day (last `days` complete days + today): growth of `name` from the size samples, scaled to 24 h."""
    pts = sorted((int(s["ts"]), int(s["sizes"][name])) for s in samples if name in (s.get("sizes") or {}))
    today = now - now % DAY
    out = []
    for i in range(days, -1, -1):
        d0, d1 = today - i * DAY, today - i * DAY + DAY
        inside = [p for p in pts if d0 <= p[0] < d1]
        if not inside:
            continue
        before = [p for p in pts if p[0] < d0 and d0 - p[0] <= 3 * 3600]
        anchor = before[-1] if before else inside[0]
        last = inside[-1]
        span = last[0] - anchor[0]
        if span < MIN_DAY_COVERAGE_S:
            continue
        out.append({"day": _day(d0), "gb": round((last[1] - anchor[1]) / 1e9, 4), "coverage_h": round(span / 3600, 1),
                    "gb_per_day": round(max(0.0, last[1] - anchor[1]) / span * DAY / 1e9, 4)})
    return out


def raw_days(paths, now: int, days: int = STEADY_DAYS) -> list[dict]:
    """Per UTC day from raw/YYYY-MM-DD (exact); today's partial day scaled by elapsed time when >= 6 h old."""
    raw = areas(paths)["raw"]
    today = now - now % DAY
    out = []
    for i in range(days, -1, -1):
        d0 = today - i * DAY
        p = raw / _day(d0)
        if not p.exists():
            continue
        size = size_bytes(p)
        span = min(DAY, now - d0)
        if span < MIN_DAY_COVERAGE_S:
            continue
        out.append({"day": _day(d0), "gb": round(size / 1e9, 4), "coverage_h": round(span / 3600, 1),
                    "gb_per_day": round(size / span * DAY / 1e9, 4)})
    return out


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    k = len(xs)
    return xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2


def steady(days: list[dict], name: str, events: list[dict]) -> dict:
    """Median daily rate over days without a one-time event / after the area's last config change."""
    backfill = {e["day"]: e for e in events if e.get("area") == name}
    cutoff = CONFIG_CHANGES.get(name)
    for d in days:
        if cutoff and d["day"] <= cutoff[0]:
            d["excluded"] = f"config: {cutoff[1]} ({cutoff[0]})"
        elif d["day"] in backfill:
            d["excluded"] = f"one-time: {backfill[d['day']].get('note') or backfill[d['day']].get('kind')}"
    use = [d["gb_per_day"] for d in days if not d.get("excluded")]
    if not use:
        return {"gb_per_day": None, "steady_days": 0, "one_time_gb": None}
    rate = _median(use)
    one_time = sum(max(0.0, d["gb"] - rate * d["coverage_h"] / 24) for d in days
                   if (d.get("excluded") or "").startswith("one-time"))
    return {"gb_per_day": round(rate, 4), "steady_days": len(use), "one_time_gb": round(one_time, 3)}


def growth(samples: list[dict], sizes: dict[str, int], now: int, raw_per_day: float | None) -> dict[str, dict]:
    """Naive 7-day mean per area: {"gb_now", "gb_per_day", "gb_30d", "method", "span_days"} (kept as naive_*)."""
    base = None
    for s in samples:               # the sample closest to 7 days ago that is at least 1 day old
        age = now - int(s["ts"])
        if age < MIN_SPAN_S:
            continue
        if base is None or abs(age - WINDOW_S) < abs(now - int(base["ts"]) - WINDOW_S):
            base = s
    out = {}
    for name, size in sizes.items():
        row = {"gb_now": round(size / 1e9, 3), "gb_per_day": None, "gb_30d": None, "method": "insufficient",
               "span_days": None}
        if name == "raw" and raw_per_day is not None:
            row.update(gb_per_day=round(raw_per_day / 1e9, 3), method="raw_dated_dirs", span_days=7)
        elif base is not None and name in base["sizes"]:
            span = (now - int(base["ts"])) / DAY
            per_day = max(0.0, (size - int(base["sizes"][name])) / span) / 1e9
            row.update(gb_per_day=round(per_day, 3), method="samples", span_days=round(span, 2))
        if row["gb_per_day"] is not None:
            row["gb_30d"] = round(row["gb_per_day"] * 30, 2)
        out[name] = row
    return out


def budget_level(gb_30d: float | None) -> str | None:
    if gb_30d is None:
        return None
    if gb_30d > BUDGET_CRIT_GB:
        return "critical"
    if gb_30d > BUDGET_WARN_GB:
        return "warn"
    return "ok"


def status(paths, now: int | None = None, write: bool = True, events: list[dict] | None = None) -> dict:
    """Sizes, steady-state growth and budget status (records an hourly sample when `write`)."""
    now = int(now or time.time())
    sizes = measure(paths)
    samples = record(paths, now, sizes) if write else load_samples(paths)
    raw_per_day, raw_n = raw_daily_bytes(paths, now)
    naive = growth(samples, sizes, now, raw_per_day)
    events = load_events(paths) if events is None else list(events)
    out: dict[str, dict] = {}
    for name, n in naive.items():
        days = raw_days(paths, now) if name == "raw" else sample_days(samples, name, now)
        st = steady(days, name, events)
        row = {"gb_now": n["gb_now"], "naive_gb_per_day": n["gb_per_day"], "naive_gb_30d": n["gb_30d"],
               "naive_method": n["method"], "span_days": n["span_days"], "days": days,
               "one_time_gb": st["one_time_gb"], "steady_days": st["steady_days"]}
        if st["gb_per_day"] is not None:
            partial = bool(days) and not days[-1].get("excluded") and days[-1]["coverage_h"] < 24 and st["steady_days"] == 1
            row.update(gb_per_day=st["gb_per_day"], gb_30d=round(st["gb_per_day"] * 30, 2),
                       method=("raw_dated_dirs" if name == "raw" else "samples") + ("_partial_day" if partial else "_daily_median"))
        else:   # no steady day yet: fall back to the naive 7-day mean (labelled)
            row.update(gb_per_day=n["gb_per_day"], gb_30d=n["gb_30d"],
                       method=("naive_" + n["method"]) if n["gb_per_day"] is not None else "insufficient")
        out[name] = row
    known = [r["gb_30d"] for r in out.values() if r["gb_30d"] is not None]
    unknown = sorted(n for n, r in out.items() if r["gb_30d"] is None and r["gb_now"] > 0)
    total = round(sum(known), 2) if known else None
    naive_known = [r["naive_gb_30d"] for r in out.values() if r["naive_gb_30d"] is not None]
    one_time = [r["one_time_gb"] for r in out.values() if r.get("one_time_gb")]
    return {"generated_at": now, "budget_warn_gb": BUDGET_WARN_GB, "budget_crit_gb": BUDGET_CRIT_GB,
            "total_gb_now": round(sum(sizes.values()) / 1e9, 2), "projected_30d_gb": total,
            "steady_30d_gb": total, "naive_projected_30d_gb": round(sum(naive_known), 2) if naive_known else None,
            "one_time_gb": round(sum(one_time), 3), "one_time_events": [e for e in events if e["day"] >= _day(now - STEADY_DAYS * DAY)],
            "config_changes": {k: {"day": d, "note": note} for k, (d, note) in CONFIG_CHANGES.items()},
            "level": budget_level(total), "unmeasured_areas": unknown, "raw_days_measured": raw_n,
            "areas": out}


# ------------------------------------------------------------------ compaction

def closed_shards(paths, now: int, quiet_s: int = DAY) -> list[Path]:
    """YYYY-MM.db shards of books/ou05/general before the current month, untouched for `quiet_s`."""
    cur = time.strftime("%Y-%m", time.gmtime(now))
    out = []
    for area in SHARD_AREAS:
        d = areas(paths)[area]
        if not d.exists():
            continue
        for p in sorted(d.glob("*.db")):
            m = SHARD_RE.match(p.name)
            if not m or m.group(1) >= cur:
                continue
            mtimes = [p.stat().st_mtime] + [q.stat().st_mtime for q in (p.with_name(p.name + "-wal"),) if q.exists()]
            if now - max(mtimes) < quiet_s:
                continue            # still written (backfills fill old months)
            out.append(p)
    return out


def compact(paths, now: int | None = None, apply: bool = False) -> list[dict]:
    now = int(now or time.time())
    out = []
    for p in closed_shards(paths, now):
        before = size_bytes(p)
        row = {"shard": str(p.relative_to(Path(paths.data))), "mb_before": round(before / 1e6, 1)}
        if apply:
            conn = sqlite3.connect(p, timeout=60)
            try:
                conn.execute("PRAGMA busy_timeout=60000")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
            finally:
                conn.close()
            row["mb_after"] = round(size_bytes(p) / 1e6, 1)
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab storage")
    ap.add_argument("action", nargs="?", default="report", choices=("report", "compact"))
    ap.add_argument("--apply", action="store_true", help="compact: actually VACUUM (default: list only)")
    ap.add_argument("--no-sample", action="store_true", help="report: do not record a size sample (read-only)")
    args = ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    if args.action == "compact":
        print(json.dumps({"apply": args.apply, "shards": compact(p, apply=args.apply)}, indent=1))
        return 0
    print(json.dumps(status(p, write=not args.no_sample), indent=1))
    return 0

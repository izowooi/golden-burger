"""Data growth budget (owner decision 2026-10-05 `storage:budget`: <= 50 GB/month, hard max 100 GB/month).

`polylab storage [report]`          sizes per data area, 7-day growth, 30-day projection (JSON)
`polylab storage compact [--apply]` VACUUM closed-month shards (books/, ou05/, general/) — never deletes anything

Growth per area (core.db, books/, raw/, ou05/, general/, strategies/, manual/, research/):
- raw/: exact, from the dated directories raw/YYYY-MM-DD of the last 7 complete UTC days (/7 per day);
- every other area: size samples kept in <state>/storage_samples.json (at most one per hour, 60 days, written by
  `polylab health`); growth = (now − the sample closest to 7 days ago) / elapsed days, needs >= 1 day of samples.
  Backfills write into old-month shards, so shard names are never used to date growth. One-off backfill growth
  shows up in the first week's projection and is labelled by the caller.
Budget status on the 30-day projection of the summed known areas: warn > 50 GB, critical > 100 GB.
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


def growth(samples: list[dict], sizes: dict[str, int], now: int, raw_per_day: float | None) -> dict[str, dict]:
    """Per area: {"gb_now", "gb_per_day", "gb_30d", "method", "span_days"}."""
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


def status(paths, now: int | None = None, write: bool = True) -> dict:
    """Sizes, growth and budget status (records an hourly sample when `write`)."""
    now = int(now or time.time())
    sizes = measure(paths)
    samples = record(paths, now, sizes) if write else load_samples(paths)
    raw_per_day, raw_days = raw_daily_bytes(paths, now)
    g = growth(samples, sizes, now, raw_per_day)
    known = [r["gb_30d"] for r in g.values() if r["gb_30d"] is not None]
    unknown = sorted(n for n, r in g.items() if r["gb_30d"] is None and r["gb_now"] > 0)
    total = round(sum(known), 2) if known else None
    return {"generated_at": now, "budget_warn_gb": BUDGET_WARN_GB, "budget_crit_gb": BUDGET_CRIT_GB,
            "total_gb_now": round(sum(sizes.values()) / 1e9, 2), "projected_30d_gb": total,
            "level": budget_level(total), "unmeasured_areas": unknown, "raw_days_measured": raw_days,
            "areas": g}


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
    args = ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    if args.action == "compact":
        print(json.dumps({"apply": args.apply, "shards": compact(p, apply=args.apply)}, indent=1))
        return 0
    print(json.dumps(status(p), indent=1))
    return 0

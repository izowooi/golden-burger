"""`polylab analyze <calibration|events|dataset|all> [--since] [--until] [--json]`.

Results are cached under <research_dir>/ (calibration.json, events.json) so the 5-minute
publish job and reports read them instead of recomputing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from polylab import db, settings
from polylab.analysis import _common as C
from polylab.analysis import calibration, dataset, events


def cache_path(paths, name: str) -> Path:
    return Path(paths.research_dir) / f"{name}.json"


def load_cached(paths, name: str) -> dict | None:
    p = cache_path(paths, name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _save(paths, name: str, result: dict) -> Path:
    p = cache_path(paths, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    tmp.replace(p)
    return p


def run(what: str, paths, since: int | None, until: int | None) -> dict:
    conn = db.core(paths, readonly=True)
    out: dict = {}
    try:
        stamp = {"generated_at": C.iso(int(time.time())), "since": C.iso(since), "until": C.iso(until)}
        if what in ("calibration", "all"):
            out["calibration"] = {**stamp, **calibration.run(conn, since, until)}
            _save(paths, "calibration", out["calibration"])
        if what in ("events", "all"):
            out["events"] = {**stamp, **events.run(conn, since, until)}
            _save(paths, "events", out["events"])
        if what in ("dataset", "all"):
            now = int(time.time())
            lo = since if since is not None else now - 2 * 86400
            files = dataset.export(conn, paths, lo, until or now)
            out["dataset"] = {**stamp, "files": [str(f) for f in files]}
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab analyze")
    ap.add_argument("what", choices=["calibration", "events", "dataset", "all"])
    ap.add_argument("--since", help="YYYY-MM-DD, Nd or unix seconds (games starting at/after)")
    ap.add_argument("--until", help="YYYY-MM-DD, Nd or unix seconds")
    ap.add_argument("--json", action="store_true", help="print full JSON result")
    args = ap.parse_args(argv)
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        print(f"analyze: {exc}", file=sys.stderr)
        return 3
    if not paths.core_db.exists():
        print(f"analyze: core db missing at {paths.core_db}", file=sys.stderr)
        return 3
    out = run(args.what, paths, C.parse_since(args.since), C.parse_since(args.until))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        return 0
    if "calibration" in out:
        c = out["calibration"]
        print(f"calibration: {c['observations']} obs / {c['games']} games, {len(c['calibration'])} sport-phase groups")
    if "events" in out:
        e = out["events"]
        print(f"events: detected {e['events_detected']}, measured {e['events_measured']}, "
              f"{len(e['event_sensitivity'])} minute buckets")
    if "dataset" in out:
        print(f"dataset: {len(out['dataset']['files'])} parquet files")
    return 0

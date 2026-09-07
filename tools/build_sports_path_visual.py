#!/usr/bin/env python3
"""Package secret-free local observation exports into bounded inline charts.

Full exports remain the audit source. Only unused display columns are omitted;
observation timestamps, displayed prices, source boundaries and gaps are unchanged.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("sports_path_visual.fragment.html")
LIMIT = 1_000_000


def display_payload(index: dict, root: Path, *, white: bool) -> dict:
    result = {"matches": [], "fragments": {}, "sports": index["sports"]}
    for match in index["matches"]:
        if str(match["source_id"]).startswith("polybot-white:") is not white:
            continue
        path = (root / match["fragment"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("fragment is outside the verified export")
        fragment = json.loads(path.read_text())
        meta = {key: match[key] for key in (
            "id", "event_id", "title", "slug", "sport", "source_id", "cohort_id",
            "start", "end", "point_count", "invalid_count", "gap_count",
        )}
        result["matches"].append(meta)
        clocks = []
        for clock in fragment.get("clocks", []):
            raw = clock.get("source_sport_context", clock)
            raw = raw.get("fields", raw)
            clocks.append({key: raw[key] for key in
                           ("period", "elapsed_raw", "elapsed", "score", "clock")
                           if raw.get(key) is not None})
        result["fragments"][match["id"]] = {
            "tokens": [{key: token.get(key) for key in (
                "label", "result_kind", "outcome_side", "payout", "payout_observed_at",
            )} for token in fragment["tokens"]],
            # Null placeholders preserve the shared column indices without
            # shipping midpoint/best-price columns that these charts don't use.
            "points": [[r[0], r[1], r[2], r[3], None, None, None, r[7], r[8], r[9]]
                       for r in fragment["points"]],
            "gaps": fragment["gaps"], "clocks": clocks,
        }
    return result


def build(index_path: Path, output: Path) -> list[dict]:
    index = json.loads(index_path.read_text())
    template = TEMPLATE.read_text()
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for white, suffix in ((False, "primary"), (True, "white")):
        payload = display_payload(index, index_path.parent, white=white)
        if not payload["matches"]:
            continue
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded = base64.b64encode(gzip.compress(raw.encode(), mtime=0)).decode()
        fragment = template.replace("__SPORTS_DATA_GZIP_BASE64__", encoded)
        fragment = fragment.replace("sports-path-explorer", "sports-path-explorer-" + suffix)
        encoded_fragment = fragment.encode()
        if len(encoded_fragment) >= LIMIT:
            raise ValueError(f"{suffix} exceeds 1 MB; split by source, never silently omit observations")
        path = output / f"sports-path-{suffix}.html"
        path.write_bytes(encoded_fragment)
        manifest.append({"path": str(path.resolve()), "bytes": len(encoded_fragment),
                         "matches": len(payload["matches"]),
                         "observation_rows": sum(len(f["points"]) for f in payload["fragments"].values()),
                         "point_columns_omitted_from_display": ["mid", "best_bid", "best_ask"],
                         "observations_downsampled": False})
    (output / "visual-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.index, args.output), indent=2))

"""Projection-only S diagnostics; no source writes, network, or trade decisions."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from types import ModuleType


def normalize_payload(payload, source, module_path, *, module_source=None):
    # Hash and execute the same bytes: timestamp-based .pyc caching is too coarse
    # for a reproducible derived metric during rapid, same-size source edits.
    code = module_path.read_bytes() if module_source is None else module_source
    math = ModuleType("sports_normalization_math")
    math.__file__ = str(module_path)
    exec(compile(code, str(module_path), "exec"), math.__dict__)
    metadata = [dict(t) for t in payload["tokens"]]
    # The recorder attests two distinct team-win propositions plus draw even
    # where the feed does not prove venue order. Partition names are not venues.
    yes = [t for t in metadata if t.get("outcome_side") == "YES"]
    if source.get("strategy") == "golden-coconut" and payload["match"]["sport"] == "soccer":
        draw = [t for t in yes if t.get("result_kind") == "DRAW"]
        teams = [t for t in yes if t.get("result_kind") != "DRAW"]
        if len(yes) == 3 and len(draw) == 1 and len(teams) == 2:
            draw[0]["partition_role"] = "DRAW"
            for role, token in zip(
                ("TEAM_A", "TEAM_B"), sorted(teams, key=lambda t: t["condition_id"]), strict=True
            ):
                token["partition_role"] = role
    groups = defaultdict(list)
    depth = payload.get("depth") or []
    if len(depth) != len(payload["points"]):
        return {"series": [], "coverage": {"reason": "원본 호가를 포함해 목록을 다시 만드세요."}}
    for index, (point, book) in enumerate(zip(payload["points"], depth, strict=True)):
        groups[book.get("run_id")].append((index, point, book))
    series, reasons, point_groups = [], Counter(), {}
    for run, rows in groups.items():
        observations = []
        for _index, p, d in rows:
            token = metadata[p[1]]
            observations.append(
                {
                    "token_id": token.get("token_id"),
                    "t": p[0],
                    "run_id": run,
                    "book": d.get("book"),
                    "valid": not bool(p[8] & 9),
                    "market_open": d.get("market_open"),
                    "fee_market": d.get("fee_market"),
                    "event_id": payload["match"]["event_id"],
                    "cohort_id": payload["match"]["cohort_id"],
                }
            )
        result = math.summarize_group(metadata, observations, payload["match"]["sport"])
        result["t"] = float(result.get("t") or max(p[0] for _, p, _ in rows))
        result["run_id"] = run
        denominator_rows = [
            r
            for r in rows
            if metadata[r[1][1]].get("outcome_side")
            == ("YES" if payload["match"]["sport"] == "soccer" else "DIRECT")
        ]
        result["timestamp_basis"] = (
            "VERIFIED_RAW_RECEIPT"
            if denominator_rows
            and all(r[2].get("identity_valid") is True for r in denominator_rows)
            else "RECORDED_SNAPSHOT_PROXY"
        )
        result["clock_index"] = max(denominator_rows or rows, key=lambda r: r[1][0])[1][9]
        group_index = len(series)
        for index, _, _ in rows:
            point_groups[str(index)] = group_index
        series.append(result)
        reasons.update(result["reasons"])
    valid = [r for r in series if r["valid"]]
    return {
        "schema": "sports-S-view-v1",
        "algorithm_sha256": hashlib.sha256(code).hexdigest(),
        "common_shares": 5,
        "max_skew_seconds": 2,
        "series": series,
        "point_groups": point_groups,
        "coverage": {
            "groups": len(series),
            "identity_time_valid": len(valid),
            "mid_complete": sum(r["s_mid"] is not None for r in valid),
            "verified_receipt_mid": sum(
                r["s_mid"] is not None and r["timestamp_basis"] == "VERIFIED_RAW_RECEIPT"
                for r in valid
            ),
            "depth_ask_complete": sum(r["s_ask"] is not None for r in valid),
            "depth_bid_complete": sum(r["s_bid"] is not None for r in valid),
            "signal_eligible": sum(r["signal_eligible"] for r in series),
            "cost_evidence_eligible": sum(r["cost_evidence_eligible"] for r in series),
            "reasons": dict(reasons),
        },
        "semantics": (
            "S는서로배타적인YES3/직접2의호가합. p/S는정규화시장가격. "
            "NO는1−정규화YES의이론값. 실제승률·편향·수익을증명하지않음."
        ),
    }

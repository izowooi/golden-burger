from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

from daily_rsync.sports_normalization import normalize_payload

MATH = Path(__file__).parents[2] / "tools/sports_price_normalization.py"


def payload():
    tokens, points, depth = [], [], []
    for role in ("HOME", "DRAW", "AWAY"):
        for side in ("YES", "NO"):
            token = role + side
            index = len(tokens)
            tokens.append(
                {
                    "token_id": token,
                    "condition_id": role,
                    "outcome_side": side,
                    "result_kind": role,
                    "label": token,
                }
            )
            timestamp = 1000 if side == "YES" else 1010
            points.append(
                [timestamp, index, 0.5, 0.49, 0.495, 0.49, 0.5, None, 0, 0 if side == "YES" else 1]
            )
            depth.append(
                {
                    "run_id": "r",
                    "identity_valid": True,
                    "market_open": True,
                    "book": {
                        "token_id": token,
                        "asks": [{"price": ".5", "size": 100}],
                        "bids": [{"price": ".49", "size": 100}],
                    },
                }
            )
    return {
        "tokens": tokens,
        "points": points,
        "depth": depth,
        "match": {"sport": "soccer", "event_id": "e", "cohort_id": "c"},
    }


def test_denominator_clock_does_not_take_later_no_information():
    out = normalize_payload(payload(), {"strategy": "golden-peach"}, MATH)
    row = out["series"][0]
    assert row["t"] == 1000 and row["clock_index"] == 0
    assert row["timestamp_basis"] == "VERIFIED_RAW_RECEIPT"
    assert out["coverage"]["verified_receipt_mid"] == 1
    assert out["coverage"]["cost_evidence_eligible"] == 0


def test_legacy_db_timestamp_is_labeled_as_proxy():
    value = payload()
    for row in value["depth"]:
        row["identity_valid"] = None
    out = normalize_payload(value, {"strategy": "golden-peach"}, MATH)
    assert out["coverage"]["mid_complete"] == 1
    assert out["coverage"]["verified_receipt_mid"] == 0
    assert out["series"][0]["timestamp_basis"] == "RECORDED_SNAPSHOT_PROXY"


def test_same_second_same_size_source_edit_never_uses_old_bytecode(tmp_path):
    path = tmp_path / "math.py"
    code = """def summarize_group(*args, **kwargs):
    return {"valid":True,"signal_eligible":False,"cost_evidence_eligible":False,
            "reasons":[],"t":"1000","s_mid":"1.00","s_ask":None,"s_bid":None}
"""
    path.write_text(code)
    os.utime(path, (1700000000, 1700000000))
    spec = importlib.util.spec_from_file_location("cached_math", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.summarize_group()["s_mid"] == "1.00"
    revised = code.replace("1.00", "1.01")
    path.write_text(revised)
    os.utime(path, (1700000000, 1700000000))
    out = normalize_payload(payload(), {}, path)
    assert out["series"][0]["s_mid"] == "1.01"
    assert out["algorithm_sha256"] == hashlib.sha256(revised.encode()).hexdigest()

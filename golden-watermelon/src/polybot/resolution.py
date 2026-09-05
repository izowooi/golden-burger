"""Terminal payouts bound to immutable token IDs, never response array order."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def terminal_token_payouts(evidence: Mapping[str, Any]) -> dict[str, float]:
    if evidence.get("closed") is not True:
        raise ValueError("terminal source must be explicitly closed")
    rows = evidence.get("token_payouts")
    if rows is None:
        tokens = evidence.get("tokens")
        if not isinstance(tokens, list) or any(
            not isinstance(t, Mapping) or not isinstance(t.get("winner"), bool)
            for t in tokens
        ):
            raise ValueError("terminal token winner evidence missing")
        rows = [
            {"token_id": t.get("token_id"), "payout": float(t["winner"])}
            for t in tokens
        ]
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("terminal evidence must contain exactly two tokens")
    payouts: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid terminal token row")
        token = str(row.get("token_id") or "").strip()
        payout = row.get("payout")
        if not token or token in payouts or isinstance(payout, bool):
            raise ValueError("terminal token identity missing or duplicate")
        if not isinstance(payout, (int, float)) or not math.isfinite(payout):
            raise ValueError("invalid terminal payout")
        payouts[token] = float(payout)
    values = sorted(payouts.values())
    if values == [0.5, 0.5]:
        if (
            evidence.get("resolution_kind") != "VOID"
            or str(evidence.get("uma_resolution_status") or "").lower() != "resolved"
        ):
            raise ValueError("void requires authoritative resolved status")
    elif values != [0.0, 1.0]:
        raise ValueError("terminal payout must be one-hot or authoritative void")
    return payouts


def aligned_winner_index(
    evidence: Mapping[str, Any], expected_tokens: Sequence[str]
) -> int | None:
    payouts = terminal_token_payouts(evidence)
    if len(expected_tokens) != 2 or len(set(expected_tokens)) != 2:
        raise ValueError("entry token identity must be an exact pair")
    if set(expected_tokens) != set(payouts):
        raise ValueError("terminal token set differs from entry token set")
    return next(
        (index for index, token in enumerate(expected_tokens) if payouts[token] == 1.0),
        None,
    )

"""Data API v2 (https://data-api.polymarket.com/v2/...) — public trades, OI, live volume, holders, resolutions.

v1 endpoints (`/trades`, `/oi`, ...) retire on 2026-10-24, so only v2 is used here.
Multi-id params are ONE comma-joined value (`condition_id=a,b`); repeated keys are rejected with 400
"duplicate field" (observed 2026-09-30; the SDK joins with commas too).
Verified facts (docs/research/api-sources.md):
- `/v2/trades?condition_id&taker_only&cursor&limit<=1000` -> {data:[{proxy_wallet, side, token_id, condition_id,
  size, price, timestamp, outcome, outcome_index, transaction_hash, ...}], pagination:{next_cursor,...}}.
  `start`/`end` are IGNORED unless `user` is set, so a market query always walks the full history.
  `taker_only=true` gives one row per fill (use it for volume); false adds the maker-side rows.
- `/v2/oi?condition_id=..(<=20)` -> {data:[{condition_id, value}]}.
- `/v2/live-volume?event_id` -> {data:{taker_volume_total, conditions:[{condition_id, taker_volume}]}}.
- `/v2/holders?condition_id` -> {data:[{token_id, holders:[{proxy_wallet, amount, outcome_index}]}]}.
- `/v2/resolutions?condition_id=..(<=20)` -> {data:[{condition_id, status, payouts?[1e6 units], resolved_at?,
  last_update_timestamp (ISO or epoch str), price, ...}]}. Feb-2026 rows have no `payouts`.
- `/v2/prices-history?token_id&start&end&bucket_seconds>=60` -> {data:[{timestamp, price, resolution_seconds}]}
  (fine grains expire after ~7 days; use CLOB prices-history for older markets).
"""

from __future__ import annotations

from typing import Any, Iterator

from polylab.api.http import DATA_URL, Client, default_client

TRADES_PAGE = 1000
IDS_PER_CALL = 20


def _pages(path: str, params: dict[str, Any], bucket: str, client: Client | None,
           max_pages: int | None = None) -> Iterator[list[dict]]:
    client = client or default_client()
    cursor = None
    pages = 0
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        body = client.get(DATA_URL + path, bucket=bucket, params=p) or {}
        rows = body.get("data") or []
        yield rows if isinstance(rows, list) else [rows]
        pages += 1
        cursor = ((body.get("pagination") or {}).get("next_cursor")) or None
        if not cursor or not rows or (max_pages and pages >= max_pages):
            return


def trades(condition_id: str, *, taker_only: bool = True, client: Client | None = None,
           max_pages: int | None = None) -> Iterator[list[dict]]:
    """Data API `GET /v2/trades?condition_id&taker_only&limit=1000&cursor` — yields pages of trades."""
    params = {"condition_id": condition_id, "taker_only": str(taker_only).lower(), "limit": TRADES_PAGE}
    return _pages("/v2/trades", params, "data_trades", client, max_pages)


def open_interest(condition_ids: list[str], client: Client | None = None) -> dict[str, float]:
    """Data API `GET /v2/oi?condition_id=..` (<=20 per call) -> {condition_id: OI in USDC}."""
    client = client or default_client()
    out: dict[str, float] = {}
    ids = list(dict.fromkeys(condition_ids))
    for i in range(0, len(ids), IDS_PER_CALL):
        body = client.get(DATA_URL + "/v2/oi", bucket="data", params={"condition_id": ",".join(ids[i:i + IDS_PER_CALL])}) or {}
        for row in body.get("data") or []:
            try:
                out[row["condition_id"]] = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def live_volume(event_id: str, client: Client | None = None) -> dict[str, float]:
    """Data API `GET /v2/live-volume?event_id=` -> {condition_id: taker_volume, '_total': total}."""
    client = client or default_client()
    body = client.get(DATA_URL + "/v2/live-volume", bucket="data", params={"event_id": event_id}) or {}
    data = body.get("data") or {}
    out: dict[str, float] = {}
    for row in data.get("conditions") or []:
        try:
            out[row["condition_id"]] = float(row["taker_volume"])
        except (KeyError, TypeError, ValueError):
            continue
    if data.get("taker_volume_total") is not None:
        out["_total"] = float(data["taker_volume_total"])
    return out


def holders(condition_id: str, client: Client | None = None, limit: int = 100) -> list[dict]:
    """Data API `GET /v2/holders?condition_id=` -> [{token_id, holders:[...]}]."""
    client = client or default_client()
    body = client.get(DATA_URL + "/v2/holders", bucket="data",
                      params={"condition_id": condition_id, "limit": limit}) or {}
    return body.get("data") or []


def resolutions(condition_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Data API `GET /v2/resolutions?condition_id=..` (<=20 per call) -> {condition_id: row}."""
    client = client or default_client()
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(condition_ids))
    for i in range(0, len(ids), IDS_PER_CALL):
        body = client.get(DATA_URL + "/v2/resolutions", bucket="data",
                          params={"condition_id": ",".join(ids[i:i + IDS_PER_CALL])}) or {}
        for row in body.get("data") or []:
            if row.get("condition_id"):
                out[row["condition_id"]] = row
    return out


def winner_from_resolution(row: dict) -> int | None:
    """Winning outcome index from a /v2/resolutions row's `payouts` (1e6 units); None when absent/split."""
    payouts = row.get("payouts")
    if row.get("status") != "resolved" or not isinstance(payouts, list) or not payouts:
        return None
    try:
        vals = [float(x) for x in payouts]
    except (TypeError, ValueError):
        return None
    best = max(vals)
    if best <= 0 or vals.count(best) != 1:
        return None                                     # 50-50 / cancelled: no single winner
    return vals.index(best)


def prices_history_v2(token_id: str, start_ts: int, end_ts: int, bucket_seconds: int = 60,
                      client: Client | None = None) -> list[tuple[int, float]]:
    """Data API `GET /v2/prices-history?token_id&start&end&bucket_seconds` -> [(t, p)] (recent data only)."""
    out: list[tuple[int, float]] = []
    params = {"token_id": token_id, "start": int(start_ts), "end": int(end_ts),
              "bucket_seconds": bucket_seconds, "limit": 10000}
    for rows in _pages("/v2/prices-history", params, "data_prices_history", client):
        for r in rows:
            try:
                out.append((int(r["timestamp"]), float(r["price"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out

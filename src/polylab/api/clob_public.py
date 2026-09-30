"""CLOB public market data (https://clob.polymarket.com) — no auth.

Verified facts (docs/research/api-sources.md, probes 2026-09-30):
- `GET /book?token_id=` -> {market, asset_id, timestamp(ms str), hash, bids[{price,size}], asks[...],
  tick_size, min_order_size, neg_risk, last_trade_price}; closed markets 404 "No orderbook exists".
- `POST /books` body `[{"token_id": ..}, ..]` -> list of the same book objects (500 tokens in one call
  returned 500 books in 0.9 s; unknown tokens are silently omitted).
- `GET /prices-history?market=<token>&startTs&endTs&fidelity=1` -> {"history":[{"t","p"}]}, window <= 15 days,
  ~1-minute points kept for months. `p` appears to be the midpoint (one observation).
- `POST /batch-prices-history` body {markets:[<=20 tokens], start_ts, end_ts, fidelity}
  -> {"history":{token:[{t,p}]}}, same 15-day limit.
Bids arrive ascending by price and asks descending (best level is the LAST element); we sort explicitly.
"""

from __future__ import annotations

from typing import Any

from polylab.api.http import CLOB_URL, Client, default_client

MAX_WINDOW_S = 15 * 86400 - 60          # stay strictly inside the 15-day limit
BOOKS_CHUNK = 200
BATCH_HISTORY_MAX = 20


def book(token_id: str, client: Client | None = None) -> dict | None:
    """CLOB `GET /book?token_id=` — None when the market has no orderbook (404)."""
    client = client or default_client()
    return client.get(CLOB_URL + "/book", bucket="clob_book", params={"token_id": token_id}, allow_404=True)


def books(token_ids: list[str], client: Client | None = None, chunk: int = BOOKS_CHUNK) -> dict[str, dict]:
    """CLOB `POST /books` — {token_id: book}; tokens without a book are absent from the result."""
    client = client or default_client()
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(token_ids))
    for i in range(0, len(ids), chunk):
        body = [{"token_id": t} for t in ids[i:i + chunk]]
        rows = client.post(CLOB_URL + "/books", bucket="clob_books", json_body=body, allow_404=True) or []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("asset_id"):
                out[str(row["asset_id"])] = row
    return out


def midpoint(token_id: str, client: Client | None = None) -> float | None:
    """CLOB `GET /midpoint?token_id=` -> {"mid": "0.515"}."""
    client = client or default_client()
    body = client.get(CLOB_URL + "/midpoint", bucket="clob_midpoint", params={"token_id": token_id}, allow_404=True)
    try:
        return float(body["mid"]) if body else None
    except (KeyError, TypeError, ValueError):
        return None


def windows(start_ts: int, end_ts: int, max_window: int = MAX_WINDOW_S) -> list[tuple[int, int]]:
    """Split [start, end] into <=15-day windows (the endpoint rejects longer intervals with 400)."""
    out = []
    s = int(start_ts)
    while s < end_ts:
        e = min(int(end_ts), s + max_window)
        out.append((s, e))
        s = e
    return out


def prices_history(token_id: str, start_ts: int, end_ts: int, fidelity: int = 1,
                   client: Client | None = None) -> list[tuple[int, float]]:
    """CLOB `GET /prices-history?market=<token>&startTs&endTs&fidelity` -> [(t, p)] (windows <= 15 days)."""
    client = client or default_client()
    out: list[tuple[int, float]] = []
    for s, e in windows(start_ts, end_ts):
        body = client.get(CLOB_URL + "/prices-history", bucket="clob_prices_history",
                          params={"market": token_id, "startTs": s, "endTs": e, "fidelity": fidelity}) or {}
        out.extend(_points(body.get("history")))
    return _dedupe(out)


def batch_prices_history(token_ids: list[str], start_ts: int, end_ts: int, fidelity: int = 1,
                         client: Client | None = None) -> dict[str, list[tuple[int, float]]]:
    """CLOB `POST /batch-prices-history` {markets<=20, start_ts, end_ts, fidelity} -> {token: [(t, p)]}."""
    client = client or default_client()
    out: dict[str, list[tuple[int, float]]] = {t: [] for t in token_ids}
    ids = list(dict.fromkeys(token_ids))
    for i in range(0, len(ids), BATCH_HISTORY_MAX):
        chunk = ids[i:i + BATCH_HISTORY_MAX]
        for s, e in windows(start_ts, end_ts):
            body = client.post(CLOB_URL + "/batch-prices-history", bucket="clob_prices_history",
                               json_body={"markets": chunk, "start_ts": s, "end_ts": e, "fidelity": fidelity}) or {}
            hist = body.get("history") or {}
            for tok, pts in hist.items():
                out.setdefault(str(tok), []).extend(_points(pts))
    return {t: _dedupe(v) for t, v in out.items()}


def _points(rows: Any) -> list[tuple[int, float]]:
    out = []
    for r in rows or []:
        try:
            out.append((int(r["t"]), float(r["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _dedupe(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
    seen: dict[int, float] = {}
    for t, p in points:
        seen[t] = p
    return sorted(seen.items())

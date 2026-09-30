"""Gamma API (https://gamma-api.polymarket.com) — events, markets, sports metadata.

Verified facts (docs/research/api-sources.md):
- `/events/keyset` and `/markets/keyset`: `limit` max 100, paginate with `after_cursor=<next_cursor>`.
- Closed markets are excluded from `/markets/keyset` unless `closed=true` (even for `condition_ids=` lookups).
  `/events/keyset?id=` returns closed events without a flag (probed 2026-09-30).
- `clobTokenIds`, `outcomes`, `outcomePrices` are JSON strings; `gameStartTime` is "YYYY-MM-DD HH:MM:SS+00".
- List params are sent as repeated keys (`tag_id=1&tag_id=2`, `sports_market_types=a&sports_market_types=b`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from polylab.api.http import GAMMA_URL, Client, default_client

PAGE_LIMIT = 100


def parse_time(value: Any) -> int | None:
    """Gamma timestamps: ISO 'Z', ISO with micros, 'YYYY-MM-DD HH:MM:SS+00', or epoch (s/ms) -> unix s."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(value).strip()
    if s.isdigit():
        v = int(s)
        return v // 1000 if v > 1e11 else v
    s = s.replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif len(s) >= 3 and s[-3] in "+-" and s[-2:].isdigit() and "T" in s and ":" not in s[-3:]:
        s = s + ":00"                       # '+00' -> '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_list(value: Any) -> list:
    """`clobTokenIds` / `outcomes` / `outcomePrices` arrive as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def _keyset(path: str, key: str, params: dict[str, Any], client: Client | None, bucket: str,
            max_pages: int | None = None, start_cursor: str | None = None) -> Iterator[tuple[list[dict], str | None]]:
    client = client or default_client()
    cursor = start_cursor
    pages = 0
    while True:
        p = dict(params)
        p.setdefault("limit", PAGE_LIMIT)
        if cursor:
            p["after_cursor"] = cursor
        body = client.get(GAMMA_URL + path, bucket=bucket, params=p) or {}
        rows = body.get(key) or []
        cursor = body.get("next_cursor") or None
        yield rows, cursor
        pages += 1
        if not cursor or not rows or (max_pages and pages >= max_pages):
            return


def markets_keyset_pages(params: dict[str, Any], client: Client | None = None, *, max_pages: int | None = None,
                         start_cursor: str | None = None) -> Iterator[tuple[list[dict], str | None]]:
    """Gamma `GET /markets/keyset` — yields (markets, next_cursor) per page (for resumable walks)."""
    return _keyset("/markets/keyset", "markets", params, client, "gamma_markets", max_pages, start_cursor)


def markets_keyset(params: dict[str, Any], client: Client | None = None, *, max_pages: int | None = None) -> list[dict]:
    """Gamma `GET /markets/keyset` — all pages flattened."""
    out: list[dict] = []
    for rows, _ in markets_keyset_pages(params, client, max_pages=max_pages):
        out.extend(rows)
    return out


def events_keyset(params: dict[str, Any], client: Client | None = None, *, max_pages: int | None = None) -> list[dict]:
    """Gamma `GET /events/keyset` — all pages flattened (events embed their markets: heavy)."""
    out: list[dict] = []
    for rows, _ in _keyset("/events/keyset", "events", params, client, "gamma_events", max_pages):
        out.extend(rows)
    return out


def events_by_ids(ids: list[str], client: Client | None = None, chunk: int = 50) -> list[dict]:
    """Gamma `GET /events/keyset?id=..&id=..` — returns open and closed events alike."""
    out: list[dict] = []
    ids = [str(i) for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), chunk):
        out.extend(events_keyset({"id": ids[i:i + chunk]}, client))
    return out


def markets_by_condition_ids(condition_ids: list[str], *, closed: bool, client: Client | None = None,
                             chunk: int = 50) -> list[dict]:
    """Gamma `GET /markets/keyset?condition_ids=..&closed=<bool>` (closed rows need closed=true)."""
    out: list[dict] = []
    ids = list(dict.fromkeys(condition_ids))
    for i in range(0, len(ids), chunk):
        out.extend(markets_keyset({"condition_ids": ids[i:i + chunk], "closed": str(closed).lower()}, client))
    return out


def sports(client: Client | None = None) -> list[dict]:
    """Gamma `GET /sports` — sport code, tag ids (CSV), series id, `ordering` (home|away listed first)."""
    client = client or default_client()
    return client.get(GAMMA_URL + "/sports", bucket="gamma") or []


def series(series_id: str, client: Client | None = None) -> dict | None:
    """Gamma `GET /series/{id}` — league/series title and slug."""
    client = client or default_client()
    return client.get(f"{GAMMA_URL}/series/{series_id}", bucket="gamma", allow_404=True)

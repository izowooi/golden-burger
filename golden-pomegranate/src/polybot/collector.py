"""One-cycle orchestration and normalization for research-full-v1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import time
from typing import Any, Callable
from uuid import uuid4
import zlib

from .api.clob_client import BookCollection, ClobPublicClient
from .api.data_client import DataApiClient, DataTradeCollection
from .api.gamma_client import GammaClient, GammaSweep
from .config import (
    BotConfig,
    DEPTH_NOTIONALS,
    RESEARCH_DATA_CONTRACT,
    assert_no_credentials,
)
from .db.repository import ResearchRepository
from .utils.retry import canonical_json, utc_now


MARKET_OBSERVATION_COLUMNS = (
    "observation_id",
    "sweep_id",
    "run_id",
    "cycle_number",
    "page_number",
    "item_number",
    "page_received_at",
    "page_request_id",
    "source_market_key",
    "condition_id",
    "market_id",
    "event_id",
    "event_slug",
    "market_slug",
    "question",
    "volume_total_raw",
    "volume_total",
    "volume_24h_raw",
    "volume_24h",
    "volume_1h_raw",
    "volume_1h",
    "volume_week_raw",
    "volume_week",
    "volume_month_raw",
    "volume_month",
    "volume_year_raw",
    "volume_year",
    "liquidity_raw",
    "liquidity",
    "liquidity_variants_json",
    "outcome_prices_json",
    "best_bid",
    "best_ask",
    "spread",
    "last_trade_price",
    "price_changes_json",
    "start_date",
    "end_date",
    "game_start_time",
    "created_at_source",
    "updated_at_source",
    "tags_json",
    "sports_json",
    "category",
    "active",
    "closed",
    "enable_order_book",
    "accepting_orders",
    "neg_risk",
    "fees_enabled",
    "fee_metadata_json",
    "tick_size_raw",
    "min_order_size_raw",
    "source_clocks_json",
    "parse_quality_json",
    "raw_market_sha256",
)


def _array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _raw(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return canonical_json(value)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _quality_raw_preview(value: Any) -> str | None:
    raw = _raw(value)
    if raw is None:
        return None
    return raw if len(raw) <= 256 else f"{raw[:253]}..."


def _record_parse_failure(
    failures: dict[str, dict[str, Any]],
    field: str,
    value: Any,
    reason: str,
) -> None:
    failures[field] = {
        "reason": reason,
        "raw_preview": _quality_raw_preview(value),
        "source_type": type(value).__name__,
    }


def _finite_with_quality(
    value: Any,
    *,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        _record_parse_failure(failures, field, value, "boolean_is_not_numeric")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _record_parse_failure(failures, field, value, "invalid_numeric")
        return None
    if not math.isfinite(number):
        _record_parse_failure(failures, field, value, "non_finite_numeric")
        return None
    return number


def _boolean_with_quality(
    value: Any,
    *,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    _record_parse_failure(failures, field, value, "invalid_boolean")
    return None


def _array_with_quality(
    value: Any,
    *,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            _record_parse_failure(failures, field, value, "invalid_json_array")
            return []
        if isinstance(parsed, list):
            return parsed
        _record_parse_failure(failures, field, value, "json_value_is_not_array")
        return []
    _record_parse_failure(failures, field, value, "invalid_array_type")
    return []


def _integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _boolean(value: Any) -> int | None:
    return int(value) if isinstance(value, bool) else None


def _first(market: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in market and market[key] is not None:
            return market[key]
    return None


def _source_market_key(market: Mapping[str, Any], raw_sha: str) -> str:
    value = _first(market, "conditionId", "condition_id", "id", "marketId", "slug")
    return (
        str(value).strip()
        if value is not None and str(value).strip()
        else f"raw:{raw_sha}"
    )


def _event(market: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = market.get("event")
    if isinstance(direct, Mapping):
        return direct
    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], Mapping):
        return events[0]
    return {}


def _market_bundle(
    sweep: GammaSweep, *, run_id: str, cycle_number: int
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    metadata_versions: list[dict[str, Any]] = []
    duplicate_counts: dict[str, int] = {}
    unique_conditions: set[str] = set()
    missing_condition = 0

    for ordinal, market in enumerate(sweep.markets):
        parse_failures: dict[str, dict[str, Any]] = {}
        page_number = int(market.pop("_page_number"))
        item_number = int(market.pop("_item_number"))
        page_received_at = str(market.pop("_page_received_at"))
        page_request_id = str(market.pop("_page_request_id"))
        raw_json = canonical_json(market)
        raw_sha = hashlib.sha256(raw_json.encode()).hexdigest()
        source_key = _source_market_key(market, raw_sha)
        duplicate_ordinal = duplicate_counts.get(source_key, 0)
        duplicate_counts[source_key] = duplicate_ordinal + 1
        condition = _first(market, "conditionId", "condition_id")
        condition_id = str(condition) if condition is not None else None
        if condition_id:
            unique_conditions.add(condition_id)
        else:
            missing_condition += 1
        market_id_value = _first(market, "id", "marketId", "market_id")
        market_id = str(market_id_value) if market_id_value is not None else None
        event = _event(market)
        event_id_value = _first(event, "id", "eventId", "event_id") or _first(
            market, "eventId", "event_id"
        )
        event_id = str(event_id_value) if event_id_value is not None else None
        event_slug = _first(event, "slug") or _first(market, "eventSlug")
        labels = _array_with_quality(
            _first(market, "outcomes"),
            field="outcomes",
            failures=parse_failures,
        )
        token_ids = _array_with_quality(
            _first(market, "clobTokenIds", "clob_token_ids"),
            field="clobTokenIds",
            failures=parse_failures,
        )
        prices = _array_with_quality(
            _first(market, "outcomePrices", "outcome_prices"),
            field="outcomePrices",
            failures=parse_failures,
        )
        parsed_outcome_prices = [
            _finite_with_quality(
                price,
                field=f"outcomePrices[{index}]",
                failures=parse_failures,
            )
            for index, price in enumerate(prices)
        ]
        observation_id = str(uuid4())

        volume_keys = {
            "total": ("volume", "volumeNum", "volumeClob"),
            "24h": ("volume24hr", "volume24h", "volume24hrClob"),
            "1h": ("volume1hr", "volume1h"),
            "week": ("volume1wk", "volumeWeek", "volume1wkClob"),
            "month": ("volume1mo", "volumeMonth", "volume1moClob"),
            "year": ("volume1yr", "volumeYear", "volume1yrClob"),
        }
        volume_values = {
            key: _first(market, *aliases) for key, aliases in volume_keys.items()
        }
        liquidity_value = _first(market, "liquidity", "liquidityNum")
        liquidity_variants = {
            key: value for key, value in market.items() if "liquidity" in key.lower()
        }
        price_changes = {
            key: value
            for key, value in market.items()
            if "pricechange" in key.lower()
            or key in {"oneDayPriceChange", "oneHourPriceChange"}
        }
        tags = _array_with_quality(
            market.get("tags", event.get("tags", [])),
            field="tags",
            failures=parse_failures,
        )
        sports = {
            key: value
            for key, value in {**event, **market}.items()
            if any(part in key.lower() for part in ("sport", "league", "game"))
        }
        fee_metadata = {
            key: value
            for key, value in market.items()
            if "fee" in key.lower() or "rebate" in key.lower()
        }
        source_clocks = {
            key: value
            for key, value in market.items()
            if key.lower().endswith(("at", "time", "timestamp"))
        }
        observation = {
            "observation_id": observation_id,
            "sweep_id": sweep.sweep_id,
            "run_id": run_id,
            "cycle_number": cycle_number,
            "page_number": page_number,
            "item_number": item_number,
            "page_received_at": page_received_at,
            "page_request_id": page_request_id,
            "source_market_key": source_key,
            "condition_id": condition_id,
            "market_id": market_id,
            "event_id": event_id,
            "event_slug": _raw(event_slug),
            "market_slug": _raw(_first(market, "slug", "marketSlug")),
            "question": _raw(_first(market, "question", "title")),
            "volume_total_raw": _raw(volume_values["total"]),
            "volume_total": _finite_with_quality(
                volume_values["total"], field="volume", failures=parse_failures
            ),
            "volume_24h_raw": _raw(volume_values["24h"]),
            "volume_24h": _finite_with_quality(
                volume_values["24h"], field="volume24h", failures=parse_failures
            ),
            "volume_1h_raw": _raw(volume_values["1h"]),
            "volume_1h": _finite_with_quality(
                volume_values["1h"], field="volume1h", failures=parse_failures
            ),
            "volume_week_raw": _raw(volume_values["week"]),
            "volume_week": _finite_with_quality(
                volume_values["week"], field="volumeWeek", failures=parse_failures
            ),
            "volume_month_raw": _raw(volume_values["month"]),
            "volume_month": _finite_with_quality(
                volume_values["month"], field="volumeMonth", failures=parse_failures
            ),
            "volume_year_raw": _raw(volume_values["year"]),
            "volume_year": _finite_with_quality(
                volume_values["year"], field="volumeYear", failures=parse_failures
            ),
            "liquidity_raw": _raw(liquidity_value),
            "liquidity": _finite_with_quality(
                liquidity_value, field="liquidity", failures=parse_failures
            ),
            "liquidity_variants_json": canonical_json(liquidity_variants),
            "outcome_prices_json": canonical_json(prices),
            "best_bid": _finite_with_quality(
                _first(market, "bestBid", "best_bid"),
                field="bestBid",
                failures=parse_failures,
            ),
            "best_ask": _finite_with_quality(
                _first(market, "bestAsk", "best_ask"),
                field="bestAsk",
                failures=parse_failures,
            ),
            "spread": _finite_with_quality(
                _first(market, "spread"), field="spread", failures=parse_failures
            ),
            "last_trade_price": _finite_with_quality(
                _first(market, "lastTradePrice", "last_trade_price"),
                field="lastTradePrice",
                failures=parse_failures,
            ),
            "price_changes_json": canonical_json(price_changes),
            "start_date": _raw(_first(market, "startDate", "start_date")),
            "end_date": _raw(_first(market, "endDate", "end_date", "endDateIso")),
            "game_start_time": _raw(_first(market, "gameStartTime", "game_start_time")),
            "created_at_source": _raw(_first(market, "createdAt", "created_at")),
            "updated_at_source": _raw(_first(market, "updatedAt", "updated_at")),
            "tags_json": canonical_json(tags),
            "sports_json": canonical_json(sports),
            "category": _raw(_first(market, "category") or event.get("category")),
            "active": _boolean_with_quality(
                market.get("active"), field="active", failures=parse_failures
            ),
            "closed": _boolean_with_quality(
                market.get("closed"), field="closed", failures=parse_failures
            ),
            "enable_order_book": _boolean_with_quality(
                _first(market, "enableOrderBook", "enable_order_book"),
                field="enableOrderBook",
                failures=parse_failures,
            ),
            "accepting_orders": _boolean_with_quality(
                _first(market, "acceptingOrders", "accepting_orders"),
                field="acceptingOrders",
                failures=parse_failures,
            ),
            "neg_risk": _boolean_with_quality(
                _first(market, "negRisk", "neg_risk"),
                field="negRisk",
                failures=parse_failures,
            ),
            "fees_enabled": _boolean_with_quality(
                _first(market, "feesEnabled", "fees_enabled"),
                field="feesEnabled",
                failures=parse_failures,
            ),
            "fee_metadata_json": canonical_json(fee_metadata),
            "tick_size_raw": _raw(
                _first(market, "orderPriceMinTickSize", "minimumTickSize", "tickSize")
            ),
            "min_order_size_raw": _raw(
                _first(market, "orderMinSize", "minimumOrderSize", "minOrderSize")
            ),
            "source_clocks_json": canonical_json(source_clocks),
            "parse_quality_json": canonical_json(parse_failures),
            "raw_market_sha256": raw_sha,
            # Ephemeral validation input. Repository columns deliberately omit
            # this duplicate because the exact page zlib + item ordinal is truth.
            "_raw_market_json": raw_json,
        }
        observations.append(observation)
        memberships.append(
            {
                "membership_id": str(uuid4()),
                "sweep_id": sweep.sweep_id,
                "observation_id": observation_id,
                "membership_ordinal": ordinal,
                "page_number": page_number,
                "item_number": item_number,
                "page_received_at": page_received_at,
                "source_market_key": source_key,
                "condition_id": condition_id,
                "market_id": market_id,
                "event_id": event_id,
                "raw_market_sha256": raw_sha,
                "duplicate_ordinal": duplicate_ordinal,
            }
        )
        for outcome_index in range(max(len(labels), len(token_ids), len(prices))):
            label = labels[outcome_index] if outcome_index < len(labels) else None
            token_id = (
                token_ids[outcome_index] if outcome_index < len(token_ids) else None
            )
            price = prices[outcome_index] if outcome_index < len(prices) else None
            parsed_price = (
                parsed_outcome_prices[outcome_index]
                if outcome_index < len(parsed_outcome_prices)
                else None
            )
            outcomes.append(
                {
                    "outcome_observation_id": str(uuid4()),
                    "observation_id": observation_id,
                    "sweep_id": sweep.sweep_id,
                    "outcome_index": outcome_index,
                    "outcome_label": _raw(label),
                    "token_id": _raw(token_id),
                    "price_raw": _raw(price),
                    "price": parsed_price,
                    "label_present": int(outcome_index < len(labels)),
                    "token_present": int(outcome_index < len(token_ids)),
                    "price_present": int(outcome_index < len(prices)),
                }
            )
        metadata = {
            "condition_id": condition_id,
            "market_id": market_id,
            "event_id": event_id,
            "event_slug": event_slug,
            "market_slug": _first(market, "slug", "marketSlug"),
            "question": _first(market, "question", "title"),
            "outcomes": labels,
            "clob_token_ids": token_ids,
            "tags": tags,
            "category": observation["category"],
            "sports": sports,
            "start_date": observation["start_date"],
            "end_date": observation["end_date"],
            "game_start_time": observation["game_start_time"],
            "fee_metadata": fee_metadata,
            "tick_size": observation["tick_size_raw"],
            "min_order_size": observation["min_order_size_raw"],
        }
        metadata_json = canonical_json(metadata)
        metadata_versions.append(
            {
                "metadata_version_id": str(uuid4()),
                "source_market_key": source_key,
                "condition_id": condition_id,
                "market_id": market_id,
                "content_sha256": hashlib.sha256(metadata_json.encode()).hexdigest(),
                "metadata_json": metadata_json,
                "first_observed_sweep_id": sweep.sweep_id,
                "first_observed_at": page_received_at,
            }
        )

    digest_scope = [
        {
            "ordinal": row["membership_ordinal"],
            "page": row["page_number"],
            "item": row["item_number"],
            "key": row["source_market_key"],
            "raw_sha256": row["raw_market_sha256"],
        }
        for row in memberships
    ]
    digest = hashlib.sha256(canonical_json(digest_scope).encode()).hexdigest()
    market_sweep = {
        "sweep_id": sweep.sweep_id,
        "run_id": run_id,
        "cycle_number": cycle_number,
        "started_at": sweep.started_at,
        "completed_at": sweep.completed_at,
        "cursor_complete": 1,
        "page_count": len(sweep.pages),
        "raw_market_count": len(observations),
        "unique_condition_count": len(unique_conditions),
        "missing_condition_id_count": missing_condition,
        "duplicate_condition_count": sum(
            max(0, count - 1) for count in duplicate_counts.values()
        ),
        "request_attestation_json": sweep.request_attestation_json,
        "request_attestation_sha256": sweep.request_attestation_sha256,
        "membership_digest_sha256": digest,
        "raw_payload_page_count": sum(
            page.raw_payload_id is not None for page in sweep.pages
        ),
        "data_contract": RESEARCH_DATA_CONTRACT,
    }
    return {
        "market_sweep": market_sweep,
        "market_observations": observations,
        "market_memberships": memberships,
        "outcome_observations": outcomes,
        "metadata_versions": metadata_versions,
        "watchlist_additions": [],
    }


def _levels(raw: Any, *, reverse: bool) -> list[tuple[float, float, str, str]]:
    if not isinstance(raw, list):
        return []
    parsed: list[tuple[float, float, str, str]] = []
    for level in raw:
        if not isinstance(level, Mapping):
            continue
        price_value, size_value = level.get("price"), level.get("size")
        price, size = _finite(price_value), _finite(size_value)
        if price is None or size is None or not 0 <= price <= 1 or size < 0:
            continue
        parsed.append((price, size, _raw(price_value) or "", _raw(size_value) or ""))
    return sorted(parsed, key=lambda row: row[0], reverse=reverse)


def _depth_metric(
    levels: Sequence[tuple[float, float, str, str]], target: float
) -> tuple[float, float, float | None, float | None, int, int]:
    remaining = target
    quote = 0.0
    base = 0.0
    worst = None
    consumed = 0
    for price, size, _, _ in levels:
        available_quote = price * size
        take_quote = min(remaining, available_quote)
        if price > 0:
            base += take_quote / price
        quote += take_quote
        remaining -= take_quote
        worst = price
        consumed += 1
        if remaining <= 1e-12:
            break
    vwap = quote / base if base > 0 else None
    return quote, base, vwap, worst, int(remaining <= 1e-12), consumed


def _book_bundle(
    collection: BookCollection,
    *,
    run_id: str,
    cycle_number: int,
    normalized_levels: int,
) -> dict[str, Any]:
    selections = [
        {
            **dict(row),
            "run_id": run_id,
            "cycle_number": cycle_number,
            "collection_id": collection.collection_id,
            "token_ids_json": canonical_json(row.get("token_ids", [])),
            "outcome_labels_json": canonical_json(row.get("outcome_labels", [])),
            "selected_at": collection.started_at,
        }
        for row in collection.selections
    ]
    snapshots: list[dict[str, Any]] = []
    level_rows: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for item in collection.books:
        raw = item["raw_book"]
        bids = _levels(raw.get("bids"), reverse=True)
        asks = _levels(raw.get("asks"), reverse=False)
        snapshot_id = str(uuid4())
        raw_json = canonical_json(raw)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        snapshots.append(
            {
                "snapshot_id": snapshot_id,
                "selection_id": item["selection_id"],
                "run_id": run_id,
                "cycle_number": cycle_number,
                "token_id": item["token_id"],
                "received_at": item["received_at"],
                "request_id": item["request_id"],
                "raw_payload_id": item["raw_payload_id"],
                "source_timestamp": _raw(raw.get("timestamp")),
                "source_hash": _raw(raw.get("hash")),
                "market": _raw(raw.get("market")),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid
                if best_ask is not None and best_bid is not None
                else None,
                "last_trade_price": _finite(raw.get("last_trade_price")),
                "tick_size_raw": _raw(raw.get("tick_size")),
                "min_order_size_raw": _raw(raw.get("min_order_size")),
                "neg_risk": _boolean(raw.get("neg_risk")),
                "raw_book_sha256": hashlib.sha256(raw_json.encode()).hexdigest(),
            }
        )
        for side, levels in (("BID", bids), ("ASK", asks)):
            for level_index, (price, size, price_raw, size_raw) in enumerate(
                levels[:normalized_levels]
            ):
                level_rows.append(
                    {
                        "level_id": str(uuid4()),
                        "snapshot_id": snapshot_id,
                        "side": side,
                        "level_index": level_index,
                        "price_raw": price_raw,
                        "price": price,
                        "size_raw": size_raw,
                        "size": size,
                    }
                )
        for side, levels in (("BUY", asks), ("SELL", bids)):
            for target in DEPTH_NOTIONALS:
                quote, base, vwap, worst, complete, consumed = _depth_metric(
                    levels, target
                )
                metrics.append(
                    {
                        "metric_id": str(uuid4()),
                        "snapshot_id": snapshot_id,
                        "side": side,
                        "target_notional": target,
                        "filled_notional": quote,
                        "base_quantity": base,
                        "vwap_price": vwap,
                        "worst_price": worst,
                        "complete": complete,
                        "levels_consumed": consumed,
                    }
                )
    return {
        "orderbook_selections": selections,
        "orderbook_token_attempts": [
            {
                **dict(row),
                "run_id": run_id,
                "cycle_number": cycle_number,
            }
            for row in collection.token_attempts
        ],
        "orderbook_snapshots": snapshots,
        "orderbook_levels": level_rows,
        "orderbook_depth_metrics": metrics,
    }


def _resolution_bundle(
    rows: Sequence[Mapping[str, Any]], *, run_id: str, cycle_number: int
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        market = row.get("raw_market")
        raw_json = canonical_json(market) if isinstance(market, Mapping) else None
        prices = (
            _array(market.get("outcomePrices")) if isinstance(market, Mapping) else []
        )
        labels = _array(market.get("outcomes")) if isinstance(market, Mapping) else []
        numeric = [_finite(value) for value in prices]
        hot = [index for index, value in enumerate(numeric) if value == 1.0]
        one_hot = (
            bool(numeric)
            and len(hot) == 1
            and all(value in {0.0, 1.0} for value in numeric)
        )
        winner_index = hot[0] if one_hot else None
        resolution_value = None
        redeemable = None
        market_id = None
        closed = None
        if isinstance(market, Mapping):
            # ``resolutionSource`` describes provenance (URL/text), not the
            # winning outcome. Keeping the two separate prevents source text
            # from being misclassified as a resolution value.
            resolution_value = _first(
                market, "resolution", "resolvedOutcome", "winningOutcome"
            )
            redeemable = _boolean(market.get("redeemable"))
            closed = _boolean(market.get("closed"))
            market_id = _first(market, "id", "marketId")
        result.append(
            {
                "resolution_observation_id": str(uuid4()),
                "run_id": run_id,
                "cycle_number": cycle_number,
                "condition_id": row["condition_id"],
                "requested_at": row["requested_at"],
                "observed_at": row["observed_at"],
                "lookup_status": row["lookup_status"],
                "request_id": row.get("request_id"),
                "market_id": _raw(market_id),
                "resolved": _boolean(market.get("resolved"))
                if isinstance(market, Mapping)
                else None,
                "closed": closed,
                "one_hot": int(one_hot) if market is not None else None,
                "one_hot_outcome_index": winner_index,
                "one_hot_outcome_label": _raw(labels[winner_index])
                if winner_index is not None and winner_index < len(labels)
                else None,
                "resolution_value_raw": _raw(resolution_value),
                "resolution_source_raw": _raw(market.get("resolutionSource"))
                if isinstance(market, Mapping)
                else None,
                "redeemable": redeemable,
                "source_updated_at": _raw(_first(market, "updatedAt", "updated_at"))
                if isinstance(market, Mapping)
                else None,
                "source_end_date": _raw(_first(market, "endDate", "end_date"))
                if isinstance(market, Mapping)
                else None,
                "outcome_prices_json": canonical_json(prices),
                "raw_market_sha256": hashlib.sha256(raw_json.encode()).hexdigest()
                if raw_json
                else None,
                "raw_market_json": raw_json,
                "error_type": row.get("error_type"),
                "error_message": row.get("error_message"),
            }
        )
    return result


def _trade_bundle(
    collection: DataTradeCollection, *, run_id: str, cycle_number: int
) -> dict[str, Any]:
    observations = []
    timestamps = []
    for trade in collection.trades:
        timestamp_raw = _raw(trade.get("timestamp"))
        timestamp_epoch = _finite(trade.get("timestamp"))
        if timestamp_raw is not None:
            timestamps.append(
                (
                    timestamp_epoch if timestamp_epoch is not None else float("nan"),
                    timestamp_raw,
                )
            )
        observations.append(
            {
                "trade_id": trade["trade_id"],
                "economic_row_hash": trade["economic_row_hash"],
                "occurrence_index": trade["occurrence_index"],
                "side": _raw(trade.get("side")),
                "asset": _raw(trade.get("asset")),
                "condition_id": _raw(trade.get("conditionId")),
                "size_raw": _raw(trade.get("size")),
                "size": _finite(trade.get("size")),
                "price_raw": _raw(trade.get("price")),
                "price": _finite(trade.get("price")),
                "timestamp_raw": timestamp_raw,
                "timestamp_epoch": timestamp_epoch,
                "transaction_hash": _raw(trade.get("transactionHash")),
                "proxy_wallet": _raw(trade.get("proxyWallet")),
                "outcome": _raw(trade.get("outcome")),
                "outcome_index_raw": _raw(trade.get("outcomeIndex")),
                "outcome_index": _integer(trade.get("outcomeIndex")),
                "sanitized_trade_json": canonical_json(
                    {
                        key: value
                        for key, value in trade.items()
                        if key
                        not in {
                            "trade_id",
                            "economic_row_hash",
                            "occurrence_index",
                            "first_received_at",
                        }
                    }
                ),
                "first_received_at": trade["first_received_at"],
            }
        )
    finite_timestamps = [
        (value, raw) for value, raw in timestamps if math.isfinite(value)
    ]
    head = max(finite_timestamps)[1] if finite_timestamps else None
    tail = min(finite_timestamps)[1] if finite_timestamps else None
    membership_digest = hashlib.sha256(
        canonical_json(
            [
                {
                    "window_id": row["window_id"],
                    "item": row["item_number"],
                    "trade_id": row["trade_id"],
                }
                for row in collection.memberships
            ]
        ).encode()
    ).hexdigest()
    sweep = {
        "trade_sweep_id": collection.collection_id,
        "run_id": run_id,
        "cycle_number": cycle_number,
        "started_at": collection.started_at,
        "completed_at": collection.completed_at,
        "target_start_epoch": collection.target_start_epoch,
        "source_target_end_epoch": collection.source_target_end_epoch,
        "bounded_target_end_epoch": collection.bounded_target_end_epoch,
        "watermark_before_epoch": collection.watermark_before_epoch,
        "watermark_advance_to_epoch": collection.watermark_advance_to_epoch,
        "status": collection.status,
        "possible_gap": int(collection.possible_gap),
        "window_count": len(collection.windows),
        "membership_count": len(collection.memberships),
        "unique_trade_count": len(collection.trades),
        "head_timestamp_raw": head,
        "tail_timestamp_raw": tail,
        "membership_digest_sha256": membership_digest,
        "error_message": collection.error_message,
    }
    windows = [
        {
            **dict(row),
            "trade_sweep_id": collection.collection_id,
        }
        for row in collection.windows
    ]
    memberships = [
        {**dict(row), "trade_sweep_id": collection.collection_id}
        for row in collection.memberships
    ]
    return {
        "trade_sweep": sweep,
        "trade_windows": windows,
        "trade_observations": observations,
        "trade_memberships": memberships,
    }


class ResearchCollector:
    """Collect primary census and independent secondary evidence components."""

    def __init__(
        self,
        config: BotConfig,
        repository: ResearchRepository | None = None,
        gamma: GammaClient | None = None,
        clob: ClobPublicClient | None = None,
        data: DataApiClient | None = None,
        *,
        now_epoch: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.repository = repository or ResearchRepository(
            config.db_path, busy_timeout_ms=config.trading.storage.busy_timeout_ms
        )
        self._staged_gamma_payloads: list[dict[str, Any]] = []
        self.gamma = gamma or GammaClient(
            config.trading.gamma,
            evidence_sink=self.repository.record_api_request,
            raw_payload_sink=self._stage_gamma_payload,
            raw_payload_every_cycles=config.trading.storage.raw_payload_every_cycles,
        )
        self.clob = clob or ClobPublicClient(
            config.trading.orderbook,
            evidence_sink=self.repository.record_api_request,
            raw_payload_sink=self.repository.record_raw_payload,
        )
        self.data = data or DataApiClient(
            config.trading.data_api,
            config.trading.gamma,
            evidence_sink=self.repository.record_api_request,
            raw_payload_sink=self.repository.record_raw_payload,
        )
        self.now_epoch = now_epoch

    def _stage_gamma_payload(
        self, *, request_id: str, kind: str, content: bytes, store_blob: bool
    ) -> str:
        exact = bytes(content)
        compressed = zlib.compress(exact, level=6) if store_blob else None
        payload_id = str(uuid4())
        self._staged_gamma_payloads.append(
            {
                "payload_id": payload_id,
                "request_id": request_id,
                "payload_kind": kind,
                "content_encoding": "zlib",
                "payload_sha256": hashlib.sha256(exact).hexdigest(),
                "uncompressed_bytes": len(exact),
                "compressed_bytes": len(compressed) if compressed is not None else None,
                "blob_stored": int(store_blob),
                "payload_blob": compressed,
                "recorded_at": utc_now(),
            }
        )
        return payload_id

    @staticmethod
    def _component(
        run_id: str,
        cycle: int,
        name: str,
        status: str,
        started: str,
        completed: str,
        *,
        requested: int | None,
        observed: int | None,
        errors: int = 0,
        possible_gap: bool = False,
        details: Mapping[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "component_run_id": str(uuid4()),
            "run_id": run_id,
            "cycle_number": cycle,
            "component": name,
            "status": status,
            "started_at": started,
            "completed_at": completed,
            "requested_count": requested,
            "observed_count": observed,
            "error_count": errors,
            "possible_gap": int(possible_gap),
            "details_json": canonical_json(dict(details or {})),
            "error_message": error_message,
        }

    def run_cycle(self, run_id: str) -> dict[str, Any]:
        assert_no_credentials()
        if self.config.trading.lifecycle_mode != "archive_only":
            raise RuntimeError("research collector must remain archive_only")
        cycle = self.repository.next_cycle_number()
        cycle_now_epoch = int(self.now_epoch())
        prior_census = self.repository.latest_census_conditions()
        self._staged_gamma_payloads.clear()
        try:
            sweep = self.gamma.fetch_complete_sweep(cycle_number=cycle, run_id=run_id)
        except Exception as error:
            self._staged_gamma_payloads.clear()
            self.repository.record_quality_issue(
                component="gamma_census",
                severity="CRITICAL",
                issue_code="incomplete_cursor_sweep",
                details={"error_type": type(error).__name__, "error": str(error)[:500]},
                run_id=run_id,
                cycle_number=cycle,
            )
            raise
        market = _market_bundle(sweep, run_id=run_id, cycle_number=cycle)
        raw_markets = sweep.markets
        current_conditions = {
            str(row["condition_id"])
            for row in market["market_memberships"]
            if row.get("condition_id")
        }
        disappeared_ids = sorted(set(prior_census) - current_conditions)
        market["watchlist_additions"] = [
            {
                "condition_id": condition_id,
                "market_id": prior_census[condition_id].get("market_id"),
                "source_market_key": prior_census[condition_id]["source_market_key"],
                "first_seen_sweep_id": prior_census[condition_id]["prior_sweep_id"],
                "first_seen_at": sweep.completed_at,
                "selection_reason": "DISAPPEARED_FROM_NON_CLOSED",
                "carried_from_utc_date": None,
                "prior_state_json": None,
                "terminal": 0,
            }
            for condition_id in disappeared_ids
        ]

        gamma_component = self._component(
            run_id,
            cycle,
            "gamma_census",
            "SUCCESS",
            sweep.started_at,
            sweep.completed_at,
            requested=len(raw_markets),
            observed=len(raw_markets),
            details={"pages": len(sweep.pages), "cursor_complete": True},
        )
        primary_quality: list[dict[str, Any]] = []
        if not prior_census:
            primary_quality.append(
                {
                    "issue_id": str(uuid4()),
                    "run_id": run_id,
                    "cycle_number": cycle,
                    "component": "resolution_watchlist",
                    "severity": "MEDIUM",
                    "issue_code": "pre_start_closed_history_coverage_gap",
                    "observed_at": utc_now(),
                    "details_json": canonical_json(
                        {
                            "baseline_available": False,
                            "scope": "closed_before_first_census",
                        }
                    ),
                }
            )
        # The complete Gamma census is the irreplaceable primary evidence.  It
        # is committed before slower/best-effort secondary sources so a timeout
        # cannot discard a successfully completed keyset walk.
        self.repository.publish_gamma_census(
            {
                "run_id": run_id,
                "cycle_number": cycle,
                "components": [gamma_component],
                "raw_payloads": list(self._staged_gamma_payloads),
                "market_observation_columns": MARKET_OBSERVATION_COLUMNS,
                **market,
                "quality_issues": primary_quality,
            }
        )

        sampler_slot = cycle_now_epoch // (self.config.trading.cadence_minutes * 60)
        book_started = utc_now()
        book_selections: list[dict[str, Any]] = []
        book_unexpected_error: str | None = None
        try:
            book_selections = self.clob.select_rotating_sample(
                raw_markets,
                cycle_number=cycle,
                sampler_slot=sampler_slot,
            )
            books = self.clob.fetch_books(
                book_selections, cycle_number=cycle, run_id=run_id
            )
            books_bundle = _book_bundle(
                books,
                run_id=run_id,
                cycle_number=cycle,
                normalized_levels=self.config.trading.orderbook.normalized_levels,
            )
        except Exception as error:
            book_unexpected_error = (
                f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
            )
            collection_id = str(uuid4())
            fallback_selections: list[dict[str, Any]] = []
            fallback_attempts: list[dict[str, Any]] = []
            for selection in book_selections:
                copied = dict(selection)
                copied.update(
                    {
                        "observed_token_count": 0,
                        "coverage_ratio": 0.0,
                        "status": "ERROR",
                        "error_message": book_unexpected_error,
                    }
                )
                fallback_selections.append(copied)
                outcome_by_token = {
                    str(item.get("token_id")): item
                    for item in copied.get("token_outcomes", [])
                    if isinstance(item, Mapping) and item.get("token_id") is not None
                }
                for token in copied.get("token_ids", []):
                    token_id = str(token)
                    outcome = outcome_by_token.get(token_id, {})
                    fallback_attempts.append(
                        {
                            "token_attempt_id": str(uuid4()),
                            "selection_id": copied["selection_id"],
                            "collection_id": collection_id,
                            "token_id": token_id,
                            "outcome_index": outcome.get("outcome_index"),
                            "outcome_label": outcome.get("outcome_label"),
                            "status": "ERROR",
                            "request_id": None,
                            "raw_payload_id": None,
                            "received_at": utc_now(),
                            "bid_level_count": 0,
                            "ask_level_count": 0,
                            "error_type": type(error).__name__,
                            "error_message": book_unexpected_error,
                        }
                    )
            books = BookCollection(
                collection_id=collection_id,
                started_at=book_started,
                completed_at=utc_now(),
                selections=tuple(fallback_selections),
                books=(),
                token_attempts=tuple(fallback_attempts),
                status="ERROR",
                error_count=max(1, len(fallback_selections)),
                sampler_metadata={
                    "sampler_slot": sampler_slot,
                    "unexpected_error": book_unexpected_error,
                },
            )
            books_bundle = _book_bundle(
                books,
                run_id=run_id,
                cycle_number=cycle,
                normalized_levels=self.config.trading.orderbook.normalized_levels,
            )

        watermark_before = self.repository.latest_trade_watermark()
        bootstrap_start = (
            self.repository.latest_trade_bootstrap_start()
            if watermark_before is None
            else None
        )
        trade_started = utc_now()
        trade_unexpected_error: str | None = None
        try:
            trades = self.data.fetch_incremental(
                watermark_epoch=watermark_before,
                bootstrap_start_epoch=bootstrap_start,
                now_epoch=cycle_now_epoch,
                cycle_number=cycle,
                run_id=run_id,
            )
            trades_bundle = _trade_bundle(trades, run_id=run_id, cycle_number=cycle)
        except Exception as error:
            trade_unexpected_error = (
                f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
            )
            source_target_end = max(
                0,
                cycle_now_epoch - self.config.trading.data_api.safety_lag_seconds,
            )
            target_end = source_target_end
            if watermark_before is None:
                target_start = (
                    max(0, int(bootstrap_start))
                    if bootstrap_start is not None
                    else max(
                        0,
                        target_end
                        - self.config.trading.data_api.initial_lookback_hours * 3600,
                    )
                )
                target_end = min(
                    target_end,
                    target_start + self.config.trading.data_api.catchup_chunk_seconds,
                )
            else:
                target_start = max(
                    0,
                    watermark_before - self.config.trading.data_api.overlap_seconds,
                )
                target_end = min(
                    target_end,
                    watermark_before
                    + self.config.trading.data_api.catchup_chunk_seconds,
                )
            collection_id = str(uuid4())
            trades = DataTradeCollection(
                collection_id=collection_id,
                started_at=trade_started,
                completed_at=utc_now(),
                target_start_epoch=target_start,
                source_target_end_epoch=source_target_end,
                bounded_target_end_epoch=target_end,
                watermark_before_epoch=watermark_before,
                watermark_advance_to_epoch=None,
                status="ERROR",
                possible_gap=True,
                windows=(
                    {
                        "window_id": str(uuid4()),
                        "parent_window_id": None,
                        "start_epoch": target_start,
                        "end_epoch": target_end,
                        "split_depth": 0,
                        "offset": 0,
                        "request_id": None,
                        "raw_payload_id": None,
                        "received_at": utc_now(),
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "ERROR",
                        "possible_gap": 1,
                        "error_message": trade_unexpected_error,
                    },
                ),
                memberships=(),
                trades=(),
                error_message=trade_unexpected_error,
            )
            trades_bundle = _trade_bundle(trades, run_id=run_id, cycle_number=cycle)

        resolution_limit = (
            self.config.trading.resolution.max_condition_lookups_per_cycle
        )
        existing_due = self.repository.select_resolution_watchlist(resolution_limit)
        watchlist = list(dict.fromkeys([*disappeared_ids, *existing_due]))[
            :resolution_limit
        ]
        resolution_attempt = str(uuid4())
        resolution_started = utc_now()
        resolution_unexpected_error: str | None = None
        try:
            resolution_raw = (
                self.gamma.fetch_resolution_batch(
                    watchlist,
                    cycle_number=cycle,
                    run_id=run_id,
                    sweep_attempt_id=resolution_attempt,
                    batch_size=self.config.trading.resolution.batch_size,
                )
                if watchlist
                else []
            )
        except Exception as error:
            message = (
                f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
            )
            resolution_unexpected_error = message
            resolution_raw = [
                {
                    "condition_id": condition_id,
                    "requested_at": resolution_started,
                    "observed_at": utc_now(),
                    "lookup_status": "ERROR",
                    "request_id": None,
                    "raw_market": None,
                    "error_type": type(error).__name__,
                    "error_message": message,
                }
                for condition_id in watchlist
            ]
        resolution_rows = _resolution_bundle(
            resolution_raw, run_id=run_id, cycle_number=cycle
        )
        resolution_errors = sum(
            row["lookup_status"] != "OBSERVED" for row in resolution_rows
        )
        resolution_observed = len(resolution_rows) - resolution_errors
        resolution_status = (
            "EMPTY"
            if not watchlist
            else (
                "SUCCESS"
                if resolution_errors == 0
                else ("ERROR" if resolution_observed == 0 else "PARTIAL")
            )
        )

        components = [
            self._component(
                run_id,
                cycle,
                "clob_books",
                books.status,
                books.started_at,
                books.completed_at,
                requested=sum(row["expected_token_count"] for row in books.selections),
                observed=len(books.books),
                errors=books.error_count,
                possible_gap=books.status in {"PARTIAL", "ERROR"},
                details=books.sampler_metadata,
                error_message=book_unexpected_error,
            ),
            self._component(
                run_id,
                cycle,
                "data_trade_tape",
                trades.status,
                trades.started_at,
                trades.completed_at,
                requested=None,
                observed=len(trades.trades),
                errors=int(trades.status == "ERROR"),
                possible_gap=trades.possible_gap,
                details={
                    "target_start": trades.target_start_epoch,
                    "bounded_target_end": trades.bounded_target_end_epoch,
                    "source_target_end": trades.source_target_end_epoch,
                    "windows": len(trades.windows),
                },
                error_message=trades.error_message or trade_unexpected_error,
            ),
            self._component(
                run_id,
                cycle,
                "resolution_watchlist",
                resolution_status,
                resolution_started,
                utc_now(),
                requested=len(watchlist),
                observed=resolution_observed,
                errors=resolution_errors,
                possible_gap=resolution_errors > 0,
                details={
                    "disappeared_enqueued": len(disappeared_ids),
                    "due_selected": len(existing_due),
                },
                error_message=resolution_unexpected_error,
            ),
        ]
        quality = []
        if books.status in {"PARTIAL", "ERROR"}:
            quality.append(
                {
                    "issue_id": str(uuid4()),
                    "run_id": run_id,
                    "cycle_number": cycle,
                    "component": "clob_books",
                    "severity": "MEDIUM",
                    "issue_code": "sample_coverage_gap",
                    "observed_at": utc_now(),
                    "details_json": canonical_json(
                        {"status": books.status, "error_count": books.error_count}
                    ),
                }
            )
        if trades.possible_gap:
            quality.append(
                {
                    "issue_id": str(uuid4()),
                    "run_id": run_id,
                    "cycle_number": cycle,
                    "component": "data_trade_tape",
                    "severity": "HIGH",
                    "issue_code": "possible_trade_tape_gap",
                    "observed_at": utc_now(),
                    "details_json": canonical_json(
                        {"status": trades.status, "watermark_advanced": False}
                    ),
                }
            )
        if resolution_errors:
            quality.append(
                {
                    "issue_id": str(uuid4()),
                    "run_id": run_id,
                    "cycle_number": cycle,
                    "component": "resolution_watchlist",
                    "severity": "MEDIUM",
                    "issue_code": "resolution_lookup_gap",
                    "observed_at": utc_now(),
                    "details_json": canonical_json(
                        {"errors_or_missing": resolution_errors}
                    ),
                }
            )

        secondary_bundle = {
            "run_id": run_id,
            "cycle_number": cycle,
            "components": components,
            **books_bundle,
            "resolution_observations": resolution_rows,
            **trades_bundle,
            "quality_issues": quality,
        }
        self.repository.publish_secondary_cycle(secondary_bundle)
        return {
            "cycle_number": cycle,
            "market_sweeps": 1,
            "gamma_page_count": len(sweep.pages),
            "markets_observed": len(market["market_observations"]),
            "outcomes_observed": len(market["outcome_observations"]),
            "orderbook_component_status": books.status,
            "orderbooks_observed": len(books.books),
            "trade_tape_component_status": trades.status,
            "trade_tape_possible_gap": trades.possible_gap,
            "trades_observed": len(trades.trades),
            "trade_watermark_advanced_to": trades.watermark_advance_to_epoch,
            "resolution_component_status": components[-1]["status"],
            "resolution_observed": len(resolution_rows),
            "data_quality_issue_count": len(primary_quality) + len(quality),
        }


__all__ = ["MARKET_OBSERVATION_COLUMNS", "ResearchCollector"]

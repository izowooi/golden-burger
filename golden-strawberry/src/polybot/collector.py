"""Atomic Last Mile census, crossing, displayed-book, and resolution collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .api.clob_client import ClobBookClient
from .api.gamma_client import GammaClient, ResolutionLookup
from .config import BotConfig
from .db.repository import ResearchRepository
from .utils.retry import PublicJsonTransport, canonical_json, iso_utc


_SPORT_TERMS = frozenset(
    {
        "sports",
        "football",
        "soccer",
        "basketball",
        "baseball",
        "hockey",
        "tennis",
        "golf",
        "cricket",
        "rugby",
        "boxing",
        "mma",
        "ufc",
        "esports",
        "nfl",
        "nba",
        "wnba",
        "mlb",
        "nhl",
        "ncaa",
        "epl",
        "fifa",
    }
)


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("source timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _source_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "0", "1"}:
        return value.strip().lower() in {"true", "1"}
    return None


def _finite(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _decode_array(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, list) else None
    return None


def _compact_tags(value: Any) -> list[Any]:
    tags = _decode_array(value)
    if tags is None:
        return []
    compact: list[Any] = []
    for item in tags:
        if isinstance(item, Mapping):
            compact.append(
                {
                    key: item[key]
                    for key in ("id", "label", "slug", "name")
                    if key in item and item[key] is not None
                }
            )
        elif isinstance(item, (str, int)):
            compact.append(item)
    return compact


def classify_sports(market: Mapping[str, Any], tags: Sequence[Any]) -> str:
    for key in (
        "sportsMarketType",
        "sports_market_type",
        "gameStartTime",
        "game_start_time",
        "sportsEventId",
        "sports_event_id",
        "sport",
    ):
        value = market.get(key)
        if value is not None and value != "" and value is not False:
            return "SPORTS"
    words: set[str] = set()
    category = market.get("category")
    if category:
        words.update(str(category).lower().replace("-", " ").split())
    for tag in tags:
        if isinstance(tag, Mapping):
            values = tag.values()
        else:
            values = (tag,)
        for value in values:
            words.update(str(value).lower().replace("-", " ").split())
    return "SPORTS" if words & _SPORT_TERMS else "NON_SPORTS"


def _event_ids(market: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    direct = market.get("eventId") or market.get("event_id")
    if direct:
        result.append(str(direct))
    events = _decode_array(market.get("events"))
    for event in events or []:
        if isinstance(event, Mapping):
            identifier = (
                event.get("id") or event.get("eventId") or event.get("event_id")
            )
            if identifier is not None:
                result.append(str(identifier))
    return list(dict.fromkeys(value for value in result if value))


@dataclass(frozen=True)
class ParsedMarket:
    catalog_row: dict[str, Any]
    outcome_rows: tuple[dict[str, Any], ...]
    membership_row: dict[str, Any]


def parse_gamma_market(
    raw_with_lineage: Mapping[str, Any],
    *,
    sweep_id: str,
    run_id: str,
    sports_classifier_version: str,
) -> ParsedMarket:
    source = {
        key: value
        for key, value in raw_with_lineage.items()
        if not str(key).startswith("_")
    }
    raw_json = canonical_json(source)
    raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    page_number = int(raw_with_lineage["_page_number"])
    item_number = int(raw_with_lineage["_item_number"])
    observed_at = str(raw_with_lineage["_page_received_at"])
    request_id = str(raw_with_lineage["_page_request_id"])

    condition_id = str(source.get("conditionId") or source.get("condition_id") or "")
    market_id = str(source.get("id") or source.get("market_id") or "") or None
    event_ids = _event_ids(source)
    event_id = event_ids[0] if event_ids else None
    event_cluster_id = event_id or condition_id or None
    labels_raw = _decode_array(source.get("outcomes"))
    tokens_raw = _decode_array(
        source.get("clobTokenIds") or source.get("clob_token_ids")
    )
    prices_raw = _decode_array(
        source.get("outcomePrices") or source.get("outcome_prices")
    )
    labels = [str(value) for value in labels_raw or []]
    tokens = [str(value) for value in tokens_raw or []]
    prices = [_finite(value) for value in prices_raw or []]
    arrays_aligned = bool(
        len(labels) >= 2
        and len(labels) == len(tokens) == len(prices)
        and all(tokens)
        and len(set(tokens)) == len(tokens)
        and all(value is not None and 0 <= value <= 1 for value in prices)
    )
    active = _source_bool(source.get("active"))
    closed = _source_bool(source.get("closed"))
    book_enabled = _source_bool(
        source.get("enableOrderBook")
        if "enableOrderBook" in source
        else source.get("enable_order_book")
    )
    accepting = _source_bool(
        source.get("acceptingOrders")
        if "acceptingOrders" in source
        else source.get("accepting_orders")
    )
    reasons: list[str] = []
    if not condition_id:
        reasons.append("MISSING_CONDITION_ID")
    if active is not True:
        reasons.append("NOT_ACTIVE")
    if closed is not False:
        reasons.append("CLOSED_OR_UNKNOWN")
    if book_enabled is not True:
        reasons.append("ORDERBOOK_DISABLED_OR_UNKNOWN")
    if accepting is not True:
        reasons.append("NOT_ACCEPTING_ORDERS")
    if not arrays_aligned:
        reasons.append("OUTCOME_TOKEN_PRICE_ARRAYS_UNALIGNED")
    tradable = not reasons
    tags = _compact_tags(source.get("tags"))
    sports = classify_sports(source, tags)
    neg_risk = bool(
        _source_bool(
            source.get("negRisk") if "negRisk" in source else source.get("neg_risk")
        )
        or False
    )
    outcome_type = "BINARY" if len(labels) == 2 else ("MULTI" if labels else None)
    liquidity = _finite(source.get("liquidityNum", source.get("liquidity")))
    volume_total = _finite(source.get("volumeNum", source.get("volume")))
    volume_24h = _finite(
        source.get("volume24hr", source.get("volume24h", source.get("volume_24h")))
    )
    end_date = source.get("endDate") or source.get("end_date")
    category = source.get("category")
    catalog_id = uuid4().hex
    normalized = {
        "condition_id": condition_id or None,
        "market_id": market_id,
        "event_ids": event_ids,
        "active": active,
        "closed": closed,
        "orderbook_enabled": book_enabled,
        "accepting_orders": accepting,
        "outcome_type": outcome_type,
        "neg_risk": neg_risk,
        "liquidity": liquidity,
        "volume_total": volume_total,
        "volume_24h": volume_24h,
        "end_date": str(end_date) if end_date is not None else None,
        "category": str(category) if category is not None else None,
        "tags": tags,
        "outcomes": labels,
        "token_ids": tokens,
        "probabilities": prices,
    }
    catalog_row = {
        "catalog_version_id": catalog_id,
        "sweep_id": sweep_id,
        "run_id": run_id,
        "page_number": page_number,
        "item_number": item_number,
        "source_received_at": observed_at,
        "source_request_id": request_id,
        "condition_id": condition_id or None,
        "market_id": market_id,
        "event_id": event_id,
        "event_ids_json": canonical_json(event_ids),
        "event_cluster_id": event_cluster_id,
        "market_slug": str(source.get("slug") or "") or None,
        "question": str(source.get("question") or "") or None,
        "active": int(active) if active is not None else None,
        "closed": int(closed) if closed is not None else None,
        "orderbook_enabled": int(book_enabled) if book_enabled is not None else None,
        "accepting_orders": int(accepting) if accepting is not None else None,
        "tradable": int(tradable),
        "exclusion_reason": "SOURCE_TRADABLE" if tradable else ";".join(reasons),
        "outcome_type": outcome_type,
        "neg_risk": int(neg_risk),
        "sports_classification": sports,
        "sports_classifier_version": sports_classifier_version,
        "liquidity": liquidity,
        "volume_total": volume_total,
        "volume_24h": volume_24h,
        "end_date": str(end_date) if end_date is not None else None,
        "category": str(category) if category is not None else None,
        "tags_json": canonical_json(tags),
        "outcome_labels_json": canonical_json(labels),
        "token_ids_json": canonical_json(tokens),
        "outcome_prices_json": canonical_json(prices),
        "raw_market_sha256": raw_hash,
        "normalized_market_json": canonical_json(normalized),
    }
    outcome_rows: list[dict[str, Any]] = []
    if arrays_aligned and condition_id and event_cluster_id and outcome_type:
        for index, (label, token, probability) in enumerate(
            zip(labels, tokens, prices)
        ):
            outcome_rows.append(
                {
                    "observation_id": uuid4().hex,
                    "catalog_version_id": catalog_id,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "condition_id": condition_id,
                    "market_id": market_id,
                    "event_id": event_id,
                    "event_cluster_id": event_cluster_id,
                    "token_id": token,
                    "outcome_index": index,
                    "outcome_label": label,
                    "probability": float(probability),
                    "observed_at": observed_at,
                    "outcome_type": outcome_type,
                    "neg_risk": int(neg_risk),
                    "sports_classification": sports,
                    "sports_classifier_version": sports_classifier_version,
                    "liquidity": liquidity,
                    "volume_total": volume_total,
                    "volume_24h": volume_24h,
                    "end_date": str(end_date) if end_date is not None else None,
                    "category": str(category) if category is not None else None,
                    "tags_json": canonical_json(tags),
                    "raw_market_sha256": raw_hash,
                }
            )
    membership_row = {
        "page_number": page_number,
        "item_number": item_number,
        "source_received_at": observed_at,
        "source_request_id": request_id,
        "condition_id": condition_id or None,
        "market_id": market_id,
        "event_cluster_id": event_cluster_id,
        "tradable": tradable,
        "exclusion_reason": catalog_row["exclusion_reason"],
        "token_ids": tokens,
        "raw_market_sha256": raw_hash,
    }
    return ParsedMarket(
        catalog_row=catalog_row,
        outcome_rows=tuple(outcome_rows),
        membership_row=membership_row,
    )


def evaluate_crossing(
    *,
    current_probability: float,
    current_observed_at: str,
    current_condition_id: str,
    threshold: float,
    prior: Mapping[str, Any] | None,
    episode_exists: bool,
    entry_start: datetime,
    entry_end: datetime,
    max_gap_minutes: float,
) -> dict[str, Any]:
    current_at = _utc(current_observed_at)
    result: dict[str, Any] = {
        "status": "BELOW_THRESHOLD",
        "prior_probability": None,
        "prior_condition_id": None,
        "prior_observed_at": None,
        "prior_gap_minutes": None,
        "interval_censored": 0,
        "jump_size": None,
    }
    if prior is None:
        result["status"] = (
            "LEFT_CENSORED" if current_probability >= threshold else "INITIAL_BELOW"
        )
        return result
    prior_probability = float(prior["probability"])
    prior_condition = str(prior["condition_id"])
    prior_at = _utc(str(prior["observed_at"]))
    gap_minutes = (current_at - prior_at).total_seconds() / 60
    result.update(
        {
            "prior_probability": prior_probability,
            "prior_condition_id": prior_condition,
            "prior_observed_at": str(prior["observed_at"]),
            "prior_gap_minutes": gap_minutes,
            "jump_size": current_probability - prior_probability,
        }
    )
    if prior_condition != current_condition_id:
        result["status"] = "CONDITION_CHANGED"
        return result
    if prior_probability >= threshold:
        result["status"] = "ALREADY_ABOVE"
        return result
    if current_probability < threshold:
        result["status"] = "BELOW_THRESHOLD"
        return result
    result["interval_censored"] = 1
    if gap_minutes <= 0 or gap_minutes > max_gap_minutes:
        result["status"] = "GAP_CENSORED"
    elif not (entry_start <= current_at < entry_end):
        result["status"] = "OUTSIDE_ENTRY_WINDOW"
    elif episode_exists:
        result["status"] = "EPISODE_EXISTS"
    else:
        result["status"] = "NEW_CROSSING"
    return result


@dataclass(frozen=True)
class NormalizedBook:
    token_id: str
    observed_at: str
    request_id: str
    raw_book_sha256: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    bid_depth_notional: float
    ask_depth_notional: float
    source_timestamp: str | None
    tick_size: float | None
    min_order_size: float | None
    fee_rate_bps: float | None
    source_metadata_json: str


def _book_levels(value: Any, *, reverse: bool) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list):
        raise ValueError("book side must be an array")
    levels: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("book level must be an object")
        price = _finite(item.get("price"))
        size = _finite(item.get("size"))
        if price is None or size is None or not (0 < price <= 1) or size <= 0:
            raise ValueError("book level contains an invalid price or size")
        levels.append((price, size))
    levels.sort(key=lambda item: item[0], reverse=reverse)
    return tuple(levels)


def normalize_book(
    token_id: str,
    book: Mapping[str, Any],
    *,
    request_id: str,
    observed_at: str,
) -> NormalizedBook:
    bids = _book_levels(book.get("bids"), reverse=True)
    asks = _book_levels(book.get("asks"), reverse=False)
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    spread = (
        best_ask - best_bid if best_ask is not None and best_bid is not None else None
    )
    metadata = {
        "timestamp": book.get("timestamp"),
        "tick_size": book.get("tick_size", book.get("tickSize")),
        "min_order_size": book.get(
            "min_order_size", book.get("minOrderSize", book.get("minimum_order_size"))
        ),
        "fee_rate_bps": book.get("fee_rate_bps", book.get("feeRateBps")),
        "market": book.get("market"),
        "hash": book.get("hash"),
    }
    raw_book = canonical_json(dict(book))
    return NormalizedBook(
        token_id=token_id,
        observed_at=observed_at,
        request_id=request_id,
        raw_book_sha256=hashlib.sha256(raw_book.encode("utf-8")).hexdigest(),
        bids=bids,
        asks=asks,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        bid_depth_notional=sum(price * size for price, size in bids),
        ask_depth_notional=sum(price * size for price, size in asks),
        source_timestamp=(
            str(book.get("timestamp")) if book.get("timestamp") else None
        ),
        tick_size=_finite(metadata["tick_size"]),
        min_order_size=_finite(metadata["min_order_size"]),
        fee_rate_bps=_finite(metadata["fee_rate_bps"]),
        source_metadata_json=canonical_json(metadata),
    )


def walk_asks(levels: Sequence[tuple[float, float]], notional: float) -> dict[str, Any]:
    if notional <= 0:
        raise ValueError("ask walk notional must be positive")
    remaining = notional
    shares = 0.0
    for price, size in levels:
        level_cost = price * size
        if level_cost >= remaining:
            shares += remaining / price
            remaining = 0.0
            break
        shares += size
        remaining -= level_cost
    if remaining > 1e-9 or shares <= 0:
        return {
            "status": "INSUFFICIENT_ASK_DEPTH",
            "shares": None,
            "vwap": None,
            "covered_notional": notional - remaining,
        }
    return {
        "status": "EXECUTABLE",
        "shares": shares,
        "vwap": notional / shares,
        "covered_notional": notional,
    }


def walk_bids(levels: Sequence[tuple[float, float]], shares: float) -> dict[str, Any]:
    if shares <= 0:
        raise ValueError("bid walk shares must be positive")
    remaining = shares
    proceeds = 0.0
    for price, size in levels:
        used = min(remaining, size)
        proceeds += used * price
        remaining -= used
        if remaining <= 1e-12:
            break
    if remaining > 1e-9:
        return {
            "status": "INSUFFICIENT_BID_DEPTH",
            "vwap": None,
            "proceeds": None,
            "covered_shares": shares - remaining,
        }
    return {
        "status": "EXECUTABLE",
        "vwap": proceeds / shares,
        "proceeds": proceeds,
        "covered_shares": shares,
    }


def _raw_payload_row(
    *,
    run_id: str,
    request_id: str,
    kind: str,
    received_at: str,
    raw: bytes,
) -> dict[str, Any]:
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    return {
        "payload_id": f"raw-{request_id}",
        "run_id": run_id,
        "request_id": request_id,
        "payload_kind": kind,
        "source_received_at": received_at,
        "content_encoding": "gzip",
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "payload_blob": compressed,
        "recorded_at": iso_utc(),
    }


def _resolution_result(
    lookup: ResolutionLookup,
) -> dict[str, Any]:
    if lookup.lookup_status != "OBSERVED" or lookup.market is None:
        return {
            "resolution_status": lookup.lookup_status,
            "raw_market_sha256": None,
            "winning_outcome_index": None,
            "winning_outcome_label": None,
            "winning_token_id": None,
            "token_payouts": {},
        }
    market = lookup.market
    raw_market = canonical_json(market)
    labels = _decode_array(market.get("outcomes"))
    tokens = _decode_array(market.get("clobTokenIds") or market.get("clob_token_ids"))
    prices_raw = _decode_array(
        market.get("outcomePrices") or market.get("outcome_prices")
    )
    prices = [_finite(value) for value in prices_raw or []]
    if not (
        labels
        and tokens
        and len(labels) == len(tokens) == len(prices)
        and all(value is not None and 0 <= value <= 1 for value in prices)
    ):
        return {
            "resolution_status": "MALFORMED",
            "raw_market_sha256": hashlib.sha256(raw_market.encode("utf-8")).hexdigest(),
            "winning_outcome_index": None,
            "winning_outcome_label": None,
            "winning_token_id": None,
            "token_payouts": {},
        }
    terminal = [
        index
        for index, value in enumerate(prices)
        if value is not None and value >= 1 - 1e-9
    ]
    all_terminal = all(
        value is not None and (value <= 1e-9 or value >= 1 - 1e-9) for value in prices
    )
    if (
        _source_bool(market.get("closed")) is not True
        or len(terminal) != 1
        or not all_terminal
    ):
        return {
            "resolution_status": "UNRESOLVED",
            "raw_market_sha256": hashlib.sha256(raw_market.encode("utf-8")).hexdigest(),
            "winning_outcome_index": None,
            "winning_outcome_label": None,
            "winning_token_id": None,
            "token_payouts": {},
        }
    winner = terminal[0]
    payouts = {
        str(token): int(round(float(prices[index])))
        for index, token in enumerate(tokens)
    }
    return {
        "resolution_status": "RESOLVED",
        "raw_market_sha256": hashlib.sha256(raw_market.encode("utf-8")).hexdigest(),
        "winning_outcome_index": winner,
        "winning_outcome_label": str(labels[winner]),
        "winning_token_id": str(tokens[winner]),
        "token_payouts": payouts,
    }


class ResearchCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        repository: ResearchRepository,
        gamma_client: GammaClient | None = None,
        clob_client: ClobBookClient | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.config = config
        self.repository = repository
        self.monotonic = monotonic
        if gamma_client is None or clob_client is None:
            gamma_config = config.trading.gamma
            transport = PublicJsonTransport(
                connect_timeout_seconds=gamma_config.connect_timeout_seconds,
                read_timeout_seconds=gamma_config.read_timeout_seconds,
                max_retries=gamma_config.max_retries,
                retry_base_seconds=gamma_config.retry_base_seconds,
                retry_max_seconds=gamma_config.retry_max_seconds,
                receipt_sink=repository.record_api_request,
            )
            gamma_client = gamma_client or GammaClient(gamma_config, transport)
            clob_client = clob_client or ClobBookClient(
                config.trading.orderbook, transport
            )
        self.gamma_client = gamma_client
        self.clob_client = clob_client

    def run_cycle(self, run_id: str) -> dict[str, Any]:
        started_clock = self.monotonic()
        cycle_number = self.repository.next_cycle_number()
        sweep_id = uuid4().hex
        sweep = self.gamma_client.collect_market_sweep(run_id)
        if not sweep.cursor_complete:
            raise RuntimeError("Gamma client returned a non-terminal sweep")

        parsed_markets = [
            parse_gamma_market(
                market,
                sweep_id=sweep_id,
                run_id=run_id,
                sports_classifier_version=(
                    self.config.trading.experiment.sports_classifier_version
                ),
            )
            for market in sweep.markets
        ]
        catalog_rows = [item.catalog_row for item in parsed_markets]
        membership_rows = [item.membership_row for item in parsed_markets]
        outcome_rows = [row for item in parsed_markets for row in item.outcome_rows]
        token_ids = [str(row["token_id"]) for row in outcome_rows]
        if len(token_ids) != len(set(token_ids)):
            raise RuntimeError("Gamma sweep contains duplicate token identity")

        catalog_by_id = {str(row["catalog_version_id"]): row for row in catalog_rows}
        prior_states = self.repository.latest_states(token_ids)
        existing_episode_keys = self.repository.episode_keys()
        decisions: list[dict[str, Any]] = []
        crossing_candidates: list[dict[str, Any]] = []
        experiment = self.config.trading.experiment
        for outcome in outcome_rows:
            catalog = catalog_by_id[str(outcome["catalog_version_id"])]
            if not bool(catalog["tradable"]):
                continue
            prior = prior_states.get(str(outcome["token_id"]))
            crossed_count = 0
            if (
                prior is not None
                and str(prior["condition_id"]) == outcome["condition_id"]
            ):
                prior_probability = float(prior["probability"])
                crossed_count = sum(
                    1
                    for threshold in experiment.entry_thresholds
                    if prior_probability < threshold <= float(outcome["probability"])
                )
            for threshold in experiment.entry_thresholds:
                key = (str(outcome["token_id"]), float(threshold))
                result = evaluate_crossing(
                    current_probability=float(outcome["probability"]),
                    current_observed_at=str(outcome["observed_at"]),
                    current_condition_id=str(outcome["condition_id"]),
                    threshold=threshold,
                    prior=prior,
                    episode_exists=key in existing_episode_keys,
                    entry_start=experiment.entry_start_utc,
                    entry_end=experiment.entry_end_utc,
                    max_gap_minutes=experiment.prior_gap_max_minutes,
                )
                decision_id = uuid4().hex
                episode_id = uuid4().hex if result["status"] == "NEW_CROSSING" else None
                decision = {
                    "decision_id": decision_id,
                    "observation_id": outcome["observation_id"],
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "condition_id": outcome["condition_id"],
                    "token_id": outcome["token_id"],
                    "entry_threshold": threshold,
                    "decided_at": outcome["observed_at"],
                    "prior_condition_id": result["prior_condition_id"],
                    "prior_probability": result["prior_probability"],
                    "prior_observed_at": result["prior_observed_at"],
                    "prior_gap_minutes": result["prior_gap_minutes"],
                    "current_probability": outcome["probability"],
                    "decision_status": result["status"],
                    "interval_censored": result["interval_censored"],
                    "jump_size": result["jump_size"],
                    "crossed_threshold_count": crossed_count,
                    "episode_id": episode_id,
                    "details_json": canonical_json(
                        {
                            "continuous_passage_asserted": False,
                            "sampling_interval": [
                                result["prior_observed_at"],
                                outcome["observed_at"],
                            ],
                        }
                    ),
                }
                if result["status"] not in {
                    "INITIAL_BELOW",
                    "BELOW_THRESHOLD",
                    "ALREADY_ABOVE",
                }:
                    decisions.append(decision)
                if episode_id is not None:
                    crossing_candidates.append(
                        {
                            "decision": decision,
                            "episode_id": episode_id,
                            "outcome": outcome,
                            "catalog": catalog,
                        }
                    )

        unresolved = self.repository.unresolved_episodes()
        followup_active = _utc(sweep.completed_at) < experiment.followup_end_utc
        crossing_tokens = {
            str(candidate["outcome"]["token_id"]) for candidate in crossing_candidates
        }
        episode_tokens = (
            {str(row["token_id"]) for row in unresolved} if followup_active else set()
        )
        requested_tokens = sorted(crossing_tokens | episode_tokens)
        books = self.clob_client.fetch_books(run_id, requested_tokens)
        normalized_books: dict[str, NormalizedBook] = {}
        normalization_errors: dict[str, str] = {}
        for token, book in books.books.items():
            attempt = books.attempts[token]
            if attempt.request_id is None or attempt.received_at is None:
                normalization_errors[token] = "book lacks request lineage"
                continue
            try:
                normalized_books[token] = normalize_book(
                    token,
                    book,
                    request_id=attempt.request_id,
                    observed_at=attempt.received_at,
                )
            except ValueError as error:
                normalization_errors[token] = str(error)

        raw_payloads: dict[str, dict[str, Any]] = {}
        page_rows: list[dict[str, Any]] = []
        for page in sweep.pages:
            raw_row = _raw_payload_row(
                run_id=run_id,
                request_id=page.request_id,
                kind="gamma_markets_keyset_page",
                received_at=page.received_at,
                raw=page.raw,
            )
            raw_payloads[page.request_id] = raw_row
            page_rows.append(
                {
                    "page_id": uuid4().hex,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "page_number": page.page_number,
                    "cursor_in": page.cursor_in,
                    "cursor_out": page.cursor_out,
                    "market_count": len(page.markets),
                    "request_id": page.request_id,
                    "raw_payload_id": raw_row["payload_id"],
                    "request_hash": page.request_hash,
                    "source_received_at": page.received_at,
                    "response_sha256": page.response_sha256,
                }
            )
        for raw in books.raw_payloads:
            raw_payloads[raw.request_id] = _raw_payload_row(
                run_id=run_id,
                request_id=raw.request_id,
                kind="clob_books",
                received_at=raw.received_at,
                raw=raw.raw,
            )

        snapshot_rows: list[dict[str, Any]] = []
        level_rows: list[dict[str, Any]] = []
        snapshot_ids: dict[str, str] = {}
        for token, book in normalized_books.items():
            snapshot_id = uuid4().hex
            snapshot_ids[token] = snapshot_id
            snapshot_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "token_id": token,
                    "request_id": book.request_id,
                    "source_received_at": book.observed_at,
                    "raw_book_sha256": book.raw_book_sha256,
                    "source_timestamp": book.source_timestamp,
                    "tick_size": book.tick_size,
                    "min_order_size": book.min_order_size,
                    "fee_rate_bps": book.fee_rate_bps,
                    "source_metadata_json": book.source_metadata_json,
                    "bid_level_count": len(book.bids),
                    "ask_level_count": len(book.asks),
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "spread": book.spread,
                    "bid_depth_notional": book.bid_depth_notional,
                    "ask_depth_notional": book.ask_depth_notional,
                }
            )
            for side, levels in (("BID", book.bids), ("ASK", book.asks)):
                for index, (price, size) in enumerate(levels):
                    level_rows.append(
                        {
                            "level_id": uuid4().hex,
                            "snapshot_id": snapshot_id,
                            "side": side,
                            "level_index": index,
                            "price": price,
                            "size": size,
                        }
                    )

        attempt_rows: list[dict[str, Any]] = []
        for token in requested_tokens:
            attempt = books.attempts.get(token)
            if attempt is None:
                status = "MISSING"
                request_id = None
                started_at = None
                received_at = None
                error_type = "MissingAttemptEvidence"
                error_message = "CLOB client returned no token attempt"
            else:
                status = (
                    "MALFORMED" if token in normalization_errors else attempt.status
                )
                request_id = attempt.request_id
                started_at = attempt.request_started_at
                received_at = attempt.received_at
                error_type = (
                    "BookNormalizationError"
                    if token in normalization_errors
                    else attempt.error_type
                )
                error_message = normalization_errors.get(token, attempt.error_message)
            role = (
                "BOTH"
                if token in crossing_tokens and token in episode_tokens
                else ("CROSSING" if token in crossing_tokens else "EPISODE")
            )
            attempt_rows.append(
                {
                    "attempt_id": uuid4().hex,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "token_id": token,
                    "attempt_role": role,
                    "status": status,
                    "request_id": request_id,
                    "request_started_at": started_at,
                    "source_received_at": received_at,
                    "error_type": error_type,
                    "error_message": error_message,
                }
            )

        episodes: list[dict[str, Any]] = []
        new_executable: list[dict[str, Any]] = []
        for candidate in crossing_candidates:
            outcome = candidate["outcome"]
            catalog = candidate["catalog"]
            token = str(outcome["token_id"])
            book = normalized_books.get(token)
            if book is None:
                attempt = books.attempts.get(token)
                entry_status = "CENSORED"
                entry_reason = (
                    "MALFORMED_BOOK"
                    if token in normalization_errors
                    else (attempt.status if attempt is not None else "MISSING")
                )
                ask_walk = {"shares": None, "vwap": None}
                entry_at = str(outcome["observed_at"])
            else:
                ask_walk = walk_asks(book.asks, experiment.simulated_notional_usdc)
                entry_status = str(ask_walk["status"])
                entry_reason = None if entry_status == "EXECUTABLE" else entry_status
                entry_at = book.observed_at
            episode = {
                "episode_id": candidate["episode_id"],
                "decision_id": candidate["decision"]["decision_id"],
                "originating_sweep_id": sweep_id,
                "run_id": run_id,
                "condition_id": outcome["condition_id"],
                "market_id": outcome["market_id"],
                "event_id": outcome["event_id"],
                "event_cluster_id": outcome["event_cluster_id"],
                "token_id": token,
                "outcome_index": outcome["outcome_index"],
                "outcome_label": outcome["outcome_label"],
                "outcome_type": outcome["outcome_type"],
                "neg_risk": outcome["neg_risk"],
                "sports_classification": outcome["sports_classification"],
                "entry_threshold": candidate["decision"]["entry_threshold"],
                "crossing_prior_probability": candidate["decision"][
                    "prior_probability"
                ],
                "crossing_probability": outcome["probability"],
                "crossing_gap_minutes": candidate["decision"]["prior_gap_minutes"],
                "interval_censored": 1,
                "entry_observed_at": entry_at,
                "entry_status": entry_status,
                "entry_censor_reason": entry_reason,
                "entry_snapshot_id": snapshot_ids.get(token),
                "entry_notional_usdc": experiment.simulated_notional_usdc,
                "entry_ask_vwap": ask_walk.get("vwap"),
                "fixed_shares": ask_walk.get("shares"),
                "best_ask": book.best_ask if book else None,
                "spread": book.spread if book else None,
                "ask_depth_notional": book.ask_depth_notional if book else None,
                "source_tick_size": book.tick_size if book else None,
                "source_min_order_size": book.min_order_size if book else None,
                "source_fee_rate_bps": book.fee_rate_bps if book else None,
                "liquidity": outcome["liquidity"],
                "volume_total": outcome["volume_total"],
                "volume_24h": outcome["volume_24h"],
                "end_date": outcome["end_date"],
                "category": outcome["category"],
                "tags_json": outcome["tags_json"],
                "created_at": iso_utc(),
            }
            episodes.append(episode)
            if entry_status == "EXECUTABLE":
                new_executable.append(episode)

        path_episodes = (unresolved + new_executable) if followup_active else []
        prior_path = self.repository.latest_path_vwaps(
            [str(row["episode_id"]) for row in path_episodes]
        )
        paths: list[dict[str, Any]] = []
        for episode in path_episodes:
            token = str(episode["token_id"])
            book = normalized_books.get(token)
            path_id = uuid4().hex
            if book is None:
                attempt = books.attempts.get(token)
                path_status = "CENSORED"
                censor_reason = (
                    "MALFORMED_BOOK"
                    if token in normalization_errors
                    else (attempt.status if attempt is not None else "MISSING")
                )
                observed_at = (
                    attempt.received_at
                    if attempt is not None and attempt.received_at is not None
                    else sweep.completed_at
                )
                bid_walk = {"vwap": None, "proceeds": None}
            else:
                bid_walk = walk_bids(book.bids, float(episode["fixed_shares"]))
                path_status = str(bid_walk["status"])
                censor_reason = None if path_status == "EXECUTABLE" else path_status
                observed_at = book.observed_at
            paths.append(
                {
                    "path_observation_id": path_id,
                    "episode_id": episode["episode_id"],
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "snapshot_id": snapshot_ids.get(token),
                    "observed_at": observed_at,
                    "path_status": path_status,
                    "censor_reason": censor_reason,
                    "fixed_shares": episode["fixed_shares"],
                    "best_bid": book.best_bid if book else None,
                    "exit_bid_vwap": bid_walk.get("vwap"),
                    "exit_proceeds_usdc": bid_walk.get("proceeds"),
                    "bid_depth_notional": book.bid_depth_notional if book else None,
                    "prior_executable_bid_vwap": prior_path.get(
                        str(episode["episode_id"])
                    ),
                    "interval_censored": 1,
                    "entry_cycle_baseline": int(
                        str(episode["originating_sweep_id"]) == sweep_id
                    ),
                    "details_json": canonical_json(
                        {
                            "displayed_book_counterfactual": True,
                            "midpoint_substitution": False,
                            "fixed_shares": episode["fixed_shares"],
                        }
                    ),
                }
            )

        existing_event_keys = self.repository.threshold_event_keys(
            [str(row["episode_id"]) for row in path_episodes]
        )
        threshold_events: list[dict[str, Any]] = []
        path_by_episode = {str(row["episode_id"]): row for row in paths}
        for episode in path_episodes:
            path = path_by_episode[str(episode["episode_id"])]
            if (
                path["path_status"] != "EXECUTABLE"
                or path["entry_cycle_baseline"]
                or path["exit_bid_vwap"] is None
            ):
                continue
            value = float(path["exit_bid_vwap"])
            prior_value = path["prior_executable_bid_vwap"]
            for kind, thresholds in (
                ("STOP", experiment.stop_thresholds),
                ("TARGET", experiment.target_thresholds),
            ):
                for threshold in thresholds:
                    key = (str(episode["episode_id"]), kind, float(threshold))
                    observed = (
                        value <= threshold if kind == "STOP" else value >= threshold
                    )
                    if not observed or key in existing_event_keys:
                        continue
                    threshold_events.append(
                        {
                            "threshold_event_id": uuid4().hex,
                            "episode_id": episode["episode_id"],
                            "path_observation_id": path["path_observation_id"],
                            "sweep_id": sweep_id,
                            "event_kind": kind,
                            "threshold": threshold,
                            "observed_at": path["observed_at"],
                            "executable_bid_vwap": value,
                            "prior_executable_bid_vwap": prior_value,
                            "interval_censored": 1,
                            "conservative_priority": 0 if kind == "STOP" else 1,
                        }
                    )
                    existing_event_keys.add(key)

        resolution_episodes = path_episodes
        resolution_condition_ids = sorted(
            {str(row["condition_id"]) for row in resolution_episodes}
        )
        resolution_lookups = self.gamma_client.fetch_resolutions(
            run_id, resolution_condition_ids
        )
        for lookup in resolution_lookups:
            if lookup.request_id and lookup.raw is not None:
                raw_payloads.setdefault(
                    lookup.request_id,
                    _raw_payload_row(
                        run_id=run_id,
                        request_id=lookup.request_id,
                        kind="gamma_resolution_lookup",
                        received_at=lookup.observed_at,
                        raw=lookup.raw,
                    ),
                )
        episodes_by_condition: dict[str, list[dict[str, Any]]] = {}
        for episode in resolution_episodes:
            episodes_by_condition.setdefault(str(episode["condition_id"]), []).append(
                episode
            )
        resolution_rows: list[dict[str, Any]] = []
        for lookup in resolution_lookups:
            parsed = _resolution_result(lookup)
            jumps: dict[str, list[float]] = {}
            if parsed["resolution_status"] == "RESOLVED":
                payouts = parsed["token_payouts"]
                for episode in episodes_by_condition.get(lookup.condition_id, []):
                    if payouts.get(str(episode["token_id"])) != 1:
                        continue
                    missing_targets = [
                        threshold
                        for threshold in experiment.target_thresholds
                        if (
                            str(episode["episode_id"]),
                            "TARGET",
                            float(threshold),
                        )
                        not in existing_event_keys
                    ]
                    if missing_targets:
                        jumps[str(episode["episode_id"])] = missing_targets
            resolution_rows.append(
                {
                    "resolution_observation_id": uuid4().hex,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "condition_id": lookup.condition_id,
                    "requested_at": lookup.requested_at,
                    "observed_at": lookup.observed_at,
                    "lookup_status": lookup.lookup_status,
                    "resolution_status": parsed["resolution_status"],
                    "request_id": lookup.request_id,
                    "raw_market_sha256": parsed["raw_market_sha256"],
                    "winning_outcome_index": parsed["winning_outcome_index"],
                    "winning_outcome_label": parsed["winning_outcome_label"],
                    "winning_token_id": parsed["winning_token_id"],
                    "token_payouts_json": canonical_json(parsed["token_payouts"]),
                    "resolution_jump_without_target_json": canonical_json(jumps),
                    "error_type": lookup.error_type,
                    "error_message": lookup.error_message,
                }
            )

        membership_json = canonical_json(membership_rows).encode("utf-8")
        membership_blob = gzip.compress(membership_json, compresslevel=9, mtime=0)
        membership_sha = hashlib.sha256(membership_json).hexdigest()
        request_lineage = [
            {
                "page_number": page.page_number,
                "cursor_in": page.cursor_in,
                "cursor_out": page.cursor_out,
                "request_id": page.request_id,
                "request_hash": page.request_hash,
                "received_at": page.received_at,
                "response_sha256": page.response_sha256,
            }
            for page in sweep.pages
        ]
        request_lineage_sha = hashlib.sha256(
            canonical_json(request_lineage).encode("utf-8")
        ).hexdigest()

        quality_issues: list[dict[str, Any]] = []
        malformed_market_count = sum(
            1 for row in catalog_rows if "UNALIGNED" in str(row["exclusion_reason"])
        )
        if malformed_market_count:
            quality_issues.append(
                self._issue(
                    run_id,
                    sweep_id,
                    "WARN",
                    "GAMMA_UNALIGNED_OUTCOME_ARRAYS",
                    {"count": malformed_market_count},
                )
            )
        bad_attempts = sum(1 for row in attempt_rows if row["status"] != "OBSERVED")
        if bad_attempts:
            quality_issues.append(
                self._issue(
                    run_id,
                    sweep_id,
                    "WARN",
                    "CLOB_EXPLICIT_CENSORING",
                    {"count": bad_attempts, "requested": len(attempt_rows)},
                )
            )
        resolution_errors = sum(
            1
            for row in resolution_rows
            if row["resolution_status"] in {"ERROR", "MALFORMED"}
        )
        if resolution_errors:
            quality_issues.append(
                self._issue(
                    run_id,
                    sweep_id,
                    "WARN",
                    "RESOLUTION_LOOKUP_CENSORING",
                    {"count": resolution_errors},
                )
            )

        completed_at = iso_utc()
        runtime_seconds = self.monotonic() - started_clock
        crossing_count = len(crossing_candidates)
        stats = {
            "cycle_number": cycle_number,
            "sweep_id": sweep_id,
            "gamma_pages": len(sweep.pages),
            "membership_markets": len(catalog_rows),
            "tradable_markets": sum(int(row["tradable"]) for row in catalog_rows),
            "aligned_outcomes": len(outcome_rows),
            "new_crossings": crossing_count,
            "new_episodes": len(episodes),
            "new_executable_episodes": len(new_executable),
            "clob_tokens_requested": len(requested_tokens),
            "clob_books_observed": len(normalized_books),
            "path_observations": len(paths),
            "resolution_observations": len(resolution_rows),
            "runtime_seconds": round(runtime_seconds, 6),
        }
        bundle = {
            "sweep": {
                "sweep_id": sweep_id,
                "run_id": run_id,
                "cycle_number": cycle_number,
                "config_hash": self.config.config_hash,
                "strategy_source_digest": self.config.trading.strategy_source_digest,
                "data_contract": self.config.trading.data_contract,
                "started_at": sweep.started_at,
                "completed_at": sweep.completed_at,
                "published_at": completed_at,
                "cursor_complete": 1,
                "page_count": len(sweep.pages),
                "membership_count": len(catalog_rows),
                "unique_condition_count": len(
                    {
                        row["condition_id"]
                        for row in catalog_rows
                        if row["condition_id"] is not None
                    }
                ),
                "aligned_outcome_count": len(outcome_rows),
                "tradable_market_count": sum(
                    int(row["tradable"]) for row in catalog_rows
                ),
                "membership_sha256": membership_sha,
                "request_lineage_sha256": request_lineage_sha,
            },
            "membership": {
                "membership_id": uuid4().hex,
                "sweep_id": sweep_id,
                "encoding": "gzip-json-v1",
                "membership_sha256": membership_sha,
                "uncompressed_bytes": len(membership_json),
                "compressed_bytes": len(membership_blob),
                "membership_blob": membership_blob,
                "recorded_at": completed_at,
            },
            "raw_payloads": list(raw_payloads.values()),
            "pages": page_rows,
            "catalog": catalog_rows,
            "outcomes": outcome_rows,
            "crossing_decisions": decisions,
            "clob_attempts": attempt_rows,
            "clob_snapshots": snapshot_rows,
            "clob_levels": level_rows,
            "episodes": episodes,
            "paths": paths,
            "threshold_events": threshold_events,
            "resolutions": resolution_rows,
            "quality_issues": quality_issues,
            "cycle_stats": {
                "cycle_stat_id": uuid4().hex,
                "run_id": run_id,
                "sweep_id": sweep_id,
                "cycle_number": cycle_number,
                "started_at": sweep.started_at,
                "completed_at": completed_at,
                "runtime_seconds": runtime_seconds,
                "page_count": len(sweep.pages),
                "membership_count": len(catalog_rows),
                "crossing_count": crossing_count,
                "executable_episode_count": len(new_executable),
                "clob_requested_count": len(requested_tokens),
                "path_observation_count": len(paths),
                "resolution_observation_count": len(resolution_rows),
                "stats_json": canonical_json(stats),
            },
            "latest_states": [
                {
                    "token_id": row["token_id"],
                    "condition_id": row["condition_id"],
                    "probability": row["probability"],
                    "observed_at": row["observed_at"],
                    "observation_id": row["observation_id"],
                    "sweep_id": sweep_id,
                    "updated_at": completed_at,
                }
                for row in outcome_rows
                if catalog_by_id[str(row["catalog_version_id"])]["tradable"]
            ],
        }
        self.repository.publish_cycle(bundle)
        return stats

    @staticmethod
    def _issue(
        run_id: str,
        sweep_id: str,
        severity: str,
        code: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "issue_id": uuid4().hex,
            "run_id": run_id,
            "sweep_id": sweep_id,
            "severity": severity,
            "issue_code": code,
            "details_json": canonical_json(details),
            "recorded_at": iso_utc(),
        }


__all__ = [
    "NormalizedBook",
    "ParsedMarket",
    "ResearchCollector",
    "classify_sports",
    "evaluate_crossing",
    "normalize_book",
    "parse_gamma_market",
    "walk_asks",
    "walk_bids",
]

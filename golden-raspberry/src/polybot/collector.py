"""One point-in-time Queue Echo collection and decision cycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from uuid import uuid4

from .api.clob_client import BookAttempt, BookCollection, ClobBookClient
from .api.gamma_client import GammaClient, GammaSweep
from .config import BotConfig, DATA_CONTRACT
from .db.repository import ResearchRepository
from .utils.retry import PublicJsonTransport, iso_utc


ARMS: tuple[tuple[str, int], ...] = (("DO", 1), ("RE", 2), ("MI", 3))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _array(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tags(market: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    containers: list[Any] = [market.get("tags")]
    events = market.get("events")
    if isinstance(events, list):
        containers.extend(event.get("tags") for event in events if isinstance(event, dict))
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if isinstance(item, dict):
                for key in ("id", "slug", "label", "name"):
                    if item.get(key) not in (None, ""):
                        values.add(str(item[key]))
            elif item not in (None, ""):
                values.add(str(item))
    return sorted(values)


def _event_id(market: dict[str, Any]) -> str | None:
    events = market.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        return None
    value = events[0].get("id") or events[0].get("slug")
    return str(value) if value not in (None, "") else None


def _horizon_bin(hours: float) -> str:
    if hours <= 24:
        return "6-24h"
    if hours <= 168:
        return "1-7d"
    return "7-90d"


@dataclass
class ParsedMarket:
    condition_id: str
    market_id: str | None
    event_id: str
    market_slug: str | None
    question: str | None
    observed_at: str
    end_date: str
    hours_to_end: float
    liquidity: float
    volume_total: float
    volume_24h: float
    token_ids: tuple[str, str]
    outcome_labels: tuple[str, str]
    outcome_prices: list[Any]
    gamma_best_bid: float | None
    gamma_best_ask: float | None
    gamma_spread: float | None
    raw_market_sha256: str
    event_selection_hash: str
    tags: list[str]
    panel_selected: bool = False
    shard_index: int = -1
    shard_selected: bool = False


@dataclass
class NormalizedBook:
    row: dict[str, Any]
    level_rows: list[dict[str, Any]]
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


class ResearchCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        repository: ResearchRepository,
        gamma_client: GammaClient | None = None,
        clob_client: ClobBookClient | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        if gamma_client is None or clob_client is None:
            gamma = config.trading.gamma
            transport = PublicJsonTransport(
                connect_timeout_seconds=gamma.connect_timeout_seconds,
                read_timeout_seconds=gamma.read_timeout_seconds,
                max_retries=gamma.max_retries,
                retry_base_seconds=gamma.retry_base_seconds,
                retry_max_seconds=gamma.retry_max_seconds,
                receipt_sink=repository.record_api_request,
            )
            gamma_client = gamma_client or GammaClient(gamma, transport)
            clob_client = clob_client or ClobBookClient(
                config.trading.orderbook, transport
            )
        self.gamma_client = gamma_client
        self.clob_client = clob_client

    def run_cycle(self, run_id: str) -> dict[str, Any]:
        cycle_started = datetime.now(timezone.utc)
        cycle_clock = time.monotonic()
        cycle_number = self.repository.next_cycle_number()
        sweep_id = uuid4().hex
        due_cases, expired_cases = self.repository.pending_cases(now=iso_utc(cycle_started))
        gamma_sweep = self.gamma_client.collect_market_sweep(run_id)
        parsed, membership, funnel = self._parse_gamma(gamma_sweep, cycle_started)
        self._select_panel_and_shard(parsed, membership)

        current_token_meta: dict[str, dict[str, Any]] = {}
        for market in parsed:
            if not market.shard_selected:
                continue
            for index, token in enumerate(market.token_ids):
                current_token_meta[token] = {
                    "condition_id": market.condition_id,
                    "market_id": market.market_id,
                    "event_id": market.event_id,
                    "outcome_index": index,
                    "outcome_label": market.outcome_labels[index],
                    "role": "UNIVERSE",
                }
        token_meta = dict(current_token_meta)
        for case in due_cases:
            token_meta.setdefault(
                str(case["token_id"]),
                {
                    "condition_id": case["condition_id"],
                    "market_id": None,
                    "event_id": case["event_id"],
                    "outcome_index": None,
                    "outcome_label": case["outcome_label"],
                    "role": "FOLLOWUP_ONLY",
                },
            )
        books = self.clob_client.fetch_books(
            run_id,
            sorted(token_meta),
            atomic_pairs=[
                market.token_ids for market in parsed if market.shard_selected
            ],
        )

        market_rows = self._market_rows(sweep_id, run_id, parsed)
        raw_payload_rows = self._raw_payload_rows(run_id, books)
        attempt_rows: list[dict[str, Any]] = []
        snapshot_rows: list[dict[str, Any]] = []
        level_rows: list[dict[str, Any]] = []
        normalized: dict[str, NormalizedBook] = {}
        issues: list[dict[str, Any]] = []
        for token, meta in token_meta.items():
            attempt = books.attempts.get(token)
            if attempt is None:
                attempt = BookAttempt(token, "MISSING", None, None, None)
            status = attempt.status
            book = books.books.get(token)
            normalized_book: NormalizedBook | None = None
            if status == "OBSERVED" and book is not None:
                try:
                    normalized_book = self._normalize_book(
                        sweep_id, run_id, token, meta, attempt, book
                    )
                except (TypeError, ValueError) as error:
                    status = "MALFORMED"
                    issues.append(
                        self._issue(
                            run_id,
                            sweep_id,
                            "HIGH",
                            "MALFORMED_CLOB_BOOK",
                            {"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "error": str(error)[:300]},
                        )
                    )
            attempt_rows.append(
                {
                    "attempt_id": uuid4().hex,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "condition_id": meta.get("condition_id"),
                    "token_id": token,
                    "outcome_index": meta.get("outcome_index"),
                    "outcome_label": meta.get("outcome_label"),
                    "attempt_role": meta["role"],
                    "status": status,
                    "request_id": attempt.request_id,
                    "request_started_at": attempt.started_at,
                    "received_at": attempt.received_at,
                    "error_type": attempt.error_type,
                    "error_message": attempt.error_message,
                }
            )
            if normalized_book is not None:
                normalized[token] = normalized_book
                snapshot_rows.append(normalized_book.row)
                level_rows.extend(normalized_book.level_rows)

        pair_info = self._score_pairs(parsed, normalized)
        self._current_normalized = normalized
        try:
            decision_rows, case_rows = self._decisions_and_cases(
                sweep_id, run_id, parsed, normalized, pair_info
            )
        finally:
            del self._current_normalized
        followup_rows = self._followups(
            run_id, due_cases, expired_cases, normalized
        )
        requested_count = len(token_meta)
        observed_count = len(normalized)
        coverage = observed_count / requested_count if requested_count else 1.0
        if coverage < 0.95:
            issues.append(
                self._issue(
                    run_id,
                    sweep_id,
                    "HIGH" if coverage < 0.90 else "WARN",
                    "CLOB_PAIR_COVERAGE_BELOW_GATE",
                    {"requested": requested_count, "normalized": observed_count, "coverage": coverage},
                )
            )
        if not parsed:
            issues.append(
                self._issue(run_id, sweep_id, "HIGH", "EMPTY_ELIGIBLE_GAMMA_FRAME", funnel)
            )

        membership_bytes = _json(membership).encode("utf-8")
        membership_digest = hashlib.sha256(membership_bytes).hexdigest()
        membership_blob = gzip.compress(membership_bytes, compresslevel=6)
        completed = datetime.now(timezone.utc)
        sweep_row = {
            "sweep_id": sweep_id,
            "run_id": run_id,
            "cycle_number": cycle_number,
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.trading.strategy_source_digest,
            "started_at": iso_utc(cycle_started),
            "completed_at": iso_utc(completed),
            "cursor_complete": 1,
            "page_count": len(gamma_sweep.pages),
            "source_envelope_count": sum(len(page.markets) for page in gamma_sweep.pages),
            "parsed_market_count": funnel["structurally_parsed"],
            "eligible_market_count": len(parsed),
            "membership_sha256": membership_digest,
            "membership_encoding": "gzip-json-v1",
            "membership_blob": membership_blob,
            "funnel_json": _json(funnel),
            "source_filter_json": _json(
                {
                    "endpoint": "/markets/keyset",
                    "closed": False,
                    "liquidity_num_min": self.config.trading.gamma.min_liquidity,
                    "volume_num_min": self.config.trading.gamma.min_total_volume,
                    "order": None,
                }
            ),
            "data_contract": DATA_CONTRACT,
        }
        runtime_seconds = time.monotonic() - cycle_clock
        stats = {
            "cycle_number": cycle_number,
            "shard_index": self.config.trading.experiment.shard_index,
            "gamma_pages": len(gamma_sweep.pages),
            "source_envelope_markets": sweep_row["source_envelope_count"],
            "eligible_markets": len(parsed),
            "panel_markets": sum(m.panel_selected for m in parsed),
            "shard_markets": sum(m.shard_selected for m in parsed),
            "books_requested": requested_count,
            "books_normalized": observed_count,
            "book_coverage": coverage,
            "decisions": len(decision_rows),
            "qualified_by_arm": {
                arm: sum(row["qualified"] for row in decision_rows if row["arm"] == arm)
                for arm, _ in ARMS
            },
            "new_cases": len(case_rows),
            "followup_attempts": len(followup_rows),
            "quality_issues": len(issues),
            "runtime_seconds": round(runtime_seconds, 3),
            "membership_uncompressed_bytes": len(membership_bytes),
            "membership_compressed_bytes": len(membership_blob),
        }
        db_bytes = self.repository.db_path.stat().st_size if self.repository.db_path.exists() else 0
        wal_path = Path(str(self.repository.db_path) + "-wal")
        cycle_stat_row = {
            "cycle_stat_id": uuid4().hex,
            "run_id": run_id,
            "sweep_id": sweep_id,
            "cycle_number": cycle_number,
            "config_hash": self.config.config_hash,
            "shard_index": self.config.trading.experiment.shard_index,
            "started_at": iso_utc(cycle_started),
            "completed_at": iso_utc(completed),
            "runtime_seconds": runtime_seconds,
            "stats_json": _json(stats),
            "db_bytes": db_bytes,
            "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
        }
        self.repository.publish_cycle(
            {
                "market_sweeps": [sweep_row],
                "market_observations": market_rows,
                "raw_payloads": raw_payload_rows,
                "orderbook_token_attempts": attempt_rows,
                "orderbook_snapshots": snapshot_rows,
                "orderbook_levels": level_rows,
                "signal_decisions": decision_rows,
                "research_cases": case_rows,
                "followup_attempts": followup_rows,
                "data_quality_issues": issues,
                "cycle_stats": [cycle_stat_row],
            }
        )
        return stats

    def _parse_gamma(
        self, sweep: GammaSweep, now: datetime
    ) -> tuple[list[ParsedMarket], list[dict[str, Any]], dict[str, int]]:
        gamma = self.config.trading.gamma
        funnel = {
            "source_envelope": 0,
            "structurally_parsed": 0,
            "standard_binary": 0,
            "gamma_price_band": 0,
            "active_orderbook": 0,
            "non_neg_risk": 0,
            "volume_24h": 0,
            "horizon": 0,
            "event_identified": 0,
            "eligible": 0,
        }
        parsed: list[ParsedMarket] = []
        membership: list[dict[str, Any]] = []
        seen_conditions: set[str] = set()
        ordinal = 0
        for page in sweep.pages:
            for item_number, market in enumerate(page.markets):
                ordinal += 1
                funnel["source_envelope"] += 1
                raw_sha = _sha(market)
                condition = str(market.get("conditionId") or "").strip()
                market_id = str(market.get("id")) if market.get("id") not in (None, "") else None
                member = {
                    "ordinal": ordinal,
                    "page_number": page.page_number,
                    "item_number": item_number,
                    "page_received_at": page.received_at,
                    "source_market_key": condition or market_id or raw_sha,
                    "condition_id": condition or None,
                    "market_id": market_id,
                    "raw_market_sha256": raw_sha,
                    "eligible": False,
                    "rejection_reason": "unparsed",
                    "panel_selected": False,
                    "shard_index": None,
                    "shard_selected": False,
                }
                membership.append(member)
                outcomes = _array(market.get("outcomes"))
                tokens = _array(market.get("clobTokenIds"))
                prices = _array(market.get("outcomePrices")) or []
                end = _dt(market.get("endDate") or market.get("endDateIso"))
                liquidity = _float(market.get("liquidityNum") or market.get("liquidity"))
                volume_total = _float(market.get("volumeNum") or market.get("volume"))
                volume_24h = _float(market.get("volume24hr"))
                if (
                    not condition
                    or condition in seen_conditions
                    or outcomes is None
                    or tokens is None
                    or end is None
                    or liquidity is None
                    or volume_total is None
                    or volume_24h is None
                ):
                    member["rejection_reason"] = "structural_parse"
                    continue
                seen_conditions.add(condition)
                funnel["structurally_parsed"] += 1
                labels = tuple(str(value).strip() for value in outcomes)
                token_values = tuple(str(value).strip() for value in tokens)
                if (
                    len(labels) != 2
                    or tuple(value.lower() for value in labels) != ("yes", "no")
                    or len(token_values) != 2
                    or not all(token_values)
                    or token_values[0] == token_values[1]
                ):
                    member["rejection_reason"] = "not_standard_binary"
                    continue
                funnel["standard_binary"] += 1
                numeric_prices = [_float(value) for value in prices]
                if (
                    len(numeric_prices) != 2
                    or any(value is None for value in numeric_prices)
                    or not all(0.20 <= float(value) <= 0.80 for value in numeric_prices)
                ):
                    member["rejection_reason"] = "gamma_price_band"
                    continue
                funnel["gamma_price_band"] += 1
                if not (
                    market.get("active") is True
                    and market.get("closed") is not True
                    and market.get("enableOrderBook") is True
                    and market.get("acceptingOrders") is True
                ):
                    member["rejection_reason"] = "inactive_or_no_orderbook"
                    continue
                funnel["active_orderbook"] += 1
                if bool(market.get("negRisk")):
                    member["rejection_reason"] = "neg_risk"
                    continue
                funnel["non_neg_risk"] += 1
                if liquidity < gamma.min_liquidity or volume_24h < gamma.min_volume_24h:
                    member["rejection_reason"] = "liquidity_or_volume24"
                    continue
                funnel["volume_24h"] += 1
                hours = (end - now).total_seconds() / 3600
                if not (gamma.min_hours_to_end <= hours <= gamma.max_hours_to_end):
                    member["rejection_reason"] = "horizon"
                    continue
                funnel["horizon"] += 1
                event = _event_id(market)
                if event is None:
                    member["rejection_reason"] = "missing_event_id"
                    continue
                funnel["event_identified"] += 1
                selection_hash = hashlib.sha256(condition.encode("utf-8")).hexdigest()
                parsed_market = ParsedMarket(
                    condition_id=condition,
                    market_id=market_id,
                    event_id=event,
                    market_slug=str(market.get("slug")) if market.get("slug") else None,
                    question=str(market.get("question")) if market.get("question") else None,
                    observed_at=page.received_at,
                    end_date=iso_utc(end),
                    hours_to_end=hours,
                    liquidity=liquidity,
                    volume_total=volume_total,
                    volume_24h=volume_24h,
                    token_ids=(token_values[0], token_values[1]),
                    outcome_labels=(labels[0], labels[1]),
                    outcome_prices=prices,
                    gamma_best_bid=_float(market.get("bestBid")),
                    gamma_best_ask=_float(market.get("bestAsk")),
                    gamma_spread=_float(market.get("spread")),
                    raw_market_sha256=raw_sha,
                    event_selection_hash=selection_hash,
                    tags=_tags(market),
                )
                parsed.append(parsed_market)
                member["eligible"] = True
                member["rejection_reason"] = "eligible_pre_panel"
                member["event_id"] = event
                member["event_selection_hash"] = selection_hash
                funnel["eligible"] += 1
        return parsed, membership, funnel

    def _select_panel_and_shard(
        self, parsed: list[ParsedMarket], membership: list[dict[str, Any]]
    ) -> None:
        by_event: dict[str, list[ParsedMarket]] = {}
        for market in parsed:
            by_event.setdefault(market.event_id, []).append(market)
        for markets in by_event.values():
            winner = min(markets, key=lambda value: (value.event_selection_hash, value.condition_id))
            winner.panel_selected = True
            winner.shard_index = int(winner.event_selection_hash, 16) % self.config.trading.experiment.shard_count
            winner.shard_selected = winner.shard_index == self.config.trading.experiment.shard_index
        lookup = {market.condition_id: market for market in parsed}
        for member in membership:
            condition = member.get("condition_id")
            market = lookup.get(str(condition)) if condition else None
            if market is None:
                continue
            member["panel_selected"] = market.panel_selected
            member["shard_index"] = market.shard_index if market.panel_selected else None
            member["shard_selected"] = market.shard_selected
            if not market.panel_selected:
                member["rejection_reason"] = "event_hash_panel_loser"
            elif not market.shard_selected:
                member["rejection_reason"] = "other_hash_shard"
            else:
                member["rejection_reason"] = "selected_for_books"

    def _market_rows(
        self, sweep_id: str, run_id: str, parsed: list[ParsedMarket]
    ) -> list[dict[str, Any]]:
        return [
            {
                "observation_id": uuid4().hex,
                "sweep_id": sweep_id,
                "run_id": run_id,
                "condition_id": market.condition_id,
                "market_id": market.market_id,
                "event_id": market.event_id,
                "market_slug": market.market_slug,
                "question": market.question,
                "observed_at": market.observed_at,
                "end_date": market.end_date,
                "hours_to_end": market.hours_to_end,
                "liquidity": market.liquidity,
                "volume_total": market.volume_total,
                "volume_24h": market.volume_24h,
                "token_ids_json": _json(market.token_ids),
                "outcome_labels_json": _json(market.outcome_labels),
                "outcome_prices_json": _json(market.outcome_prices),
                "gamma_best_bid": market.gamma_best_bid,
                "gamma_best_ask": market.gamma_best_ask,
                "gamma_spread": market.gamma_spread,
                "raw_market_sha256": market.raw_market_sha256,
                "event_selection_hash": market.event_selection_hash,
                "panel_selected": int(market.panel_selected),
                "shard_index": market.shard_index,
                "shard_selected": int(market.shard_selected),
                "tags_json": _json(market.tags),
            }
            for market in parsed
        ]

    @staticmethod
    def _raw_payload_rows(run_id: str, collection: BookCollection) -> list[dict[str, Any]]:
        rows = []
        for payload in collection.raw_payloads:
            compressed = gzip.compress(payload.raw, compresslevel=6)
            rows.append(
                {
                    "payload_id": uuid4().hex,
                    "run_id": run_id,
                    "request_id": payload.request_id,
                    "payload_kind": "clob_books",
                    "content_encoding": "gzip",
                    "payload_sha256": payload.response_sha256,
                    "uncompressed_bytes": len(payload.raw),
                    "compressed_bytes": len(compressed),
                    "payload_blob": compressed,
                    "recorded_at": payload.received_at,
                }
            )
        return rows

    def _normalize_book(
        self,
        sweep_id: str,
        run_id: str,
        token: str,
        meta: dict[str, Any],
        attempt: BookAttempt,
        book: dict[str, Any],
    ) -> NormalizedBook:
        if attempt.received_at is None:
            raise ValueError("observed book is missing receipt time")
        bids = self._levels(book.get("bids"), reverse=True)
        asks = self._levels(book.get("asks"), reverse=False)
        tick = _float(book.get("tick_size"))
        min_order = _float(book.get("min_order_size"))
        if tick is None or tick <= 0 or min_order is None or min_order <= 0:
            raise ValueError("book tick/minimum order metadata is invalid")
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        bid_weighted = self._weighted_size(bids, best_bid, tick, side="BID")
        ask_weighted = self._weighted_size(asks, best_ask, tick, side="ASK")
        denominator = bid_weighted + ask_weighted
        imbalance = (bid_weighted - ask_weighted) / denominator if denominator > 0 else None
        near = self.config.trading.orderbook.near_touch_window
        near_bid = (
            sum(price * size for price, size in bids if price >= best_bid - near - 1e-12)
            if best_bid is not None
            else 0.0
        )
        near_ask = (
            sum(price * size for price, size in asks if price <= best_ask + near + 1e-12)
            if best_ask is not None
            else 0.0
        )
        entry_cost, entry_shares, entry_vwap, entry_complete, used_asks = self._walk_asks(
            asks, self.config.trading.experiment.simulated_notional_usdc
        )
        best_bid_notional = best_bid * bids[0][1] if best_bid is not None else 0.0
        best_ask_notional = best_ask * asks[0][1] if best_ask is not None else 0.0
        one_tick = bool(
            spread is not None
            and abs(spread - tick) <= max(1e-9, tick * 0.05)
        )
        orderbook = self.config.trading.orderbook
        quote_eligible = bool(
            best_bid is not None
            and best_ask is not None
            and one_tick
            and orderbook.min_price <= best_ask <= orderbook.max_price
            and best_bid_notional >= self.config.trading.experiment.simulated_notional_usdc
            and best_ask_notional >= self.config.trading.experiment.simulated_notional_usdc
            and near_bid >= orderbook.min_near_touch_notional
            and near_ask >= orderbook.min_near_touch_notional
            and entry_complete
        )
        snapshot_id = uuid4().hex
        raw_hash = _sha(book)
        row = {
            "snapshot_id": snapshot_id,
            "sweep_id": sweep_id,
            "run_id": run_id,
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.trading.strategy_source_digest,
            "condition_id": meta.get("condition_id"),
            "market_id": meta.get("market_id"),
            "event_id": meta.get("event_id"),
            "token_id": token,
            "outcome_index": meta.get("outcome_index"),
            "outcome_label": meta.get("outcome_label"),
            "request_started_at": attempt.started_at,
            "observed_at": attempt.received_at,
            "source_timestamp": str(book.get("timestamp")) if book.get("timestamp") is not None else None,
            "source_hash": str(book.get("hash")) if book.get("hash") is not None else None,
            "raw_book_sha256": raw_hash,
            "bid_level_count": len(bids),
            "ask_level_count": len(asks),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "tick_size": tick,
            "min_order_size": min_order,
            "best_bid_notional": best_bid_notional,
            "best_ask_notional": best_ask_notional,
            "one_tick_spread": int(one_tick),
            "near_bid_notional": near_bid,
            "near_ask_notional": near_ask,
            "weighted_imbalance": imbalance,
            "pair_score": None,
            "entry_notional_usdc": entry_cost,
            "entry_vwap": entry_vwap,
            "entry_shares": entry_shares,
            "entry_complete": int(entry_complete),
            "quote_eligible": int(quote_eligible),
            "candidate_up": 0,
        }
        level_rows: list[dict[str, Any]] = []
        for side, levels in (("BID", bids), ("ASK", asks)):
            best = levels[0][0] if levels else None
            for index, (price, size) in enumerate(levels):
                near_flag = bool(
                    best is not None
                    and (
                        price >= best - near - 1e-12
                        if side == "BID"
                        else price <= best + near + 1e-12
                    )
                )
                used = side == "ASK" and index in used_asks
                if not (
                    index < orderbook.stored_levels_per_side or near_flag or used
                ):
                    continue
                level_rows.append(
                    {
                        "level_id": uuid4().hex,
                        "snapshot_id": snapshot_id,
                        "side": side,
                        "level_index": index,
                        "price": price,
                        "size": size,
                        "in_near_touch_window": int(near_flag),
                        "used_for_entry": int(used),
                    }
                )
        return NormalizedBook(row=row, level_rows=level_rows, bids=bids, asks=asks)

    @staticmethod
    def _levels(value: Any, *, reverse: bool) -> list[tuple[float, float]]:
        if not isinstance(value, list):
            raise ValueError("book side must be a list")
        levels: list[tuple[float, float]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("book level must be an object")
            price = _float(item.get("price"))
            size = _float(item.get("size"))
            if price is None or size is None or not (0 < price < 1) or size <= 0:
                raise ValueError("book level price/size is invalid")
            levels.append((price, size))
        levels.sort(key=lambda pair: pair[0], reverse=reverse)
        return levels

    def _weighted_size(
        self,
        levels: list[tuple[float, float]],
        best: float | None,
        tick: float,
        *,
        side: str,
    ) -> float:
        if best is None:
            return 0.0
        exp = self.config.trading.experiment
        total = 0.0
        for price, size in levels:
            raw_distance = (best - price) / tick if side == "BID" else (price - best) / tick
            distance = round(raw_distance)
            if distance < 0 or distance >= exp.weighted_tick_levels:
                continue
            if abs(raw_distance - distance) > 0.05:
                continue
            total += (exp.level_weight_decay**distance) * size
        return total

    @staticmethod
    def _walk_asks(
        asks: list[tuple[float, float]], notional: float
    ) -> tuple[float, float, float | None, bool, set[int]]:
        remaining = notional
        shares = 0.0
        cost = 0.0
        used: set[int] = set()
        for index, (price, size) in enumerate(asks):
            if remaining <= 1e-9:
                break
            available = price * size
            spent = min(remaining, available)
            if spent <= 0:
                continue
            shares += spent / price
            cost += spent
            remaining -= spent
            used.add(index)
        complete = remaining <= 1e-6
        return cost, shares, cost / shares if shares else None, complete, used

    @staticmethod
    def _walk_bids(
        bids: list[tuple[float, float]], shares: float
    ) -> tuple[float, float | None, bool]:
        remaining = shares
        proceeds = 0.0
        sold = 0.0
        for price, size in bids:
            if remaining <= 1e-9:
                break
            quantity = min(remaining, size)
            proceeds += quantity * price
            sold += quantity
            remaining -= quantity
        return proceeds, proceeds / sold if sold else None, remaining <= 1e-6

    def _score_pairs(
        self,
        parsed: list[ParsedMarket],
        normalized: dict[str, NormalizedBook],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        threshold = self.config.trading.experiment.score_threshold
        for market in parsed:
            if not market.shard_selected:
                continue
            yes = normalized.get(market.token_ids[0])
            no = normalized.get(market.token_ids[1])
            info: dict[str, Any] = {
                "pair_valid": False,
                "score": None,
                "selected": None,
                "opposite": None,
                "neutral": False,
                "skew_seconds": None,
                "snapshot_ids": [],
                "reason": "missing_pair",
            }
            if yes is None or no is None:
                result[market.condition_id] = info
                continue
            info["snapshot_ids"] = [yes.row["snapshot_id"], no.row["snapshot_id"]]
            yes_time = _dt(yes.row["observed_at"])
            no_time = _dt(no.row["observed_at"])
            skew = abs((yes_time - no_time).total_seconds()) if yes_time and no_time else math.inf
            info["skew_seconds"] = skew
            iy = yes.row["weighted_imbalance"]
            ino = no.row["weighted_imbalance"]
            if iy is None or ino is None or skew > 2:
                info["reason"] = "invalid_pair_feature_or_skew"
                result[market.condition_id] = info
                continue
            score = (iy - ino) / 2
            yes.row["pair_score"] = score
            no.row["pair_score"] = score
            info["score"] = score
            pair_valid = bool(yes.row["quote_eligible"] and no.row["quote_eligible"])
            info["pair_valid"] = pair_valid
            info["neutral"] = pair_valid and abs(score) <= self.config.trading.experiment.neutral_score_max
            if not pair_valid:
                info["reason"] = "pair_quote_gate"
            elif score >= threshold and iy > 0 and ino < 0:
                info["selected"] = yes
                info["opposite"] = no
                info["reason"] = "yes_displayed_depth_pressure"
                yes.row["candidate_up"] = 1
            elif score <= -threshold and ino > 0 and iy < 0:
                info["selected"] = no
                info["opposite"] = yes
                info["reason"] = "no_displayed_depth_pressure"
                no.row["candidate_up"] = 1
            else:
                info["reason"] = "score_below_or_direction_incoherent"
            result[market.condition_id] = info
        return result

    def _decisions_and_cases(
        self,
        sweep_id: str,
        run_id: str,
        parsed: list[ParsedMarket],
        normalized: dict[str, NormalizedBook],
        pair_info: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        decisions: list[dict[str, Any]] = []
        cases: list[dict[str, Any]] = []
        neutral_pool = self._neutral_pool(parsed, pair_info)
        exp = self.config.trading.experiment
        for market in parsed:
            if not market.shard_selected:
                continue
            info = pair_info.get(market.condition_id, {})
            selected: NormalizedBook | None = info.get("selected")
            evaluated_at = (
                selected.row["observed_at"]
                if selected is not None
                else max(
                    [
                        normalized[token].row["observed_at"]
                        for token in market.token_ids
                        if token in normalized
                    ],
                    default=market.observed_at,
                )
            )
            evaluated_dt = _dt(evaluated_at) or datetime.now(timezone.utc)
            signal_prior = self._prior_move(
                market,
                selected,
                evaluated_at,
            )
            for arm, steps in ARMS:
                decision_id = uuid4().hex
                prior: list[dict[str, Any]] = []
                if selected is not None:
                    prior = self.repository.recent_candidate_snapshots(
                        condition_id=market.condition_id,
                        token_id=selected.row["token_id"],
                        config_hash=self.config.config_hash,
                        strategy_source_digest=self.config.trading.strategy_source_digest,
                        before=evaluated_at,
                        limit=steps - 1,
                    )
                history = prior + (
                    [
                        {
                            "snapshot_id": selected.row["snapshot_id"],
                            "observed_at": evaluated_at,
                            "pair_score": selected.row["pair_score"],
                        }
                    ]
                    if selected is not None
                    else []
                )
                times = [_dt(item["observed_at"]) for item in history]
                gaps = [
                    (times[index] - times[index - 1]).total_seconds() / 60
                    for index in range(1, len(times))
                    if times[index] is not None and times[index - 1] is not None
                ]
                persistence = bool(
                    selected is not None
                    and len(history) == steps
                    and len(gaps) == max(0, steps - 1)
                    and all(
                        exp.history_gap_min_minutes <= gap <= exp.history_gap_max_minutes
                        for gap in gaps
                    )
                )
                last = self.repository.last_qualified_at(
                    event_id=market.event_id,
                    arm=arm,
                    config_hash=self.config.config_hash,
                )
                last_dt = _dt(last)
                cooldown = last_dt is None or (
                    evaluated_dt - last_dt >= timedelta(hours=exp.cooldown_hours)
                )
                in_window = exp.start_utc <= evaluated_dt < exp.end_utc
                qualified = bool(selected is not None and persistence and cooldown and in_window)
                if selected is None:
                    rejection = str(info.get("reason") or "no_directional_candidate")
                elif not persistence:
                    rejection = "insufficient_consecutive_displayed_pressure"
                elif not cooldown:
                    rejection = "arm_condition_cooldown"
                elif not in_window:
                    rejection = "outside_preregistered_window"
                else:
                    rejection = "qualified"
                target = evaluated_dt + timedelta(minutes=exp.followup_minutes) if qualified else None
                window_end = (
                    target + timedelta(minutes=exp.followup_grace_minutes)
                    if target is not None
                    else None
                )
                control = (
                    self._match_control(
                        market,
                        selected,
                        signal_prior,
                        neutral_pool,
                    )
                    if qualified
                    else None
                )
                decision = {
                    "decision_id": decision_id,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "config_hash": self.config.config_hash,
                    "strategy_source_digest": self.config.trading.strategy_source_digest,
                    "condition_id": market.condition_id,
                    "market_id": market.market_id,
                    "event_id": market.event_id,
                    "arm": arm,
                    "confirmation_steps": steps,
                    "evaluated_at": evaluated_at,
                    "pair_snapshot_ids_json": _json(info.get("snapshot_ids", [])),
                    "pair_score": info.get("score"),
                    "pair_received_skew_seconds": info.get("skew_seconds"),
                    "neutral_candidate": int(bool(info.get("neutral"))),
                    "selected_snapshot_id": selected.row["snapshot_id"] if selected else None,
                    "selected_token_id": selected.row["token_id"] if selected else None,
                    "selected_outcome_label": selected.row["outcome_label"] if selected else None,
                    "history_snapshot_ids_json": _json([item["snapshot_id"] for item in history]),
                    "history_timestamps_json": _json([item["observed_at"] for item in history]),
                    "history_scores_json": _json([item["pair_score"] for item in history]),
                    "history_gaps_minutes_json": _json(gaps),
                    "one_sided_candidate": int(selected is not None),
                    "persistence_passed": int(persistence),
                    "cooldown_allowed": int(cooldown),
                    "experiment_window_eligible": int(in_window),
                    "qualified": int(qualified),
                    "rejection_reason": rejection,
                    "target_at": iso_utc(target) if target else None,
                    "window_end": iso_utc(window_end) if window_end else None,
                    "prior_price_snapshot_id": signal_prior["snapshot_id"],
                    "prior_15m_move": signal_prior["move"],
                    "prior_move_bin": signal_prior["bin"],
                    "matched_control_snapshot_id": control["book"].row["snapshot_id"] if control else None,
                    "matched_control_prior_price_snapshot_id": (
                        control["prior"]["snapshot_id"] if control else None
                    ),
                    "matched_control_prior_15m_move": (
                        control["prior"]["move"] if control else None
                    ),
                    "matched_control_prior_move_bin": (
                        control["prior"]["bin"] if control else None
                    ),
                    "control_match_distance": control["distance"] if control else None,
                }
                decisions.append(decision)
                if qualified and selected is not None and target and window_end:
                    cases.extend(
                        self._case_rows(
                            decision_id,
                            market,
                            selected,
                            info.get("opposite"),
                            control,
                            target,
                            window_end,
                        )
                    )
        return decisions, cases

    def _neutral_pool(
        self, parsed: list[ParsedMarket], pair_info: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        pool: list[dict[str, Any]] = []
        for market in parsed:
            info = pair_info.get(market.condition_id, {})
            if not market.shard_selected or not info.get("neutral"):
                continue
            yes_no = [info_book for info_book in (info.get("selected"), info.get("opposite")) if info_book]
            if yes_no:
                continue
            # Neutral pairs have no directional selected book. Choose a side by
            # frozen condition hash, independent of any future return.
            token_index = int(market.event_selection_hash[-2:], 16) % 2
            # Pair snapshots are already normalized and can be resolved by token.
            pool.append({"market": market, "token_index": token_index})
        return pool

    def _match_control(
        self,
        signal_market: ParsedMarket,
        selected: NormalizedBook | None,
        signal_prior: dict[str, Any],
        neutral_pool: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if selected is None or selected.row["best_ask"] is None:
            return None
        candidates: list[
            tuple[float, str, ParsedMarket, NormalizedBook, dict[str, Any]]
        ] = []
        # Resolve neutral books from the current cycle via the selected snapshot
        # IDs encoded in pair_info is intentionally avoided; token hash fixes side.
        # The caller's normalized map is exposed through a short-lived attribute.
        normalized = getattr(self, "_current_normalized", None)
        if not isinstance(normalized, dict):
            return None
        signal_depth = selected.row["near_bid_notional"] + selected.row["near_ask_notional"]
        for item in neutral_pool:
            market: ParsedMarket = item["market"]
            if market.event_id == signal_market.event_id:
                continue
            book = normalized.get(market.token_ids[item["token_index"]])
            if book is None or not book.row["quote_eligible"]:
                continue
            control_prior = self._prior_move(
                market,
                book,
                str(book.row["observed_at"]),
            )
            if control_prior["bin"] != signal_prior["bin"]:
                continue
            if int(book.row["best_ask"] * 10) != int(selected.row["best_ask"] * 10):
                continue
            if _horizon_bin(market.hours_to_end) != _horizon_bin(signal_market.hours_to_end):
                continue
            depth = book.row["near_bid_notional"] + book.row["near_ask_notional"]
            if min(depth, signal_depth) <= 0 or max(depth, signal_depth) / min(depth, signal_depth) > 2:
                continue
            distance = (
                abs(book.row["best_ask"] - selected.row["best_ask"]) / 0.10
                + abs(math.log1p(market.volume_24h) - math.log1p(signal_market.volume_24h)) / 2
                + abs(math.log1p(depth) - math.log1p(signal_depth)) / math.log(2)
            )
            candidates.append(
                (distance, market.condition_id, market, book, control_prior)
            )
        if not candidates:
            return None
        distance, _, market, book, prior = min(
            candidates, key=lambda value: (value[0], value[1])
        )
        return {
            "distance": distance,
            "market": market,
            "book": book,
            "prior": prior,
        }

    def _prior_move(
        self,
        market: ParsedMarket,
        book: NormalizedBook | None,
        observed_at: str,
    ) -> dict[str, Any]:
        missing = {"snapshot_id": None, "move": None, "bin": "MISSING"}
        if book is None or book.row.get("best_ask") is None:
            return missing
        observed = _dt(observed_at)
        if observed is None:
            return missing
        prior = self.repository.latest_quote_snapshot(
            condition_id=market.condition_id,
            token_id=str(book.row["token_id"]),
            config_hash=self.config.config_hash,
            strategy_source_digest=self.config.trading.strategy_source_digest,
            after=iso_utc(observed - timedelta(minutes=15)),
            before=iso_utc(observed),
        )
        if prior is None or prior.get("best_ask") is None:
            return missing
        move = float(book.row["best_ask"]) - float(prior["best_ask"])
        if move < -0.01:
            move_bin = "DOWN"
        elif move > 0.01:
            move_bin = "UP"
        else:
            move_bin = "FLAT"
        return {
            "snapshot_id": str(prior["snapshot_id"]),
            "move": move,
            "bin": move_bin,
        }

    def _case_rows(
        self,
        decision_id: str,
        market: ParsedMarket,
        selected: NormalizedBook,
        opposite: NormalizedBook | None,
        control: dict[str, Any] | None,
        target: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        pair_id = uuid4().hex
        candidates: list[tuple[str, ParsedMarket, NormalizedBook, float | None]] = [
            ("SIGNAL", market, selected, None)
        ]
        if opposite is not None and opposite.row["quote_eligible"]:
            candidates.append(("OPPOSITE", market, opposite, None))
        if control is not None:
            candidates.append(("CONTROL", control["market"], control["book"], control["distance"]))
        rows: list[dict[str, Any]] = []
        for kind, case_market, book, distance in candidates:
            if not book.row["entry_complete"] or book.row["entry_vwap"] is None:
                continue
            rows.append(
                {
                    "case_id": uuid4().hex,
                    "decision_id": decision_id,
                    "case_kind": kind,
                    "matched_pair_id": pair_id,
                    "condition_id": case_market.condition_id,
                    "event_id": case_market.event_id,
                    "token_id": book.row["token_id"],
                    "outcome_label": book.row["outcome_label"],
                    "entry_snapshot_id": book.row["snapshot_id"],
                    "entry_at": book.row["observed_at"],
                    "entry_cost_usdc": book.row["entry_notional_usdc"],
                    "entry_shares": book.row["entry_shares"],
                    "entry_vwap": book.row["entry_vwap"],
                    "target_at": iso_utc(target),
                    "window_end": iso_utc(window_end),
                    "control_match_distance": distance,
                }
            )
        return rows

    def _followups(
        self,
        run_id: str,
        due_cases: list[dict[str, Any]],
        expired_cases: list[dict[str, Any]],
        normalized: dict[str, NormalizedBook],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        exp = self.config.trading.experiment
        for case in expired_cases:
            rows.append(
                {
                    "followup_id": uuid4().hex,
                    "case_id": case["case_id"],
                    "observing_run_id": run_id,
                    "attempted_at": iso_utc(),
                    "status": "WINDOW_EXPIRED",
                    "source_snapshot_id": None,
                    "observed_at": None,
                    "exit_bid": None,
                    "exit_vwap": None,
                    "exit_proceeds_usdc": None,
                    "executable_return_bps": None,
                    "base_stressed_return_bps": None,
                    "severe_stressed_return_bps": None,
                    "details_json": _json({"reason": "no_valid_quote_before_window_end"}),
                }
            )
        for case in due_cases:
            book = normalized.get(str(case["token_id"]))
            attempted_at = (
                (book.row["request_started_at"] or book.row["observed_at"])
                if book is not None
                else iso_utc()
            )
            base = {
                "followup_id": uuid4().hex,
                "case_id": case["case_id"],
                "observing_run_id": run_id,
                "attempted_at": attempted_at,
                "source_snapshot_id": book.row["snapshot_id"] if book else None,
                "observed_at": book.row["observed_at"] if book else None,
                "exit_bid": book.row["best_bid"] if book else None,
            }
            if book is None:
                rows.append(
                    {
                        **base,
                        "status": "SOURCE_MISSING",
                        "exit_vwap": None,
                        "exit_proceeds_usdc": None,
                        "executable_return_bps": None,
                        "base_stressed_return_bps": None,
                        "severe_stressed_return_bps": None,
                        "details_json": _json({"reason": "book_not_normalized"}),
                    }
                )
                continue
            attempted_dt = _dt(attempted_at)
            target_dt = _dt(case["target_at"])
            end_dt = _dt(case["window_end"])
            if (
                attempted_dt is None
                or target_dt is None
                or end_dt is None
                or attempted_dt < target_dt
                or attempted_dt > end_dt
            ):
                rows.append(
                    {
                        **base,
                        "status": "INVALID_QUOTE",
                        "exit_vwap": None,
                        "exit_proceeds_usdc": None,
                        "executable_return_bps": None,
                        "base_stressed_return_bps": None,
                        "severe_stressed_return_bps": None,
                        "details_json": _json({"reason": "request_started_outside_followup_window"}),
                    }
                )
                continue
            proceeds, exit_vwap, complete = self._walk_bids(
                book.bids, float(case["entry_shares"])
            )
            if not complete or exit_vwap is None:
                rows.append(
                    {
                        **base,
                        "status": "INVALID_QUOTE",
                        "exit_vwap": exit_vwap,
                        "exit_proceeds_usdc": proceeds,
                        "executable_return_bps": None,
                        "base_stressed_return_bps": None,
                        "severe_stressed_return_bps": None,
                        "details_json": _json({"reason": "insufficient_exit_bid_depth"}),
                    }
                )
                continue
            raw_return = (proceeds / float(case["entry_cost_usdc"]) - 1) * 10_000
            rows.append(
                {
                    **base,
                    "status": "QUOTE_COMPLETE",
                    "exit_vwap": exit_vwap,
                    "exit_proceeds_usdc": proceeds,
                    "executable_return_bps": raw_return,
                    "base_stressed_return_bps": raw_return - exp.base_cost_stress_bps,
                    "severe_stressed_return_bps": raw_return - exp.severe_taker_stress_bps,
                    "details_json": _json({"shares": case["entry_shares"], "cost_usdc": case["entry_cost_usdc"]}),
                }
            )
        return rows

    @staticmethod
    def _issue(
        run_id: str,
        sweep_id: str,
        severity: str,
        code: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "issue_id": uuid4().hex,
            "run_id": run_id,
            "sweep_id": sweep_id,
            "severity": severity,
            "issue_code": code,
            "details_json": _json(details),
            "recorded_at": iso_utc(),
        }

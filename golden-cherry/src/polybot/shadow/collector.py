"""Prospective paired-cell collector for Cherry Shadow Resolution v2."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping
from uuid import uuid4

from .clients import BookRead, GammaSweep, ResolutionRead, ShadowClobClient, ShadowGammaClient
from .config import EntryBand, ExitPolicy, ShadowConfig, canonical_json
from .db import ShadowRepository
from .transport import CollectionDeadline, iso_utc


def _array(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, list) else None
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event(market: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_events = market.get("events")
    events = raw_events if isinstance(raw_events, list) else []
    valid = [dict(item) for item in events if isinstance(item, Mapping)]
    ids = {str(item.get("id") or "").strip() for item in valid}
    ids.discard("")
    if len(ids) != 1:
        return None
    event_id = next(iter(ids))
    matching = [item for item in valid if str(item.get("id") or "").strip() == event_id]
    if len(matching) != 1:
        return None
    return matching[0]


def _tags(value: Any) -> list[dict[str, Any] | str]:
    rows = _array(value) or []
    return [dict(row) if isinstance(row, Mapping) else str(row) for row in rows]


def _aligned_identity(market: Mapping[str, Any]) -> tuple[list[str], list[str], list[float]] | None:
    outcomes_raw = _array(market.get("outcomes"))
    tokens_raw = _array(market.get("clobTokenIds"))
    prices_raw = _array(market.get("outcomePrices"))
    if not outcomes_raw or not tokens_raw or not prices_raw:
        return None
    outcomes = [str(value or "").strip() for value in outcomes_raw]
    tokens = [str(value or "").strip() for value in tokens_raw]
    prices = [_number(value) for value in prices_raw]
    if (
        len(outcomes) != len(tokens)
        or len(tokens) != len(prices)
        or len(tokens) < 2
        or any(not value for value in outcomes + tokens)
        or len(set(tokens)) != len(tokens)
        or any(value is None or not 0 <= value <= 1 for value in prices)
    ):
        return None
    return outcomes, tokens, [float(value) for value in prices]


def normalized_levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    raw = book.get(side)
    if not isinstance(raw, list):
        raise ValueError(f"book {side} must be an array")
    rows: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"book {side} level must be an object")
        price = _number(item.get("price"))
        size = _number(item.get("size"))
        if price is None or size is None or not 0 < price <= 1 or size <= 0:
            raise ValueError(f"book {side} level has invalid price/size")
        rows.append((price, size))
    return sorted(rows, key=lambda row: row[0], reverse=side == "bids")


def walk_asks(book: Mapping[str, Any], notional: float) -> dict[str, float] | None:
    remaining = float(notional)
    shares = 0.0
    levels = normalized_levels(book, "asks")
    for price, size in levels:
        spend = min(remaining, price * size)
        shares += spend / price
        remaining -= spend
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or shares <= 0 or not levels:
        return None
    return {
        "best_ask": levels[0][0],
        "vwap": notional / shares,
        "shares": shares,
        "cost": notional,
    }


def walk_bids(book: Mapping[str, Any], shares: float) -> dict[str, float | bool]:
    remaining = float(shares)
    filled = 0.0
    proceeds = 0.0
    levels = normalized_levels(book, "bids")
    for price, size in levels:
        quantity = min(remaining, size)
        filled += quantity
        proceeds += quantity * price
        remaining -= quantity
        if remaining <= 1e-9:
            break
    return {
        "best_bid": levels[0][0] if levels else None,
        "vwap": proceeds / filled if filled > 0 else None,
        "filled": filled,
        "remaining": max(0.0, remaining),
        "proceeds": proceeds,
        "complete": remaining <= 1e-7 and filled > 0,
    }


def _classify_market(
    market: Mapping[str, Any],
    *,
    source_received_at: str,
    observed_at: datetime,
    config: ShadowConfig,
) -> dict[str, Any]:
    condition_id = str(market.get("conditionId") or "").strip()
    event = _event(market)
    event_id = str(event.get("id") or "").strip() if event else ""
    identity = _aligned_identity(market)
    active = _boolean(market.get("active"))
    closed = _boolean(market.get("closed"))
    accepting = _boolean(market.get("acceptingOrders"))
    book_enabled = _boolean(market.get("enableOrderBook"))
    liquidity = _number(market.get("liquidity"))
    volume = _number(market.get("volume"))
    end_date = _utc(market.get("endDate"))
    game_start = _utc(market.get("gameStartTime"))
    sports_type = str(market.get("sportsMarketType") or "").strip() or None
    sports_timed = market.get("gameStartTime") not in (None, "") or sports_type is not None
    reasons: list[str] = []
    if not condition_id:
        reasons.append("MISSING_CONDITION_ID")
    if not event_id:
        reasons.append("MISSING_UNIQUE_EVENT_ID")
    if identity is None:
        reasons.append("OUTCOME_PRICE_TOKEN_IDENTITY_UNALIGNED")
    if active is not True or closed is not False or accepting is not True or book_enabled is not True:
        reasons.append("NOT_OPEN_TRADABLE")
    if liquidity is None or liquidity < config.gamma.min_liquidity:
        reasons.append("LIQUIDITY_BELOW_125000")
    if volume is None or volume < config.gamma.min_total_volume:
        reasons.append("CUMULATIVE_VOLUME_BELOW_5000")

    time_stratum = "UNKNOWN"
    entry_reference = None
    hours_left = None
    if sports_timed:
        entry_reference = "game_start_time"
        if game_start is None:
            reasons.append("SPORTS_GAME_START_MISSING_OR_INVALID")
        else:
            hours_left = (game_start - observed_at).total_seconds() / 3600
            if hours_left > 0:
                time_stratum = "PRE_GAME"
                if not 0 < hours_left <= config.gamma.entry_hours_max:
                    reasons.append("OUTSIDE_0_120H_PREGAME_WINDOW")
            else:
                time_stratum = "IN_PLAY"
    else:
        entry_reference = "end_date"
        time_stratum = "NON_SPORTS"
        if end_date is None:
            reasons.append("NON_SPORTS_END_DATE_MISSING_OR_INVALID")
        else:
            hours_left = (end_date - observed_at).total_seconds() / 3600
            if not 0 < hours_left <= config.gamma.entry_hours_max:
                reasons.append("OUTSIDE_0_120H_NON_SPORTS_WINDOW")

    outcomes: list[str] = []
    tokens: list[str] = []
    probabilities: list[float] = []
    if identity is not None:
        outcomes, tokens, probabilities = identity
        primary_probability = probabilities[0]
        if not (
            config.gamma.gamma_probability_min
            <= primary_probability
            <= config.gamma.gamma_probability_max
        ):
            reasons.append("PRIMARY_GAMMA_PROBABILITY_OUTSIDE_075_088")
    primary_probability = probabilities[0] if probabilities else None
    primary_token = tokens[0] if tokens else None
    primary_outcome = outcomes[0] if outcomes else None
    return {
        "condition_id": condition_id,
        "event_id": event_id,
        "event_cluster_id": event_id,
        "event_slug": str((event or {}).get("slug") or "") or None,
        "event_title": str((event or {}).get("title") or "") or None,
        "category": str(market.get("category") or (event or {}).get("category") or "") or None,
        "event_tags_json": canonical_json(_tags((event or {}).get("tags"))),
        "market_tags_json": canonical_json(_tags(market.get("tags"))),
        "market_id": str(market.get("id") or "") or None,
        "market_slug": str(market.get("slug") or "") or None,
        "question": str(market.get("question") or "") or None,
        "source_received_at": source_received_at,
        "end_date": iso_utc(end_date) if end_date else None,
        "game_start_time": iso_utc(game_start) if game_start else None,
        "entry_reference": entry_reference,
        "hours_until_entry_reference": hours_left,
        "sports_market_type": sports_type,
        "time_stratum": time_stratum,
        "liquidity": liquidity,
        "volume_total": volume,
        "active": int(active) if active is not None else None,
        "closed": int(closed) if closed is not None else None,
        "accepting_orders": int(accepting) if accepting is not None else None,
        "enable_order_book": int(book_enabled) if book_enabled is not None else None,
        "outcomes_json": canonical_json(outcomes),
        "token_ids_json": canonical_json(tokens),
        "gamma_probabilities_json": canonical_json(probabilities),
        "primary_outcome_index": 0 if identity is not None else None,
        "primary_outcome_label": primary_outcome,
        "primary_token_id": primary_token,
        "primary_gamma_probability": primary_probability,
        "identity_aligned": int(identity is not None),
        "eligible": not reasons,
        "reasons": reasons,
    }


def _resolution_proof(read: ResolutionRead, token_id: str) -> dict[str, Any]:
    market = read.market
    if market is None:
        return {
            "status": "NOT_FINAL",
            "outcomes": [],
            "tokens": [],
            "prices": [],
            "winner_index": None,
            "token_payout": None,
            "basis": "gamma_exact_condition_not_final",
        }
    identity = _aligned_identity(market)
    if identity is None:
        return {
            "status": "NOT_PROVEN",
            "outcomes": [],
            "tokens": [],
            "prices": [],
            "winner_index": None,
            "token_payout": None,
            "basis": "gamma_closed_identity_unaligned",
        }
    outcomes, tokens, prices = identity
    if (
        tokens.count(str(token_id)) != 1
        or any(price not in {0.0, 1.0} for price in prices)
        or prices.count(1.0) != 1
    ):
        return {
            "status": "NOT_PROVEN",
            "outcomes": outcomes,
            "tokens": tokens,
            "prices": prices,
            "winner_index": None,
            "token_payout": None,
            "basis": "gamma_closed_final_prices_not_unique_one_hot_exact_token",
        }
    winner = prices.index(1.0)
    token_index = tokens.index(str(token_id))
    return {
        "status": "PROVEN",
        "outcomes": outcomes,
        "tokens": tokens,
        "prices": prices,
        "winner_index": winner,
        "token_payout": prices[token_index],
        "basis": "gamma_closed_final_outcome_prices_unique_one_hot_exact_token",
    }


def _policy_trigger(
    policy: ExitPolicy,
    *,
    entry_vwap: float,
    bid_vwap: float,
    peak: float,
) -> tuple[str, float] | None:
    if policy.id == "hold_to_resolution":
        return None
    roi = bid_vwap / entry_vwap - 1
    if policy.stop_loss is not None and roi <= policy.stop_loss:
        return "STOP_LOSS", roi
    if policy.take_profit is not None and roi >= policy.take_profit:
        return "TAKE_PROFIT", roi
    if policy.trailing is not None and bid_vwap < peak * (1 - policy.trailing):
        return "TRAILING_STOP", (peak - bid_vwap) / peak
    return None


class ShadowCollector:
    def __init__(
        self,
        config: ShadowConfig,
        repository: ShadowRepository,
        gamma: ShadowGammaClient,
        clob: ShadowClobClient,
        deadline: CollectionDeadline,
    ) -> None:
        self.config = config
        self.repository = repository
        self.gamma = gamma
        self.clob = clob
        self.deadline = deadline

    def collect(self, run_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.deadline.require()
        sweep_started = iso_utc(observed_at)
        sweep: GammaSweep = self.gamma.fetch_sweep(run_id)
        if not sweep.cursor_complete:
            raise RuntimeError("Gamma cursor sweep did not reach a terminal cursor")

        raw_payloads = [
            self.repository.raw_payload_row(
                run_id, "GAMMA_MARKETS_KEYSET_PAGE", page.request_id,
                page.received_at, page.raw,
            )
            for page in sweep.pages
        ]
        raw_memberships: list[dict[str, Any]] = []
        selected_by_condition: dict[str, tuple[dict[str, Any], Mapping[str, Any]]] = {}
        for page in sweep.pages:
            for page_ordinal, raw_market in enumerate(page.markets):
                raw_json = canonical_json(raw_market)
                condition_id = str(raw_market.get("conditionId") or "").strip()
                raw_memberships.append(
                    {
                        "page_number": page.page_number,
                        "page_ordinal": page_ordinal,
                        "condition_id": condition_id or None,
                        "raw_market_sha256": hashlib.sha256(raw_json.encode()).hexdigest(),
                        "source_received_at": page.received_at,
                        "raw_market": raw_market,
                    }
                )
                classified = _classify_market(
                    raw_market,
                    source_received_at=page.received_at,
                    observed_at=observed_at,
                    config=self.config,
                )
                if not condition_id:
                    continue
                prior = selected_by_condition.get(condition_id)
                if prior is not None:
                    prior_identity = canonical_json(
                        {
                            key: prior[0][key]
                            for key in (
                                "event_id", "outcomes_json", "token_ids_json",
                                "gamma_probabilities_json",
                            )
                        }
                    )
                    current_identity = canonical_json(
                        {
                            key: classified[key]
                            for key in (
                                "event_id", "outcomes_json", "token_ids_json",
                                "gamma_probabilities_json",
                            )
                        }
                    )
                    if prior_identity != current_identity:
                        raise RuntimeError(
                            f"conflicting duplicate Gamma condition: {condition_id}"
                        )
                    continue
                selected_by_condition[condition_id] = (classified, raw_market)

        ordered = sorted(
            selected_by_condition.values(),
            key=lambda item: (
                item[0]["event_cluster_id"], item[0]["condition_id"],
                item[0]["primary_token_id"] or "",
            ),
        )
        entry_open = (
            self.config.experiment.start_utc
            <= observed_at
            < self.config.experiment.entry_end_utc
        )
        candidates = [row for row, _ in ordered if row["eligible"] and entry_open]
        open_episodes = self.repository.open_episodes()
        open_tokens = sorted({str(row["token_id"]) for row in open_episodes})
        if len(open_tokens) > self.config.clob.max_books_per_run:
            raise RuntimeError("open episode tokens exceed frozen book budget")
        candidate_tokens = [
            row["primary_token_id"]
            for row in candidates
            if row["primary_token_id"] not in set(open_tokens)
        ]
        available = self.config.clob.max_books_per_run - len(open_tokens)
        selected_candidate_tokens = set(candidate_tokens[:available])
        selected_tokens = open_tokens + candidate_tokens[:available]
        capped_tokens = set(candidate_tokens[available:])

        book_reads: dict[str, BookRead] = {}
        for token_id in selected_tokens:
            self.deadline.require()
            read = self.clob.fetch_book(run_id, token_id)
            if read.status not in {"OBSERVED", "NO_BOOK"}:
                raise RuntimeError(f"incomplete book evidence for token {token_id}")
            if token_id in selected_candidate_tokens and read.status != "OBSERVED":
                raise RuntimeError(
                    f"active entry candidate lacks complete CLOB book: {token_id}"
                )
            book_reads[token_id] = read
            if read.raw is not None and read.received_at is not None:
                raw_payloads.append(
                    self.repository.raw_payload_row(
                        run_id, "CLOB_FULL_BOOK", read.request_id,
                        read.received_at, read.raw,
                    )
                )

        current_conditions = set(selected_by_condition)
        resolution_conditions = {
            str(episode["condition_id"])
            for episode in open_episodes
            if (
                str(episode["token_id"]) in book_reads
                and book_reads[str(episode["token_id"])].status == "NO_BOOK"
            )
            or str(episode["condition_id"]) not in current_conditions
            or (
                _utc(episode.get("end_date")) is not None
                and _utc(episode.get("end_date")) <= observed_at
            )
        }
        resolution_reads: dict[str, ResolutionRead] = {}
        for condition_id in sorted(resolution_conditions):
            self.deadline.require()
            read = self.gamma.fetch_resolution(run_id, condition_id)
            resolution_reads[condition_id] = read
            if read.raw is not None and read.received_at is not None:
                raw_payloads.append(
                    self.repository.raw_payload_row(
                        run_id, "GAMMA_EXACT_RESOLUTION", read.request_id,
                        read.received_at, read.raw,
                    )
                )

        sweep_id = uuid4().hex
        membership_order = sorted(
            raw_memberships,
            key=lambda row: (
                row["condition_id"] or "",
                row["page_number"], row["page_ordinal"], row["raw_market_sha256"],
            ),
        )
        membership_digest = hashlib.sha256(
            canonical_json(
                [
                    {key: row[key] for key in (
                        "page_number", "page_ordinal", "condition_id",
                        "raw_market_sha256", "source_received_at",
                    )}
                    for row in membership_order
                ]
            ).encode()
        ).hexdigest()
        membership_rows = []
        classification_by_condition = {row["condition_id"]: row for row, _ in ordered}
        for deterministic_ordinal, row in enumerate(membership_order):
            classified = classification_by_condition.get(row["condition_id"] or "")
            reasons = classified["reasons"] if classified else ["MISSING_CONDITION_ID"]
            membership_rows.append(
                {
                    "membership_id": uuid4().hex,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "page_number": row["page_number"],
                    "page_ordinal": row["page_ordinal"],
                    "deterministic_ordinal": deterministic_ordinal,
                    "condition_id": row["condition_id"],
                    "raw_market_sha256": row["raw_market_sha256"],
                    "source_received_at": row["source_received_at"],
                    "qualification_status": "ELIGIBLE" if classified and classified["eligible"] else "EXCLUDED",
                    "exclusion_reasons_json": canonical_json(reasons),
                }
            )

        observation_rows = []
        observation_ids: dict[str, str] = {}
        for ordinal, (classified, _) in enumerate(ordered):
            observation_id = uuid4().hex
            observation_ids[classified["condition_id"]] = observation_id
            token = classified["primary_token_id"]
            if not classified["eligible"]:
                selection = "NOT_CANDIDATE"
            elif not entry_open:
                selection = "ENTRY_WINDOW_CLOSED"
            elif token in capped_tokens:
                selection = "BOOK_CAP_EXCLUDED"
            elif token in selected_candidate_tokens or token in open_tokens:
                selection = "BOOK_SELECTED"
            else:
                selection = "NOT_SELECTED"
            observation_rows.append(
                {
                    "observation_id": observation_id,
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "deterministic_ordinal": ordinal,
                    **{key: classified[key] for key in (
                        "event_cluster_id", "event_id", "event_slug", "event_title",
                        "category", "event_tags_json", "market_tags_json",
                        "condition_id", "market_id", "market_slug", "question",
                        "source_received_at", "end_date", "game_start_time",
                        "entry_reference", "hours_until_entry_reference",
                        "sports_market_type", "time_stratum", "liquidity",
                        "volume_total", "active", "closed", "accepting_orders",
                        "enable_order_book", "outcomes_json", "token_ids_json",
                        "gamma_probabilities_json", "primary_outcome_index",
                        "primary_outcome_label", "primary_token_id",
                        "primary_gamma_probability", "identity_aligned",
                    )},
                    "eligibility_status": "ELIGIBLE" if classified["eligible"] else "EXCLUDED",
                    "exclusion_reasons_json": canonical_json(classified["reasons"]),
                    "book_selection_status": selection,
                }
            )

        book_attempt_rows = []
        snapshot_rows = []
        level_rows = []
        snapshot_ids: dict[str, str] = {}
        for token_id in selected_tokens:
            read = book_reads[token_id]
            purpose = "OPEN_PATH" if token_id in open_tokens else "ENTRY_CANDIDATE"
            book_attempt_rows.append(
                {
                    "attempt_id": uuid4().hex,
                    "run_id": run_id,
                    "token_id": token_id,
                    "purpose": purpose,
                    "status": read.status,
                    "request_id": read.request_id,
                    "source_received_at": read.received_at,
                    "error_type": read.error_type,
                }
            )
            if read.status != "OBSERVED" or read.book is None:
                continue
            bids = normalized_levels(read.book, "bids")
            asks = normalized_levels(read.book, "asks")
            snapshot_id = uuid4().hex
            snapshot_ids[token_id] = snapshot_id
            snapshot_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "run_id": run_id,
                    "token_id": token_id,
                    "request_id": read.request_id,
                    "source_received_at": read.received_at,
                    "raw_book_sha256": read.response_sha256,
                    "source_timestamp": str(read.book.get("timestamp") or "") or None,
                    "market_hash": str(read.book.get("market") or "") or None,
                    "best_bid": bids[0][0] if bids else None,
                    "best_ask": asks[0][0] if asks else None,
                    "bid_level_count": len(bids),
                    "ask_level_count": len(asks),
                }
            )
            for side, levels in (("BID", bids), ("ASK", asks)):
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

        existing = self.repository.existing_episode_keys()
        decision_rows = []
        episode_rows = []
        policy_rows = []
        new_episodes: list[dict[str, Any]] = []
        for classified in candidates:
            token_id = str(classified["primary_token_id"])
            read = book_reads.get(token_id)
            entry = (
                walk_asks(
                    read.book, self.config.experiment.simulated_notional_usdc
                )
                if read and read.book
                else None
            )
            for band in self.config.experiment.entry_bands:
                episode_key = (classified["condition_id"], token_id, band.id)
                episode_id = existing.get(episode_key)
                if token_id in capped_tokens:
                    status = "BOOK_CAP_EXCLUDED"
                elif read is None or read.status != "OBSERVED":
                    status = "BOOK_UNAVAILABLE"
                elif entry is None:
                    status = "INSUFFICIENT_ASK_DEPTH"
                elif not band.low <= float(entry["vwap"]) <= band.high:
                    status = "OUTSIDE_BAND"
                elif episode_id:
                    status = "ALREADY_OPENED"
                else:
                    status = "OPENED"
                    episode_id = uuid4().hex
                    existing[episode_key] = episode_id
                    episode = {
                        "episode_id": episode_id,
                        "opened_run_id": run_id,
                        "config_hash": self.config.config_hash,
                        "strategy_source_digest": self.config.strategy_source_digest,
                        "preregistration_sha256": self.config.preregistration_sha256,
                        "event_cluster_id": classified["event_cluster_id"],
                        "event_id": classified["event_id"],
                        "condition_id": classified["condition_id"],
                        "question": classified["question"],
                        "category": classified["category"],
                        "token_id": token_id,
                        "outcome_index": 0,
                        "outcome_label": classified["primary_outcome_label"],
                        "band_id": band.id,
                        "band_role": band.role,
                        "entered_at": iso_utc(observed_at),
                        "source_received_at": read.received_at,
                        "time_stratum": classified["time_stratum"],
                        "end_date": classified["end_date"],
                        "game_start_time": classified["game_start_time"],
                        "liquidity": classified["liquidity"],
                        "volume_total": classified["volume_total"],
                        "entry_best_ask": entry["best_ask"],
                        "entry_vwap": entry["vwap"],
                        "entry_shares": entry["shares"],
                        "entry_cost": entry["cost"],
                    }
                    episode_rows.append(episode)
                    new_episodes.append(episode)
                    for policy in self.config.experiment.exit_policies:
                        policy_rows.append(
                            {
                                "episode_policy_id": uuid4().hex,
                                "episode_id": episode_id,
                                "policy_id": policy.id,
                                "policy_role": policy.role,
                                "take_profit": policy.take_profit,
                                "stop_loss": policy.stop_loss,
                                "trailing": policy.trailing,
                                "created_at": iso_utc(observed_at),
                            }
                        )
                decision_rows.append(
                    {
                        "decision_id": uuid4().hex,
                        "run_id": run_id,
                        "market_observation_id": observation_ids[classified["condition_id"]],
                        "snapshot_id": snapshot_ids.get(token_id),
                        "event_cluster_id": classified["event_cluster_id"],
                        "condition_id": classified["condition_id"],
                        "token_id": token_id,
                        "band_id": band.id,
                        "band_role": band.role,
                        "band_low": band.low,
                        "band_high": band.high,
                        "decided_at": iso_utc(observed_at),
                        "entry_best_ask": entry["best_ask"] if entry else None,
                        "entry_vwap": entry["vwap"] if entry else None,
                        "entry_shares": entry["shares"] if entry else None,
                        "entry_cost": entry["cost"] if entry else None,
                        "decision_status": status,
                        "details_json": canonical_json(
                            {"book_status": read.status if read else "CAPPED", "notional": 5}
                        ),
                        "episode_id": episode_id,
                    }
                )

        active_episodes = open_episodes + new_episodes
        prior_exits = self.repository.policy_exits()
        path_rows = []
        resolution_rows = []
        exit_rows = []
        resolution_ids: dict[tuple[str, str], tuple[str, dict[str, Any], ResolutionRead]] = {}
        for episode in active_episodes:
            token_id = str(episode["token_id"])
            read = book_reads.get(token_id)
            if read and read.book:
                bid = walk_bids(read.book, float(episode["entry_shares"]))
                prior_peak = self.repository.prior_peak(
                    str(episode["episode_id"]), float(episode["entry_vwap"])
                )
                current_vwap = _number(bid["vwap"])
                peak = max(prior_peak, current_vwap or prior_peak)
                path_rows.append(
                    {
                        "path_id": uuid4().hex,
                        "episode_id": episode["episode_id"],
                        "run_id": run_id,
                        "snapshot_id": snapshot_ids.get(token_id),
                        "observed_at": iso_utc(observed_at),
                        "source_received_at": read.received_at,
                        "best_bid": bid["best_bid"],
                        "executable_bid_vwap": bid["vwap"],
                        "executable_proceeds": bid["proceeds"],
                        "filled_shares": bid["filled"],
                        "remaining_shares": bid["remaining"],
                        "depth_complete": int(bool(bid["complete"])),
                        "peak_executable_bid_vwap": peak,
                        "path_status": "FULL_DEPTH" if bid["complete"] else "PARTIAL_OR_EMPTY_DEPTH",
                    }
                )
                if bid["complete"] and current_vwap is not None:
                    for policy in self.config.experiment.exit_policies:
                        key = (str(episode["episode_id"]), policy.id)
                        if key in prior_exits:
                            continue
                        trigger = _policy_trigger(
                            policy,
                            entry_vwap=float(episode["entry_vwap"]),
                            bid_vwap=current_vwap,
                            peak=peak,
                        )
                        if trigger is None:
                            continue
                        pnl = float(bid["proceeds"]) - float(episode["entry_cost"])
                        exit_rows.append(
                            {
                                "exit_id": uuid4().hex,
                                "episode_id": episode["episode_id"],
                                "policy_id": policy.id,
                                "run_id": run_id,
                                "exited_at": iso_utc(observed_at),
                                "source_received_at": read.received_at,
                                "exit_kind": trigger[0],
                                "trigger_value": trigger[1],
                                "exit_price_vwap": current_vwap,
                                "exit_proceeds": bid["proceeds"],
                                "pnl_usdc": pnl,
                                "roi": pnl / float(episode["entry_cost"]),
                                "resolution_id": None,
                                "evidence_basis": "displayed_full_bid_depth_counterfactual",
                            }
                        )
                        prior_exits.add(key)
            else:
                path_rows.append(
                    {
                        "path_id": uuid4().hex,
                        "episode_id": episode["episode_id"],
                        "run_id": run_id,
                        "snapshot_id": None,
                        "observed_at": iso_utc(observed_at),
                        "source_received_at": None,
                        "best_bid": None,
                        "executable_bid_vwap": None,
                        "executable_proceeds": None,
                        "filled_shares": 0.0,
                        "remaining_shares": episode["entry_shares"],
                        "depth_complete": 0,
                        "peak_executable_bid_vwap": self.repository.prior_peak(
                            str(episode["episode_id"]), float(episode["entry_vwap"])
                        ),
                        "path_status": "NO_BOOK_EXPLICIT",
                    }
                )

            condition_id = str(episode["condition_id"])
            resolution_read = resolution_reads.get(condition_id)
            if resolution_read is not None:
                proof = _resolution_proof(resolution_read, token_id)
                resolution_key = (condition_id, token_id)
                if resolution_key not in resolution_ids:
                    resolution_id = uuid4().hex
                    resolution_ids[resolution_key] = (resolution_id, proof, resolution_read)
                    resolution_rows.append(
                        {
                            "resolution_id": resolution_id,
                            "run_id": run_id,
                            "condition_id": condition_id,
                            "token_id": token_id,
                            "request_id": resolution_read.request_id,
                            "source_received_at": resolution_read.received_at,
                            "resolution_status": proof["status"],
                            "outcomes_json": canonical_json(proof["outcomes"]),
                            "token_ids_json": canonical_json(proof["tokens"]),
                            "final_prices_json": canonical_json(proof["prices"]),
                            "winner_index": proof["winner_index"],
                            "token_payout": proof["token_payout"],
                            "evidence_basis": proof["basis"],
                        }
                    )
                resolution_id, proof, resolution_read = resolution_ids[resolution_key]
                if proof["status"] == "PROVEN":
                    proceeds = float(proof["token_payout"]) * float(episode["entry_shares"])
                    pnl = proceeds - float(episode["entry_cost"])
                    for policy in self.config.experiment.exit_policies:
                        key = (str(episode["episode_id"]), policy.id)
                        if key in prior_exits:
                            continue
                        exit_rows.append(
                            {
                                "exit_id": uuid4().hex,
                                "episode_id": episode["episode_id"],
                                "policy_id": policy.id,
                                "run_id": run_id,
                                "exited_at": iso_utc(observed_at),
                                "source_received_at": resolution_read.received_at,
                                "exit_kind": "RESOLUTION",
                                "trigger_value": proof["token_payout"],
                                "exit_price_vwap": None,
                                "exit_proceeds": proceeds,
                                "pnl_usdc": pnl,
                                "roi": pnl / float(episode["entry_cost"]),
                                "resolution_id": resolution_id,
                                "evidence_basis": proof["basis"],
                            }
                        )
                        prior_exits.add(key)

        sweep_row = {
            "sweep_id": sweep_id,
            "run_id": run_id,
            "started_at": sweep_started,
            "completed_at": iso_utc(),
            "page_count": len(sweep.pages),
            "raw_market_count": len(raw_memberships),
            "unique_condition_count": len(ordered),
            "eligible_candidate_count": len(candidates),
            "selected_book_count": len(selected_tokens),
            "capped_candidate_count": len(capped_tokens),
            "cursor_complete": 1,
            "membership_sha256": membership_digest,
            "request_envelope_json": canonical_json(
                {
                    "endpoint": "/markets/keyset",
                    "closed": False,
                    "include_tag": True,
                    "page_size": self.config.gamma.page_size,
                    "liquidity_num_min": self.config.gamma.min_liquidity,
                    "volume_num_min": self.config.gamma.min_total_volume,
                    "gamma_probability": [0.75, 0.88],
                    "entry_hours": [0, 120],
                    "primary_outcome_index": 0,
                    "deterministic_order": "event_id,condition_id,token_id",
                }
            ),
        }
        bundle = {
            "shadow_raw_payloads": raw_payloads,
            "shadow_market_sweeps": [sweep_row],
            "shadow_sweep_memberships": membership_rows,
            "shadow_market_observations": observation_rows,
            "shadow_book_attempts": book_attempt_rows,
            "shadow_book_snapshots": snapshot_rows,
            "shadow_book_levels": level_rows,
            "shadow_cell_decisions": decision_rows,
            "shadow_episodes": episode_rows,
            "shadow_episode_policies": policy_rows,
            "shadow_path_observations": path_rows,
            "shadow_resolution_observations": resolution_rows,
            "shadow_policy_exits": exit_rows,
            "shadow_data_quality_issues": [],
        }
        self.deadline.require()
        self.repository.publish(bundle)
        return {
            "sweep_id": sweep_id,
            "cursor_complete": True,
            "pages": len(sweep.pages),
            "raw_markets": len(raw_memberships),
            "unique_conditions": len(ordered),
            "eligible_candidates": len(candidates),
            "books_selected": len(selected_tokens),
            "capped_candidates": len(capped_tokens),
            "episodes_opened": len(episode_rows),
            "paths_recorded": len(path_rows),
            "resolution_observations": len(resolution_rows),
            "policy_exits": len(exit_rows),
            "elapsed_seconds": round(self.deadline.elapsed_seconds, 3),
        }

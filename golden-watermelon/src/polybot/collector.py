"""In-play match-winner exact-book counterfactual collector."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping
from uuid import uuid4

from .api.clob_client import (
    ClobClient,
    normalized_levels,
    walk_asks,
    walk_bids,
    walk_bids_partial,
)
from .api.gamma_client import GammaClient
from .config import BotConfig
from .db.repository import ResearchRepository
from .utils.retry import canonical_json, iso_utc


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
    if value is None or value == "":
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalized_name(value: Any) -> str:
    if value is None or value == "":
        return ""
    return " ".join(
        "".join(character if character.isalnum() else " " for character in str(value))
        .casefold()
        .split()
    )


def _team_forms(team: Mapping[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            _normalized_name(team.get("name")),
            _normalized_name(team.get("alias")),
            _normalized_name(team.get("abbreviation")),
        )
        if normalized
    }


def _select_event(
    market: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[str], int]:
    events = [
        item
        for item in (_array(market.get("events")) or [])
        if isinstance(item, Mapping)
    ]
    if len(events) == 1:
        return events[0], [], 1
    return (events[0] if events else {}), ["EVENT_RELATION_NOT_UNIQUE"], len(events)


def classify_match_winner(
    event: Mapping[str, Any],
    market: Mapping[str, Any],
    labels: list[str],
    tokens: list[str],
    probabilities: list[float | None],
) -> tuple[str, tuple[int, ...], dict[str, Any], list[str]]:
    """Fail-closed classifier for whole-match winners, never child games/props."""
    reasons: list[str] = []
    sports_market_type = str(market.get("sportsMarketType") or "").strip()
    if sports_market_type != "moneyline":
        reasons.append("NOT_TOP_LEVEL_MONEYLINE")
    aligned = (
        len(labels) == len(tokens) == len(probabilities) == 2
        and all(labels)
        and len(set(labels)) == 2
        and all(tokens)
        and len(set(tokens)) == 2
        and all(value is not None and 0 <= value <= 1 for value in probabilities)
    )
    if not aligned:
        reasons.append("NOT_ALIGNED_TWO_OUTCOME")

    raw_teams = event.get("teams")
    teams = (
        [dict(item) for item in raw_teams if isinstance(item, Mapping)]
        if isinstance(raw_teams, list)
        else []
    )
    if len(teams) != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    forms = [_team_forms(team) for team in teams]
    if len(forms) == 2 and (not forms[0] or not forms[1] or forms[0] & forms[1]):
        reasons.append("TEAM_IDENTITY_AMBIGUOUS")

    neg_risk = _boolean(market.get("negRisk"))
    if neg_risk is None:
        reasons.append("NEG_RISK_UNKNOWN")

    match_class = "REJECTED"
    eligible_indices: tuple[int, ...] = ()
    selected_team_index: int | None = None
    if not reasons and neg_risk is False:
        label_forms = [_normalized_name(label) for label in labels]
        matched = [
            [index for index, candidates in enumerate(forms) if label in candidates]
            for label in label_forms
        ]
        if all(len(item) == 1 for item in matched) and matched[0][0] != matched[1][0]:
            match_class = "ALIGNED_TWO_TEAM_MONEYLINE"
            eligible_indices = (0, 1)
        else:
            reasons.append("OUTCOME_LABELS_DO_NOT_MATCH_TEAMS")
    elif not reasons and neg_risk is True:
        normalized_labels = [_normalized_name(label) for label in labels]
        if set(normalized_labels) != {"yes", "no"}:
            reasons.append("NEGRISK_OUTCOMES_NOT_YES_NO")
        else:
            descriptor = _normalized_name(market.get("groupItemTitle"))
            if not descriptor:
                question = str(market.get("question") or "")
                lowered = question.casefold()
                descriptor = _normalized_name(
                    question[5 : lowered.index(" win")]
                    if lowered.startswith("will ") and " win" in lowered
                    else ""
                )
            if descriptor in {"draw", "tie"} or descriptor.startswith("draw "):
                reasons.append("DRAW_OUTCOME_EXCLUDED")
            else:
                matched_teams = [
                    index for index, candidates in enumerate(forms)
                    if descriptor in candidates
                ]
                if len(matched_teams) != 1:
                    reasons.append("TEAM_PROPOSITION_NOT_IDENTIFIED")
                else:
                    selected_team_index = matched_teams[0]
                    match_class = "NEGRISK_TEAM_WIN_YES"
                    eligible_indices = (normalized_labels.index("yes"),)

    evidence = {
        "sports_market_type": sports_market_type,
        "neg_risk": neg_risk,
        "group_item_title": str(market.get("groupItemTitle") or "") or None,
        "team_names": [str(team.get("name") or "") for team in teams],
        "team_aliases": [str(team.get("alias") or "") or None for team in teams],
        "outcome_labels": labels,
        "selected_team_index": selected_team_index,
        "eligible_outcome_indices": list(eligible_indices),
    }
    return match_class, eligible_indices, evidence, reasons


def _fee_rate(market: Mapping[str, Any], fallback: float) -> tuple[float, dict[str, Any]]:
    enabled = _boolean(market.get("feesEnabled"))
    schedule = market.get("feeSchedule")
    schedule_dict = dict(schedule) if isinstance(schedule, Mapping) else {}
    configured = _number(schedule_dict.get("rate"))
    if enabled is False:
        return 0.0, schedule_dict
    if configured is not None and 0 <= configured <= 1:
        return configured, schedule_dict
    return fallback, schedule_dict


def _execution_fee(shares: float, price: float, fee_rate: float) -> float:
    if shares <= 0 or price <= 0 or fee_rate <= 0:
        return 0.0
    return shares * fee_rate * price * (1 - price)


def _stop_policy_key(stop: float) -> str:
    return f"STOP_{stop:.2f}"


def _parse_market(
    market: Mapping[str, Any],
    *,
    sweep_id: str,
    run_id: str,
    observed_at: datetime,
    config: BotConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    event, event_reasons, event_relation_count = _select_event(market)
    event_id = str(event.get("id") or "")
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
    labels = _array(market.get("outcomes")) or []
    tokens = _array(market.get("clobTokenIds") or market.get("clob_token_ids")) or []
    probabilities = _array(market.get("outcomePrices") or market.get("outcome_prices")) or []
    labels = [str(value).strip() for value in labels]
    tokens = [str(value).strip() for value in tokens]
    probability_values = [_number(value) for value in probabilities]
    match_class, eligible_indices, classification, classification_reasons = (
        classify_match_winner(event, market, labels, tokens, probability_values)
    )
    end_date = _utc(market.get("endDate") or market.get("end_date") or event.get("endDate"))
    game_start = _utc(
        market.get("gameStartTime")
        or event.get("eventDate")
        or event.get("startTime")
    )
    hours_until_end = (end_date - observed_at).total_seconds() / 3600 if end_date else None
    liquidity = _number(market.get("liquidityNum", market.get("liquidity")))
    volume = _number(market.get("volumeNum", market.get("volume")))
    active = _boolean(market.get("active"))
    closed = _boolean(market.get("closed"))
    accepting = _boolean(market.get("acceptingOrders"))
    book_enabled = _boolean(market.get("enableOrderBook"))
    neg_risk = _boolean(market.get("negRisk"))
    event_live = _boolean(event.get("live"))
    event_ended = _boolean(event.get("ended"))
    event_game_status = str(event.get("gameStatus") or "") or None
    reasons: list[str] = [*event_reasons, *classification_reasons]
    if not event_id or not condition_id:
        reasons.append("MISSING_ID")
    if active is not True or closed is not False or accepting is not True or book_enabled is not True:
        reasons.append("NOT_OPEN_TRADABLE")
    phase = "UNKNOWN"
    if game_start is None:
        reasons.append("GAME_START_TIME_MISSING")
    elif observed_at < game_start:
        phase = "PRE_GAME"
        reasons.append("NOT_IN_PLAY")
    elif event_ended is True:
        phase = "FINISHED"
        reasons.append("EVENT_ENDED")
    elif event_live is False:
        phase = "POST_START_LIVE_FALSE"
        reasons.append("EVENT_LIVE_FALSE")
    else:
        phase = "IN_PLAY_EXPLICIT" if event_live is True else "IN_PLAY_INFERRED"
    if end_date is None:
        reasons.append("END_DATE_MISSING")
    fee_rate, fee_schedule = _fee_rate(market, config.trading.experiment.fee_rate_fallback)
    observation_id = uuid4().hex
    market_row = {
        "observation_id": observation_id, "sweep_id": sweep_id, "run_id": run_id,
        "event_id": event_id, "event_title": str(event.get("title") or "") or None,
        "condition_id": condition_id or None, "market_id": str(market.get("id") or "") or None,
        "question": str(market.get("question") or "") or None,
        "group_item_title": str(market.get("groupItemTitle") or "") or None,
        "sports_market_type": str(market.get("sportsMarketType") or "") or None,
        "observed_at": iso_utc(observed_at),
        "end_date": iso_utc(end_date) if end_date else None,
        "game_start_time": iso_utc(game_start) if game_start else None,
        "hours_until_end": hours_until_end, "sports_phase": phase,
        "event_live": int(event_live) if event_live is not None else None,
        "event_ended": int(event_ended) if event_ended is not None else None,
        "event_game_status": event_game_status,
        "liquidity": liquidity, "volume_total": volume,
        "active": int(active) if active is not None else None,
        "closed": int(closed) if closed is not None else None,
        "accepting_orders": int(accepting) if accepting is not None else None,
        "enable_order_book": int(book_enabled) if book_enabled is not None else None,
        "neg_risk": int(neg_risk) if neg_risk is not None else None,
        "match_winner_class": match_class,
        "eligible_outcome_indices_json": canonical_json(list(eligible_indices)),
        "classification_evidence_json": canonical_json({
            **classification,
            "event_relation_count": event_relation_count,
        }),
        "cadence_arm": config.trading.cadence_arm,
        "fee_rate": fee_rate, "fee_schedule_json": canonical_json(fee_schedule),
        "outcome_labels_json": canonical_json(labels), "token_ids_json": canonical_json(tokens),
        "outcome_prices_json": canonical_json(probability_values),
        "eligible": int(not reasons), "exclusion_reason": "ELIGIBLE" if not reasons else ";".join(reasons),
        "normalized_json": canonical_json({
            "event_id": event_id, "condition_id": condition_id, "labels": labels,
            "tokens": tokens, "probabilities": probability_values,
            "end_date": iso_utc(end_date) if end_date else None,
            "game_start_time": iso_utc(game_start) if game_start else None,
            "liquidity": liquidity, "volume_total": volume,
            "neg_risk": neg_risk, "fee_rate": fee_rate,
            "sports_market_type": market.get("sportsMarketType"),
            "match_winner_class": match_class,
            "eligible_outcome_indices": list(eligible_indices),
            "event_live": event_live, "event_ended": event_ended,
            "event_game_status": event_game_status,
        }),
    }
    outcome_rows: list[dict[str, Any]] = []
    if len(labels) == len(tokens) == len(probability_values) == 2 and condition_id and event_id:
        for index, (label, token, probability) in enumerate(zip(labels, tokens, probability_values)):
            outcome_rows.append({
                "outcome_observation_id": uuid4().hex, "market_observation_id": observation_id,
                "sweep_id": sweep_id, "run_id": run_id, "condition_id": condition_id,
                "event_id": event_id, "token_id": token, "outcome_index": index,
                "outcome_label": label, "gamma_probability": probability,
                "entry_eligible": int(index in eligible_indices and not reasons),
                "observed_at": iso_utc(observed_at),
            })
    context = {
        "market_row": market_row, "outcomes": outcome_rows, "eligible": not reasons,
        "eligible_indices": eligible_indices,
        "match_winner_class": match_class,
        "event": event, "market": market, "labels": labels, "tokens": tokens,
        "probabilities": probability_values, "fee_rate": fee_rate,
        "end_date": end_date, "game_start": game_start, "phase": phase,
        "liquidity": liquidity, "volume": volume,
    }
    return market_row, outcome_rows, context


class Collector:
    def __init__(self, config: BotConfig, repository: ResearchRepository, gamma: GammaClient, clob: ClobClient) -> None:
        self.config = config
        self.repository = repository
        self.gamma = gamma
        self.clob = clob

    def collect(self, run_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        sweep_id = uuid4().hex
        sweep = self.gamma.fetch_moneyline_markets(run_id, observed_at=now)
        if not sweep.cursor_complete:
            self.repository.record_issue(run_id=run_id, severity="CRITICAL", issue_type="GAMMA_CURSOR_INCOMPLETE", detail={"pages": len(sweep.pages)})
            raise RuntimeError("Gamma moneyline keyset sweep exceeded the frozen page cap")

        payloads = [
            self.repository.payload_row(run_id=run_id, kind="GAMMA_MARKET_PAGE", request_id=page.request_id, observed_at=page.received_at, raw=page.raw)
            for page in sweep.pages
        ]
        market_rows: list[dict[str, Any]] = []
        outcome_rows: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        for page in sweep.pages:
            observed = _utc(page.received_at) or now
            for market in page.markets:
                row, outcomes, context = _parse_market(
                    market,
                    sweep_id=sweep_id,
                    run_id=run_id,
                    observed_at=observed,
                    config=self.config,
                )
                market_rows.append(row)
                outcome_rows.extend(outcomes)
                contexts.append(context)

        open_before = self.repository.open_episodes()
        active_stop_before = self.repository.active_stop_policies()
        tokens = [
            context["tokens"][index]
            for context in contexts
            if context["eligible"]
            for index in context["eligible_indices"]
        ]
        tokens.extend(str(row["token_id"]) for row in open_before)
        books = self.clob.fetch_books(run_id, tokens)
        payloads.extend(
            self.repository.payload_row(run_id=run_id, kind="CLOB_BOOK_BATCH", request_id=item.request_id, observed_at=item.received_at, raw=item.raw)
            for item in books.raw_payloads
        )

        attempt_rows: list[dict[str, Any]] = []
        snapshot_rows: list[dict[str, Any]] = []
        level_rows: list[dict[str, Any]] = []
        snapshot_by_token: dict[str, dict[str, Any]] = {}
        for token, attempt in books.attempts.items():
            attempt_rows.append({
                "attempt_id": uuid4().hex, "run_id": run_id, "token_id": token,
                "status": attempt.status, "request_id": attempt.request_id,
                "observed_at": attempt.received_at, "error_type": attempt.error_type,
                "error_message": attempt.error_message,
            })
        for token, book in books.books.items():
            attempt = books.attempts[token]
            bids = normalized_levels(book, "bids")
            asks = normalized_levels(book, "asks")
            snapshot_id = uuid4().hex
            raw_hash = hashlib.sha256(canonical_json(book).encode()).hexdigest()
            row = {
                "snapshot_id": snapshot_id, "run_id": run_id, "token_id": token,
                "request_id": str(attempt.request_id), "observed_at": str(attempt.received_at),
                "raw_book_sha256": raw_hash,
                "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
                "bid_level_count": len(bids), "ask_level_count": len(asks),
                "source_timestamp": str(book.get("timestamp") or "") or None,
                "tick_size": _number(book.get("tick_size")),
                "min_order_size": _number(book.get("min_order_size")),
            }
            snapshot_rows.append(row)
            snapshot_by_token[token] = row
            for side, levels_for_side in (("BID", bids), ("ASK", asks)):
                for index, (price, size) in enumerate(levels_for_side):
                    level_rows.append({
                        "level_id": uuid4().hex, "snapshot_id": snapshot_id,
                        "side": side, "level_index": index, "price": price, "size": size,
                    })

        existing = self.repository.existing_episode_keys()
        prior_vwaps = self.repository.latest_entry_vwaps()
        decisions: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        policies: list[dict[str, Any]] = []
        new_active_stops: list[dict[str, Any]] = []
        experiment = self.config.trading.experiment
        for context in contexts:
            if not context["eligible"]:
                continue
            market_row = context["market_row"]
            for outcome in context["outcomes"]:
                if not outcome["entry_eligible"]:
                    continue
                token = outcome["token_id"]
                snapshot = snapshot_by_token.get(token)
                book = books.books.get(token)
                walk = walk_asks(book, experiment.simulated_notional_usdc) if book else None
                prior_vwap = prior_vwaps.get(str(token))
                for threshold in experiment.entry_thresholds:
                    decision_id = uuid4().hex
                    episode_id: str | None = None
                    provenance: str | None = None
                    status = "NO_FULL_5_USDC_DEPTH"
                    if walk is not None:
                        if now < experiment.start_utc or now >= experiment.entry_end_utc:
                            status = "OUTSIDE_ENTRY_PERIOD"
                        elif (str(market_row["condition_id"]), token, threshold) in existing:
                            status = "EPISODE_ALREADY_EXISTS"
                        elif walk.vwap < threshold:
                            status = "BELOW_ENTRY_THRESHOLD"
                        elif prior_vwap is None:
                            status = "OPENED_FIRST_FULL_DEPTH_ABOVE"
                            provenance = "FIRST_FULL_DEPTH_ABOVE"
                            episode_id = uuid4().hex
                            existing.add((str(market_row["condition_id"]), token, threshold))
                        elif prior_vwap < threshold <= walk.vwap:
                            status = "OPENED_UPWARD_CROSS"
                            provenance = "UPWARD_CROSS"
                            episode_id = uuid4().hex
                            existing.add((str(market_row["condition_id"]), token, threshold))
                        else:
                            status = "ABOVE_WITHOUT_NEW_CROSS"
                    decision = {
                        "decision_id": decision_id, "run_id": run_id,
                        "market_observation_id": market_row["observation_id"],
                        "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                        "condition_id": market_row["condition_id"], "event_id": market_row["event_id"],
                        "token_id": token, "outcome_index": outcome["outcome_index"],
                        "threshold": threshold, "decided_at": iso_utc(now),
                        "best_ask": walk.best_ask if walk else (snapshot["best_ask"] if snapshot else None),
                        "entry_vwap": walk.vwap if walk else None,
                        "entry_shares": walk.shares if walk else None,
                        "entry_cost": walk.cost if walk else None,
                        "prior_entry_vwap": prior_vwap,
                        "entry_provenance": provenance,
                        "decision_status": status,
                        "details_json": canonical_json({
                            "rule": "first full-depth observation or upward threshold cross",
                            "levels_used": walk.levels_used if walk else None,
                            "cadence_arm": self.config.trading.cadence_arm,
                        }),
                        "episode_id": episode_id,
                    }
                    decisions.append(decision)
                    if episode_id and walk and context["end_date"]:
                        episode = {
                            "episode_id": episode_id, "decision_id": decision_id, "run_id": run_id,
                            "condition_id": market_row["condition_id"], "event_id": market_row["event_id"],
                            "event_title": market_row["event_title"], "question": market_row["question"],
                            "token_id": token, "outcome_index": outcome["outcome_index"],
                            "outcome_label": outcome["outcome_label"], "threshold": threshold,
                            "cadence_arm": self.config.trading.cadence_arm,
                            "match_winner_class": context["match_winner_class"],
                            "entry_provenance": str(provenance),
                            "entered_at": iso_utc(now), "end_date": iso_utc(context["end_date"]),
                            "game_start_time": iso_utc(context["game_start"]) if context["game_start"] else None,
                            "sports_phase": context["phase"], "liquidity": context["liquidity"],
                            "volume_total": context["volume"], "fee_rate": context["fee_rate"],
                            "entry_best_ask": walk.best_ask, "entry_vwap": walk.vwap,
                            "entry_shares": walk.shares, "entry_cost": walk.cost,
                        }
                        episodes.append(episode)
                        policies.append({
                            "policy_id": uuid4().hex, "episode_id": episode_id,
                            "created_run_id": run_id, "policy_key": "HOLD_TO_RESOLUTION",
                            "stop_price": None, "created_at": iso_utc(now),
                        })
                        for stop in experiment.stop_levels:
                            policy = {
                                "policy_id": uuid4().hex, "episode_id": episode_id,
                                "created_run_id": run_id,
                                "policy_key": _stop_policy_key(stop),
                                "stop_price": stop, "created_at": iso_utc(now),
                            }
                            policies.append(policy)
                            new_active_stops.append({
                                **episode, **policy,
                                "prior_attempt_count": 0,
                                "prior_filled_shares": 0.0,
                                "prior_gross_proceeds": 0.0,
                                "prior_estimated_fee": 0.0,
                                "prior_net_proceeds": 0.0,
                                "first_triggered_at": None,
                                "first_trigger_best_bid": None,
                                "prior_best_bid": None,
                            })

        paths: list[dict[str, Any]] = []
        for episode in open_before + episodes:
            token = str(episode["token_id"])
            snapshot = snapshot_by_token.get(token)
            book = books.books.get(token)
            exit_walk = walk_bids(book, float(episode["entry_shares"])) if book else None
            paths.append({
                "path_id": uuid4().hex, "episode_id": episode["episode_id"], "run_id": run_id,
                "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                "observed_at": iso_utc(now), "best_bid": exit_walk.best_ask if exit_walk else (snapshot["best_bid"] if snapshot else None),
                "executable_bid_vwap": exit_walk.vwap if exit_walk else None,
                "executable_proceeds": exit_walk.cost if exit_walk else None,
                "status": "OBSERVED" if exit_walk else "INSUFFICIENT_BID_DEPTH",
            })

        stop_attempts: list[dict[str, Any]] = []
        stop_exits: list[dict[str, Any]] = []
        for policy in active_stop_before + new_active_stops:
            token = str(policy["token_id"])
            snapshot = snapshot_by_token.get(token)
            book = books.books.get(token)
            stop_price = float(policy["stop_price"])
            prior_attempt_count = int(policy["prior_attempt_count"] or 0)
            prior_filled = float(policy["prior_filled_shares"] or 0)
            entry_shares = float(policy["entry_shares"])
            remaining_before = max(0.0, entry_shares - prior_filled)
            if remaining_before <= 1e-7:
                continue
            current_walk = walk_bids_partial(book, remaining_before) if book else None
            current_best_bid = (
                current_walk.best_bid
                if current_walk is not None
                else (snapshot["best_bid"] if snapshot else None)
            )
            already_triggered = prior_attempt_count > 0
            if not already_triggered and (
                current_best_bid is None or float(current_best_bid) > stop_price + 1e-9
            ):
                continue

            attempt_id = uuid4().hex
            fee_rate = float(policy["fee_rate"])
            if current_walk is None:
                filled = 0.0
                remaining_after = remaining_before
                exit_vwap = None
                gross = 0.0
                fee = 0.0
                levels_used = 0
                status = "NO_BID_DEPTH"
            else:
                filled = current_walk.filled_shares
                remaining_after = current_walk.remaining_shares
                exit_vwap = current_walk.vwap
                gross = current_walk.proceeds
                fee = _execution_fee(filled, exit_vwap, fee_rate)
                levels_used = current_walk.levels_used
                status = "FULL_EXIT" if current_walk.complete else "PARTIAL_FILL"
            net = gross - fee
            prior_best_bid = _number(policy.get("prior_best_bid"))
            gap_from_stop = stop_price - exit_vwap if exit_vwap is not None else None
            drop_from_prior = (
                prior_best_bid - float(current_best_bid)
                if prior_best_bid is not None and current_best_bid is not None
                else None
            )
            attempt = {
                "attempt_id": attempt_id, "policy_id": policy["policy_id"],
                "episode_id": policy["episode_id"], "run_id": run_id,
                "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                "observed_at": iso_utc(now), "stop_price": stop_price,
                "prior_best_bid": prior_best_bid, "trigger_best_bid": current_best_bid,
                "requested_shares": remaining_before, "filled_shares": filled,
                "remaining_shares": remaining_after, "exit_vwap": exit_vwap,
                "gross_proceeds": gross, "fee_rate": fee_rate,
                "estimated_fee": fee, "net_proceeds": net,
                "levels_used": levels_used, "status": status,
                "gap_from_stop": gap_from_stop, "drop_from_prior": drop_from_prior,
            }
            stop_attempts.append(attempt)

            total_filled = prior_filled + filled
            if total_filled + 1e-7 < entry_shares:
                continue
            total_gross = float(policy["prior_gross_proceeds"] or 0) + gross
            total_fee = float(policy["prior_estimated_fee"] or 0) + fee
            total_net = float(policy["prior_net_proceeds"] or 0) + net
            exit_vwap_total = total_gross / total_filled
            first_triggered_at = str(policy.get("first_triggered_at") or iso_utc(now))
            first_trigger_best_bid = policy.get("first_trigger_best_bid")
            if first_trigger_best_bid is None:
                first_trigger_best_bid = current_best_bid
            stop_exits.append({
                "exit_id": uuid4().hex, "policy_id": policy["policy_id"],
                "episode_id": policy["episode_id"], "completed_run_id": run_id,
                "completed_attempt_id": attempt_id,
                "first_triggered_at": first_triggered_at, "completed_at": iso_utc(now),
                "stop_price": stop_price,
                "first_trigger_best_bid": first_trigger_best_bid,
                "exit_vwap": exit_vwap_total, "requested_shares": entry_shares,
                "filled_shares": total_filled, "gross_proceeds": total_gross,
                "estimated_fee": total_fee, "net_proceeds": total_net,
                "attempt_count": prior_attempt_count + 1,
                "gap_from_stop": stop_price - exit_vwap_total,
            })

        event_ids = {
            str(context["market_row"]["event_id"])
            for context in contexts
            if context["market_row"]["event_id"]
        }
        event_count = len(event_ids)
        eligible_outcome_count = sum(
            int(row["entry_eligible"]) for row in outcome_rows
        )
        exclusion_counts = Counter(
            reason
            for row in market_rows
            if not row["eligible"]
            for reason in str(row["exclusion_reason"]).split(";")
        )
        self.repository.record_collection(
            sweep={
                "sweep_id": sweep_id, "run_id": run_id, "started_at": iso_utc(now),
                "completed_at": iso_utc(), "page_count": len(sweep.pages), "event_count": event_count,
                "market_count": len(market_rows), "eligible_market_count": sum(row["eligible"] for row in market_rows),
                "eligible_outcome_count": eligible_outcome_count,
                "cursor_complete": 1,
                "request_envelope_json": canonical_json({
                    "closed": False,
                    "sports_market_types": list(
                        self.config.trading.gamma.sports_market_types
                    ),
                    "end_date_lookback_hours": self.config.trading.gamma.lookback_hours,
                    "end_date_lookahead_hours": self.config.trading.gamma.lookahead_hours,
                    "liquidity_filter": None,
                    "volume_filter": None,
                    "page_size": self.config.trading.gamma.page_size,
                }),
            },
            payloads=payloads, markets=market_rows, outcomes=outcome_rows,
            attempts=attempt_rows, snapshots=snapshot_rows, levels=level_rows,
            decisions=decisions, episodes=episodes, policies=policies, paths=paths,
            stop_attempts=stop_attempts, stop_exits=stop_exits,
        )

        resolved = 0
        open_by_condition: dict[str, dict[str, Any]] = {}
        for episode in self.repository.open_episodes():
            open_by_condition.setdefault(str(episode["condition_id"]), episode)
        for condition_id, episode in open_by_condition.items():
            game_start = _utc(episode["game_start_time"])
            if (
                game_start is None
                or now < game_start
                or not self.repository.resolution_due(condition_id, now=now)
            ):
                continue
            result = self.clob.fetch_resolution(run_id, condition_id)
            attempt = {
                "attempt_id": uuid4().hex, "run_id": run_id,
                "condition_id": result.condition_id, "attempted_at": iso_utc(now),
                "status": result.status, "request_id": result.request_id,
                "winner_index": result.winner_index, "error_type": result.error_type,
                "error_message": result.error_message,
            }
            raw_payload = None
            resolution = None
            if result.raw_payload is not None:
                raw_payload = self.repository.payload_row(
                    run_id=run_id, kind="CLOB_MARKET_RESOLUTION",
                    request_id=result.raw_payload.request_id,
                    observed_at=result.raw_payload.received_at, raw=result.raw_payload.raw,
                )
            if result.status == "RESOLVED" and result.winner_index is not None and result.market is not None and result.raw_payload is not None:
                resolution = {
                    "resolution_id": uuid4().hex, "run_id": run_id,
                    "condition_id": result.condition_id, "observed_at": str(result.observed_at),
                    "winner_index": result.winner_index, "request_id": str(result.request_id),
                    "raw_market_sha256": result.raw_payload.response_sha256,
                    "evidence_json": canonical_json({"closed": result.market.get("closed"), "tokens": result.market.get("tokens")}),
                }
                resolved += 1
            self.repository.record_resolution(attempt=attempt, resolution=resolution, payload=raw_payload)

        return {
            "events": event_count, "markets": len(market_rows),
            "eligible_markets": sum(row["eligible"] for row in market_rows),
            "eligible_outcomes": eligible_outcome_count,
            "exclusions": dict(sorted(exclusion_counts.items())),
            "cadence_arm": self.config.trading.cadence_arm,
            "book_tokens": len(books.attempts), "episodes_opened": len(episodes),
            "stop_attempts": len(stop_attempts), "stop_exits": len(stop_exits),
            "resolutions_added": resolved, "pages": len(sweep.pages), "cursor_complete": True,
        }

"""Read-only authenticated evidence probe for uncertain CLOB submissions."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .execution_ledger import ExecutionLedger, normalize_clob_response_list

_FIXED_6_SCALE = 1_000_000.0
_PRICE_TOLERANCE = 0.000001
_QUANTITY_RELATIVE_TOLERANCE = 0.05


class IntentProbeConfigurationError(ValueError):
    """Raised when a read-only venue probe cannot be configured safely."""


@dataclass(frozen=True)
class AuthenticatedClobSession:
    """Authenticated SDK client plus the public funder used for correlation."""

    client: Any
    funder_address: str


@dataclass(frozen=True)
class _OpenOrderQuery:
    id: str | None = None
    market: str | None = None
    asset_id: str | None = None


@dataclass(frozen=True)
class _TradeQuery:
    id: str | None = None
    maker_address: str | None = None
    market: str | None = None
    asset_id: str | None = None
    before: int | None = None
    after: int | None = None


def authenticated_clob_session_from_environment(
    environment: Mapping[str, str] | None = None,
) -> AuthenticatedClobSession:
    """Build an L2 client without printing or persisting credential material."""
    values = os.environ if environment is None else environment
    private_key = str(values.get("POLYMARKET_PRIVATE_KEY") or "").strip()
    funder_address = str(values.get("POLYMARKET_FUNDER_ADDRESS") or "").strip()
    if not private_key:
        raise IntentProbeConfigurationError(
            "POLYMARKET_PRIVATE_KEY environment variable이 필요합니다"
        )
    if not funder_address:
        raise IntentProbeConfigurationError(
            "POLYMARKET_FUNDER_ADDRESS environment variable이 필요합니다"
        )
    try:
        signature_type = int(values.get("POLYMARKET_SIGNATURE_TYPE", "1"))
    except (TypeError, ValueError) as error:
        raise IntentProbeConfigurationError(
            "POLYMARKET_SIGNATURE_TYPE은 정수여야 합니다"
        ) from error
    if signature_type < 0:
        raise IntentProbeConfigurationError(
            "POLYMARKET_SIGNATURE_TYPE은 음수일 수 없습니다"
        )

    try:
        from py_clob_client_v2 import ClobClient
    except ImportError as error:
        raise IntentProbeConfigurationError(
            "py-clob-client-v2가 설치된 전략 uv 환경에서 실행해야 합니다"
        ) from error

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key.removeprefix("0x"),
        chain_id=137,
        signature_type=signature_type,
        funder=funder_address,
    )
    # ``derive_api_key`` is read-only and avoids the expected create-key 400 log.
    client.set_api_creds(client.derive_api_key())
    return AuthenticatedClobSession(
        client=client,
        funder_address=funder_address,
    )


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    numeric = _number(value)
    if numeric is not None:
        if numeric > 10_000_000_000:
            numeric /= 1_000
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_full_quantity(value: Any, requested_size: float) -> float | None:
    raw = _number(value)
    if raw is None or raw <= 0 or requested_size <= 0:
        return None
    candidates = (raw, raw / _FIXED_6_SCALE)
    normalized = min(
        candidates,
        key=lambda candidate: abs(candidate - requested_size) / requested_size,
    )
    relative_error = abs(normalized - requested_size) / requested_size
    return normalized if relative_error <= _QUANTITY_RELATIVE_TOLERANCE else None


def _normalize_partial_quantity(value: Any, requested_size: float) -> float | None:
    raw = _number(value)
    if raw is None or raw <= 0 or requested_size <= 0:
        return None
    if raw <= requested_size * (1 + _QUANTITY_RELATIVE_TOLERANCE):
        return raw
    fixed_6 = raw / _FIXED_6_SCALE
    if fixed_6 <= requested_size * (1 + _QUANTITY_RELATIVE_TOLERANCE):
        return fixed_6
    return None


def _same_address(left: Any, right: Any) -> bool:
    return (
        bool(left and right) and str(left).strip().lower() == str(right).strip().lower()
    )


def _price_matches_order(value: Any, requested_price: float) -> bool:
    price = _number(value)
    return price is not None and abs(price - requested_price) <= _PRICE_TOLERANCE


def _price_is_compatible_fill(value: Any, requested_price: float, side: str) -> bool:
    price = _number(value)
    if price is None or not 0 < price < 1:
        return False
    if side == "BUY":
        return price <= requested_price + _PRICE_TOLERANCE
    return price >= requested_price - _PRICE_TOLERANCE


def _safe_query(
    source: str,
    query: Callable[[], Any],
    *,
    response_type: str,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    try:
        response = query()
        return normalize_clob_response_list(response, response_type=response_type)
    except Exception as error:  # SDK/network errors must remain secret-free.
        errors.append({"source": source, "error_type": type(error).__name__})
        return []


def _order_candidate(
    order: Mapping[str, Any],
    *,
    source: str,
    token_id: str,
    side: str,
    requested_price: float,
    requested_size: float,
    submitted_at: datetime,
    window_seconds: int,
    funder_address: str,
) -> dict[str, Any] | None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return None

    missing: list[str] = []
    asset_id = str(order.get("asset_id") or "").strip()
    if not asset_id:
        missing.append("asset_id")
    elif asset_id != token_id:
        return None

    order_side = str(order.get("side") or "").strip().upper()
    if not order_side:
        missing.append("side")
    elif order_side != side:
        return None

    maker_address = order.get("maker_address")
    if not maker_address:
        missing.append("maker_address")
    elif not _same_address(maker_address, funder_address):
        return None

    if order.get("price") in (None, ""):
        missing.append("price")
    elif not _price_matches_order(order.get("price"), requested_price):
        return None

    normalized_size = _normalize_full_quantity(
        order.get("original_size"), requested_size
    )
    if order.get("original_size") in (None, ""):
        missing.append("original_size")
    elif normalized_size is None:
        return None

    created_at = _parse_timestamp(order.get("created_at"))
    seconds_from_submission: float | None = None
    if created_at is None:
        missing.append("created_at")
    else:
        seconds_from_submission = (created_at - submitted_at).total_seconds()
        if abs(seconds_from_submission) > window_seconds:
            return None

    size_matched = _normalize_partial_quantity(
        order.get("size_matched"), requested_size
    )
    return {
        "source": source,
        "order_id": order_id,
        "status": str(order.get("status") or "").strip().upper(),
        "side": order_side or None,
        "price": _number(order.get("price")),
        "original_size": normalized_size,
        "size_matched": size_matched,
        "created_at": created_at.isoformat() if created_at else None,
        "seconds_from_submission": seconds_from_submission,
        "missing_evidence": missing,
        "strong_match": not missing,
    }


def _trade_order_candidates(
    trade: Mapping[str, Any],
    *,
    token_id: str,
    side: str,
    requested_price: float,
    requested_size: float,
    submitted_at: datetime,
    window_seconds: int,
    funder_address: str,
) -> list[dict[str, Any]]:
    trade_id = str(trade.get("id") or "").strip()
    role = str(trade.get("trader_side") or "").strip().upper()
    match_time = _parse_timestamp(trade.get("match_time"))
    seconds_from_submission = (
        (match_time - submitted_at).total_seconds() if match_time else None
    )
    if (
        seconds_from_submission is not None
        and abs(seconds_from_submission) > window_seconds
    ):
        return []

    candidates: list[dict[str, Any]] = []
    if role in {"TAKER", ""}:
        candidates.append(
            {
                "role": "TAKER",
                "order_id": str(trade.get("taker_order_id") or "").strip(),
                "asset_id": trade.get("asset_id"),
                "side": trade.get("side"),
                "price": trade.get("price"),
                "size": trade.get("size"),
                "maker_address_matches": True,
                "maker_address_present": True,
                "role_reported": bool(role),
            }
        )
    if role in {"MAKER", ""}:
        for maker_order in trade.get("maker_orders") or []:
            candidates.append(
                {
                    "role": "MAKER",
                    "order_id": str(maker_order.get("order_id") or "").strip(),
                    "asset_id": maker_order.get("asset_id") or trade.get("asset_id"),
                    "side": maker_order.get("side"),
                    "price": maker_order.get("price"),
                    "size": maker_order.get("matched_amount"),
                    "maker_address_matches": _same_address(
                        maker_order.get("maker_address"), funder_address
                    ),
                    "maker_address_present": bool(maker_order.get("maker_address")),
                    "role_reported": bool(role),
                }
            )

    matched: list[dict[str, Any]] = []
    for candidate in candidates:
        order_id = candidate["order_id"]
        if not order_id:
            continue
        missing: list[str] = []

        asset_id = str(candidate["asset_id"] or "").strip()
        if not asset_id:
            missing.append("asset_id")
        elif asset_id != token_id:
            continue

        candidate_side = str(candidate["side"] or "").strip().upper()
        if not candidate_side:
            missing.append("side")
        elif candidate_side != side:
            continue

        if candidate["price"] in (None, ""):
            missing.append("price")
        elif candidate["role"] == "MAKER":
            if not _price_matches_order(candidate["price"], requested_price):
                continue
        elif not _price_is_compatible_fill(candidate["price"], requested_price, side):
            continue

        raw_size = _number(candidate["size"])
        if candidate["size"] in (None, ""):
            missing.append("size")
        elif raw_size is None or raw_size <= 0:
            continue

        if match_time is None:
            missing.append("match_time")
        if not candidate["role_reported"]:
            missing.append("trader_side")
        if candidate["role"] == "MAKER":
            if not candidate["maker_address_present"]:
                missing.append("maker_address")
            elif not candidate["maker_address_matches"]:
                continue

        matched.append(
            {
                "source": f"authenticated_trade_{candidate['role'].lower()}",
                "order_id": order_id,
                "trade_id": trade_id or None,
                "trade_status": str(trade.get("status") or "").strip().upper(),
                "side": candidate_side or None,
                "price": _number(candidate["price"]),
                "raw_matched_size": raw_size,
                "match_time": match_time.isoformat() if match_time else None,
                "seconds_from_submission": seconds_from_submission,
                "missing_evidence": missing,
                "strong_match": not missing,
            }
        )
    return matched


def _aggregate_trade_candidates(
    candidates: list[dict[str, Any]],
    *,
    requested_size: float,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str | None]] = set()
    for candidate in candidates:
        order_id = candidate["order_id"]
        identity = (order_id, candidate.get("trade_id"))
        if identity in seen:
            continue
        seen.add(identity)
        group = grouped.setdefault(
            order_id,
            {
                "order_id": order_id,
                "sources": set(),
                "trade_ids": set(),
                "trade_statuses": set(),
                "raw_matched_size": 0.0,
                "prices": set(),
                "max_abs_seconds_from_submission": 0.0,
                "missing_evidence": set(),
                "strong_match": False,
            },
        )
        group["sources"].add(candidate["source"])
        if candidate.get("trade_id"):
            group["trade_ids"].add(candidate["trade_id"])
        if candidate.get("trade_status"):
            group["trade_statuses"].add(candidate["trade_status"])
        group["raw_matched_size"] += candidate.get("raw_matched_size") or 0.0
        if candidate.get("price") is not None:
            group["prices"].add(candidate["price"])
        if candidate.get("seconds_from_submission") is not None:
            group["max_abs_seconds_from_submission"] = max(
                group["max_abs_seconds_from_submission"],
                abs(candidate["seconds_from_submission"]),
            )
        group["missing_evidence"].update(candidate["missing_evidence"])
        group["strong_match"] = group["strong_match"] or candidate["strong_match"]

    result: list[dict[str, Any]] = []
    for order_id in sorted(grouped):
        group = grouped[order_id]
        raw_size = group["raw_matched_size"]
        size_candidates = (raw_size, raw_size / _FIXED_6_SCALE)
        observed_size = min(
            size_candidates,
            key=lambda candidate: abs(candidate - requested_size) / requested_size,
        )
        size_matches_requested = (
            abs(observed_size - requested_size) / requested_size
            <= _QUANTITY_RELATIVE_TOLERANCE
        )
        missing_evidence = set(group["missing_evidence"])
        if not size_matches_requested:
            missing_evidence.add("aggregate_size_differs_from_requested")
        result.append(
            {
                "order_id": order_id,
                "sources": sorted(group["sources"]),
                "trade_ids": sorted(group["trade_ids"]),
                "trade_statuses": sorted(group["trade_statuses"]),
                "observed_matched_size": observed_size,
                "size_matches_requested": size_matches_requested,
                "prices": sorted(group["prices"]),
                "max_abs_seconds_from_submission": group[
                    "max_abs_seconds_from_submission"
                ],
                "missing_evidence": sorted(missing_evidence),
                "strong_match": group["strong_match"] and not missing_evidence,
            }
        )
    return result


def probe_unresolved_intent(
    db_path: str | Path,
    *,
    strategy_name: str,
    submission_id: str,
    client: Any,
    funder_address: str,
    window_seconds: int = 600,
) -> dict[str, Any]:
    """Correlate one uncertain intent against authenticated venue evidence.

    This function never mutates the operator outcome resolution. Even an empty,
    fully successful venue scan is not sufficient proof that no order existed.
    """
    if window_seconds < 1 or window_seconds > 86_400:
        raise ValueError("window_seconds는 1~86400 범위여야 합니다")
    ledger = ExecutionLedger(db_path, strategy_name=strategy_name)
    unresolved = {
        row["submission_id"]: row for row in ledger.unresolved_submission_outcomes()
    }
    intent = unresolved.get(submission_id)
    if intent is None:
        raise ValueError("해당 submission_id의 불확실한 live intent를 찾을 수 없습니다")

    submitted_at = _parse_timestamp(intent.get("submitted_at"))
    if submitted_at is None:
        raise ValueError("intent submitted_at을 안전하게 해석할 수 없습니다")
    token_id = str(intent.get("token_id") or "").strip()
    side = str(intent.get("side") or "").strip().upper()
    requested_price = _number(intent.get("requested_price"))
    requested_size = _number(intent.get("requested_size"))
    if not token_id or side not in {"BUY", "SELL"}:
        raise ValueError("intent token_id/side evidence가 올바르지 않습니다")
    if requested_price is None or requested_size is None or requested_size <= 0:
        raise ValueError("intent price/size evidence가 올바르지 않습니다")

    timestamp = submitted_at.timestamp()
    after = math.floor(timestamp - window_seconds)
    before = math.ceil(timestamp + window_seconds)
    errors: list[dict[str, str]] = []
    current_orders = _safe_query(
        "current_orders",
        lambda: client.get_open_orders(
            _OpenOrderQuery(asset_id=token_id), only_first_page=False
        ),
        response_type="order",
        errors=errors,
    )
    pre_migration_orders = _safe_query(
        "pre_migration_orders",
        lambda: client.get_pre_migration_orders(only_first_page=False),
        response_type="order",
        errors=errors,
    )
    trades = _safe_query(
        "authenticated_trades",
        lambda: client.get_trades(
            _TradeQuery(asset_id=token_id, after=after, before=before),
            only_first_page=False,
        ),
        response_type="trade",
        errors=errors,
    )

    order_candidates: list[dict[str, Any]] = []
    for source, orders in (
        ("current_orders", current_orders),
        ("pre_migration_orders", pre_migration_orders),
    ):
        for order in orders:
            candidate = _order_candidate(
                order,
                source=source,
                token_id=token_id,
                side=side,
                requested_price=requested_price,
                requested_size=requested_size,
                submitted_at=submitted_at,
                window_seconds=window_seconds,
                funder_address=funder_address,
            )
            if candidate is not None:
                order_candidates.append(candidate)

    raw_trade_candidates: list[dict[str, Any]] = []
    for trade in trades:
        raw_trade_candidates.extend(
            _trade_order_candidates(
                trade,
                token_id=token_id,
                side=side,
                requested_price=requested_price,
                requested_size=requested_size,
                submitted_at=submitted_at,
                window_seconds=window_seconds,
                funder_address=funder_address,
            )
        )
    trade_candidates = _aggregate_trade_candidates(
        raw_trade_candidates,
        requested_size=requested_size,
    )

    candidate_ids = sorted(
        {candidate["order_id"] for candidate in order_candidates}
        | {candidate["order_id"] for candidate in trade_candidates}
    )
    strong_ids = {
        candidate["order_id"]
        for candidate in order_candidates + trade_candidates
        if candidate["strong_match"]
    }
    unique_order_id = (
        candidate_ids[0]
        if len(candidate_ids) == 1 and candidate_ids[0] in strong_ids
        else None
    )
    resolution_evidence = None
    if unique_order_id:
        resolution_evidence = {
            "resolution": "ORDER_ID_LINKED",
            "order_id": unique_order_id,
            "confirmation": f"LINK_{submission_id}_TO_{unique_order_id}",
            "reason": (
                "authenticated CLOB order/trade history exact match around "
                "submission timestamp"
            ),
        }

    return {
        "intent": intent,
        "probe_window": {
            "seconds": window_seconds,
            "after": datetime.fromtimestamp(after, tz=timezone.utc).isoformat(),
            "before": datetime.fromtimestamp(before, tz=timezone.utc).isoformat(),
        },
        "scanned_counts": {
            "current_orders": len(current_orders),
            "pre_migration_orders": len(pre_migration_orders),
            "authenticated_trades": len(trades),
        },
        "query_errors": errors,
        "order_candidates": order_candidates,
        "trade_order_candidates": trade_candidates,
        "candidate_order_ids": candidate_ids,
        "unique_candidate_order_id": unique_order_id,
        "resolution_evidence": resolution_evidence,
        "no_order_created_proven": False,
        "operator_note": (
            "후보가 없어도 NO_ORDER_CREATED 증거는 아닙니다. "
            "ORDER_ID_LINKED는 unique candidate가 있을 때만 사용하세요."
        ),
    }

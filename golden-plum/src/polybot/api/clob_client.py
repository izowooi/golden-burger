"""CLOB API client wrapper for order execution.

Polymarket이 2026년 4월 CLOB v2로 마이그레이션함에 따라 본 모듈은
`py-clob-client-v2` (import: `py_clob_client_v2`) 를 사용한다.
구버전 `py-clob-client` 는 `order_version_mismatch` 오류로 더 이상 동작하지 않는다.
"""
import hashlib
import json
import logging
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from py_clob_client_v2 import BookParams

from polybot_observability import (
    ClobReconciliationPhaseError,
    ClobResponseContractError,
    ClobResponseUnavailableError,
    ExecutionLedger,
    SubmissionEvidenceError,
    SubmissionOutcomeQuarantinedError,
    normalize_clob_response,
    normalize_clob_response_list,
    safe_clob_response_shape,
)
from ..config import ApiConfig
from ..utils.deadline import CycleBudget
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)

_PROVABLY_UNFILLED_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}
_TERMINAL_ORDER_STATUSES = _PROVABLY_UNFILLED_ORDER_STATUSES | {"MATCHED"}
# Exact-USDC market BUY responses can report six-decimal matched shares while
# their signed taker amount is four-decimal venue precision.  Preserve both
# exact values and permit at most one signed-share quantum during status audit.
_MARKET_BUY_QUANTITY_TOLERANCE = 0.0001
_MARKET_BUY_TAKER_QUANTUM_MICROS = 100
_FIXED_6 = Decimal(1_000_000)
_FEE_QUANTUM_USDC = Decimal("0.00001")


@dataclass(frozen=True)
class ClobV2FeeSchedule:
    """Authoritative dynamic fee parameters for one CLOB token."""

    condition_id: str
    rate: Decimal
    exponent: int
    taker_only: bool


class PreSubmissionContractError(ClobResponseContractError):
    """A deterministic live-order contract failure proven to precede POST."""


def _decode_catalog_token_ids(value: Any) -> tuple[list[str], bool]:
    """Decode canonical token IDs plus the one known legacy double encoding."""
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ClobResponseContractError(
            "Gamma fee catalog contains malformed token identity"
        ) from error
    legacy_double_encoded = isinstance(parsed, str)
    if legacy_double_encoded:
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ClobResponseContractError(
                "Gamma fee catalog contains malformed legacy token identity"
            ) from error
    if not isinstance(parsed, list):
        raise ClobResponseContractError(
            "Gamma fee catalog token identity is not a list"
        )
    normalized = [str(item or "").strip() for item in parsed]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ClobResponseContractError(
            "Gamma fee catalog token identity is empty or duplicated"
        )
    return normalized, legacy_double_encoded


@dataclass(frozen=True)
class BuyBookWalk:
    """A full displayed-ask walk for one fixed USDC notional."""

    token_id: str
    best_bid: Optional[float]
    best_ask: float
    spread: Optional[float]
    vwap: float
    shares: float
    cost: float
    limit_price: float
    levels_used: int


@dataclass(frozen=True)
class SellBookWalk:
    """A full displayed-bid walk for one fixed share quantity."""

    token_id: str
    best_bid: float
    best_ask: Optional[float]
    spread: Optional[float]
    vwap: float
    shares: float
    proceeds: float
    limit_price: float
    levels_used: int


@dataclass(frozen=True)
class ClobResolutionToken:
    """One normalized public CLOB market token at resolution lookup time."""

    outcome: str
    token_id: str
    price: float
    winner: bool


@dataclass(frozen=True)
class ClobResolutionProof:
    """Fail-closed CLOB market result for one exact condition ID."""

    condition_id: str
    status: str
    observed_at: str
    tokens: tuple[ClobResolutionToken, ...]
    winner_index: Optional[int]
    evidence_sha256: str
    evidence_json: str


def _book_field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _normalize_book_levels(
    value: Any,
    side: str,
    *,
    allow_empty: bool = False,
) -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        raise ClobResponseContractError(f"CLOB {side} levels must be a sequence")
    levels: list[tuple[float, float]] = []
    for raw in value:
        try:
            price = float(_book_field(raw, "price"))
            size = float(_book_field(raw, "size"))
        except (TypeError, ValueError) as error:
            raise ClobResponseContractError(
                f"CLOB {side} level price/size must be numeric"
            ) from error
        if (
            not math.isfinite(price)
            or not 0 < price <= 1
            or not math.isfinite(size)
            or size <= 0
        ):
            raise ClobResponseContractError(
                f"CLOB {side} level price/size is outside its domain"
            )
        levels.append((price, size))
    if not levels and not allow_empty:
        raise ClobResponseUnavailableError(f"CLOB {side} side is empty")
    return sorted(levels, key=lambda row: row[0], reverse=side == "bid")


def _walk_buy_book(book: Any, token_id: str, notional_usdc: float) -> BuyBookWalk:
    if not math.isfinite(notional_usdc) or notional_usdc <= 0:
        raise ValueError("notional_usdc must be finite and positive")
    bids = _normalize_book_levels(
        _book_field(book, "bids"), "bid", allow_empty=True
    )
    asks = _normalize_book_levels(_book_field(book, "asks"), "ask")
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0]
    if best_bid is not None and best_bid > best_ask + 1e-9:
        raise ClobResponseContractError("CLOB order book is crossed")
    remaining = notional_usdc
    shares = 0.0
    levels_used = 0
    limit_price = best_ask
    for price, size in asks:
        available_cost = price * size
        spent = min(remaining, available_cost)
        shares += spent / price
        remaining -= spent
        levels_used += 1
        limit_price = price
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or shares <= 0:
        raise ClobResponseUnavailableError("full $5 displayed ask depth is unavailable")
    return BuyBookWalk(
        token_id=str(token_id),
        best_bid=best_bid,
        best_ask=best_ask,
        spread=(best_ask - best_bid if best_bid is not None else None),
        vwap=notional_usdc / shares,
        shares=shares,
        cost=notional_usdc,
        limit_price=limit_price,
        levels_used=levels_used,
    )


def _walk_sell_book(book: Any, token_id: str, shares: float) -> SellBookWalk:
    """Walk bids for the full holding; never impute missing stop depth."""
    if not math.isfinite(shares) or shares <= 0:
        raise ValueError("shares must be finite and positive")
    bids = _normalize_book_levels(_book_field(book, "bids"), "bid")
    asks = _normalize_book_levels(
        _book_field(book, "asks"), "ask", allow_empty=True
    )
    best_bid = bids[0][0]
    best_ask = asks[0][0] if asks else None
    if best_ask is not None and best_bid > best_ask + 1e-9:
        raise ClobResponseContractError("CLOB order book is crossed")
    remaining = shares
    proceeds = 0.0
    levels_used = 0
    limit_price = best_bid
    for price, size in bids:
        sold = min(remaining, size)
        proceeds += sold * price
        remaining -= sold
        levels_used += 1
        limit_price = price
        if remaining <= 1e-9:
            break
    if remaining > 1e-7:
        raise ClobResponseUnavailableError(
            "full displayed bid depth for stop shares is unavailable"
        )
    return SellBookWalk(
        token_id=str(token_id),
        best_bid=best_bid,
        best_ask=best_ask,
        spread=(best_ask - best_bid if best_ask is not None else None),
        vwap=proceeds / shares,
        shares=shares,
        proceeds=proceeds,
        limit_price=limit_price,
        levels_used=levels_used,
    )


def _canonical_book_evidence(book: Any, token_id: str) -> str:
    """Serialize direct displayed levels for later counterfactual replay."""
    payload = {
        "schema_version": 1,
        "token_id": str(token_id),
        "bids": [
            {"price": price, "size": size}
            for price, size in _normalize_book_levels(
                _book_field(book, "bids"), "bid", allow_empty=True
            )
        ],
        "asks": [
            {"price": price, "size": size}
            for price, size in _normalize_book_levels(
                _book_field(book, "asks"), "ask", allow_empty=True
            )
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_execution_capacity_evidence(
    book_json: str,
    notionals_usdc: Iterable[float],
) -> str:
    """Materialize same-snapshot displayed-depth scaling evidence.

    The result is deliberately a counterfactual: it records whether each BUY
    notional could consume the displayed asks and whether the resulting shares
    could immediately consume the displayed bids. It is neither an order nor
    proof that the displayed size would remain available after submission.
    """
    try:
        book = json.loads(book_json)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ClobResponseContractError(
            "cached CLOB book evidence is not valid JSON"
        ) from error
    if not isinstance(book, Mapping):
        raise ClobResponseContractError("cached CLOB book evidence must be an object")
    token_id = str(book.get("token_id") or "").strip()
    if not token_id:
        raise ClobResponseContractError("cached CLOB book evidence has no token ID")

    bids = _normalize_book_levels(book.get("bids"), "bid", allow_empty=True)
    asks = _normalize_book_levels(book.get("asks"), "ask", allow_empty=True)
    rows: list[dict[str, Any]] = []
    normalized_notionals: list[float] = []
    for raw_notional in notionals_usdc:
        try:
            notional = float(raw_notional)
        except (TypeError, ValueError) as error:
            raise ValueError("scaling notionals must be numeric") from error
        if not math.isfinite(notional) or notional <= 0:
            raise ValueError("scaling notionals must be finite and positive")
        normalized_notionals.append(notional)
    if not normalized_notionals or len(set(normalized_notionals)) != len(
        normalized_notionals
    ):
        raise ValueError("scaling notionals must be nonempty and unique")
    if normalized_notionals != sorted(normalized_notionals):
        raise ValueError("scaling notionals must be strictly increasing")

    for notional in normalized_notionals:
        row: dict[str, Any] = {
            "notional_usdc": notional,
            "buy_full_fill": False,
            "sell_full_fill": False,
        }
        try:
            buy = _walk_buy_book(book, token_id, notional)
        except ClobResponseUnavailableError:
            rows.append(row)
            continue
        row.update(
            {
                "buy_full_fill": True,
                "buy_vwap": buy.vwap,
                "buy_shares": buy.shares,
                "buy_limit_price": buy.limit_price,
                "buy_levels_used": buy.levels_used,
            }
        )
        try:
            sell = _walk_sell_book(book, token_id, buy.shares)
        except ClobResponseUnavailableError:
            rows.append(row)
            continue
        round_trip_pnl = sell.proceeds - notional
        row.update(
            {
                "sell_full_fill": True,
                "sell_vwap": sell.vwap,
                "sell_limit_price": sell.limit_price,
                "sell_proceeds_usdc": sell.proceeds,
                "sell_levels_used": sell.levels_used,
                "same_snapshot_round_trip_pnl_usdc": round_trip_pnl,
                "same_snapshot_round_trip_bps": round_trip_pnl / notional * 10_000,
            }
        )
        rows.append(row)

    payload = {
        "schema_version": 1,
        "semantics": (
            "same_snapshot_displayed_depth_counterfactual_no_fees_not_actual_fill"
        ),
        "token_id": token_id,
        "displayed_ask_notional_usdc": sum(price * size for price, size in asks),
        "displayed_bid_shares": sum(size for _price, size in bids),
        "notionals": rows,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalize_order_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    prefix = "ORDER_STATUS_"
    return status[len(prefix):] if status.startswith(prefix) else status


def _is_explicit_zero(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number == 0.0


def _recorded_trade_ids(value: Any) -> list[str]:
    """Decode previously observed exact trade IDs without guessing evidence."""
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError) as error:
        raise ClobResponseContractError(
            "recorded associated trade IDs JSON이 유효하지 않습니다"
        ) from error
    if not isinstance(decoded, list):
        raise ClobResponseContractError(
            "recorded associated trade IDs가 list가 아닙니다"
        )
    trade_ids = [str(item or "").strip() for item in decoded]
    if any(not trade_id for trade_id in trade_ids):
        raise ClobResponseContractError(
            "recorded associated trade ID가 비어 있습니다"
        )
    if len(set(trade_ids)) != len(trade_ids):
        raise ClobResponseContractError(
            "recorded associated trade ID가 중복되었습니다"
        )
    return trade_ids


def _normalize_clob_resolution(
    condition_id: str,
    value: Any,
    *,
    observed_at: Optional[str] = None,
) -> ClobResolutionProof:
    """Accept only an exact closed two-token market with one 0/1 winner.

    An open market is returned as ``OPEN``.  A closed market without a unique
    winner remains ``CLOSED_UNRESOLVED``.  Malformed identity, payout, or
    winner fields raise instead of being interpreted as settlement evidence.
    """
    normalized_condition = str(condition_id or "").strip()
    if not normalized_condition:
        raise ValueError("condition_id is required")
    if not isinstance(value, Mapping):
        raise ClobResponseContractError("CLOB market response must be a mapping")
    returned_condition = value.get("condition_id") or value.get("conditionId")
    if (
        returned_condition not in (None, "")
        and str(returned_condition) != normalized_condition
    ):
        raise ClobResponseContractError("CLOB market condition_id mismatch")
    closed = value.get("closed")
    if not isinstance(closed, bool):
        raise ClobResponseContractError("CLOB market closed flag must be boolean")
    observed = observed_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    if not closed:
        evidence_json = json.dumps(
            {"closed": False}, sort_keys=True, separators=(",", ":")
        )
        return ClobResolutionProof(
            condition_id=normalized_condition,
            status="OPEN",
            observed_at=observed,
            tokens=(),
            winner_index=None,
            evidence_sha256=hashlib.sha256(evidence_json.encode()).hexdigest(),
            evidence_json=evidence_json,
        )

    raw_tokens = value.get("tokens")
    if not isinstance(raw_tokens, list) or len(raw_tokens) != 2:
        raise ClobResponseContractError(
            "closed CLOB market must contain exactly two tokens"
        )
    tokens: list[ClobResolutionToken] = []
    for raw_token in raw_tokens:
        if not isinstance(raw_token, Mapping):
            raise ClobResponseContractError("CLOB market token must be a mapping")
        outcome = str(raw_token.get("outcome") or "").strip()
        token_id = str(raw_token.get("token_id") or "").strip()
        winner = raw_token.get("winner")
        try:
            price = float(raw_token.get("price"))
        except (TypeError, ValueError) as error:
            raise ClobResponseContractError(
                "CLOB resolution token price must be numeric"
            ) from error
        if (
            not outcome
            or not token_id
            or not isinstance(winner, bool)
            or not math.isfinite(price)
            or not 0 <= price <= 1
        ):
            raise ClobResponseContractError(
                "CLOB resolution token identity/payout is invalid"
            )
        tokens.append(ClobResolutionToken(outcome, token_id, price, winner))
    if len({token.token_id for token in tokens}) != 2 or len(
        {token.outcome for token in tokens}
    ) != 2:
        raise ClobResponseContractError(
            "CLOB resolution token identities must be distinct"
        )
    winners = [index for index, token in enumerate(tokens) if token.winner]
    status = "RESOLVED" if len(winners) == 1 else "CLOSED_UNRESOLVED"
    winner_index = winners[0] if status == "RESOLVED" else None
    if winner_index is not None:
        expected_prices = [0.0, 0.0]
        expected_prices[winner_index] = 1.0
        if any(
            not math.isclose(token.price, expected_prices[index], abs_tol=1e-12)
            for index, token in enumerate(tokens)
        ):
            raise ClobResponseContractError(
                "CLOB unique winner is not aligned with exact 0/1 payouts"
            )
    evidence = {
        "closed": True,
        "tokens": [
            {
                "outcome": token.outcome,
                "price": token.price,
                "token_id": token.token_id,
                "winner": token.winner,
            }
            for token in tokens
        ],
    }
    evidence_json = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return ClobResolutionProof(
        condition_id=normalized_condition,
        status=status,
        observed_at=observed,
        tokens=tuple(tokens),
        winner_index=winner_index,
        evidence_sha256=hashlib.sha256(evidence_json.encode()).hexdigest(),
        evidence_json=evidence_json,
    )


def _trade_references_exact_order(
    trade: Mapping[str, Any], order_id: str
) -> bool:
    """Match authenticated trade evidence only by an exact venue order ID."""
    expected_order_id = str(order_id)
    if str(trade.get("taker_order_id") or "") == expected_order_id:
        return True
    return any(
        isinstance(maker_order, Mapping)
        and str(maker_order.get("order_id") or "") == expected_order_id
        for maker_order in (trade.get("maker_orders") or [])
    )


def _exact_order_trade_ids(
    trades: Iterable[Mapping[str, Any]], order_id: str
) -> list[str]:
    """Return stable unique trade IDs carrying exact order-ID evidence."""
    trade_ids: list[str] = []
    seen = set()
    for trade in trades:
        if not _trade_references_exact_order(trade, order_id):
            continue
        trade_id = str(trade.get("id") or "").strip()
        if not trade_id:
            raise ClobResponseContractError(
                "exact order ID와 일치한 authenticated trade ID가 비어 있습니다"
            )
        # One trade ID can legitimately have multiple bucket rows.  Re-fetch
        # that canonical ID once while retaining strict exact-order matching.
        if trade_id not in seen:
            seen.add(trade_id)
            trade_ids.append(trade_id)
    return trade_ids


class ClobClientWrapper:
    """Wrapper for Polymarket CLOB v2 API client.

    Handles:
    - Authentication with L1 (wallet) and L2 (API key) credentials
    - Order placement and cancellation
    - Price and orderbook queries
    - Simulation mode for testing
    """

    HOST = "https://clob.polymarket.com"
    DEFAULT_TICK_SIZE = 0.01  # Polymarket default tick size
    MAX_MIDPOINT_BATCH_SIZE = 500

    def __init__(
        self,
        config: ApiConfig,
        simulation_mode: bool = False,
        *,
        audit_db_path=None,
        strategy_name: str = "unknown",
        cycle_budget: Optional[CycleBudget] = None,
    ):
        """Initialize CLOB client.

        Args:
            config: API configuration with credentials
            simulation_mode: If True, don't execute real orders
        """
        self.config = config
        self.simulation_mode = simulation_mode
        self._client = None
        self._initialized = False
        self._midpoint_snapshot: Optional[Dict[str, Optional[float]]] = None
        self._book_evidence_by_token: Dict[str, str] = {}
        self._fee_schedules_by_token: Dict[str, ClobV2FeeSchedule] = {}
        self.cycle_budget = cycle_budget
        self.execution_ledger = (
            ExecutionLedger(audit_db_path, strategy_name=strategy_name)
            if audit_db_path is not None
            else None
        )

    def close(self) -> None:
        """Close the SDK's process-global HTTP/2 pool at CLI shutdown.

        ``py-clob-client-v2`` keeps one module-level synchronous ``httpx``
        client.  A one-shot Jenkins process has no later request to reuse that
        pool, and leaving it open can keep the shell step alive after the run
        audit has already succeeded.  This method is deliberately called only
        after the complete reconciliation/trading cycle has returned.
        """
        if not self._initialized:
            return
        try:
            from py_clob_client_v2.http_helpers import helpers
        except ImportError:
            logger.warning("CLOB v2 HTTP client module is unavailable during shutdown")
            return
        http_client = getattr(helpers, "_http_client", None)
        close = getattr(http_client, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _positive_fill_quantity(raw_size: Any, requested_size: Any) -> Decimal:
        """Decode an SDK-human or raw fixed-6 fill quantity fail-closed."""
        try:
            raw = Decimal(str(raw_size))
            requested = Decimal(str(requested_size))
        except Exception as error:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill quantity is not numeric"
            ) from error
        if (
            not raw.is_finite()
            or not requested.is_finite()
            or raw <= 0
            or requested <= 0
        ):
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill quantity is not positive"
            )
        if raw > requested * Decimal(1_000):
            normalized = raw / _FIXED_6
        elif raw <= requested * Decimal("1.05"):
            normalized = raw
        else:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill quantity representation is ambiguous"
            )
        if normalized <= 0 or normalized > requested * Decimal("1.05"):
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill quantity exceeds the order envelope"
            )
        return normalized

    def _open_evidence_db_read_only(self) -> sqlite3.Connection:
        """Open this runtime's already-created SQLite evidence DB read-only."""
        ledger = self.execution_ledger
        db_path = getattr(ledger, "db_path", None)
        if db_path is None:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence database identity is unavailable"
            )
        resolved = db_path.expanduser().resolve()
        if not resolved.is_file():
            raise ClobResponseContractError(
                "CLOB v2 fee evidence database does not exist"
            )
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _catalog_fee_schedule(self, token_id: str) -> ClobV2FeeSchedule:
        """Resolve the exact Gamma catalog row that owns a token."""
        normalized_token = str(token_id or "").strip()
        if not normalized_token:
            raise ClobResponseContractError("CLOB v2 fee evidence token ID is empty")
        try:
            with self._open_evidence_db_read_only() as connection:
                rows = connection.execute(
                    "SELECT condition_id, token_ids_json, fees_enabled, fee_rate, "
                    "fee_exponent, fee_taker_only FROM market_catalog"
                ).fetchall()
        except ClobResponseContractError:
            raise
        except Exception as error:
            raise ClobResponseContractError(
                "Gamma fee catalog could not be read"
            ) from error

        matches = []
        legacy_rows = 0
        for row in rows:
            token_ids, legacy_double_encoded = _decode_catalog_token_ids(
                row["token_ids_json"]
            )
            legacy_rows += int(legacy_double_encoded)
            if normalized_token in set(token_ids):
                matches.append(row)
        if legacy_rows:
            logger.warning(
                "Gamma fee catalog의 legacy double-encoded token row %s건을 "
                "읽기 호환 처리했습니다; 다음 catalog upsert에서 canonicalize됩니다",
                legacy_rows,
            )
        if len(matches) != 1:
            raise ClobResponseContractError(
                "Gamma fee catalog does not bind the token to exactly one market"
            )

        row = matches[0]
        condition_id = str(row["condition_id"] or "").strip()
        try:
            rate = Decimal(str(row["fee_rate"]))
            exponent_decimal = Decimal(str(row["fee_exponent"]))
            fees_enabled = int(row["fees_enabled"])
            taker_only_int = int(row["fee_taker_only"])
        except Exception as error:
            raise ClobResponseContractError(
                "Gamma fee catalog omitted explicit fee parameters"
            ) from error
        if (
            not condition_id
            or not rate.is_finite()
            or rate < 0
            or rate > 1
            or not exponent_decimal.is_finite()
            or exponent_decimal < 0
            or exponent_decimal != exponent_decimal.to_integral_value()
            or exponent_decimal > 10
            or fees_enabled not in {0, 1}
            or taker_only_int not in {0, 1}
            or bool(rate) != bool(fees_enabled)
        ):
            raise ClobResponseContractError(
                "Gamma fee catalog parameters are outside the contract"
            )
        return ClobV2FeeSchedule(
            condition_id=condition_id,
            rate=rate,
            exponent=int(exponent_decimal),
            taker_only=bool(taker_only_int),
        )

    def _clob_v2_fee_schedule(self, token_id: str) -> ClobV2FeeSchedule:
        """Cross-check Gamma and authoritative CLOB dynamic fee parameters."""
        normalized_token = str(token_id or "").strip()
        if not normalized_token:
            raise ClobResponseContractError("CLOB v2 fee evidence token ID is empty")
        cache = getattr(self, "_fee_schedules_by_token", None)
        if cache is None:
            cache = {}
            self._fee_schedules_by_token = cache
        cached = cache.get(normalized_token)
        if cached is not None:
            return cached

        catalog_schedule = self._catalog_fee_schedule(normalized_token)
        market_info = self.client.get_clob_market_info(
            catalog_schedule.condition_id
        )
        if not isinstance(market_info, Mapping):
            raise ClobResponseContractError("CLOB market-info response is not an object")
        returned_condition = str(
            market_info.get("c") or market_info.get("condition_id") or ""
        ).strip()
        market_tokens = {
            str(item.get("t") or item.get("token_id") or "").strip()
            for item in (market_info.get("t") or market_info.get("tokens") or [])
            if isinstance(item, Mapping)
        }
        if (
            returned_condition != catalog_schedule.condition_id
            or normalized_token not in market_tokens
        ):
            raise ClobResponseContractError(
                "CLOB market-info identity does not match the requested token"
            )
        fee_details = market_info.get("fd") or market_info.get("fee_details")
        if not isinstance(fee_details, Mapping):
            raise ClobResponseContractError(
                "CLOB market-info omitted dynamic fee parameters"
            )
        try:
            rate = Decimal(str(fee_details.get("r")))
            exponent_decimal = Decimal(str(fee_details.get("e")))
        except Exception as error:
            raise ClobResponseContractError(
                "CLOB market-info fee parameters are not numeric"
            ) from error
        taker_only = fee_details.get("to")
        if (
            not rate.is_finite()
            or rate < 0
            or rate > 1
            or not exponent_decimal.is_finite()
            or exponent_decimal < 0
            or exponent_decimal != exponent_decimal.to_integral_value()
            or exponent_decimal > 10
            or not isinstance(taker_only, bool)
        ):
            raise ClobResponseContractError(
                "CLOB market-info dynamic fee parameters are outside the contract"
            )
        schedule = ClobV2FeeSchedule(
            condition_id=catalog_schedule.condition_id,
            rate=rate,
            exponent=int(exponent_decimal),
            taker_only=taker_only,
        )
        if schedule != catalog_schedule:
            raise ClobResponseContractError(
                "Gamma and CLOB dynamic fee parameters do not match"
            )
        cache[normalized_token] = schedule
        return schedule

    def _submission_requested_size(
        self,
        pending: Mapping[str, Any],
        *,
        order_id: str,
    ) -> Decimal:
        """Read and identity-check the persisted order quantity."""
        submission_id = str(pending.get("submission_id") or "").strip()
        if not submission_id:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence submission ID is empty"
            )
        try:
            with self._open_evidence_db_read_only() as connection:
                rows = connection.execute(
                    "SELECT order_id, token_id, side, requested_size "
                    "FROM order_submissions WHERE submission_id = ?",
                    (submission_id,),
                ).fetchall()
        except ClobResponseContractError:
            raise
        except Exception as error:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence submission could not be read"
            ) from error
        if len(rows) != 1:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence submission identity is not unique"
            )
        row = rows[0]
        if (
            str(row["order_id"] or "") != str(order_id)
            or str(row["token_id"] or "") != str(pending.get("token_id") or "")
            or str(row["side"] or "").strip().upper() not in {"BUY", "SELL"}
        ):
            raise ClobResponseContractError(
                "CLOB v2 fee evidence submission identity does not match"
            )
        try:
            requested_size = Decimal(str(row["requested_size"]))
        except Exception as error:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence requested quantity is not numeric"
            ) from error
        if not requested_size.is_finite() or requested_size <= 0:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence requested quantity is not positive"
            )
        return requested_size

    def _attach_clob_v2_fee_evidence(
        self,
        trade: Mapping[str, Any],
        *,
        pending: Mapping[str, Any],
        order_id: str,
    ) -> Dict[str, Any]:
        """Replace the V2 legacy zero-rate placeholder with exact fee evidence."""
        enriched: Dict[str, Any] = dict(trade)
        status = str(enriched.get("status") or "").strip().upper()
        for prefix in ("TRADE_STATUS_", "ORDER_STATUS_"):
            if status.startswith(prefix):
                status = status[len(prefix) :]
        if status != "CONFIRMED":
            return enriched

        maker_orders = [
            dict(item)
            for item in (enriched.get("maker_orders") or [])
            if isinstance(item, Mapping)
        ]
        enriched["maker_orders"] = maker_orders
        maker_match = next(
            (
                item
                for item in maker_orders
                if str(item.get("order_id") or "") == str(order_id)
            ),
            None,
        )
        reported_role = str(enriched.get("trader_side") or "").strip().upper()
        taker_match = (
            bool(enriched.get("taker_order_id"))
            and str(enriched.get("taker_order_id")) == str(order_id)
        ) or (reported_role == "TAKER" and maker_match is None)
        if (maker_match is None) == (not taker_match):
            raise ClobResponseContractError(
                "CLOB v2 fee evidence cannot determine one liquidity role"
            )
        if maker_match is not None:
            fee_target = maker_match
            raw_size = maker_match.get("matched_amount")
            raw_price = maker_match.get("price")
            liquidity_role = "MAKER"
        else:
            fee_target = enriched
            raw_size = enriched.get("size")
            raw_price = enriched.get("price")
            liquidity_role = "TAKER"

        requested_size = self._submission_requested_size(
            pending, order_id=order_id
        )
        size = self._positive_fill_quantity(raw_size, requested_size)
        try:
            price = Decimal(str(raw_price))
        except Exception as error:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill price is not numeric"
            ) from error
        if not price.is_finite() or not 0 < price < 1:
            raise ClobResponseContractError(
                "CLOB v2 fee evidence fill price is outside (0, 1)"
            )

        schedule = self._clob_v2_fee_schedule(str(pending.get("token_id") or ""))
        fee = Decimal(0)
        if liquidity_role == "TAKER" or not schedule.taker_only:
            fee = (
                size
                * schedule.rate
                * (price * (Decimal(1) - price)) ** schedule.exponent
            )
        # The venue's documented platform-fee precision is five decimals.
        fee = fee.quantize(_FEE_QUANTUM_USDC, rounding=ROUND_HALF_UP)
        fee_micros = int(fee * _FIXED_6)

        raw_reported_fee = fee_target.get("fee_amount_usdc")
        if raw_reported_fee not in (None, ""):
            try:
                reported_fee = Decimal(str(raw_reported_fee)) / _FIXED_6
            except Exception as error:
                raise ClobResponseContractError(
                    "CLOB v2 trade fee amount is not fixed-6 numeric evidence"
                ) from error
            if abs(reported_fee - fee) > _FEE_QUANTUM_USDC:
                raise ClobResponseContractError(
                    "CLOB v2 trade fee conflicts with market-info fee parameters"
                )

        # CLOB V2 keeps a legacy fee_rate_bps field in some trade payloads even
        # though fees are operator-set at match time.  A reported zero is not a
        # zero-fee proof.  Persist the fee amount derived from exact fill and
        # authoritative market-info; the shared ledger decodes fixed-6 amounts.
        fee_target["fee_rate_bps"] = None
        fee_target["fee_amount_usdc"] = str(fee_micros)
        logger.info(
            "CLOB v2 fee evidence - condition=%s role=%s rate=%s exponent=%s "
            "fee_usdc=%s",
            schedule.condition_id[:14],
            liquidity_role,
            schedule.rate,
            schedule.exponent,
            fee,
        )
        return enriched

    def _ensure_initialized(self):
        """Lazy initialization of the CLOB client."""
        if self._initialized:
            return

        try:
            from py_clob_client_v2 import ClobClient

            if self.simulation_mode:
                self._client = ClobClient(
                    host=self.HOST,
                    chain_id=self.config.chain_id,
                )
                self._initialized = True
                logger.info("CLOB public-only simulation client initialized")
                return

            self._client = ClobClient(
                host=self.HOST,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address,
            )

            # Live A/B jobs reuse existing wallets with already-provisioned API keys.
            # `create_or_derive_api_key()` tries POST /auth/api-key first, which
            # emits a 400 error on every five-minute cycle before successfully
            # deriving the same key.  Derive directly so initialization is
            # read-only with respect to the wallet's API-key inventory.  If the
            # existing key cannot be derived, fail closed instead of silently
            # creating a replacement credential during a live cycle.
            api_creds = self._client.derive_api_key()
            self._client.set_api_creds(api_creds)
            self._initialized = True
            logger.info("CLOB client 초기화 완료 (v2)")

        except Exception as e:
            logger.error(f"CLOB client 초기화 실패: {e}")
            raise

    @property
    def client(self):
        """Get initialized CLOB client."""
        cycle_budget = getattr(self, "cycle_budget", None)
        if cycle_budget is not None:
            cycle_budget.ensure_can_start_request("CLOB client request")
        self._ensure_initialized()
        if cycle_budget is not None:
            cycle_budget.ensure_can_start_request("CLOB request dispatch")
        return self._client

    def _round_to_tick(
        self,
        price: float,
        tick_size: float = None,
        *,
        direction: str = "nearest",
    ) -> float:
        """Snap a book-derived limit to the venue tick without losing crossing.

        Polymarket requires prices to be in tick_size increments (default 0.01).
        This prevents INVALID_ORDER_MIN_TICK_SIZE errors.

        Args:
            price: Raw price value
            tick_size: Tick size to round to (default: 0.01)

        Returns:
            Price rounded to nearest tick
        """
        if tick_size is None:
            tick_size = self.DEFAULT_TICK_SIZE
        if (
            not math.isfinite(float(price))
            or not math.isfinite(float(tick_size))
            or not 0 < float(price) < 1
            or not 0 < float(tick_size) < 1
        ):
            raise ValueError("price and tick_size must be finite and in (0, 1)")
        rounding = {
            "nearest": ROUND_HALF_UP,
            "up": ROUND_CEILING,
            "down": ROUND_FLOOR,
        }.get(direction)
        if rounding is None:
            raise ValueError("direction must be nearest, up, or down")
        tick = Decimal(str(tick_size))
        ticks = (Decimal(str(price)) / tick).to_integral_value(rounding=rounding)
        snapped = ticks * tick
        lower = tick
        upper = Decimal("1") - tick
        return float(min(max(snapped, lower), upper))

    @rate_limit_handler(max_retries=3)
    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token.

        Args:
            token_id: Token ID to query

        Returns:
            Midpoint price as float (0.0-1.0)
        """
        normalized_token_id = str(token_id).strip()
        if (
            self._midpoint_snapshot is not None
            and normalized_token_id in self._midpoint_snapshot
        ):
            cached = self._midpoint_snapshot[normalized_token_id]
            if cached is None:
                raise ClobResponseUnavailableError(
                    "batch midpoint snapshot에 요청한 token의 사용 가능한 응답이 없습니다"
                )
            return cached

        try:
            result = self.client.get_midpoint(token_id)
            # API returns dict like {'mid': '0.875'}
            if isinstance(result, Mapping):
                price = result.get("mid", 0)
            else:
                price = getattr(result, "mid", result)
            return float(price) if price else 0.0
        except Exception as e:
            # 해결/비유동 시장은 orderbook이 없어 404가 흔하다. 정상 흐름이므로 debug로 낮춘다.
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"midpoint 조회 실패 - token: {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def get_market_resolution(self, condition_id: str) -> ClobResolutionProof:
        """Read public CLOB one-hot winner evidence for one condition."""
        result = self.client.get_market(str(condition_id))
        return _normalize_clob_resolution(str(condition_id), result)

    @staticmethod
    def _normalize_midpoint_value(value: Any) -> Optional[float]:
        """Normalize one SDK batch value without accepting sentinel prices."""
        if isinstance(value, Mapping):
            value = value.get("mid")
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or not 0 < price < 1:
            return None
        return price

    def get_midpoints(
        self,
        token_ids: Iterable[str],
    ) -> Dict[str, Optional[float]]:
        """Fetch midpoint prices in bounded public SDK batches.

        Every token from a successful chunk is present in the result.
        Missing/malformed values are represented as None so callers fail
        closed without an N+1 request burst. Tokens from a failed chunk are
        omitted so the scoped caller falls back to the existing single-token
        path instead of skipping every exit check during a batch outage.
        """
        unique_tokens = []
        seen = set()
        for raw_token in token_ids:
            if raw_token is None:
                continue
            token = str(raw_token).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            unique_tokens.append(token)

        results: Dict[str, Optional[float]] = {}
        chunk_count = 0
        failed_chunks = 0

        for offset in range(0, len(unique_tokens), self.MAX_MIDPOINT_BATCH_SIZE):
            chunk = unique_tokens[offset : offset + self.MAX_MIDPOINT_BATCH_SIZE]
            chunk_count += 1
            try:
                response = self.client.get_midpoints(
                    [BookParams(token_id=token) for token in chunk]
                )
                if not isinstance(response, Mapping):
                    raise ClobResponseContractError(
                        "CLOB batch midpoint response가 mapping이 아닙니다"
                    )
                for token in chunk:
                    results[token] = self._normalize_midpoint_value(
                        response.get(token)
                    )
            except Exception as exc:
                failed_chunks += 1
                logger.warning(
                    "midpoint batch chunk 조회 실패 - 단건 조회로 fallback "
                    "(token %d개, error=%s)",
                    len(chunk),
                    type(exc).__name__,
                )

        valid_count = sum(value is not None for value in results.values())
        fallback_count = len(unique_tokens) - len(results)
        logger.info(
            "midpoint 배치 조회 - 요청 %d, 성공 %d, 누락/오류 %d, "
            "fallback %d, chunk %d, 실패 chunk %d",
            len(unique_tokens),
            valid_count,
            len(results) - valid_count,
            fallback_count,
            chunk_count,
            failed_chunks,
        )
        return results

    @contextmanager
    def midpoint_snapshot(
        self,
        token_ids: Iterable[str],
    ) -> Iterator[Dict[str, Optional[float]]]:
        """Scope a batch snapshot while restoring any enclosing snapshot."""
        previous_snapshot = self._midpoint_snapshot
        snapshot = self.get_midpoints(token_ids)
        self._midpoint_snapshot = snapshot
        try:
            yield snapshot
        finally:
            self._midpoint_snapshot = previous_snapshot

    def get_buy_book_walks(
        self,
        token_ids: Iterable[str],
        *,
        notional_usdc: float,
        batch_size: int = 250,
    ) -> Dict[str, BuyBookWalk]:
        """Fetch full books in batches and return only complete exact walks."""
        unique = list(
            dict.fromkeys(
                str(value).strip()
                for value in token_ids
                if value is not None and str(value).strip()
            )
        )
        results: Dict[str, BuyBookWalk] = {}
        self._book_evidence_by_token = {}
        failed = 0
        for offset in range(0, len(unique), batch_size):
            chunk = unique[offset : offset + batch_size]
            try:
                response = self.client.get_order_books(
                    [BookParams(token_id=token) for token in chunk]
                )
                if not isinstance(response, (list, tuple)):
                    raise ClobResponseContractError(
                        "CLOB batch order-book response must be a sequence"
                    )
                for book in response:
                    token = str(_book_field(book, "asset_id") or "").strip()
                    if token not in chunk or token in results:
                        continue
                    try:
                        self._book_evidence_by_token[token] = (
                            _canonical_book_evidence(book, token)
                        )
                        results[token] = _walk_buy_book(
                            book, token, notional_usdc
                        )
                    except (
                        ClobResponseContractError,
                        ClobResponseUnavailableError,
                    ):
                        failed += 1
            except Exception as error:
                failed += len(chunk)
                logger.warning(
                    "CLOB full-book batch failed closed - tokens=%s error=%s",
                    len(chunk),
                    type(error).__name__,
                )
        logger.info(
            "CLOB exact $%.2f ask walks - requested=%s complete=%s failed_or_shallow=%s",
            notional_usdc,
            len(unique),
            len(results),
            failed + max(0, len(unique) - len(results) - failed),
        )
        return results

    def get_cached_book_evidence(self, token_id: str) -> Optional[str]:
        """Return the raw normalized levels from the latest batch read."""
        return self._book_evidence_by_token.get(str(token_id))

    def get_buy_book_walk(
        self, token_id: str, *, notional_usdc: float
    ) -> BuyBookWalk:
        result = self.client.get_order_book(str(token_id))
        return _walk_buy_book(result, str(token_id), notional_usdc)

    def get_sell_book_walk(self, token_id: str, *, shares: float) -> SellBookWalk:
        """Return a complete executable walk for every share in one holding."""
        result = self.client.get_order_book(str(token_id))
        return _walk_sell_book(result, str(token_id), shares)

    def get_sell_book_walks(
        self,
        token_shares: Mapping[str, float],
        *,
        batch_size: int = 250,
    ) -> Dict[str, Optional[SellBookWalk]]:
        """Fetch holding books once per cycle instead of one request per trade.

        A key mapped to ``None`` means the batch answered but that exact book
        could not prove full executable depth.  A missing key means the whole
        chunk failed and lets the caller use its existing single-read fallback.
        """
        normalized: Dict[str, float] = {}
        for raw_token, raw_shares in token_shares.items():
            token = str(raw_token or "").strip()
            try:
                shares = float(raw_shares)
            except (TypeError, ValueError):
                continue
            if token and math.isfinite(shares) and shares > 0:
                normalized[token] = shares

        results: Dict[str, Optional[SellBookWalk]] = {}
        tokens = list(normalized)
        failed_chunks = 0
        for offset in range(0, len(tokens), batch_size):
            chunk = tokens[offset : offset + batch_size]
            try:
                response = self.client.get_order_books(
                    [BookParams(token_id=token) for token in chunk]
                )
                if not isinstance(response, (list, tuple)):
                    raise ClobResponseContractError(
                        "CLOB batch order-book response must be a sequence"
                    )
                seen: set[str] = set()
                for book in response:
                    token = str(_book_field(book, "asset_id") or "").strip()
                    if token not in normalized or token in seen:
                        continue
                    seen.add(token)
                    try:
                        results[token] = _walk_sell_book(
                            book, token, normalized[token]
                        )
                    except (
                        ClobResponseContractError,
                        ClobResponseUnavailableError,
                    ):
                        results[token] = None
                for token in chunk:
                    results.setdefault(token, None)
            except Exception as error:
                failed_chunks += 1
                logger.warning(
                    "CLOB holding-book batch failed; scoped single fallback "
                    "enabled - tokens=%s error=%s",
                    len(chunk),
                    type(error).__name__,
                )
        logger.info(
            "CLOB holding-book batch - requested=%s complete=%s unavailable=%s "
            "failed_chunks=%s",
            len(tokens),
            sum(value is not None for value in results.values()),
            sum(value is None for value in results.values()),
            failed_chunks,
        )
        return results

    @rate_limit_handler(max_retries=3)
    def get_best_bid(self, token_id: str) -> float:
        """Get best bid price.

        Args:
            token_id: Token ID

        Returns:
            Best bid price
        """
        try:
            result = self.client.get_price(token_id, side="BUY")
            price = result.get("price", 0) if isinstance(result, Mapping) else getattr(
                result, "price", result
            )
            return float(price) if price else 0.0
        except Exception as e:
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"best bid 조회 실패 - token: {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def get_best_ask(self, token_id: str) -> float:
        """Get best ask price.

        Args:
            token_id: Token ID

        Returns:
            Best ask price
        """
        try:
            result = self.client.get_price(token_id, side="SELL")
            price = result.get("price", 0) if isinstance(result, Mapping) else getattr(
                result, "price", result
            )
            return float(price) if price else 0.0
        except Exception as e:
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"best ask 조회 실패 - token: {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def place_market_buy(
        self,
        token_id: str,
        amount_usdc: float,
    ) -> Dict[str, Any]:
        """Place a market buy order.

        Args:
            token_id: Token to buy
            amount_usdc: Amount in USDC to spend

        Returns:
            Order result dictionary
        """
        if self.simulation_mode:
            logger.info(f"[SIM] Market BUY - token: {token_id}, 금액: ${amount_usdc}")
            return {
                "success": True,
                "orderID": f"SIM_BUY_{token_id[:8]}",
                "simulated": True,
            }

        try:
            from py_clob_client_v2 import MarketOrderArgs, OrderType

            # v2: side는 MarketOrderArgs 안의 필드로 들어감 (v1 에서는 별도 인자였음)
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usdc,
                side="BUY",
            )
            response = self.client.create_and_post_market_order(
                order_args,
                order_type=OrderType.FOK,
            )
            response = normalize_clob_response(
                response, response_type="submission"
            )
            logger.info(f"Market BUY 주문 완료: {response}")
            return response

        except Exception as e:
            logger.error(f"Market BUY 주문 실패: {e}")
            return {"success": False, "error": str(e)}

    @rate_limit_handler(max_retries=3)
    def place_fok_buy(
        self,
        token_id: str,
        amount_usdc: float,
        limit_price: float,
        max_limit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Submit an exact-USDC FOK BUY with an explicit fresh-book limit.

        CLOB v2 requires the BUY maker amount (USDC) at two-decimal precision
        and the taker amount (shares) at no more than four decimals.  A limit
        ``OrderArgs`` envelope derives maker USDC from shares and can therefore
        produce a four-decimal maker amount.  ``MarketOrderArgs`` represents the
        intended fixed USDC amount directly while retaining an explicit limit
        price.
        """
        amount = Decimal(str(amount_usdc))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount_usdc must be finite and positive")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("FOK BUY maker amount must have at most two decimals")
        if not math.isfinite(limit_price) or not 0 < limit_price < 1:
            raise ValueError("limit_price must be finite and inside (0, 1)")
        maximum = limit_price if max_limit_price is None else float(max_limit_price)
        if not math.isfinite(maximum) or not 0 < maximum < 1:
            raise ValueError("max_limit_price must be finite and inside (0, 1)")
        if maximum + 1e-12 < limit_price:
            raise ValueError("max_limit_price cannot be below limit_price")

        tick_size = self.DEFAULT_TICK_SIZE
        if not self.simulation_mode:
            tick_size = float(self.client.get_tick_size(str(token_id)))
        rounded_price = self._round_to_tick(
            limit_price,
            tick_size,
            direction="up",
        )
        rounded_maximum = self._round_to_tick(
            maximum,
            tick_size,
            direction="down",
        )
        if rounded_price > rounded_maximum + 1e-12:
            raise PreSubmissionContractError(
                "fresh book limit exceeds the preregistered entry ceiling"
            )

        if self.simulation_mode:
            requested_size = float(amount / Decimal(str(rounded_price)))
            result = {
                "success": True,
                "orderID": f"SIM_BUY_{token_id[:8]}",
                "simulated": True,
                "price": rounded_price,
                "maker_amount_usdc": float(amount),
                "requested_size": requested_size,
            }
            self._record_limit_submission(
                token_id, rounded_price, requested_size, "BUY", result
            )
            return result

        try:
            from py_clob_client_v2 import MarketOrderArgs, OrderType
            from py_clob_client_v2.clob_types import PartialCreateOrderOptions

            if self.execution_ledger is not None:
                self.execution_ledger.assert_submission_allowed(
                    token_id=token_id,
                    side="BUY",
                )
                # Dynamic fees are operator-set at CLOB V2 match time.  Prove
                # that Gamma's persisted schedule and the current CLOB market
                # info agree before an irreversible live order is submitted.
                try:
                    self._clob_v2_fee_schedule(str(token_id))
                except ClobResponseContractError as error:
                    raise PreSubmissionContractError(str(error)) from error

            # Sign locally before persisting an intent.  Some valid fine-tick
            # prices cause the SDK to emit five-decimal taker shares although
            # the venue accepts only four.  Walk upward on the *venue tick grid*
            # only as far as the frozen arm ceiling and select the first signed
            # envelope that preserves exact $5 maker amount and venue precision.
            # This avoids losing a valid opportunity at (for example) 0.965,
            # while never widening outside the preregistered entry band.
            expected_maker_micros = int(amount * Decimal(1_000_000))
            tick_decimal = Decimal(str(tick_size))
            price_decimal = Decimal(str(rounded_price))
            maximum_decimal = Decimal(str(rounded_maximum))
            signed_order = None
            maker_micros = 0
            taker_micros = 0
            selected_price = None
            while price_decimal <= maximum_decimal:
                candidate_price = float(price_decimal)
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=float(amount),
                    side="BUY",
                    price=candidate_price,
                    order_type=OrderType.FOK,
                )
                cent_aligned = (
                    price_decimal == price_decimal.quantize(Decimal("0.01"))
                )
                signing_options = None
                if tick_decimal < Decimal("0.01") and cent_aligned:
                    signing_options = PartialCreateOrderOptions(tick_size="0.01")
                if signing_options is None:
                    candidate_order = self.client.create_market_order(order_args)
                else:
                    candidate_order = self.client.create_market_order(
                        order_args,
                        options=signing_options,
                    )
                try:
                    candidate_maker = int(str(candidate_order.makerAmount))
                    candidate_taker = int(str(candidate_order.takerAmount))
                except (AttributeError, TypeError, ValueError) as error:
                    raise PreSubmissionContractError(
                        "signed FOK BUY amount evidence is unavailable"
                    ) from error
                if candidate_maker != expected_maker_micros or candidate_taker <= 0:
                    raise PreSubmissionContractError(
                        "signed FOK BUY does not preserve exact maker USDC"
                    )
                if candidate_taker % _MARKET_BUY_TAKER_QUANTUM_MICROS == 0:
                    signed_order = candidate_order
                    maker_micros = candidate_maker
                    taker_micros = candidate_taker
                    selected_price = candidate_price
                    break
                price_decimal += tick_decimal
            if signed_order is None or selected_price is None:
                raise PreSubmissionContractError(
                    "no four-decimal exact-$5 FOK BUY envelope inside entry ceiling"
                )
            if selected_price > rounded_price + 1e-12:
                logger.warning(
                    "FOK BUY signer-compatible limit widened within frozen band - "
                    "token=%s book_limit=%.4f signed_limit=%.4f ceiling=%.4f",
                    str(token_id)[:16],
                    rounded_price,
                    selected_price,
                    rounded_maximum,
                )
            rounded_price = selected_price
            requested_size = taker_micros / 1_000_000

            def submit_order() -> Dict[str, Any]:
                return self.client.post_order(signed_order, OrderType.FOK)

            if self.execution_ledger is None:
                response = normalize_clob_response(
                    submit_order(), response_type="submission"
                )
            else:
                response = self.execution_ledger.submit_and_record(
                    token_id=token_id,
                    side="BUY",
                    requested_price=rounded_price,
                    requested_size=requested_size,
                    submit=submit_order,
                    cancel=lambda order_id: self.client.cancel_orders([order_id]),
                    signed_making_amount=maker_micros,
                    signed_taking_amount=taker_micros,
                )
            result = dict(response)
            result.update(
                {
                    "price": rounded_price,
                    "maker_amount_usdc": maker_micros / 1_000_000,
                    "requested_size": requested_size,
                }
            )
            logger.info(
                "Exact-USDC FOK BUY 주문 완료 @ %.4f maker=$%.2f shares=%.4f",
                rounded_price,
                maker_micros / 1_000_000,
                requested_size,
            )
            return result
        except SubmissionOutcomeQuarantinedError as error:
            logger.warning(
                "CLOB BUY 결과가 불확실해 token을 격리하고 cycle을 계속합니다"
            )
            return {
                "success": False,
                "error": str(error),
                "submission_outcome_unknown": True,
                "quarantined": True,
            }
        except SubmissionEvidenceError:
            logger.critical("BUY execution ledger 정합성 유지 실패", exc_info=True)
            raise
        except ClobResponseContractError:
            logger.critical(
                "BUY contract 검증 실패; Jenkins cycle을 실패 처리합니다",
                exc_info=True,
            )
            raise
        except Exception as error:
            logger.error("Exact-USDC FOK BUY 주문 실패: %s", error)
            return {"success": False, "error": str(error)}

    @rate_limit_handler(max_retries=3)
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> Dict[str, Any]:
        """Place a limit order.

        Args:
            token_id: Token ID
            price: Limit price (0.0-1.0)
            size: Number of shares
            side: "BUY" or "SELL"

        Returns:
            Order result dictionary
        """
        normalized_order_type = str(order_type).strip().upper()
        if normalized_order_type not in {"GTC", "FOK"}:
            raise ValueError("order_type must be GTC or FOK")
        order_side = "BUY" if side.upper() == "BUY" else "SELL"

        # FOK orders must remain marketable after snapping. The venue-specific
        # tick is fetched before intent persistence; a read failure therefore
        # cannot create an uncertain POST.
        tick_size = self.DEFAULT_TICK_SIZE
        if not self.simulation_mode:
            tick_size = float(self.client.get_tick_size(str(token_id)))
        if normalized_order_type == "FOK" and order_side == "BUY":
            direction = "up"
        elif normalized_order_type == "FOK" and order_side == "SELL":
            direction = "down"
        else:
            direction = "nearest"
        rounded_price = self._round_to_tick(
            price,
            tick_size,
            direction=direction,
        )

        if self.simulation_mode:
            logger.info(f"[SIM] Limit {side} - {size:.2f}주 @ {rounded_price:.2f}, token: {token_id}")
            result = {
                "success": True,
                "orderID": f"SIM_{side}_{token_id[:8]}",
                "simulated": True,
                "price": rounded_price,
                "requested_size": float(size),
            }
            self._record_limit_submission(token_id, rounded_price, size, side, result)
            return result

        try:
            from py_clob_client_v2 import OrderArgs, OrderType

            order_args = OrderArgs(
                token_id=token_id,
                price=rounded_price,
                size=size,
                side=order_side,
            )

            if self.execution_ledger is not None:
                self.execution_ledger.assert_submission_allowed(
                    token_id=token_id,
                    side=order_side,
                )
                # STOP SELLs are taker orders too.  Prove the exact token's
                # Gamma/CLOB dynamic-fee identity before an irreversible POST,
                # just as the FOK BUY path does.
                self._clob_v2_fee_schedule(str(token_id))

            # create_order performs signing and read-only preflight such as
            # tick-size/neg-risk lookups. Finish it before recording an intent
            # so a GET timeout cannot be mistaken for an uncertain POST.
            signed_order = self.client.create_order(order_args)
            ledger_requested_size = float(size)
            signed_making_amount = None
            signed_taking_amount = None
            if self.execution_ledger is not None:
                try:
                    signed_making_amount = int(str(signed_order.makerAmount))
                    signed_taking_amount = int(str(signed_order.takerAmount))
                except (AttributeError, TypeError, ValueError) as error:
                    raise ClobResponseContractError(
                        "signed limit order amount evidence is unavailable"
                    ) from error
                if signed_making_amount <= 0 or signed_taking_amount <= 0:
                    raise ClobResponseContractError(
                        "signed limit order amounts must be positive"
                    )
                signed_share_micros = (
                    signed_making_amount
                    if order_side == "SELL"
                    else signed_taking_amount
                )
                ledger_requested_size = signed_share_micros / 1_000_000
                residual = float(size) - ledger_requested_size
                if (
                    ledger_requested_size <= 0
                    or residual < -_MARKET_BUY_QUANTITY_TOLERANCE
                    or residual >= 0.01 + _MARKET_BUY_QUANTITY_TOLERANCE
                ):
                    raise ClobResponseContractError(
                        "signed limit order share quantity drift exceeds one SDK quantum"
                    )

            venue_order_type = (
                OrderType.FOK if normalized_order_type == "FOK" else OrderType.GTC
            )

            def submit_order() -> Dict[str, Any]:
                return self.client.post_order(signed_order, venue_order_type)

            if self.execution_ledger is None:
                response = normalize_clob_response(
                    submit_order(), response_type="submission"
                )
            else:
                response = self.execution_ledger.submit_and_record(
                    token_id=token_id,
                    side=order_side,
                    requested_price=rounded_price,
                    requested_size=ledger_requested_size,
                    submit=submit_order,
                    cancel=lambda order_id: self.client.cancel_orders([order_id]),
                    signed_making_amount=signed_making_amount,
                    signed_taking_amount=signed_taking_amount,
                )

            logger.info(
                "Limit %s %s 주문 완료 @ %.2f: %s",
                side,
                normalized_order_type,
                rounded_price,
                response,
            )
            result = dict(response)
            result.update(
                {
                    "price": rounded_price,
                    "requested_size": ledger_requested_size,
                }
            )
            return result

        except SubmissionOutcomeQuarantinedError as error:
            logger.warning(
                "CLOB 주문 결과가 불확실해 동일 token/side를 격리하고 "
                "trading cycle을 계속합니다 - side=%s",
                error.side,
            )
            return {
                "success": False,
                "error": str(error),
                "submission_outcome_unknown": True,
                "quarantined": True,
            }
        except SubmissionEvidenceError:
            logger.critical("접수 주문과 execution ledger 정합성 유지 실패", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Limit 주문 실패: {e}")
            return {"success": False, "error": str(e)}

    def _record_limit_submission(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        result: Dict[str, Any],
    ) -> None:
        if self.execution_ledger is None:
            return
        self.execution_ledger.record_submission(
            token_id=token_id,
            side=side,
            requested_price=price,
            requested_size=size,
            result=result,
            simulation=self.simulation_mode,
        )

    def reconcile_order_ledger(self) -> Dict[str, int]:
        """Poll persisted orders and store actual order/trade lifecycle evidence."""
        stats = {
            "checked": 0,
            "fills": 0,
            "completed": 0,
            "legacy_unavailable": 0,
            "errors": 0,
            "buy_errors": 0,
            "sell_errors": 0,
            "unknown_side_errors": 0,
            "unresolved_buy_outcomes": 0,
            "unresolved_sell_outcomes": 0,
            "reconciliation_buy_gaps": 0,
            "reconciliation_sell_gaps": 0,
            # Uncertain POST outcomes require exact venue/operator proof.  An
            # absent open order cannot distinguish no order from a filled FOK.
            "intent_autoresolved": 0,
        }
        if self.simulation_mode or self.execution_ledger is None:
            return stats

        from py_clob_client_v2 import OpenOrderParams, TradeParams

        pre_migration_index = None
        token_trade_catalog_cache = {}
        for pending in self.execution_ledger.pending_submissions():
            stats["checked"] += 1
            submission_id = pending["submission_id"]
            order_id = pending["order_id"]
            phase = "fetch_order"
            response_shape = "not_observed"
            trade_ids = None
            recovered_from_token_trade_catalog = (
                pending.get("reconciliation_proof")
                == "AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS"
            )
            try:
                raw_detail = self.client.get_order(order_id)
                response_shape = safe_clob_response_shape(raw_detail)
                phase = "normalize_order"
                try:
                    detail = normalize_clob_response(
                        raw_detail, response_type="order"
                    )
                except ClobResponseUnavailableError as unavailable_error:
                    phase = "fetch_current_order_catalog"
                    raw_current_orders = self.client.get_open_orders(
                        OpenOrderParams(id=str(order_id)), only_first_page=True
                    )
                    response_shape = safe_clob_response_shape(raw_current_orders)
                    phase = "normalize_current_order_catalog"
                    current_orders = normalize_clob_response_list(
                        raw_current_orders, response_type="order"
                    )
                    phase = "match_current_order_catalog"
                    current_matches = [
                        order
                        for order in current_orders
                        if str(order.get("id") or "") == str(order_id)
                    ]
                    if len(current_matches) > 1:
                        raise ClobResponseContractError(
                            "current order catalog에 exact order ID가 중복되었습니다"
                        )
                    if current_matches:
                        detail = current_matches[0]
                        response_shape = safe_clob_response_shape(detail)
                    else:
                        if pre_migration_index is None:
                            phase = "fetch_pre_migration_orders"
                            raw_legacy_orders = self.client.get_pre_migration_orders()
                            response_shape = safe_clob_response_shape(raw_legacy_orders)
                            phase = "normalize_pre_migration_orders"
                            legacy_orders = normalize_clob_response_list(
                                raw_legacy_orders, response_type="order"
                            )
                            pre_migration_index = {}
                            for legacy_order in legacy_orders:
                                legacy_order_id = str(legacy_order.get("id") or "")
                                if legacy_order_id:
                                    pre_migration_index.setdefault(
                                        legacy_order_id, []
                                    ).append(legacy_order)
                        phase = "match_pre_migration_order"
                        matches = pre_migration_index.get(str(order_id), [])
                        if len(matches) > 1:
                            raise ClobResponseContractError(
                                "pre-migration catalog에 exact order ID가 중복되었습니다"
                            )
                        if matches:
                            detail = matches[0]
                            response_shape = safe_clob_response_shape(detail)
                        elif pending["response_status"] == "LEGACY_ASSUMED":
                            phase = "close_legacy_evidence_gap"
                            self.execution_ledger.mark_legacy_unavailable(submission_id)
                            stats["legacy_unavailable"] += 1
                            logger.warning(
                                "legacy CLOB evidence gap 종결 - phase=%s "
                                "response_shape=%s",
                                phase,
                                response_shape,
                            )
                            continue
                        else:
                            trade_ids = _recorded_trade_ids(
                                pending["associated_trade_ids_json"]
                            )
                            if not trade_ids:
                                token_id = str(pending["token_id"] or "").strip()
                                if not token_id:
                                    raise ClobResponseContractError(
                                        "pending submission token ID가 비어 있습니다"
                                    )
                                cached = token_id in token_trade_catalog_cache
                                if not cached:
                                    phase = "fetch_token_trade_catalog"
                                    raw_trade_catalog = self.client.get_trades(
                                        TradeParams(asset_id=token_id),
                                        only_first_page=False,
                                    )
                                    response_shape = safe_clob_response_shape(
                                        raw_trade_catalog
                                    )
                                    phase = "normalize_token_trade_catalog"
                                    trade_catalog = normalize_clob_response_list(
                                        raw_trade_catalog, response_type="trade"
                                    )
                                    token_trade_catalog_cache[token_id] = (
                                        trade_catalog,
                                        response_shape,
                                    )
                                else:
                                    trade_catalog, response_shape = (
                                        token_trade_catalog_cache[token_id]
                                    )
                                phase = "match_token_trade_catalog"
                                trade_ids = _exact_order_trade_ids(
                                    trade_catalog, str(order_id)
                                )
                                logger.warning(
                                    "authenticated token trade catalog exact-order "
                                    "scan - trades=%s matches=%s cached=%s",
                                    len(trade_catalog),
                                    len(trade_ids),
                                    cached,
                                )
                                if not trade_ids:
                                    phase = "match_authoritative_order_catalogs"
                                    raise unavailable_error
                                recovered_from_token_trade_catalog = True
                                logger.warning(
                                    "order catalog에서 사라진 주문을 authenticated "
                                    "token trade catalog의 exact order ID로 "
                                    "복구합니다 - count=%s",
                                    len(trade_ids),
                                )
                            else:
                                logger.warning(
                                    "order catalog에서 사라진 주문의 기존 exact trade "
                                    "evidence를 재조회합니다 - count=%s",
                                    len(trade_ids),
                                )
                if trade_ids is None:
                    phase = "validate_order_identity"
                    returned_order_id = str(detail.get("id") or "")
                    if returned_order_id and returned_order_id != str(order_id):
                        raise ClobResponseContractError(
                            "CLOB order response ID가 요청 order ID와 다릅니다"
                        )
                    phase = "persist_order_status"
                    trade_ids = self.execution_ledger.record_order_status(
                        submission_id,
                        detail,
                        quantity_tolerance=_MARKET_BUY_QUANTITY_TOLERANCE,
                    )
                for trade_id in trade_ids:
                    phase = "fetch_trades"
                    raw_trades = self.client.get_trades(
                        TradeParams(id=trade_id), only_first_page=True
                    )
                    response_shape = safe_clob_response_shape(raw_trades)
                    phase = "normalize_trades"
                    trades = normalize_clob_response_list(
                        raw_trades, response_type="trade"
                    )
                    phase = "validate_trades"
                    returned_trade_ids = [str(trade.get("id") or "") for trade in trades]
                    if not returned_trade_ids:
                        raise ClobResponseContractError(
                            "associated trade ID 조회 결과가 비어 있습니다"
                        )
                    if any(
                        returned_id != str(trade_id)
                        for returned_id in returned_trade_ids
                    ):
                        raise ClobResponseContractError(
                            "associated trade ID 조회 결과가 요청 ID와 다릅니다"
                        )
                    for trade in trades:
                        if (
                            recovered_from_token_trade_catalog
                            and not _trade_references_exact_order(
                                trade, str(order_id)
                            )
                        ):
                            raise ClobResponseContractError(
                                "exact trade 재조회 결과가 pending order ID를 "
                                "참조하지 않습니다"
                            )
                        phase = "resolve_dynamic_fee_evidence"
                        trade = self._attach_clob_v2_fee_evidence(
                            trade,
                            pending=pending,
                            order_id=str(order_id),
                        )
                        phase = "persist_fill"
                        self.execution_ledger.record_fill(
                            submission_id, order_id, trade
                        )
                        stats["fills"] += 1
                if recovered_from_token_trade_catalog:
                    phase = "persist_recovered_trade_associations"
                    self.execution_ledger.record_recovered_trade_associations(
                        submission_id, order_id, trade_ids
                    )
                phase = "finalize_reconciliation"
                reconciliation_finished = (
                    self.execution_ledger.finish_reconciliation(submission_id)
                )
                if reconciliation_finished:
                    stats["completed"] += 1
                elif recovered_from_token_trade_catalog:
                    raise ClobResponseContractError(
                        "authenticated token trade evidence가 terminal full-fill "
                        "수량을 증명하지 못했습니다"
                    )
            except Exception as error:
                stats["errors"] += 1
                pending_side = str(pending.get("side") or "").strip().upper()
                if pending_side == "BUY":
                    stats["buy_errors"] += 1
                elif pending_side == "SELL":
                    stats["sell_errors"] += 1
                else:
                    stats["unknown_side_errors"] += 1
                phase_error = ClobReconciliationPhaseError(
                    phase, error, response_shape
                )
                self.execution_ledger.record_reconciliation_error(
                    submission_id, phase_error
                )
                logger.warning(
                    "주문 원장 대사 실패 - phase=%s error=%s response_shape=%s",
                    phase,
                    type(error).__name__,
                    response_shape,
                )

        if stats["checked"]:
            logger.info(
                f"주문 원장 대사 - 확인 {stats['checked']}, fill {stats['fills']}, "
                f"완료 {stats['completed']}, legacy gap "
                f"{stats['legacy_unavailable']}, 오류 {stats['errors']}"
            )
        stats["unresolved_buy_outcomes"] = (
            self.execution_ledger.unresolved_submission_count(side="BUY")
        )
        stats["unresolved_sell_outcomes"] = (
            self.execution_ledger.unresolved_submission_count(side="SELL")
        )
        stats["reconciliation_buy_gaps"] = (
            self.execution_ledger.reconciliation_gap_count(side="BUY")
        )
        stats["reconciliation_sell_gaps"] = (
            self.execution_ledger.reconciliation_gap_count(side="SELL")
        )
        if any(
            stats[key]
            for key in (
                "unresolved_buy_outcomes",
                "unresolved_sell_outcomes",
                "reconciliation_buy_gaps",
                "reconciliation_sell_gaps",
            )
        ):
            logger.warning(
                "order health degraded - unresolved_buy=%s unresolved_sell=%s "
                "buy_gaps=%s sell_gaps=%s",
                stats["unresolved_buy_outcomes"],
                stats["unresolved_sell_outcomes"],
                stats["reconciliation_buy_gaps"],
                stats["reconciliation_sell_gaps"],
            )
        return stats

    @rate_limit_handler(max_retries=3)
    def get_open_orders(self) -> list:
        """Get all open orders.

        Returns:
            List of open orders
        """
        try:
            # v2: get_orders() 제거됨 → get_open_orders() 사용
            return self.client.get_open_orders()
        except Exception as e:
            logger.error(f"미체결 주문 조회 실패: {e}")
            return []

    @rate_limit_handler(max_retries=3)
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order.

        Args:
            order_id: Order ID (hash) to cancel

        Returns:
            Cancellation result
        """
        if self.simulation_mode:
            logger.info(f"[SIM] 주문 취소 - order: {order_id}")
            return {"success": True, "simulated": True}

        try:
            # canceled 응답만으로는 부분 체결 여부를 알 수 없으므로,
            # 직후 authoritative order detail도 검증한다. not_canceled여도
            # 이미 취소된 zero-fill 주문이면 후속 detail로 idempotent 성공 가능하다.
            result = normalize_clob_response(
                self.client.cancel_orders([str(order_id)]),
                response_type="cancellation",
            )
            detail = normalize_clob_response(
                self.client.get_order(str(order_id)), response_type="order"
            )
            returned_order_id = str(detail.get("id") or "")
            status = _normalize_order_status(detail.get("status"))
            size_matched = detail.get("size_matched")
            if (
                returned_order_id != str(order_id)
                or status not in _PROVABLY_UNFILLED_ORDER_STATUSES
                or not _is_explicit_zero(size_matched)
            ):
                raise SubmissionEvidenceError(
                    "CLOB order detail이 exact zero-fill cancellation을 증명하지 못했습니다"
                )
            logger.info(f"주문 취소 완료: {order_id}")
            return {
                **result,
                "verified_order_status": status,
                "verified_size_matched": float(size_matched),
            }
        except SubmissionEvidenceError:
            logger.warning(
                "주문 취소 후 zero-fill 증거 확인 실패 - order=%s",
                order_id,
            )
            raise
        except Exception as error:
            logger.error("주문 취소 실패 - error=%s", type(error).__name__)
            raise SubmissionEvidenceError(
                "CLOB 주문 취소 결과를 증명할 수 없습니다"
            ) from error

    @rate_limit_handler(max_retries=3)
    def cancel_order_for_reconciliation(
        self, order_id: str, *, minimum_age_minutes: float
    ) -> Dict[str, Any]:
        """Cancel an expired FOK BUY/SELL while preserving any actual fill."""
        return self._cancel_with_terminal_evidence(
            order_id, minimum_age_minutes=minimum_age_minutes
        )

    def _cancel_with_terminal_evidence(
        self, order_id: str, *, minimum_age_minutes: float
    ) -> Dict[str, Any]:
        """Return exact terminal identity, status, and matched-size evidence."""
        if self.simulation_mode:
            logger.info("[SIM] 주문 취소 - order: %s", order_id)
            return {
                "success": True,
                "simulated": True,
                "verified_order_status": "CANCELED",
                "verified_size_matched": 0.0,
            }
        try:
            result = normalize_clob_response(
                self.client.cancel_orders([str(order_id)]),
                response_type="cancellation",
            )
            try:
                detail = normalize_clob_response(
                    self.client.get_order(str(order_id)), response_type="order"
                )
            except ClobResponseUnavailableError:
                if self.execution_ledger is None:
                    raise
                pending = [
                    item
                    for item in self.execution_ledger.pending_submissions()
                    if str(item.get("order_id") or "") == str(order_id)
                ]
                if len(pending) != 1:
                    raise SubmissionEvidenceError(
                        "stale FOK order와 연결된 pending submission이 "
                        "정확히 1건이 아닙니다"
                    )
                token_id = str(pending[0].get("token_id") or "").strip()
                if not token_id:
                    raise SubmissionEvidenceError(
                        "stale FOK order의 token ID가 비어 있습니다"
                    )
                from py_clob_client_v2 import TradeParams

                raw_trades = self.client.get_trades(
                    TradeParams(asset_id=token_id), only_first_page=False
                )
                trades = normalize_clob_response_list(
                    raw_trades, response_type="trade"
                )
                proof = self.execution_ledger.record_delayed_fok_zero_fill(
                    order_id=str(order_id),
                    token_id=token_id,
                    cancellation=result,
                    authenticated_trades=trades,
                    minimum_age_minutes=minimum_age_minutes,
                )
                logger.info(
                    "stale DELAYED FOK zero-fill 종결: order=%s proof=%s "
                    "authenticated_token_trades=%s",
                    order_id,
                    proof,
                    len(trades),
                )
                return {
                    **result,
                    "verified_order_status": "CANCELED",
                    "verified_size_matched": 0.0,
                    "reconciliation_proof": proof,
                }
            returned_order_id = str(detail.get("id") or "")
            status = _normalize_order_status(detail.get("status"))
            try:
                size_matched = float(detail.get("size_matched"))
            except (TypeError, ValueError) as error:
                raise SubmissionEvidenceError(
                    "CLOB terminal order의 size_matched가 숫자가 아닙니다"
                ) from error
            if (
                returned_order_id != str(order_id)
                or status not in _TERMINAL_ORDER_STATUSES
                or not math.isfinite(size_matched)
                or size_matched < 0
            ):
                raise SubmissionEvidenceError(
                    "CLOB order detail이 exact terminal cancellation을 증명하지 못했습니다"
                )
            if self.execution_ledger is not None and size_matched == 0:
                pending = [
                    item
                    for item in self.execution_ledger.pending_submissions()
                    if str(item.get("order_id") or "") == str(order_id)
                ]
                if len(pending) != 1:
                    raise SubmissionEvidenceError(
                        "terminal zero-fill FOK와 연결된 pending submission이 "
                        "정확히 1건이 아닙니다"
                    )
                submission_id = str(pending[0].get("submission_id") or "").strip()
                if not submission_id:
                    raise SubmissionEvidenceError(
                        "terminal zero-fill FOK의 submission ID가 비어 있습니다"
                    )
                self.execution_ledger.record_order_status(submission_id, detail)
                if not self.execution_ledger.finish_reconciliation(submission_id):
                    raise SubmissionEvidenceError(
                        "terminal zero-fill FOK 원장이 종결되지 않았습니다"
                    )
            logger.info(
                "주문 취소/종결 확인: order=%s status=%s matched=%.6f",
                order_id,
                status,
                size_matched,
            )
            return {
                **result,
                "verified_order_status": status,
                "verified_size_matched": size_matched,
            }
        except SubmissionEvidenceError:
            raise
        except Exception as error:
            logger.error("주문 취소 실패 - error=%s", type(error).__name__)
            raise SubmissionEvidenceError(
                "CLOB 주문 취소 결과를 증명할 수 없습니다"
            ) from error

    def test_connection(self) -> bool:
        """Test API connection and credentials.

        Returns:
            True if connection successful
        """
        try:
            self._ensure_initialized()
            # v2: 연결 확인용으로 get_open_orders() 사용
            self.client.get_open_orders()
            return True
        except Exception as e:
            logger.error(f"연결 테스트 실패: {e}")
            return False

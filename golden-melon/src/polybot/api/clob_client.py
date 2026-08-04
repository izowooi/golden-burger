"""CLOB API client wrapper for order execution.

Polymarket이 2026년 4월 CLOB v2로 마이그레이션함에 따라 본 모듈은
`py-clob-client-v2` (import: `py_clob_client_v2`) 를 사용한다.
구버전 `py-clob-client` 는 `order_version_mismatch` 오류로 더 이상 동작하지 않는다.
"""
import json
import os
import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
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
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)

_PROVABLY_UNFILLED_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}


@dataclass(frozen=True)
class BuyBookDepth:
    """One validated CLOB order-book snapshot for a prospective BUY."""

    best_bid: float
    best_ask: float
    spread: float
    ask_depth_shares: float
    ask_limit_price: float


def _book_field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _normalize_book_levels(value: Any, side: str) -> list[tuple[float, float]]:
    """Return finite positive ``(price, size)`` levels without guessing."""
    if not isinstance(value, (list, tuple)):
        raise ClobResponseContractError(f"CLOB {side} levels가 sequence가 아닙니다")
    levels: list[tuple[float, float]] = []
    for raw_level in value:
        raw_price = _book_field(raw_level, "price")
        raw_size = _book_field(raw_level, "size")
        try:
            price = float(raw_price)
            size = float(raw_size)
        except (TypeError, ValueError) as error:
            raise ClobResponseContractError(
                f"CLOB {side} level의 price/size가 숫자가 아닙니다"
            ) from error
        if (
            not math.isfinite(price)
            or not 0 < price < 1
            or not math.isfinite(size)
            or size <= 0
        ):
            raise ClobResponseContractError(
                f"CLOB {side} level의 price/size 범위가 유효하지 않습니다"
            )
        levels.append((price, size))
    if not levels:
        raise ClobResponseUnavailableError(f"CLOB {side} order book이 비어 있습니다")
    return levels


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
        execution_mode: str = "passive",
        intent_autoresolve: bool = True,
    ):
        """Initialize CLOB client.

        Args:
            config: API configuration with credentials
            simulation_mode: If True, don't execute real orders
            execution_mode: "passive" | "nearest" | "cross" — 틱 반올림 방향.
                전략의 핵심 축이므로 `_round_to_tick` 설명을 볼 것.
            intent_autoresolve: 격리 intent 자가 해제 여부. env가 아니라 인자로
                받는 이유는 `config_hash`에 담기게 하기 위함이다 — env로만 읽으면
                동작이 다른 두 run이 같은 cohort로 묶인다.
        """
        self.config = config
        self.simulation_mode = simulation_mode
        self.execution_mode = execution_mode
        self.intent_autoresolve = intent_autoresolve
        self._client = None
        self._initialized = False
        self._midpoint_snapshot: Optional[Dict[str, Optional[float]]] = None
        self.execution_ledger = (
            ExecutionLedger(audit_db_path, strategy_name=strategy_name)
            if audit_db_path is not None
            else None
        )

    def _ensure_initialized(self):
        """Lazy initialization of the CLOB client."""
        if self._initialized:
            return

        try:
            from py_clob_client_v2 import ClobClient

            self._client = ClobClient(
                host=self.HOST,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address,
            )

            # Create or derive API credentials (v2: create_or_derive_api_key)
            api_creds = self._client.create_or_derive_api_key()
            self._client.set_api_creds(api_creds)
            self._initialized = True
            logger.info("CLOB client 초기화 완료 (v2)")

        except Exception as e:
            logger.error(f"CLOB client 초기화 실패: {e}")
            raise

    @property
    def client(self):
        """Get initialized CLOB client."""
        self._ensure_initialized()
        return self._client

    def _round_to_tick(
        self,
        price: float,
        tick_size: float = None,
        side: str = None,
    ) -> float:
        """Round price to tick size, honouring the configured execution mode.

        이 메서드가 golden-melon 전략의 핵심이다.

        Polymarket 틱은 0.01이고 midpoint는 대개 반 틱 위에 놓인다(호가 0.79/0.80이면
        midpoint 0.795). 기존 14개 봇은 `round()`로 **가장 가까운 틱**에 붙였는데,
        그러면 midpoint의 셋째 자리에 따라 매수가 0.80(매도호가 리프트 = taker)이
        되기도 하고 0.79(매수호가 합류 = maker)가 되기도 한다. **실행 측면이 사실상
        무작위로 결정된다.**

        실측된 왕복 비용은 이 선택에 따라 부호가 바뀐다
        (`docs/retro/2026-07-29-execution-cost-floor.md`):

            maker → maker : -31.1 bps  (버는 쪽)
            taker → taker : +72.5 bps  (내는 쪽)

        차이가 **103 bps**다. 어떤 방향성 edge보다 크고, 우리가 완전히 통제할 수 있다.

        execution_mode (BUY leg only):
          passive  — BUY는 내림 (기본 처치군).
          nearest  — BUY는 최근접 반올림 (대조군).
          cross    — BUY는 올림 (비용 상한 측정용).
          SELL은 안전한 청산을 위해 세 모드 모두 nearest로 고정한다.

        Args:
            price: Raw price value
            tick_size: Tick size to round to (default: 0.01)
            side: "BUY" 또는 "SELL". None이면 nearest로 동작한다.

        Returns:
            Price rounded to the tick chosen by execution_mode
        """
        if tick_size is None:
            tick_size = self.DEFAULT_TICK_SIZE

        mode = (self.execution_mode or "nearest").lower()
        ticks = price / tick_size
        side_u = (side or "").upper()

        # ── 처치는 **진입(BUY)에만** 적용한다 ────────────────────────────
        # SELL에 passive를 적용하면 ceil(mid)가 되어 최우선 매수호가보다 위에
        # 주문이 걸린다. 익절은 그래도 괜찮지만 `absolute_stop`은 치명적이다 —
        # 시장가보다 비싸게 내놓는 손절은 체결되지 않고, **체결되지 않는 손절은
        # 손절이 아니다.** time_exit도 없으므로 대체 경로가 없다.
        #
        # 비대칭이 실재한다: 진입에서 미체결은 무해하지만(그 시장을 안 사면 그만),
        # 손절에서 미체결은 손실 포지션을 그대로 안고 가는 것이다.
        #
        # 부수 효과로 청산이 전 팔에서 동일해져 진입 축의 대비가 더 깨끗해진다.
        if side_u == "SELL":
            snapped = round(ticks)
        elif side_u != "BUY" or mode == "nearest":
            snapped = round(ticks)
        elif mode == "passive":
            # 사는 쪽은 더 싸게, 파는 쪽은 더 비싸게 걸어 호가에 합류한다.
            snapped = math.floor(ticks) if side_u == "BUY" else math.ceil(ticks)
        elif mode == "cross":
            snapped = math.ceil(ticks) if side_u == "BUY" else math.floor(ticks)
        else:
            raise ValueError(f"unknown execution_mode: {self.execution_mode}")

        rounded = round(snapped * tick_size, 2)
        # Clamp to tick-aligned bounds. Polymarket rejects prices outside
        # [tick_size, 1 - tick_size] with "invalid price (1.0)" / "(0.0)".
        return min(max(rounded, tick_size), round(1 - tick_size, 2))

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
    def get_buy_book_depth(
        self,
        token_id: str,
        *,
        ask_limit_price: float,
        max_price_window: Optional[float] = None,
    ) -> BuyBookDepth:
        """Read one book and measure executable asks up to a strict price cap.

        A single snapshot avoids combining bid and ask values observed at
        different times.  Malformed, crossed, empty, or stale-looking response
        shapes fail closed; the caller never substitutes Gamma metadata depth.
        """
        try:
            limit = float(ask_limit_price)
        except (TypeError, ValueError) as error:
            raise ValueError("ask_limit_price must be numeric") from error
        if not math.isfinite(limit) or not 0 < limit < 1:
            raise ValueError("ask_limit_price must be in (0, 1)")
        window: Optional[float] = None
        if max_price_window is not None:
            try:
                window = float(max_price_window)
            except (TypeError, ValueError) as error:
                raise ValueError("max_price_window must be numeric") from error
            if not math.isfinite(window) or not 0 < window < 1:
                raise ValueError("max_price_window must be in (0, 1)")

        try:
            result = self.client.get_order_book(str(token_id))
            bids = _normalize_book_levels(_book_field(result, "bids"), "bid")
            asks = _normalize_book_levels(_book_field(result, "asks"), "ask")
            best_bid = max(price for price, _ in bids)
            best_ask = min(price for price, _ in asks)
            if best_bid > best_ask + 1e-9:
                raise ClobResponseContractError(
                    "CLOB order book이 crossed 상태입니다"
                )
            depth_limit = (
                min(limit, best_ask + window)
                if window is not None
                else limit
            )
            depth = sum(
                size for price, size in asks if price <= depth_limit + 1e-9
            )
            return BuyBookDepth(
                best_bid=best_bid,
                best_ask=best_ask,
                spread=best_ask - best_bid,
                ask_depth_shares=depth,
                ask_limit_price=depth_limit,
            )
        except (ClobResponseContractError, ClobResponseUnavailableError):
            raise
        except Exception as error:
            if "No orderbook" in str(error):
                logger.debug("orderbook 없음 - token: %s: %s", token_id, error)
            else:
                logger.error(
                    "BUY orderbook depth 조회 실패 - token: %s: %s",
                    token_id,
                    error,
                )
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
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
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
        # Round price to tick size to avoid INVALID_ORDER_MIN_TICK_SIZE error.
        # side를 넘겨야 BUY leg에 execution_mode가 적용되고 SELL은 nearest로 고정된다.
        rounded_price = self._round_to_tick(price, side=side)

        if self.simulation_mode:
            logger.info(f"[SIM] Limit {side} - {size:.2f}주 @ {rounded_price:.2f}, token: {token_id}")
            result = {
                "success": True,
                "orderID": f"SIM_{side}_{token_id[:8]}",
                "simulated": True,
                "price": rounded_price,
            }
            self._record_limit_submission(token_id, rounded_price, size, side, result)
            return result

        try:
            from py_clob_client_v2 import OrderArgs, OrderType

            order_side = "BUY" if side.upper() == "BUY" else "SELL"
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

            # create_order performs signing and read-only preflight such as
            # tick-size/neg-risk lookups. Finish it before recording an intent
            # so a GET timeout cannot be mistaken for an uncertain POST.
            signed_order = self.client.create_order(order_args)

            def submit_order() -> Dict[str, Any]:
                return self.client.post_order(signed_order, OrderType.GTC)

            if self.execution_ledger is None:
                response = normalize_clob_response(
                    submit_order(), response_type="submission"
                )
            else:
                response = self.execution_ledger.submit_and_record(
                    token_id=token_id,
                    side=order_side,
                    requested_price=rounded_price,
                    requested_size=size,
                    submit=submit_order,
                    cancel=lambda order_id: self.client.cancel_orders([order_id]),
                )

            logger.info(f"Limit {side} 주문 완료 @ {rounded_price:.2f}: {response}")
            return dict(response)

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
                        submission_id, detail
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

        # 격리 자가 해제. CLOB POST가 5xx/timeout으로 끝나면 그 token/side의 주문이
        # 영구히 막히는데 시간 기반 해제가 없다. 거래소 열린 주문 목록에 없으면
        # 주문이 만들어지지 않은 것이 확인되므로 안전하게 풀 수 있다.
        # 조회 실패 시에는 절대 해제하지 않는다(빈 목록과 구분 불가).
        if self.intent_autoresolve:
            try:
                raw_open = self.client.get_open_orders()
                open_orders = normalize_clob_response_list(
                    raw_open, response_type="order"
                )
                live_keys = {
                    (
                        str(o.get("asset_id") or o.get("token_id") or ""),
                        str(o.get("side", "")).upper(),
                    )
                    for o in (open_orders or [])
                }
                heal = self.execution_ledger.autoresolve_stale_sell_intents(
                    live_order_keys=live_keys
                )
                stats["intent_autoresolved"] = heal["resolved"]
                if heal["resolved"] or heal["kept_live_order"]:
                    logger.info(
                        "격리 자가 해제 - 해제 %s건, 거래소 주문 실재로 보류 %s건, "
                        "최근이라 보류 %s건",
                        heal["resolved"],
                        heal["kept_live_order"],
                        heal["too_recent"],
                    )
            except Exception as heal_error:  # noqa: BLE001
                logger.warning(
                    "격리 자가 해제 생략 - 거래소 열린 주문 조회 실패: %s",
                    heal_error,
                )

        if stats["checked"]:
            logger.info(
                f"주문 원장 대사 - 확인 {stats['checked']}, fill {stats['fills']}, "
                f"완료 {stats['completed']}, legacy gap "
                f"{stats['legacy_unavailable']}, 오류 {stats['errors']}"
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

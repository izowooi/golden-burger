"""Trading execution logic (Fear Spike Fade strategy)."""
import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from polybot_observability import SubmissionEvidenceError

from ..db.repository import TradeRepository
from ..db.models import TradeStatus, STRATEGY_NAME
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import evaluate_exit, take_profit_target, retrace_target_price

logger = logging.getLogger(__name__)

# CLOB 매도 거절 사유가 "보유 토큰 잔고 0"인지 판별하는 패턴.
# GTC limit 매수는 접수 즉시 HOLDING으로 기록되지만(체결 가정), 실제로 체결되지
# 않은 유령 포지션은 매도 시 "not enough balance ... balance: 0"으로 거절된다.
# balance가 0이 아닌 거절(부분 체결/allowance 문제)은 유령이 아니므로 제외한다.
_ZERO_BALANCE_PATTERN = re.compile(r"not enough balance.*balance:\s*0(?:\D|$)")


def is_zero_balance_error(result: dict) -> bool:
    """매도 주문 실패가 '잔고 0(매수 미체결)' 때문인지 판별."""
    return bool(_ZERO_BALANCE_PATTERN.search(str(result.get("error", ""))))


# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# CLOB 잔고 거절은 두 가지 형식으로 온다. 실측(2026-07-28 Jenkins 로그):
#   (1) balance: N, order amount: M
#   (2) balance: N, sum of active orders: X, sum of matched orders: Y,
#       order amount (inc. fees): Z
# (2)에서 X가 N과 같으면 잔고 전액이 **자기 자신의 미체결 주문**에 묶인 것이다.
# 이 경우 수량을 줄여도 절대 팔리지 않는다 — 기존 주문을 먼저 취소해야 한다.
_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*(?:sum of active orders:\s*\d+.*?)?"
    r"order amount(?:\s*\(inc\. fees\))?:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_ACTIVE_ORDERS_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*sum of active orders:\s*(\d+)", re.IGNORECASE
)


def locked_in_own_orders(result: dict) -> bool:
    """잔고 전액이 자기 미체결 주문에 묶였는지. 수량 축소로는 해결되지 않는다."""
    match = _ACTIVE_ORDERS_PATTERN.search(str(result.get("error", "")))
    if match is None:
        return False
    balance, active = int(match.group(1)), int(match.group(2))
    return balance > 0 and active >= balance
_CLOB_QUANTITY_SCALE = 1_000_000
_SELL_BALANCE_SAFETY_FACTOR = 0.99


def available_shares_from_error(result: dict) -> Optional[float]:
    """거절 메시지에 실린 CLOB 조건부토큰 잔고를 주(share) 단위로 뽑는다."""
    match = _AVAILABLE_BALANCE_PATTERN.search(str(result.get("error", "")))
    if match is None:
        return None
    return int(match.group(1)) / _CLOB_QUANTITY_SCALE

# ── 매도 실패 진단 ────────────────────────────────────────────────────
# 매도 거절 중 상당수는 재시도해도 성공하지 않는다. 그런데 실패 분기가 trade
# 상태를 바꾸지 않으므로 HOLDING으로 남아 매 사이클 같은 주문을 반복 제출한다.
# golden-cherry 실측(2026-07-11~28): 실패한 SELL 제출 73,238건 / 401 token
# = 토큰당 평균 182.6회, 최대 4,002회(17일간 사실상 매 사이클).
# 원인은 둘이었다 — (1) 부분 체결로 실제 잔고 < DB 수량인데 줄이지 않음,
# (2) 잔고와 정확히 같은 수량을 제출(거래소가 반올림 여유를 요구).
_SELL_GONE_PATTERNS = ("invalid token id", "orderbook id does not exist")


def classify_sell_failure(
    result: dict, requested_size: float, min_order_size: float = 5.0
) -> str:
    """매도 실패를 재시도 가능성 기준으로 분류한다 (로그 집계용).

    market_gone / dust_unsellable 은 영구적이라 재시도가 무의미하다.
    partial_balance / balance_edge 는 수량을 줄이면 체결될 수 있다.
    """
    message = str(result.get("error", "")).lower()
    if any(pattern in message for pattern in _SELL_GONE_PATTERNS):
        return "market_gone"
    if "not enough balance" not in message:
        if "not ready" in message or "request exception" in message:
            return "transient"
        return "other"
    if locked_in_own_orders(result):
        return "locked_in_own_orders"
    available = available_shares_from_error(result)
    if available is None:
        return "balance_unparsed"
    if available <= 0:
        return "zero_balance"
    if available < min_order_size:
        return "dust_unsellable"
    if available < requested_size:
        return "partial_balance"
    return "balance_edge"



# 해결 후 이 시간이 지나도 가격 조회가 안 되면 EXPIRED 마감 처리
RESOLVED_GRACE_HOURS = 24.0

# NO 매수가 상한 (스펙 §B.3: NO 가격 ∈ [0.70, 0.95]).
# 하한 0.70은 1 - yes_max에서 유도되고, 상한 0.95는 YES가 이미 붕괴해
# 페이드할 프리미엄이 남지 않은 시장(YES < 5%)을 배제한다.
NO_PRICE_MAX = 0.95


class Trader:
    """Executes buy and sell orders for the Fear Spike Fade strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
        mode: str = "live",
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
            mode: "live" 또는 "sim" (부록 §A.2 - trades.mode 기록용)
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = mode

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market (NO 토큰).

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id (NO 토큰)
                - probability (NO 가격)
                - yes_price
                - outcome ("No")
                - question / market_slug / liquidity / volume_24h
                - entry_reason
                - end_date (datetime)
                - hours_until_resolution (float)
                - base_price / spike_peak / spike_age_minutes / vol_mult

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 이미 보유 중인 시장?
        if self.repo.has_holding(condition_id):
            logger.info(f"이미 보유 중인 시장 skip: {condition_id}")
            return None

        # Check: 재진입 쿨다운?
        if self.repo.is_in_reentry_cooldown(
            condition_id, self.config.reentry_cooldown_hours
        ):
            logger.info(
                f"재진입 쿨다운 중 skip: {condition_id} "
                f"(쿨다운: {self.config.reentry_cooldown_hours}h)"
            )
            return None

        # Check: Max positions limit
        if self.config.max_positions > 0:
            current_positions = self.repo.get_position_count()
            if current_positions >= self.config.max_positions:
                logger.info(f"최대 포지션 수 ({self.config.max_positions}) 도달")
                return None

        # Get current price (re-verify before buying)
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.warning(f"가격 조회 실패 - condition: {condition_id}: {e}")
            return None

        if current_price <= 0:
            logger.warning(f"유효하지 않은 가격 - 매수 skip: {condition_id}")
            return None

        # NO 매수 밴드 재검증: [1 - yes_max, NO_PRICE_MAX] = [0.70, 0.95]
        no_min = 1.0 - self.config.strategy.yes_max
        no_max = NO_PRICE_MAX

        # NO 급등 = YES 스파이크 붕괴 완료. 페이드 기회 소멸 → 쿨다운 skip 기록
        if current_price > no_max:
            logger.info(
                f"스파이크 붕괴 완료 - 매수 skip: {condition_id} "
                f"(NO 가격: {current_price:.1%} > 상한 {no_max:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "spike_collapsed")
            return None

        # NO 하락 = YES가 더 오름 = 스파이크 재점화 (진짜 정보 가능성). 이번 사이클만 skip
        if current_price < no_min:
            logger.info(
                f"스파이크 재점화로 매수 조건 미충족: {condition_id} "
                f"(NO 가격: {current_price:.1%} < 하한 {no_min:.1%})"
            )
            return None

        # Calculate order size
        buy_shares = self.config.buy_amount_usdc / current_price

        # Check minimum order size
        if buy_shares < MIN_ORDER_SIZE:
            logger.warning(
                f"주문 수량 {buy_shares:.2f}주 < 최소 {MIN_ORDER_SIZE}주 - {condition_id}. "
                f"buy_amount_usdc를 늘리거나 낮은 가격에서 매수하세요."
            )
            return None

        entry_reason = candidate.get("entry_reason", "unknown")
        end_date = candidate.get("end_date")
        hours_until_resolution = candidate.get("hours_until_resolution")
        hours_str = (
            f"{hours_until_resolution:.1f}h"
            if hours_until_resolution is not None else "N/A"
        )

        # Place order
        logger.info(
            f"매수: {candidate['outcome']} - '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f}주, ${self.config.buy_amount_usdc}) "
            f"[사유: {entry_reason}, 해결까지 {hours_str}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=buy_shares,
            side="BUY",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Record trade in DB (부록 §A: strategy_name/mode/volume/시그널 컬럼)
            trade = self.repo.create_trade(
                condition_id=condition_id,
                market_slug=candidate["market_slug"],
                question=candidate["question"],
                outcome=candidate["outcome"],
                token_id=token_id,
                buy_price=current_price,
                buy_amount=self.config.buy_amount_usdc,
                buy_shares=buy_shares,
                buy_order_id=result.get("orderID"),
                buy_timestamp=datetime.utcnow(),
                buy_probability=current_price,
                liquidity_at_buy=candidate["liquidity"],
                market_tags=candidate.get("market_tags", ""),
                status=TradeStatus.HOLDING,
                entry_reason=entry_reason,
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
                volume_24h_at_buy=candidate.get("volume_24h"),
                yes_price_at_buy=candidate.get("yes_price"),
                base_price_at_buy=candidate.get("base_price"),
                spike_peak_at_buy=candidate.get("spike_peak"),
                spike_age_minutes_at_buy=candidate.get("spike_age_minutes"),
                vol_mult_at_buy=candidate.get("vol_mult"),
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _expire_if_resolved(self, trade) -> bool:
        """가격 조회 실패 시 해결된 시장 leak 처리 (§3.4).

        market_end_date가 24시간 이상 지났으면 EXPIRED로 마감 처리하고
        수동 redeem 필요 경고를 남긴다. EXPIRED는 get_holding_trades에서
        제외되므로 좀비 포지션이 무한 반복 조회되지 않는다.

        Returns:
            True if trade was marked EXPIRED
        """
        end_date = trade.market_end_date
        if end_date is None:
            return False

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if now - end_date < timedelta(hours=RESOLVED_GRACE_HOURS):
            return False

        self.repo.update_trade(
            trade.id,
            status=TradeStatus.EXPIRED,
            exit_reason="resolved_unredeemed",
            realized_pnl=None,
        )
        logger.warning(
            f"해결된 시장 마감 처리(EXPIRED): Trade #{trade.id} - "
            f"'{trade.question[:50]}...' "
            f"(해결 {end_date.isoformat()} 후 {RESOLVED_GRACE_HOURS:.0f}h 경과, "
            f"midpoint 조회 불가). 수동 redeem 필요."
        )
        return True

    def _latest_yes_price(self, condition_id: str) -> Optional[float]:
        """retrace_target 판정용 최신 YES 가격 (스냅샷 기준).

        1-NO midpoint 근사 대신 Phase 0에서 저장된 스냅샷의 최신 YES를 쓴다
        (스냅샷은 항상 YES 기준 저장 - 진입 시그널과 같은 단위).
        스냅샷이 없으면 None → retrace 판정 보류.
        """
        snapshot = self.repo.get_latest_snapshot(condition_id)
        if snapshot is None:
            return None
        return float(snapshot.probability)

    def _holding_hours(self, trade, now: Optional[datetime] = None) -> Optional[float]:
        """매수 후 경과 시간 (max_holding 판정용)."""
        if trade.buy_timestamp is None:
            return None
        if now is None:
            now = datetime.utcnow()
        return (now - trade.buy_timestamp).total_seconds() / 3600.0

    def _place_sell_with_balance_retry(
        self,
        *,
        token_id: str,
        price: float,
        requested_size: float,
    ):
        """매도를 제출하고, 잔고 부족 거절이면 가용 잔고에 맞춰 **한 번만** 재시도한다.

        재시도를 1회로 제한하는 이유가 핵심이다 — 무한 재시도가 원래 문제였다.
        안전계수 0.99를 곱하는 이유: 잔고와 정확히 같은 수량을 제출하면 거래소가
        반올림 여유 부족으로 거절한다(golden-cherry에서 같은 토큰이 1,469회 거절).

        Returns:
            (result, 실제 제출한 수량)
        """
        result = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=requested_size,
            side="SELL",
        )
        if result.get("success") or result.get("orderID"):
            return result, requested_size

        if locked_in_own_orders(result):
            # 잔고 전액이 자기 미체결 주문에 묶였다. 수량을 줄여도 팔리지 않으므로
            # 재시도하지 않는다. 기존 주문 취소가 선행되어야 한다.
            logger.warning(
                "매도 실패 진단 - 사유=locked_in_own_orders token=%s 요청=%.6f "
                "(잔고 전액이 자기 미체결 주문에 묶여 축소 재시도로 해결 불가)",
                str(token_id)[:16], requested_size,
            )
            return result, requested_size

        available = available_shares_from_error(result)
        if available is None or available <= 0:
            # 파싱 불가 또는 잔고 0 - 기존 유령 판정 경로가 처리한다.
            return result, requested_size

        basis = min(available, requested_size)
        retry_size = math.floor(
            basis * _SELL_BALANCE_SAFETY_FACTOR * _CLOB_QUANTITY_SCALE
        ) / _CLOB_QUANTITY_SCALE
        if retry_size < MIN_ORDER_SIZE:
            logger.warning(
                "매도 실패 진단 - 사유=dust_unsellable token=%s 요청=%.6f 가용=%.6f "
                "(최소 주문량 %.1f주 미만이라 이 포지션은 영구히 매도 불가)",
                str(token_id)[:16], requested_size, available, MIN_ORDER_SIZE,
            )
            return result, requested_size

        logger.warning(
            "매도 수량을 CLOB 가용 잔고 기준으로 축소해 1회 재시도 - "
            "token=%s 요청=%.6f 가용=%.6f 제출=%.6f",
            str(token_id)[:16], requested_size, available, retry_size,
        )
        retry = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=retry_size,
            side="SELL",
        )
        if retry.get("success") or retry.get("orderID"):
            return retry, retry_size
        return retry, requested_size

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순, trailing 없음):
        1. 손절: P&L <= -10% (YES가 계속 오름 = 진짜 정보 → 즉시 손절)
        2. retrace_target (주 청산): 스냅샷 최신 YES <= base + 0.5*(peak - base)
        3. 익절(보조): NO 현재가 >= buy_price*(1+8%) [0.99 캡]
        4. max_holding: 보유 72h 초과 (되돌림 실패)
        5. 시간: 해결 24시간 이내

        Args:
            trade: Trade object from DB

        Returns:
            True if sell executed successfully
        """
        token_id = trade.token_id
        condition_id = trade.condition_id

        # Get current price
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.warning(f"가격 조회 실패 - condition: {condition_id}: {e}")
            # 해결된 시장 leak 수정: 24h 지난 시장은 EXPIRED 마감
            self._expire_if_resolved(trade)
            return False

        # midpoint가 예외 없이 0으로 오는 경우도 조회 실패로 취급한다.
        # 0을 그대로 쓰면 P&L -100% → stop_loss 판정 → 0.01에 투매된다.
        if current_price <= 0:
            logger.warning(
                f"유효하지 않은 midpoint({current_price}) - 매도 판정 skip: {condition_id}"
            )
            self._expire_if_resolved(trade)
            return False

        # Update max_price (분석용 - trailing stop은 없음)
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        hours_left = get_hours_until_resolution(trade.market_end_date)
        current_yes_price = self._latest_yes_price(condition_id)
        holding_hours = self._holding_hours(trade)

        signal = evaluate_exit(
            buy_price=trade.buy_price,
            current_price=current_price,
            current_yes_price=current_yes_price,
            base_price=trade.base_price_at_buy,
            spike_peak=trade.spike_peak_at_buy,
            retrace_ratio=self.config.strategy.retrace_ratio,
            holding_hours=holding_hours,
            hours_left=hours_left,
            take_profit_percent=self.config.take_profit_percent,
            stop_loss_percent=self.config.stop_loss_percent,
            exit_hours=float(self.config.time_based.exit_hours),
            max_holding_hours=float(self.config.strategy.max_holding_hours),
        )

        pnl_percent = 0.0
        if trade.buy_price and trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        if not signal.should_sell:
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            yes_str = f"{current_yes_price:.2%}" if current_yes_price is not None else "N/A"
            holding_str = f"{holding_hours:.1f}h" if holding_hours is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(NO: {current_price:.2%}, YES 스냅샷: {yes_str}, P&L: {pnl_percent:.1%}, "
                f"보유: {holding_str}, 해결까지: {hours_str})"
            )
            return False

        if signal.reason == "stop_loss":
            logger.info(
                f"손절 조건 충족 - 매도: {condition_id} "
                f"(손실: {pnl_percent:.1%} <= {self.config.stop_loss_percent:.1%}, "
                f"YES 지속 상승 = 진짜 정보 신호)"
            )
        elif signal.reason == "retrace_target":
            target_yes = retrace_target_price(
                trade.base_price_at_buy,
                trade.spike_peak_at_buy,
                self.config.strategy.retrace_ratio,
            )
            logger.info(
                f"retrace 익절 조건 충족 - 매도: {condition_id} "
                f"(YES {current_yes_price:.2%} <= 목표 {target_yes:.2%} "
                f"[base {trade.base_price_at_buy:.2%}, peak {trade.spike_peak_at_buy:.2%}])"
            )
        elif signal.reason == "take_profit":
            target = take_profit_target(trade.buy_price, self.config.take_profit_percent)
            logger.info(
                f"익절 조건 충족 - 매도: {condition_id} "
                f"(NO: {current_price:.2%} >= 목표 {target:.2%})"
            )
        elif signal.reason == "max_holding":
            logger.info(
                f"최대 보유 시간 초과 - 매도: {condition_id} "
                f"(보유 {holding_hours:.1f}h >= {self.config.strategy.max_holding_hours}h, "
                f"되돌림 실패 → 자본 회수)"
            )
        elif signal.reason == "time_exit":
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.info(
                f"시간 기반 청산 - 매도: {condition_id} "
                f"(해결까지: {hours_str} < {self.config.time_based.exit_hours}h)"
            )

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) [사유: {signal.reason}]"
        )

        result, sell_shares = self._place_sell_with_balance_retry(
            token_id=token_id,
            price=current_price,
            requested_size=trade.buy_shares,
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Calculate P&L
            sell_value = current_price * sell_shares
            buy_value = trade.buy_price * sell_shares
            realized_pnl = sell_value - buy_value

            # Update trade record (yes_price_at_exit = 청산 판정에 쓴 시그널 - 부록 §A.5)
            self.repo.update_trade(
                trade.id,
                sell_price=current_price,
                sell_shares=sell_shares,
                sell_order_id=result.get("orderID"),
                sell_timestamp=datetime.utcnow(),
                sell_probability=current_price,
                realized_pnl=realized_pnl,
                status=TradeStatus.COMPLETED,
                exit_reason=signal.reason,
                yes_price_at_exit=current_yes_price,
            )

            pnl_percent_display = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            logger.info(
                f"매도 주문 완료: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({pnl_percent_display:.1f}%), "
                f"사유: {signal.reason}"
            )
            return True
        else:
            if is_zero_balance_error(result):
                self._mark_unfilled(trade)
                return False
            _available = available_shares_from_error(result)
            logger.warning(
                "매도 실패 진단 - 사유=%s trade=%s token=%s 요청=%.6f 가용=%s",
                classify_sell_failure(result, trade.buy_shares, MIN_ORDER_SIZE),
                trade.id,
                str(token_id)[:16],
                trade.buy_shares,
                f"{_available:.6f}" if _available is not None else "미상",
            )
            logger.error(f"매도 주문 실패: {result}")
            return False

    def _mark_unfilled(self, trade) -> None:
        """유령 포지션 마감: 매수 GTC가 체결되지 않았음이 확인된 trade.

        지갑 잔고 0으로 매도가 거절됐다 = 매수 지정가가 한 번도 잡히지 않았다.
        (1) 호가창에 남은 매수 주문을 취소해 뒤늦은 역선택 체결을 막고,
        (2) status를 UNFILLED로 바꿔 매도 재시도 루프를 끊는다.
        회고에서 UNFILLED 건수는 체결 가정(fill assumption) 편향의 정량 지표다.
        """
        if trade.buy_order_id and not str(trade.buy_order_id).startswith("SIM"):
            try:
                cancel_result = self.clob.cancel_order(trade.buy_order_id)
            except SubmissionEvidenceError as error:
                logger.error(
                    "유령 포지션 판정 보류 - buy order의 zero-fill 취소를 "
                    "증명하지 못해 HOLDING 유지: trade=%s order=%s error=%s",
                    trade.id,
                    trade.buy_order_id,
                    type(error).__name__,
                )
                return
            logger.info(f"미체결 매수 주문 취소: {trade.buy_order_id} -> {cancel_result}")
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.UNFILLED,
            exit_reason="buy_unfilled",
        )
        logger.warning(
            f"유령 포지션 마감 [UNFILLED]: Trade #{trade.id} "
            f"'{trade.question[:50]}...' - 매수 GTC 미체결 확인 (지갑 잔고 0). "
            f"P&L 집계에서 제외."
        )

    def check_and_sell_holdings(self) -> int:
        """Check all holding positions and sell if conditions met.

        Returns:
            Number of positions sold
        """
        holdings = self.repo.get_holding_trades()
        sold_count = 0

        logger.info(f"보유 포지션 {len(holdings)}개 확인 중")

        for trade in holdings:
            if self.execute_sell(trade):
                sold_count += 1

        return sold_count

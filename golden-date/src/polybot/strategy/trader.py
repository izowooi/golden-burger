"""Trading execution logic for the Conviction Ladder strategy."""
import logging
import math
import re
from datetime import datetime
from typing import Optional
from polybot_observability import SubmissionEvidenceError

from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import ladder_band, evaluate_exit, take_profit_target

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

_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*order amount:\s*(\d+)", re.IGNORECASE
)
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



# 봇 식별 상수 (trades.strategy_name — 교차 봇 UNION 쿼리용)
STRATEGY_NAME = "date"

# 해결 후 이 시간이 지나도록 midpoint 조회가 안 되면 EXPIRED 처리 (§3.4)
RESOLVED_UNREDEEMED_GRACE_HOURS = 24.0


class Trader:
    """Executes buy and sell orders based on the Conviction Ladder strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
        simulation_mode: bool = False,
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
            simulation_mode: config.simulation_mode (trades.mode 기록용)
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = "sim" if simulation_mode else "live"

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id
                - probability
                - outcome ("Yes" or "No")
                - question
                - market_slug
                - liquidity
                - entry_reason
                - end_date (datetime)
                - hours_until_resolution (float)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 재진입 가능? (HOLDING / 매도·skip 쿨다운, §3.3)
        can_enter, reenter_reason = self.repo.can_reenter(
            condition_id, self.config.reentry_cooldown_hours
        )
        if not can_enter:
            logger.info(f"재진입 조건 미충족 - 매수 skip: {condition_id} ({reenter_reason})")
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
            logger.warning(f"유효하지 않은 midpoint - 매수 skip: {condition_id}")
            return None

        # 사다리 밴드 재검증 (스캔~주문 사이 가격/시간 드리프트 대응)
        hours_left = get_hours_until_resolution(candidate.get("end_date"))
        band = ladder_band(
            hours_left, self.config.ladder.entry_hours_min, self.config.ladder.rungs()
        )
        if band is None:
            logger.info(
                f"시간 창 이탈로 매수 skip: {condition_id} "
                f"(해결까지: {hours_left if hours_left is None else round(hours_left, 1)}h)"
            )
            return None

        band_no, band_min, band_max = band

        # Check: Price jumped above band max?
        if current_price > band_max:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 밴드{band_no} 상한 {band_max:.1%}, "
                f"쿨다운 {self.config.reentry_cooldown_hours}h 후 재평가)"
            )
            # 영구 밴이 아니라 skipped_at 기준 쿨다운 후 재평가된다
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # Check: Price dropped below band min?
        if current_price < band_min:
            logger.info(
                f"가격 하락으로 매수 조건 미충족: {condition_id} "
                f"(가격: {current_price:.1%} < 밴드{band_no} 하한 {band_min:.1%})"
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

        # Place order
        hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
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
            # Record trade in DB
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
                hours_until_resolution_at_buy=hours_left,
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
                volume_24h_at_buy=candidate.get("volume_24h"),
                ladder_band_at_buy=candidate.get("ladder_band"),
                momentum_at_buy=candidate.get("momentum_change"),
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _handle_price_lookup_failure(self, trade, error) -> bool:
        """midpoint 조회 실패 처리 (§3.4 해결된 시장 leak 수정).

        market_end_date가 24시간 이상 지났으면 EXPIRED로 마감 처리해
        영구 HOLDING 좀비 포지션을 막는다. realized_pnl은 NULL로 두고
        수동 redeem 대상임을 WARNING으로 알린다.

        Returns:
            항상 False (매도가 발생한 것이 아니므로)
        """
        hours_left = get_hours_until_resolution(trade.market_end_date)

        if hours_left is not None and hours_left <= -RESOLVED_UNREDEEMED_GRACE_HOURS:
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.EXPIRED,
                exit_reason="resolved_unredeemed",
                realized_pnl=None,
            )
            logger.warning(
                f"해결 후 {RESOLVED_UNREDEEMED_GRACE_HOURS:.0f}h 경과 - EXPIRED 처리: "
                f"Trade #{trade.id} '{trade.question[:50]}...' ({trade.condition_id}). "
                f"수동 redeem 필요 (P&L 미확정)."
            )
            return False

        logger.warning(f"가격 조회 실패 - condition: {trade.condition_id}: {error}")
        return False

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

        청산 조건 (우선순위 순, signals.evaluate_exit):
        1. 손절: P&L <= -8%
        2. 익절: 현재가 >= 목표가 (buy*(1+12%), 0.99 캡)
        3. 트레일링 스탑: 최고점 대비 -5%
        4. 시간: 해결 2시간 이내 (마지막 수렴 구간까지 보유)

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
            return self._handle_price_lookup_failure(trade, e)

        if current_price <= 0:
            return self._handle_price_lookup_failure(trade, "midpoint <= 0")

        # Update max_price if current price is higher
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        # Calculate P&L
        pnl_percent = 0.0
        if trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        hours_left = get_hours_until_resolution(trade.market_end_date)

        exit_reason = evaluate_exit(
            buy_price=trade.buy_price,
            current_price=current_price,
            max_price=max_price,
            hours_left=hours_left,
            stop_loss_percent=self.config.stop_loss_percent,
            take_profit_percent=self.config.take_profit_percent,
            trailing_enabled=self.config.trailing_stop.enabled,
            trailing_percent=self.config.trailing_stop.percent,
            exit_hours=self.config.exit_hours,
        )

        if exit_reason is None:
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"최고가: {max_price:.2%}, 해결까지: {hours_str})"
            )
            return False

        hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
        tp_target = take_profit_target(trade.buy_price, self.config.take_profit_percent)
        logger.info(
            f"청산 조건 충족 [{exit_reason}]: {condition_id} "
            f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
            f"최고가: {max_price:.2%}, 익절 목표가: {tp_target:.2f}, 해결까지: {hours_str})"
        )

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) [사유: {exit_reason}]"
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

            # Update trade record
            self.repo.update_trade(
                trade.id,
                sell_price=current_price,
                sell_shares=sell_shares,
                sell_order_id=result.get("orderID"),
                sell_timestamp=datetime.utcnow(),
                sell_probability=current_price,
                realized_pnl=realized_pnl,
                status=TradeStatus.COMPLETED,
                exit_reason=exit_reason,
            )

            pnl_percent_display = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            logger.info(
                f"매도 주문 완료: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({pnl_percent_display:.1f}%), "
                f"사유: {exit_reason}"
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

"""Trading execution logic with resolution momentum strategy."""
import logging
import math
import re
from datetime import datetime
from typing import Optional
from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)

from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution

logger = logging.getLogger(__name__)

# CLOB 매도 거절 사유가 "보유 토큰 잔고 0"인지 판별하는 패턴.
# GTC limit 매수는 접수 즉시 HOLDING으로 기록되지만(체결 가정), 실제로 체결되지
# 않은 유령 포지션은 매도 시 "not enough balance ... balance: 0"으로 거절된다.
# balance가 0이 아닌 거절(부분 체결/allowance 문제)은 유령이 아니므로 제외한다.
_ZERO_BALANCE_PATTERN = re.compile(r"not enough balance.*balance:\s*0(?:\D|$)")
_BALANCE_ALLOWANCE_PATTERN = re.compile(
    r"not enough balance\s*/\s*allowance", re.IGNORECASE
)
_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*order amount:\s*(\d+)", re.IGNORECASE
)
_CLOB_QUANTITY_SCALE = 1_000_000
_SELL_BALANCE_SAFETY_FACTOR = 0.99


def is_zero_balance_error(result: dict) -> bool:
    """매도 주문 실패가 '잔고 0(매수 미체결)' 때문인지 판별."""
    return bool(_ZERO_BALANCE_PATTERN.search(str(result.get("error", ""))))


def is_balance_allowance_error(result: dict) -> bool:
    """Return whether CLOB rejected an order for balance or allowance."""
    return bool(
        _BALANCE_ALLOWANCE_PATTERN.search(str(result.get("error", "")))
    )


def available_shares_from_error(result: dict) -> Optional[float]:
    """Extract the conditional-token balance reported by CLOB, in shares."""
    match = _AVAILABLE_BALANCE_PATTERN.search(str(result.get("error", "")))
    if match is None:
        return None
    return int(match.group(1)) / _CLOB_QUANTITY_SCALE

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0


class Trader:
    """Executes buy and sell orders based on resolution momentum strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.buying_disabled = False

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

        if self.buying_disabled:
            return None

        # Check: Already traded?
        if self.repo.is_already_traded(condition_id):
            logger.info(f"이미 거래한 시장: {condition_id}")
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

        # Check: Price jumped above sell threshold?
        if current_price > self.config.sell_threshold:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 매도 기준 {self.config.sell_threshold:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # Check: Price dropped below buy threshold?
        if current_price < self.config.buy_threshold:
            logger.info(
                f"가격 하락으로 매수 조건 미충족: {condition_id} "
                f"(가격: {current_price:.1%} < 매수 기준 {self.config.buy_threshold:.1%})"
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

        # Place order
        logger.info(
            f"매수: {candidate['outcome']} - '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f}주, ${self.config.buy_amount_usdc}) "
            f"[사유: {entry_reason}, 해결까지 {hours_until_resolution:.1f}h]"
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
                # Resolution momentum strategy fields
                entry_reason=entry_reason,
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            if is_balance_allowance_error(result):
                self.buying_disabled = True
                logger.warning(
                    "collateral 잔고/allowance가 매수금액보다 부족해 "
                    "이번 cycle의 남은 매수를 중단합니다"
                )
            else:
                logger.error(f"매수 주문 실패: {result}")
            return None

    def _place_sell_with_balance_retry(
        self,
        *,
        token_id: str,
        price: float,
        requested_size: float,
    ) -> tuple[dict, float]:
        """Retry one SELL below a smaller balance explicitly reported by CLOB."""
        result = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=requested_size,
            side="SELL",
        )
        if result.get("success") or result.get("orderID"):
            return result, requested_size

        available_shares = available_shares_from_error(result)
        if (
            available_shares is None
            or available_shares <= 0
            or available_shares >= requested_size
        ):
            return result, requested_size

        retry_size = math.floor(
            available_shares
            * _SELL_BALANCE_SAFETY_FACTOR
            * _CLOB_QUANTITY_SCALE
        ) / _CLOB_QUANTITY_SCALE
        if retry_size < MIN_ORDER_SIZE:
            logger.warning(
                "부분 체결 잔고가 최소 주문량보다 작아 매도 보류 - "
                "token=%s available=%.6f",
                token_id,
                available_shares,
            )
            return result, requested_size

        logger.warning(
            "DB 수량보다 CLOB token 잔고가 작아 가용 잔고의 99%%로 "
            "매도를 한 번 재시도합니다 - requested=%.6f available=%.6f "
            "retry=%.6f",
            requested_size,
            available_shares,
            retry_size,
        )
        retry_result = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=retry_size,
            side="SELL",
        )
        return retry_result, retry_size

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순):
        1. 손절: P&L <= -8%
        2. 익절: P&L >= configured take-profit threshold
        3. 트레일링 스탑: 최고점 대비 -5%
        4. 시간: 설정된 exit_hours 이내 (0이면 비활성화)

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
            return False

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

        # Determine exit signal and reason
        should_sell = False
        exit_reason = "hold"

        # 1. Stop Loss: P&L <= -8%
        if pnl_percent <= self.config.stop_loss_percent:
            should_sell = True
            exit_reason = "stop_loss"
            logger.info(
                f"손절 조건 충족 - 매도: {condition_id} "
                f"(손실: {pnl_percent:.1%} <= {self.config.stop_loss_percent:.1%})"
            )

        # 2. Take Profit: P&L >= +15%
        elif pnl_percent >= self.config.take_profit_percent:
            should_sell = True
            exit_reason = "take_profit"
            logger.info(
                f"익절 조건 충족 - 매도: {condition_id} "
                f"(수익: {pnl_percent:.1%} >= {self.config.take_profit_percent:.1%})"
            )

        # 3. Trailing Stop: current_price < max_price * (1 - trailing_percent)
        elif self.config.trailing_stop.enabled and max_price > 0:
            trailing_trigger = max_price * (1 - self.config.trailing_stop.percent)
            if current_price < trailing_trigger:
                should_sell = True
                exit_reason = "trailing_stop"
                drawdown = (max_price - current_price) / max_price
                logger.info(
                    f"트레일링 스탑 충족 - 매도: {condition_id} "
                    f"(최고가: {max_price:.2%}, 현재가: {current_price:.2%}, "
                    f"하락폭: {drawdown:.1%})"
                )

        # 4. Time-based Exit: only unresolved markets within the exit window.
        # exit_hours=0 disables this rule. A past endDate is not proof of
        # resolution and must never trigger a CLOB sell by itself.
        if not should_sell and self.config.time_based.enabled:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            exit_hours = self.config.time_based.exit_hours
            if (
                exit_hours > 0
                and hours_left is not None
                and 0 < hours_left <= exit_hours
            ):
                should_sell = True
                exit_reason = "time_exit"
                logger.info(
                    f"시간 기반 청산 - 매도: {condition_id} "
                    f"(해결까지: {hours_left:.1f}h <= {exit_hours}h)"
                )

        if not should_sell:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            hours_str = f"{hours_left:.1f}h" if hours_left else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"최고가: {max_price:.2%}, 해결까지: {hours_str})"
            )
            return False

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
                if isinstance(error.__cause__, ClobResponseUnavailableError):
                    self.repo.update_trade(
                        trade.id,
                        status=TradeStatus.QUARANTINED,
                        exit_reason="zero_balance_order_unavailable",
                        realized_pnl=None,
                    )
                    logger.warning(
                        "zero-balance 포지션 격리 [QUARANTINED]: "
                        "Trade #%s - 과거 buy order가 CLOB catalog에서 "
                        "사라져 zero-fill 여부를 확정하지 못했습니다",
                        trade.id,
                    )
                    return
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

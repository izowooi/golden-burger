"""Trading execution logic for the Patience Premium strategy."""
import logging
import re
from datetime import datetime
from typing import Optional
from polybot_observability import SubmissionEvidenceError

from ..db.repository import TradeRepository
from ..db.models import TradeStatus, STRATEGY_NAME
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import carry_yield, check_carry_entry, evaluate_exit, take_profit_target

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

# 해결 후 이 시간이 지나도록 midpoint 조회가 안 되면 EXPIRED 처리 (§3.4)
RESOLVED_UNREDEEMED_GRACE_HOURS = 24.0


class Trader:
    """Executes buy and sell orders based on the Patience Premium strategy."""

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

    @property
    def _mode(self) -> str:
        """회고 로깅용 실행 모드 ("live" / "sim", 부록 §A)."""
        return "sim" if getattr(self.clob, "simulation_mode", False) else "live"

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
                - volume_24h
                - entry_reason
                - end_date (datetime)
                - hours_until_resolution (float)
                - carry_yield (float)
                - momentum_change (float)

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

        # 캐리 조건 재검증 (스캔~주문 사이 가격/시간 드리프트 대응)
        carry_cfg = self.config.carry
        hours_left = get_hours_until_resolution(candidate.get("end_date"))
        carry_ok, carry_reason, current_yield = check_carry_entry(
            current_price,
            hours_left,
            yield_min=carry_cfg.yield_min,
            prob_min=carry_cfg.prob_min,
            prob_max=carry_cfg.prob_max,
            entry_hours_min=carry_cfg.entry_hours_min,
            entry_hours_max=carry_cfg.entry_hours_max,
        )
        if not carry_ok:
            if current_price > carry_cfg.prob_max:
                # 밴드 상한 돌파 = 이미 수렴 완료 구간 - 쿨다운 후 재평가
                logger.info(
                    f"급등 감지 - 매수 skip: {condition_id} "
                    f"(가격: {current_price:.1%} > 상한 {carry_cfg.prob_max:.1%}, "
                    f"쿨다운 {self.config.reentry_cooldown_hours}h 후 재평가)"
                )
                self.repo.mark_as_skipped(condition_id, "rapid_jump")
            else:
                logger.info(
                    f"캐리 조건 이탈로 매수 skip: {condition_id} "
                    f"(가격: {current_price:.1%}, 사유: {carry_reason})"
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
            f"[사유: {entry_reason}, y={current_yield:.2f}, 해결까지 {hours_str}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=buy_shares,
            side="BUY",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Record trade in DB (회고 로깅 표준 §A 컬럼 포함)
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
                strategy_name=STRATEGY_NAME,
                mode=self._mode,
                volume_24h_at_buy=candidate.get("volume_24h"),
                carry_yield_at_buy=current_yield,
                momentum_6h_at_buy=candidate.get("momentum_change"),
                max_price=current_price,  # 회고용 고점 추적 초기값
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_left,
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

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순, signals.evaluate_exit):
        1. 손절: P&L <= -6% (수렴 실패 신호)
        2. 익절: 현재가 >= 0.99 (수렴 완료 - tp 기본 9.99라 목표가는 항상 0.99 캡)
        3. 시간: 해결 2시간 이내 (마지막까지 캐리 수확)
        trailing stop 없음 - 수렴 보유가 본질.

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

        # 회고용 고점 추적 (trailing stop 아님 - 청산 판정에 쓰지 않는다)
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
            hours_left=hours_left,
            stop_loss_percent=self.config.stop_loss_percent,
            take_profit_percent=self.config.take_profit_percent,
            exit_hours=self.config.exit_hours,
        )

        if exit_reason is None:
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"해결까지: {hours_str})"
            )
            return False

        hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
        tp_target = take_profit_target(trade.buy_price, self.config.take_profit_percent)
        logger.info(
            f"청산 조건 충족 [{exit_reason}]: {condition_id} "
            f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
            f"익절 목표가: {tp_target:.2f}, 해결까지: {hours_str})"
        )

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) [사유: {exit_reason}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=trade.buy_shares,
            side="SELL",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Calculate P&L
            sell_value = current_price * trade.buy_shares
            buy_value = trade.buy_price * trade.buy_shares
            realized_pnl = sell_value - buy_value

            # Update trade record (청산 시점 캐리 재계산 = carry_yield_at_exit, §A)
            self.repo.update_trade(
                trade.id,
                sell_price=current_price,
                sell_shares=trade.buy_shares,
                sell_order_id=result.get("orderID"),
                sell_timestamp=datetime.utcnow(),
                sell_probability=current_price,
                realized_pnl=realized_pnl,
                status=TradeStatus.COMPLETED,
                exit_reason=exit_reason,
                carry_yield_at_exit=carry_yield(current_price, hours_left),
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

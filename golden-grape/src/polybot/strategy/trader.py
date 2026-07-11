"""Trading execution logic for the Cascade Rider strategy."""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import compute_death_drift, is_drift_dead, take_profit_target

logger = logging.getLogger(__name__)

# CLOB 매도 거절 사유가 "보유 토큰 잔고 0"인지 판별하는 패턴.
# GTC limit 매수는 접수 즉시 HOLDING으로 기록되지만(체결 가정), 실제로 체결되지
# 않은 유령 포지션은 매도 시 "not enough balance ... balance: 0"으로 거절된다.
# balance가 0이 아닌 거절(부분 체결/allowance 문제)은 유령이 아니므로 제외한다.
_ZERO_BALANCE_PATTERN = re.compile(r"not enough balance.*balance:\s*0(?:\D|$)")


def is_zero_balance_error(result: dict) -> bool:
    """매도 주문 실패가 '잔고 0(매수 미체결)' 때문인지 판별."""
    return bool(_ZERO_BALANCE_PATTERN.search(str(result.get("error", ""))))


# 회고 로깅용 봇 식별 상수 (교차 봇 UNION 쿼리 계약)
STRATEGY_NAME = "grape"

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# 해결된 시장 판정: end_date가 이 시간 이상 지났는데 orderbook이 없으면 EXPIRED
RESOLVED_GRACE_HOURS = 24


class Trader:
    """Executes buy and sell orders based on the Cascade Rider strategy."""

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
            mode: 회고 로깅용 실행 모드 - "live" 또는 "sim" (config.simulation_mode 기준)
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = mode

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary (scanner 참고)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: Reentry block (HOLDING 또는 쿨다운)
        block = self.repo.get_reentry_block(
            condition_id, self.config.reentry_cooldown_hours
        )
        if block:
            logger.info(f"재진입 차단({block}) - skip: {condition_id}")
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

        # Check: Price jumped above band?
        if current_price > self.config.cascade.prob_max:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 밴드 상한 {self.config.cascade.prob_max:.1%})"
            )
            # 쿨다운 기반 skip (영구 차단 아님 - 쿨다운 후 재평가)
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # Check: Price dropped below band?
        if current_price < self.config.cascade.prob_min:
            logger.info(
                f"가격 하락으로 매수 조건 미충족: {condition_id} "
                f"(가격: {current_price:.1%} < 밴드 하한 {self.config.cascade.prob_min:.1%})"
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
            f"[사유: {entry_reason}, 드리프트: {candidate.get('drift', 0):+.3f}, "
            f"해결까지 {hours_until_resolution:.1f}h]"
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
            # end_date는 naive UTC로 저장 (DB 규약)
            if end_date is not None and end_date.tzinfo is not None:
                end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)

            trade = self.repo.create_trade(
                condition_id=condition_id,
                market_slug=candidate["market_slug"],
                question=candidate["question"],
                outcome=candidate["outcome"],
                token_index=candidate.get("token_index"),
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
                # Cascade rider strategy fields
                entry_reason=entry_reason,
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
                drift_at_buy=candidate.get("drift"),
                consistency_at_buy=candidate.get("consistency"),
                vol_accel_at_buy=candidate.get("vol_accel"),
                # 회고 로깅 (A/B 포스트모템 계약)
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
                volume_24h_at_buy=candidate.get("volume_24h"),
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _handle_midpoint_failure(self, trade) -> None:
        """midpoint 조회 실패 처리: 해결된 시장 leak 수정 (§EXPIRED).

        market_end_date가 24시간 이상 지났으면 EXPIRED로 마감 처리하고
        수동 redeem 필요 경고를 남긴다. realized_pnl은 NULL 유지.
        """
        end_date = trade.market_end_date
        if end_date is None:
            return

        if end_date.tzinfo is not None:
            end_date = end_date.replace(tzinfo=None)

        if datetime.utcnow() - end_date >= timedelta(hours=RESOLVED_GRACE_HOURS):
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.EXPIRED,
                exit_reason="resolved_unredeemed",
            )
            logger.warning(
                f"해결된 시장 미청산 - EXPIRED 처리: Trade #{trade.id} "
                f"'{trade.question[:50]}...' (종료: {end_date.isoformat()}). "
                f"수동 redeem 필요 - realized_pnl은 기록되지 않음."
            )

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순):
        1. 손절: P&L <= -8%
        2. 익절: 목표가 도달 (buy_price*(1+15%), 0.99 캡)
        3. 드리프트 소멸: 최근 6h 매수 토큰 가격 변화 <= 0
        4. 트레일링 스탑: 최고점 대비 -6%
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
            self._handle_midpoint_failure(trade)
            return False

        # midpoint 0.0(falsy 응답)을 실가격으로 취급하면 P&L -100% 오판 → 0.01 투매가 발생한다
        if current_price <= 0:
            logger.warning(f"가격 조회 실패(0 반환) - condition: {condition_id}")
            self._handle_midpoint_failure(trade)
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

        # 최근 6h 스냅샷: drift_death 판정(아래 3번)과 회고용 drift_at_exit 기록에 공용
        death_hours = self.config.cascade.death_window_hours
        since = datetime.utcnow() - timedelta(hours=death_hours)
        snapshots = self.repo.get_snapshots_since(condition_id, since)
        token_index = trade.token_index if trade.token_index is not None else 0
        death_min_points = self.config.cascade.death_window_min_points
        death_min_coverage = self.config.cascade.death_window_min_coverage
        drift_at_exit = compute_death_drift(
            snapshots,
            token_index,
            death_hours,
            min_points=death_min_points,
            min_coverage=death_min_coverage,
        )

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

        # 2. Take Profit: 목표가 도달 (0.99 캡 - TP 도달 불가 버그 수정)
        if not should_sell:
            tp_target = take_profit_target(
                trade.buy_price, self.config.take_profit_percent
            )
            if current_price >= tp_target:
                should_sell = True
                exit_reason = "take_profit"
                logger.info(
                    f"익절 조건 충족 - 매도: {condition_id} "
                    f"(현재가: {current_price:.2%} >= 목표가: {tp_target:.2%})"
                )

        # 3. Drift Death: 최근 6h 매수 토큰 기준 변화 <= 0
        if not should_sell:
            dead = is_drift_dead(
                snapshots,
                token_index,
                death_hours,
                min_points=death_min_points,
                min_coverage=death_min_coverage,
            )
            if dead:
                should_sell = True
                exit_reason = "drift_death"
                logger.info(
                    f"드리프트 소멸 - 매도: {condition_id} "
                    f"(최근 {death_hours}h 변화 <= 0)"
                )

        # 4. Trailing Stop: current_price < max_price * (1 - trailing_percent)
        if not should_sell and self.config.trailing_stop.enabled and max_price > 0:
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

        # 5. Time-based Exit: hours_until_resolution < exit_hours
        if not should_sell:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            if hours_left is not None and hours_left < self.config.exit_hours:
                should_sell = True
                exit_reason = "time_exit"
                logger.info(
                    f"시간 기반 청산 - 매도: {condition_id} "
                    f"(해결까지: {hours_left:.1f}h < {self.config.exit_hours}h)"
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

            # Update trade record
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
                drift_at_exit=drift_at_exit,
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
            cancel_result = self.clob.cancel_order(trade.buy_order_id)
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

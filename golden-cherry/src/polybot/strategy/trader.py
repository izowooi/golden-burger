"""Trading execution logic with resolution momentum strategy."""
import logging
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional
from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)

from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import (
    evaluate_game_start,
    get_hours_until_resolution,
    is_valid_time_entry,
)

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
_GENERIC_SELL_RETRY_FACTOR = 0.99


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


def floor_clob_shares(value: float, *, reserve_micro_shares: int = 0) -> float:
    """Floor a share quantity to the CLOB's six-decimal integer scale."""
    raw_units = int(
        (Decimal(str(value)) * _CLOB_QUANTITY_SCALE).to_integral_value(
            rounding=ROUND_DOWN
        )
    )
    raw_units = max(0, raw_units - reserve_micro_shares)
    return raw_units / _CLOB_QUANTITY_SCALE

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0


def _utcnow_naive() -> datetime:
    """Return explicit UTC with the legacy SQLite-naive representation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        self.buys_placed_this_cycle = 0

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

        if self.buys_placed_this_cycle >= self.config.max_new_positions_per_cycle:
            logger.warning(
                "cycle 신규 포지션 상한(%d)에 도달해 남은 매수를 중단합니다",
                self.config.max_new_positions_per_cycle,
            )
            self.buying_disabled = True
            return None

        # Check: Already traded?
        if self.repo.is_already_traded(condition_id):
            logger.info(f"이미 거래한 시장: {condition_id}")
            return None

        # Check: finite position and requested-notional exposure limits. The
        # repository counts quarantined/pending rows conservatively because
        # those positions may still exist at the venue.
        current_positions = self.repo.get_position_count()
        if current_positions >= self.config.max_positions:
            logger.warning(f"최대 포지션 수 ({self.config.max_positions}) 도달")
            return None
        open_notional = self.repo.get_open_notional_usdc()
        projected_notional = open_notional + self.config.buy_amount_usdc
        if projected_notional > self.config.max_open_notional_usdc + 1e-9:
            logger.warning(
                "오픈 매수원금 상한 초과로 신규 매수 차단 - "
                "현재=$%.2f, 주문=$%.2f, 상한=$%.2f",
                open_notional,
                self.config.buy_amount_usdc,
                self.config.max_open_notional_usdc,
            )
            return None

        liquidity = float(candidate.get("liquidity") or 0)
        required_liquidity = self.config.effective_min_liquidity
        if not math.isfinite(liquidity) or liquidity < required_liquidity:
            logger.warning(
                "주문금액 대비 유동성 부족으로 매수 차단 - "
                "condition=%s liquidity=$%.2f required=$%.2f",
                condition_id,
                liquidity,
                required_liquidity,
            )
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

        # The keyset sweep can take long enough for a game to move from pregame
        # to in-play. Re-evaluate immediately before the order POST.
        game_start = evaluate_game_start(
            {
                "gameStartTime": candidate.get("game_start_time"),
                "sportsMarketType": candidate.get("sports_market_type"),
            },
            self.config.game_start,
        )
        if candidate.get("is_sports_timed") and not game_start.valid:
            logger.warning(
                "경기 상태 재검증 실패로 매수 차단 - condition=%s reason=%s",
                condition_id,
                game_start.reason,
            )
            return None

        entry_reason = candidate.get("entry_reason", "unknown")
        end_date = candidate.get("end_date")
        entry_time_reference = candidate.get("entry_time_reference", "end_date")
        entry_deadline = (
            game_start.game_start_time
            if entry_time_reference == "game_start_time"
            else end_date
        )
        hours_until_resolution = get_hours_until_resolution(end_date)
        entry_hours_left = get_hours_until_resolution(entry_deadline)
        if game_start.phase == "in_play":
            entry_hours_left = (
                game_start.minutes_until_game_start / 60
                if game_start.minutes_until_game_start is not None
                else None
            )
            entry_reason = game_start.reason
        elif self.config.time_based.enabled:
            still_valid, timing_reason, entry_hours_left = is_valid_time_entry(
                entry_deadline,
                self.config.time_based.entry_hours_max,
                self.config.time_based.entry_hours_min,
                (
                    0
                    if entry_time_reference == "game_start_time"
                    else self.config.time_based.exit_hours
                ),
            )
            if not still_valid:
                logger.warning(
                    "주문 직전 진입 기준시각 재검증 실패로 매수 차단 - "
                    "condition=%s reference=%s reason=%s",
                    condition_id,
                    entry_time_reference,
                    timing_reason,
                )
                return None
        if game_start.phase == "in_play" and game_start.minutes_until_game_start is not None:
            hours_text = f"경기 시작 후 {abs(game_start.minutes_until_game_start):.1f}m"
        else:
            hours_text = (
                f"기준시각까지 {entry_hours_left:.1f}h"
                if entry_hours_left is not None
                else "N/A"
            )

        # Place order
        logger.info(
            f"매수: {candidate['outcome']} - '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f}주, ${self.config.buy_amount_usdc}) "
            f"[사유: {entry_reason}, 기준={entry_time_reference}, 시간={hours_text}]"
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
                buy_timestamp=_utcnow_naive(),
                buy_probability=current_price,
                liquidity_at_buy=candidate["liquidity"],
                market_tags=candidate.get("market_tags", ""),
                status=TradeStatus.HOLDING,
                # Resolution momentum strategy fields
                entry_reason=entry_reason,
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
                market_game_start_time=game_start.game_start_time,
                minutes_until_game_start_at_buy=game_start.minutes_until_game_start,
                entry_time_reference=entry_time_reference,
                hours_until_entry_deadline_at_buy=entry_hours_left,
                sports_market_type=game_start.sports_market_type,
                sports_phase_at_buy=game_start.phase,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            self.buys_placed_this_cycle += 1
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
    ) -> tuple[dict, float, float]:
        """Clamp SELL to live token balance and retry one stale-balance reject.

        Returns ``(result, submitted_size, unsold_size)``. The initial request
        uses the smaller of the DB quantity and the authenticated CLOB balance.
        If CLOB still reports a smaller integer balance, the retry reserves one
        micro-share rather than discarding a fixed 1% of a potentially large
        position. A generic balance-cache rejection retains a one-time 99%
        compatibility fallback; any sellable remainder stays open.
        """
        available_shares = None
        try:
            available_shares = self.clob.get_conditional_token_balance(token_id)
        except Exception as error:
            logger.warning(
                "SELL token 잔고 preflight 실패 - DB 수량으로 시도 후 "
                "CLOB 거절 응답을 사용합니다: token=%s error=%s",
                token_id,
                type(error).__name__,
            )

        sell_basis = requested_size
        initial_size = floor_clob_shares(requested_size)
        if available_shares is not None and available_shares > 0:
            sell_basis = min(requested_size, available_shares)
            initial_size = floor_clob_shares(sell_basis)
            if initial_size < requested_size:
                logger.warning(
                    "DB 수량을 실제 CLOB token 잔고로 선제 보정합니다 - "
                    "requested=%.6f available=%.6f submitted=%.6f",
                    requested_size,
                    available_shares,
                    initial_size,
                )

        # A sub-minimum preflight balance is not enough evidence to rewrite the
        # trade lifecycle. Send the DB size once so the venue can return its
        # explicit order error and the existing zero-balance proof path remains.
        if initial_size < MIN_ORDER_SIZE:
            initial_size = floor_clob_shares(requested_size)
            sell_basis = requested_size

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=initial_size,
            side="SELL",
        )
        if result.get("success") or result.get("orderID"):
            return result, initial_size, max(0.0, sell_basis - initial_size)

        reported_shares = available_shares_from_error(result)
        retry_basis = initial_size
        if reported_shares == 0:
            return result, initial_size, sell_basis
        if reported_shares is not None and 0 < reported_shares <= initial_size:
            retry_basis = reported_shares
            retry_size = floor_clob_shares(
                reported_shares,
                reserve_micro_shares=1,
            )
            retry_reason = "CLOB reported balance minus one micro-share"
        elif reported_shares is not None:
            return result, initial_size, sell_basis
        elif is_balance_allowance_error(result):
            retry_size = floor_clob_shares(
                initial_size * _GENERIC_SELL_RETRY_FACTOR
            )
            retry_reason = "generic balance-cache 99% fallback"
        else:
            return result, initial_size, sell_basis

        if retry_size < MIN_ORDER_SIZE:
            logger.warning(
                "부분 체결 잔고가 최소 주문량보다 작아 매도 보류 - "
                "token=%s available=%s",
                token_id,
                f"{reported_shares:.6f}" if reported_shares is not None else "unknown",
            )
            return result, initial_size, sell_basis

        logger.warning(
            "SELL 수량을 보정해 한 번 재시도합니다 - reason=%s "
            "requested=%.6f first=%.6f reported=%s retry=%.6f",
            retry_reason,
            requested_size,
            initial_size,
            f"{reported_shares:.6f}" if reported_shares is not None else "unknown",
            retry_size,
        )
        retry_result = self.clob.place_limit_order(
            token_id=token_id,
            price=price,
            size=retry_size,
            side="SELL",
        )
        unsold_size = max(0.0, retry_basis - retry_size)
        return retry_result, retry_size, unsold_size

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

        result, sell_shares, unsold_shares = self._place_sell_with_balance_retry(
            token_id=token_id,
            price=current_price,
            requested_size=trade.buy_shares,
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Calculate P&L
            sell_value = current_price * sell_shares
            buy_value = trade.buy_price * sell_shares
            partial_pnl = sell_value - buy_value
            previous_realized_pnl = float(
                getattr(trade, "realized_pnl", None) or 0.0
            )
            realized_pnl = previous_realized_pnl + partial_pnl
            previous_sell_shares = float(
                getattr(trade, "sell_shares", None) or 0.0
            )
            cumulative_sell_shares = previous_sell_shares + sell_shares

            # A one-micro-share rounding reserve is economically unsellable and
            # does not justify an endless stop-loss retry loop. A sellable 99%
            # fallback remainder, however, stays HOLDING for the next cycle.
            completed = unsold_shares < MIN_ORDER_SIZE
            next_status = TradeStatus.COMPLETED if completed else TradeStatus.HOLDING
            next_exit_reason = (
                exit_reason if completed else f"partial_{exit_reason}"
            )

            # Update trade record
            update_fields = dict(
                sell_price=current_price,
                sell_shares=cumulative_sell_shares,
                sell_order_id=result.get("orderID"),
                sell_timestamp=_utcnow_naive(),
                sell_probability=current_price,
                realized_pnl=realized_pnl,
                status=next_status,
                exit_reason=next_exit_reason,
            )
            if not completed:
                update_fields["buy_shares"] = unsold_shares
            self.repo.update_trade(trade.id, **update_fields)

            pnl_percent_display = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            if completed:
                if unsold_shares > 0:
                    logger.warning(
                        "최소 주문량 미만 잔여 %.6f주는 dust로 남기고 청산 처리합니다",
                        unsold_shares,
                    )
                logger.info(
                    f"매도 주문 완료: Trade #{trade.id}, "
                    f"P&L: ${realized_pnl:.4f} ({pnl_percent_display:.1f}%), "
                    f"사유: {exit_reason}"
                )
                return True

            logger.warning(
                "부분 매도 접수: Trade #%s, sold=%.6f, remaining=%.6f. "
                "다음 cycle에서 잔여 수량을 다시 청산합니다",
                trade.id,
                sell_shares,
                unsold_shares,
            )
            return False
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

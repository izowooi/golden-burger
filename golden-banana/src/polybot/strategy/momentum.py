"""모멘텀 계산 및 시그널 감지 모듈.

이 모듈은 가격 스냅샷을 기반으로 모멘텀을 계산하고,
골든크로스/데드크로스 시그널을 감지합니다.

모멘텀 = (최신 확률 - 가장 오래된 확률) / 스냅샷 수

골든크로스: 단기 모멘텀 - 장기 모멘텀 >= threshold (진입 시그널)
데드크로스: 단기 모멘텀 - 장기 모멘텀 <= -threshold (청산 시그널)
"""
import logging
from typing import List, Optional, Tuple
from ..db.models import MarketSnapshot
from ..config import MomentumConfig

logger = logging.getLogger(__name__)


class MomentumCalculator:
    """마켓 모멘텀 계산기.

    스냅샷 데이터를 기반으로 단기/장기 모멘텀을 계산하고,
    골든크로스/데드크로스 시그널을 감지합니다.
    """

    def __init__(self, config: MomentumConfig):
        """초기화.

        Args:
            config: 모멘텀 설정 (short_window, long_window, thresholds)
        """
        self.config = config

    def calculate_momentum(self, snapshots: List[MarketSnapshot]) -> Optional[float]:
        """스냅샷 리스트로부터 모멘텀 계산.

        모멘텀 = (최신 확률 - 가장 오래된 확률) / 스냅샷 수

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)

        Returns:
            모멘텀 값 (양수: 상승, 음수: 하락) 또는 데이터 부족 시 None
        """
        if not snapshots or len(snapshots) < 2:
            return None

        oldest = snapshots[0].probability
        newest = snapshots[-1].probability

        # 0으로 나누기 방지
        if len(snapshots) == 0:
            return None

        return (newest - oldest) / len(snapshots)

    def get_short_momentum(
        self,
        snapshots: List[MarketSnapshot]
    ) -> Optional[float]:
        """단기(15분) 모멘텀 계산.

        최근 short_window 개 스냅샷 사용.

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트

        Returns:
            단기 모멘텀 값 또는 None
        """
        short_window = self.config.short_window
        if len(snapshots) < short_window:
            return None
        return self.calculate_momentum(snapshots[-short_window:])

    def get_long_momentum(
        self,
        snapshots: List[MarketSnapshot]
    ) -> Optional[float]:
        """장기(6시간) 모멘텀 계산.

        최근 long_window 개 스냅샷 사용.
        데이터 부족 시 가능한 만큼 사용.

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트

        Returns:
            장기 모멘텀 값 또는 None
        """
        long_window = self.config.long_window
        if len(snapshots) < long_window:
            # 데이터 부족 시 가능한 만큼 사용 (최소 단기 윈도우의 2배)
            min_required = self.config.short_window * 2
            if len(snapshots) >= min_required:
                return self.calculate_momentum(snapshots)
            return None
        return self.calculate_momentum(snapshots[-long_window:])

    def detect_golden_cross(
        self,
        short_momentum: Optional[float],
        long_momentum: Optional[float]
    ) -> bool:
        """골든크로스 감지: 단기 모멘텀이 장기 모멘텀을 의미있게 앞지름.

        조건: short_momentum - long_momentum >= threshold

        Args:
            short_momentum: 단기 모멘텀
            long_momentum: 장기 모멘텀

        Returns:
            골든크로스 여부
        """
        if short_momentum is None or long_momentum is None:
            return False

        diff = short_momentum - long_momentum
        return diff >= self.config.golden_cross_threshold

    def detect_dead_cross(
        self,
        short_momentum: Optional[float],
        long_momentum: Optional[float]
    ) -> bool:
        """데드크로스 감지: 장기 모멘텀이 단기 모멘텀을 의미있게 앞지름.

        조건: short_momentum - long_momentum <= -threshold

        Args:
            short_momentum: 단기 모멘텀
            long_momentum: 장기 모멘텀

        Returns:
            데드크로스 여부
        """
        if short_momentum is None or long_momentum is None:
            return False

        diff = short_momentum - long_momentum
        return diff <= self.config.dead_cross_threshold

    def get_entry_signal(
        self,
        snapshots: List[MarketSnapshot],
        current_probability: float
    ) -> Tuple[bool, str]:
        """진입 시그널 판단.

        1. 모멘텀 비활성화 시: 무조건 진입 허용
        2. 장기 데이터 부족 시: 단기 모멘텀이 양수면 진입
        3. 골든크로스 발생 시: 진입

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트
            current_probability: 현재 확률

        Returns:
            (진입 여부, 사유)
        """
        if not self.config.enabled:
            return True, "momentum_disabled"

        short_mom = self.get_short_momentum(snapshots)
        long_mom = self.get_long_momentum(snapshots)

        # 단기 데이터도 부족한 경우
        if short_mom is None:
            logger.debug(f"단기 모멘텀 데이터 부족 (스냅샷 {len(snapshots)}개)")
            return False, "insufficient_short_data"

        # 장기 데이터 부족 시 단기 모멘텀으로만 판단
        if long_mom is None:
            if short_mom > 0:
                logger.debug(f"장기 데이터 부족, 단기 모멘텀 양수: {short_mom:.6f}")
                return True, "short_momentum_positive"
            logger.debug(f"장기 데이터 부족, 단기 모멘텀 음수: {short_mom:.6f}")
            return False, "short_momentum_negative"

        # 골든크로스 확인
        if self.detect_golden_cross(short_mom, long_mom):
            logger.debug(
                f"골든크로스 감지 - 단기: {short_mom:.6f}, 장기: {long_mom:.6f}, "
                f"차이: {short_mom - long_mom:.6f}"
            )
            return True, "golden_cross"

        logger.debug(
            f"진입 조건 미충족 - 단기: {short_mom:.6f}, 장기: {long_mom:.6f}, "
            f"차이: {short_mom - long_mom:.6f} (필요: >= {self.config.golden_cross_threshold})"
        )
        return False, "no_signal"

    def get_exit_signal(
        self,
        snapshots: List[MarketSnapshot],
        entry_price: float,
        current_price: float,
        take_profit: float,
        stop_loss: float
    ) -> Tuple[bool, str]:
        """청산 시그널 판단.

        우선순위:
        1. 손절: 진입가 대비 손실이 stop_loss 이하
        2. 이익실현: 진입가 대비 수익이 take_profit 이상
        3. 데드크로스: 모멘텀 역전

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트
            entry_price: 진입가
            current_price: 현재가
            take_profit: 이익실현 임계값 (예: 0.07 = +7%)
            stop_loss: 손절 임계값 (예: -0.10 = -10%)

        Returns:
            (청산 여부, 사유)
        """
        # P&L 계산
        if entry_price == 0:
            pnl_percent = 0
        else:
            pnl_percent = (current_price - entry_price) / entry_price

        # 1. 손절 체크
        if pnl_percent <= stop_loss:
            logger.info(
                f"손절 조건 충족 - 진입: {entry_price:.2%}, 현재: {current_price:.2%}, "
                f"손실: {pnl_percent:.2%}"
            )
            return True, "stop_loss"

        # 2. 이익실현 체크
        if pnl_percent >= take_profit:
            logger.info(
                f"이익실현 조건 충족 - 진입: {entry_price:.2%}, 현재: {current_price:.2%}, "
                f"수익: {pnl_percent:.2%}"
            )
            return True, "take_profit"

        # 3. 모멘텀 청산 체크
        if self.config.enabled:
            short_mom = self.get_short_momentum(snapshots)
            long_mom = self.get_long_momentum(snapshots)

            if short_mom is not None and long_mom is not None:
                if self.detect_dead_cross(short_mom, long_mom):
                    logger.info(
                        f"데드크로스 감지 - 단기: {short_mom:.6f}, 장기: {long_mom:.6f}"
                    )
                    return True, "dead_cross"

        return False, "hold"

    def get_momentum_info(
        self,
        snapshots: List[MarketSnapshot]
    ) -> Tuple[Optional[float], Optional[float]]:
        """현재 모멘텀 정보 조회.

        Args:
            snapshots: 시간순 정렬된 스냅샷 리스트

        Returns:
            (단기 모멘텀, 장기 모멘텀)
        """
        return (
            self.get_short_momentum(snapshots),
            self.get_long_momentum(snapshots)
        )

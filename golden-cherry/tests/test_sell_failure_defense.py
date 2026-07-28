"""매도 무한 재시도 방어 — 거절 사유 분류와 축소 재시도.

배경: golden-cherry 실측(2026-07-11~28) 실패한 SELL 제출 73,238건 / 401 token
= 토큰당 평균 182.6회, 최대 4,002회. 영구 실패를 재시도 가능으로 오분류하고
실패 분기가 trade 상태를 바꾸지 않아 매 사이클 같은 주문을 반복 제출했다.
"""
from polybot.strategy.trader import (
    available_shares_from_error,
    classify_sell_failure,
)


def _err(msg):
    return {"success": False, "error": msg}


# 실제 CLOB 거절 형식 (golden-cherry order_submissions에서 확인)
NOT_ENOUGH = (
    "PolyApiException[status_code=400, error_message={'error': 'not enough "
    "balance / allowance: the balance is not enough -> balance: %s, "
    "order amount: %s'}]"
)


def test_available_shares_parses_micro_share_scale():
    """CLOB은 잔고를 1e6 배 정수로 준다. 48400000 = 48.4주."""
    assert available_shares_from_error(_err(NOT_ENOUGH % ("48400000", "8839780000"))) == 48.4
    assert available_shares_from_error(_err("no balance here")) is None


def test_market_gone_is_permanent():
    """토큰/오더북이 사라진 거절은 재시도해도 절대 성공하지 않는다."""
    gone = _err("PolyApiException[status_code=400, error_message={'error': 'invalid token id'}]")
    assert classify_sell_failure(gone, 100.0) == "market_gone"


def test_dust_below_minimum_is_permanent():
    """가용 잔고가 최소 주문량 미만이면 영구히 팔 수 없다 - 재시도 금지 대상."""
    dust = _err(NOT_ENOUGH % ("3000000", "100000000"))          # 3.0주 < 5주
    assert classify_sell_failure(dust, 100.0, 5.0) == "dust_unsellable"


def test_partial_balance_is_retryable():
    """부분 체결로 잔고가 요청보다 작은 경우 - 수량을 줄이면 팔린다."""
    partial = _err(NOT_ENOUGH % ("48400000", "8839780000"))      # 48.4주
    assert classify_sell_failure(partial, 8839.78, 5.0) == "partial_balance"


def test_balance_edge_when_available_equals_requested():
    """잔고 >= 요청인데 거절 - 반올림 여유 부족. 이것도 축소하면 팔린다.

    golden-cherry에서 같은 토큰이 이 사유로 1,469회 거절됐다.
    """
    edge = _err(NOT_ENOUGH % ("9248547141", "9248550000"))       # 9248.547141주
    assert classify_sell_failure(edge, 9248.547141, 5.0) == "balance_edge"


def test_zero_balance_still_routes_to_ghost_path():
    """잔고 0은 기존 유령 판정 경로가 처리한다 - 분류만 확인."""
    zero = _err(NOT_ENOUGH % ("0", "10000000"))
    assert classify_sell_failure(zero, 10.0) == "zero_balance"


def test_transient_errors_are_not_permanent():
    ready = _err("PolyApiException[status_code=425, error_message={'error': 'order manager not ready, please retry'}]")
    assert classify_sell_failure(ready, 10.0) == "transient"

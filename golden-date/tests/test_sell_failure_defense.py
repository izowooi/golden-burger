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


def test_sell_retry_shrinks_to_available_and_attempts_only_once():
    """가용 잔고의 99%로 한 번만 재시도한다.

    99%인 이유: 잔고와 정확히 같은 수량은 거래소가 반올림 여유 부족으로 거절한다.
    1회로 제한하는 이유: 무한 재시도가 원래 문제였다.
    """
    from polybot.strategy import trader as trader_mod

    calls = []

    class FakeClob:
        def place_limit_order(self, *, token_id, price, size, side):
            calls.append(size)
            if len(calls) == 1:
                return {"success": False, "error": NOT_ENOUGH % ("48400000", "8839780000")}
            return {"success": True, "orderID": "0xabc"}

    obj = trader_mod.Trader.__new__(trader_mod.Trader)
    obj.clob = FakeClob()
    result, sold = obj._place_sell_with_balance_retry(
        token_id="t", price=0.5, requested_size=8839.78
    )
    assert result.get("orderID") == "0xabc"
    assert len(calls) == 2, "재시도는 정확히 1회여야 한다"
    assert calls[0] == 8839.78
    assert abs(sold - 47.916) < 1e-6, f"48.4 x 0.99 = 47.916, got {sold}"


def test_sell_retry_refuses_when_available_is_dust():
    """가용 잔고가 최소 주문량 미만이면 재시도하지 않는다 (팔 수 없으므로)."""
    from polybot.strategy import trader as trader_mod

    calls = []

    class FakeClob:
        def place_limit_order(self, *, token_id, price, size, side):
            calls.append(size)
            return {"success": False, "error": NOT_ENOUGH % ("3000000", "100000000")}

    obj = trader_mod.Trader.__new__(trader_mod.Trader)
    obj.clob = FakeClob()
    result, sold = obj._place_sell_with_balance_retry(
        token_id="t", price=0.5, requested_size=100.0
    )
    assert len(calls) == 1, "먼지 잔고에는 재시도하지 않아야 한다"
    assert sold == 100.0


def test_locked_in_own_orders_is_not_retryable():
    """잔고 전액이 자기 미체결 주문에 묶인 경우 - 수량 축소로 해결되지 않는다.

    2026-07-28 honeydew 실측: balance 8.86 / sum of active orders 8.86.
    축소 재시도(8.77주)도 같은 사유로 거절됐다. 기존 주문 취소가 선행되어야 한다.
    """
    from polybot.strategy.trader import locked_in_own_orders

    locked = _err(
        "PolyApiException[status_code=400, error_message={'error': 'not enough "
        "balance / allowance: the balance is not enough -> balance: 8860000, "
        "sum of active orders: 8860000, sum of matched orders: 0, "
        "order amount (inc. fees): 8770000'}]"
    )
    assert locked_in_own_orders(locked) is True
    assert classify_sell_failure(locked, 10.752688) == "locked_in_own_orders"
    # 형식 2도 잔고 파싱은 되어야 한다 (balance_unparsed로 새지 않도록)
    assert available_shares_from_error(locked) == 8.86

    plain = _err(NOT_ENOUGH % ("48400000", "8839780000"))
    assert locked_in_own_orders(plain) is False

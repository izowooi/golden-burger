import logging

import pytest

from polybot_observability import log_reconciliation_continuity


def test_per_order_errors_warn_and_continue(caplog):
    logger = logging.getLogger("test.reconciliation.continuity")

    result = log_reconciliation_continuity({"errors": 8}, logger=logger)

    assert result == 8
    assert "해당 token/side 신규 주문만 격리" in caplog.text
    assert "trading cycle을 계속" in caplog.text


def test_zero_errors_do_not_warn(caplog):
    logger = logging.getLogger("test.reconciliation.clean")

    assert log_reconciliation_continuity({}, logger=logger) == 0

    assert caplog.text == ""


@pytest.mark.parametrize("value", [True, -1, 1.0, "1", None])
def test_malformed_error_count_fails_closed(value):
    logger = logging.getLogger("test.reconciliation.invalid")

    with pytest.raises(ValueError, match="order reconciliation errors"):
        log_reconciliation_continuity({"errors": value}, logger=logger)

"""Runtime policy for per-order reconciliation evidence gaps."""

from __future__ import annotations

import logging
from typing import Any, Mapping


def log_reconciliation_continuity(
    reconciliation: Mapping[str, Any], *, logger: logging.Logger
) -> int:
    """Warn on isolated order errors while rejecting malformed statistics.

    ``ExecutionLedger.assert_submission_allowed`` enforces the matching
    ``token_id × side`` quarantine.  This function deliberately does not turn
    a per-order evidence gap into a global cycle gate.  Failures that escape
    ``reconcile_order_ledger`` itself still propagate and fail the cycle.
    """
    error_count = reconciliation.get("errors", 0)
    if isinstance(error_count, bool) or not isinstance(error_count, int):
        raise ValueError("order reconciliation errors must be an integer")
    if error_count < 0:
        raise ValueError("order reconciliation errors must be nonnegative")
    if error_count:
        logger.warning(
            "미완료 CLOB 주문 대사 실패 %s건은 해당 token/side 신규 "
            "주문만 격리하고 trading cycle을 계속합니다",
            error_count,
        )
    return error_count

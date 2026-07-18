"""Shared observability primitives for Polymarket strategy bots."""

from .run_audit import RunAudit, current_run_id
from .reconciliation_policy import log_reconciliation_continuity
from .execution_ledger import (
    ClobReconciliationPhaseError,
    ClobResponseContractError,
    ClobResponseUnavailableError,
    ExecutionLedger,
    SubmissionEvidenceError,
    UnresolvedSubmissionOutcomeError,
    UnresolvedTokenSubmissionError,
    normalize_clob_response,
    normalize_clob_response_list,
    safe_clob_response_shape,
)

__all__ = [
    "ClobReconciliationPhaseError",
    "ClobResponseContractError",
    "ClobResponseUnavailableError",
    "ExecutionLedger",
    "RunAudit",
    "SubmissionEvidenceError",
    "UnresolvedSubmissionOutcomeError",
    "UnresolvedTokenSubmissionError",
    "current_run_id",
    "log_reconciliation_continuity",
    "normalize_clob_response",
    "normalize_clob_response_list",
    "safe_clob_response_shape",
]

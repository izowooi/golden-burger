"""Append-only order/trade reconciliation ledger for CLOB execution evidence."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .run_audit import _safe_error_message, current_run_id

_TERMINAL_ORDER_STATUSES = {
    "MATCHED",
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}
_TERMINAL_TRADE_STATUSES = {"CONFIRMED", "FAILED"}
_FIXED_6_SCALE = 1_000_000
_QUANTITY_TOLERANCE = 0.000001
_REPAIRABLE_QUANTITY_SCALE_FILL_ERRORS = {"quantity_scale_missing"}
CATALOG_GAP_CONFIRMATION_TEMPLATE = "ACKNOWLEDGE_{count}_CLOB_EVIDENCE_GAPS"
LINKED_CATALOG_GAP_CONFIRMATION_TEMPLATE = (
    "ACKNOWLEDGE_{count}_CLOB_EVIDENCE_GAPS_WITH_LINKED_EVIDENCE"
)
QUANTITY_SCALE_REPAIR_CONFIRMATION_TEMPLATE = (
    "REPAIR_{count}_CLOB_QUANTITIES_X1000000"
)
_MISSING = object()
_MAX_RESPONSE_JSON_LENGTH = 1_000_000
_MAX_RESPONSE_UNWRAP_DEPTH = 4
_RESPONSE_ENVELOPE_KEYS = ("order", "data", "result", "root", "__root__")

# Only fields needed for execution evidence are copied out of SDK response
# models.  In particular, we deliberately do not persist or log ``__dict__``
# wholesale because future SDK models could carry credentials or signer state.
_CLOB_RESPONSE_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "submission": {
        "success": ("success",),
        "orderID": ("orderID", "order_id", "orderId"),
        "status": ("status",),
        "makingAmount": ("makingAmount", "making_amount"),
        "takingAmount": ("takingAmount", "taking_amount"),
        "tradeIDs": ("tradeIDs", "trade_ids", "tradeIds"),
        "error": ("error",),
        "errorMsg": ("errorMsg", "error_msg"),
    },
    "order": {
        "id": ("id", "orderID", "order_id", "orderId"),
        "status": ("status",),
        "associate_trades": (
            "associate_trades",
            "associated_trades",
            "associateTrades",
            "associatedTrades",
        ),
        "original_size": ("original_size", "originalSize"),
        "size_matched": ("size_matched", "sizeMatched"),
        "price": ("price",),
    },
    "trade": {
        "id": ("id", "trade_id", "tradeId"),
        "status": ("status",),
        "maker_orders": ("maker_orders", "makerOrders"),
        "taker_order_id": ("taker_order_id", "takerOrderId"),
        "trader_side": ("trader_side", "traderSide"),
        "side": ("side",),
        "size": ("size",),
        "price": ("price",),
        "fee_rate_bps": ("fee_rate_bps", "feeRateBps"),
        "fee_amount_usdc": ("fee_amount_usdc", "feeAmountUsdc"),
        "fee_amount": ("fee_amount", "feeAmount"),
        "fee": ("fee",),
        "bucket_index": ("bucket_index", "bucketIndex"),
        "match_time": ("match_time", "matchTime", "matchtime", "timestamp"),
        "last_update": ("last_update", "lastUpdate"),
        "transaction_hash": ("transaction_hash", "transactionHash"),
    },
    "maker_order": {
        "order_id": ("order_id", "orderId"),
        "matched_amount": ("matched_amount", "matchedAmount"),
        "price": ("price",),
        "side": ("side",),
        "fee_rate_bps": ("fee_rate_bps", "feeRateBps"),
        "fee_amount_usdc": ("fee_amount_usdc", "feeAmountUsdc"),
        "fee_amount": ("fee_amount", "feeAmount"),
        "fee": ("fee",),
    },
    "cancellation": {
        "canceled": ("canceled", "cancelled"),
        "not_canceled": ("not_canceled", "notCanceled", "not_cancelled"),
    },
}


class SubmissionEvidenceError(RuntimeError):
    """Raised when an external order cannot be kept consistent with its ledger."""


class ClobResponseContractError(ValueError):
    """Raised when a decoded CLOB response is ambiguous or incomplete."""


class ClobResponseUnavailableError(ClobResponseContractError):
    """Raised when the venue returned no usable response object."""


class ClobReconciliationPhaseError(RuntimeError):
    """Secret-safe reconciliation failure with phase and response shape only."""

    def __init__(self, phase: str, error: BaseException, response_shape: str) -> None:
        self.phase = phase
        self.error_type = type(error).__name__
        self.response_shape = response_shape
        super().__init__(
            f"phase={phase} error={self.error_type} response_shape={response_shape}"
        )


class UnresolvedSubmissionOutcomeError(SubmissionEvidenceError):
    """Raised while a possible submitted order still lacks operator resolution."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            "결과가 불확실한 CLOB 주문 intent가 "
            f"{count}건 남아 있어 새 trading cycle을 중단합니다"
        )


class _OrderResponseContractError(SubmissionEvidenceError):
    """Internal marker: anomalous response was persisted successfully."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize_status(value: Any) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    for prefix in ("ORDER_STATUS_", "TRADE_STATUS_"):
        if status.startswith(prefix):
            status = status[len(prefix) :]
    return status


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_metadata_present(value: Any) -> bool:
    """Treat blank optional numeric API fields as missing, not malformed."""
    return value is not None and not (
        isinstance(value, str) and not value.strip()
    )


def _fixed_6_number(value: Any) -> float | None:
    """Decode CLOB v2 fixed-math amounts into human token/USDC units."""
    number = _number(value)
    return None if number is None else number / _FIXED_6_SCALE


def _quantity_number(value: Any, scale: float | None) -> float | None:
    number = _number(value)
    if number is None or scale not in {1.0, float(_FIXED_6_SCALE)}:
        return None
    return number / scale


def _infer_quantity_scale(raw_original_size: Any, requested_size: Any) -> float | None:
    """Distinguish raw fixed-6 responses from SDK-normalized human quantities."""
    raw = _number(raw_original_size)
    requested = _number(requested_size)
    if not _finite_positive(raw) or not _finite_positive(requested):
        return None
    candidates = (
        (1.0, raw),
        (float(_FIXED_6_SCALE), raw / _FIXED_6_SCALE),
    )
    scale, normalized = min(
        candidates,
        key=lambda candidate: abs(candidate[1] - requested) / requested,
    )
    relative_error = abs(normalized - requested) / requested
    return scale if relative_error <= 0.05 else None


def _infer_partial_quantity_scale(raw_size: Any, requested_size: Any) -> float | None:
    """Infer representation for a partial fill bounded by the requested size."""
    raw = _number(raw_size)
    requested = _number(requested_size)
    if not _finite_positive(raw) or not _finite_positive(requested):
        return None
    if raw > requested * 1_000:
        return float(_FIXED_6_SCALE)
    if raw <= requested * 1.05:
        return 1.0
    return None


def _bucket_index(value: Any) -> tuple[int, str | None]:
    if value is None:
        return 0, None
    parsed: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
    if parsed is None or parsed < 0:
        return -1, "bucket_index_invalid"
    return parsed, None


def _finite_positive(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and number > 0


def _finite_nonnegative(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and number >= 0


def _valid_fill_price(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and 0 < number < 1


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Return the first explicitly present value, preserving numeric zero."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    identifiers: list[str] = []
    for item in value:
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            if item != "":
                identifiers.append(str(item))
            continue
        item_mapping = _model_mapping(item)
        identifier = _lookup_model_value(
            item, item_mapping, ("id", "trade_id", "tradeId")
        )
        if identifier not in (_MISSING, None, ""):
            identifiers.append(str(identifier))
    return identifiers


def _model_dump(value: Any) -> Any:
    """Return a mapping/list representation from common SDK model APIs."""
    if isinstance(value, (Mapping, list, tuple)):
        return value
    for method_name in ("model_dump", "to_dict", "dict", "_asdict"):
        try:
            method = getattr(value, method_name)
        except Exception:
            continue
        if not callable(method):
            continue
        try:
            dumped = method()
        except Exception:
            continue
        if isinstance(dumped, (Mapping, list, tuple)):
            return dumped
    # Pydantic v1 custom-root models expose ``__root__`` even when ``dict()``
    # returns an envelope. Attribute access is allowlisted and never logged.
    try:
        root = getattr(value, "__root__")
    except Exception:
        return _MISSING
    return root if isinstance(root, (Mapping, list, tuple)) else _MISSING


def _model_mapping(value: Any) -> Mapping[str, Any] | None:
    """Expose a response model as a mapping without serializing arbitrary state."""
    dumped = _model_dump(value)
    return dumped if isinstance(dumped, Mapping) else None


def _lookup_model_value(
    value: Any,
    mapping: Mapping[str, Any] | None,
    aliases: tuple[str, ...],
) -> Any:
    if mapping is not None:
        for alias in aliases:
            if alias in mapping:
                return mapping[alias]
    for alias in aliases:
        try:
            return getattr(value, alias)
        except Exception:
            continue
    return _MISSING


def _known_response_keys() -> set[str]:
    return {
        alias
        for fields_by_type in _CLOB_RESPONSE_FIELDS.values()
        for aliases in fields_by_type.values()
        for alias in aliases
    } | set(_RESPONSE_ENVELOPE_KEYS) | {"trades", "results"}


def safe_clob_response_shape(value: Any) -> str:
    """Describe container shape without exposing response values or unknown keys."""
    if value is _MISSING:
        return "not_observed"
    if value is None:
        return "null"
    if isinstance(value, str):
        stripped = value.lstrip()
        json_like = bool(stripped) and stripped[0] in "[{\""
        return f"string(len={len(value)},json_like={str(json_like).lower()})"
    if isinstance(value, (list, tuple)):
        item_type = type(value[0]).__name__ if value else "none"
        return f"sequence(len={len(value)},item_type={item_type})"
    dumped = _model_dump(value)
    mapping = dumped if isinstance(dumped, Mapping) else None
    if mapping is not None:
        known = sorted(
            str(key) for key in mapping if str(key) in _known_response_keys()
        )
        known_label = ",".join(known) if known else "none"
        prefix = "mapping" if isinstance(value, Mapping) else "model_mapping"
        return f"{prefix}(count={len(mapping)},known={known_label})"
    if isinstance(dumped, (list, tuple)):
        return (
            "model_sequence("
            f"len={len(dumped)},item_type="
            f"{type(dumped[0]).__name__ if dumped else 'none'})"
        )
    known_attributes = sorted(
        alias
        for alias in _known_response_keys()
        if _lookup_model_value(value, None, (alias,)) is not _MISSING
    )
    known_label = ",".join(known_attributes) if known_attributes else "none"
    return f"object(type={type(value).__name__},known={known_label})"


def _decode_response_json(value: str, *, response_type: str) -> Any:
    if len(value) > _MAX_RESPONSE_JSON_LENGTH:
        raise ClobResponseContractError(
            f"CLOB {response_type} JSON string이 허용 길이를 초과했습니다"
        )
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response가 plain string입니다"
        ) from error
    if isinstance(decoded, str):
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response JSON이 scalar string입니다"
        )
    return decoded


def _has_known_field(
    mapping: Mapping[str, Any], field_aliases: Mapping[str, tuple[str, ...]]
) -> bool:
    return any(alias in mapping for aliases in field_aliases.values() for alias in aliases)


def _unwrap_single_response(
    value: Any, *, response_type: str, depth: int = 0, decoded_json: bool = False
) -> Any:
    if depth > _MAX_RESPONSE_UNWRAP_DEPTH:
        raise ClobResponseContractError(
            f"CLOB {response_type} response envelope가 너무 깊습니다"
        )
    if value is None:
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response가 null입니다"
        )
    if isinstance(value, str):
        if decoded_json:
            raise ClobResponseUnavailableError(
                f"CLOB {response_type} response가 scalar string입니다"
            )
        return _unwrap_single_response(
            _decode_response_json(value, response_type=response_type),
            response_type=response_type,
            depth=depth + 1,
            decoded_json=True,
        )

    dumped = _model_dump(value)
    candidate = dumped if dumped is not _MISSING else value
    if isinstance(candidate, (list, tuple)):
        if not candidate:
            raise ClobResponseUnavailableError(
                f"CLOB {response_type} response sequence가 비어 있습니다"
            )
        if len(candidate) != 1:
            raise ClobResponseContractError(
                f"CLOB {response_type} singleton response가 {len(candidate)}건입니다"
            )
        return _unwrap_single_response(
            candidate[0],
            response_type=response_type,
            depth=depth + 1,
            decoded_json=decoded_json,
        )
    if isinstance(candidate, Mapping):
        fields_for_type = _CLOB_RESPONSE_FIELDS[response_type]
        if _has_known_field(candidate, fields_for_type):
            return candidate
        envelopes = [key for key in _RESPONSE_ENVELOPE_KEYS if key in candidate]
        if len(envelopes) > 1:
            raise ClobResponseContractError(
                f"CLOB {response_type} response envelope가 중복되었습니다"
            )
        if envelopes:
            return _unwrap_single_response(
                candidate[envelopes[0]],
                response_type=response_type,
                depth=depth + 1,
                decoded_json=decoded_json,
            )
        if response_type == "submission" and not candidate:
            # Internal unknown-outcome persistence deliberately uses {}.
            return candidate
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response에 알려진 evidence field가 없습니다"
        )
    return value


def normalize_clob_response(value: Any, *, response_type: str) -> dict[str, Any]:
    """Convert SDK mappings/models into a secret-safe plain evidence mapping.

    ``py-clob-client-v2`` currently returns JSON mappings, while typed SDK
    releases and test doubles may return dataclasses, Pydantic models, objects
    exposing ``to_dict()``, or attribute-only response objects.  This adapter
    accepts those representation differences but keeps the venue contract
    strict: only an allowlist of evidence fields is retained and an unrelated
    object is rejected instead of being treated as a successful response.
    """
    field_aliases = _CLOB_RESPONSE_FIELDS.get(response_type)
    if field_aliases is None:
        raise ValueError(f"지원하지 않는 CLOB response_type입니다: {response_type}")
    value = _unwrap_single_response(value, response_type=response_type)
    mapping = _model_mapping(value)
    normalized: dict[str, Any] = {}
    for canonical_name, aliases in field_aliases.items():
        selected = _lookup_model_value(value, mapping, aliases)
        if selected is _MISSING:
            continue
        if canonical_name == "maker_orders":
            if selected is None:
                selected = []
            if not isinstance(selected, (list, tuple)):
                raise ClobResponseContractError(
                    "CLOB trade maker_orders가 sequence가 아닙니다"
                )
            selected = [
                normalize_clob_response(item, response_type="maker_order")
                for item in selected
            ]
        normalized[canonical_name] = selected

    if not normalized and not (response_type == "submission" and mapping == {}):
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response model에 알려진 evidence field가 없습니다"
        )
    if response_type == "order" and not str(normalized.get("status") or "").strip():
        raise ClobResponseContractError("CLOB order response에 status가 없습니다")
    if response_type == "trade" and not str(normalized.get("id") or "").strip():
        raise ClobResponseContractError("CLOB trade response에 id가 없습니다")
    if response_type == "cancellation" and not any(
        key in normalized for key in ("canceled", "not_canceled")
    ):
        raise ClobResponseContractError(
            "CLOB cancellation response에 결과 필드가 없습니다"
        )
    return normalized


def normalize_clob_response_list(
    value: Any, *, response_type: str
) -> list[dict[str, Any]]:
    """Normalize a list/root/page of typed SDK response models."""
    candidate = value
    if isinstance(candidate, str):
        candidate = _decode_response_json(candidate, response_type=response_type)
    dumped = _model_dump(candidate)
    if dumped is not _MISSING:
        candidate = dumped
    elif not isinstance(candidate, (Mapping, list, tuple)):
        selected = _lookup_model_value(
            candidate,
            None,
            ("data", "trades", "results", "root", "__root__"),
        )
        if selected is not _MISSING:
            candidate = selected
            nested_dump = _model_dump(candidate)
            if nested_dump is not _MISSING:
                candidate = nested_dump
    if isinstance(candidate, Mapping):
        if _has_known_field(candidate, _CLOB_RESPONSE_FIELDS[response_type]):
            candidate = [candidate]
        else:
            envelopes = [
                key
                for key in ("data", "trades", "results", "root", "__root__")
                if key in candidate
            ]
            if len(envelopes) != 1:
                raise ClobResponseContractError(
                    f"CLOB {response_type} collection envelope가 유일하지 않습니다"
                )
            candidate = candidate[envelopes[0]]
            nested_dump = _model_dump(candidate)
            if nested_dump is not _MISSING:
                candidate = nested_dump
    if candidate is None:
        raise ClobResponseUnavailableError(
            f"CLOB {response_type} response collection이 null입니다"
        )
    if not isinstance(candidate, (list, tuple)):
        raise ClobResponseContractError(
            f"CLOB {response_type} response collection이 sequence가 아닙니다"
        )
    return [
        normalize_clob_response(item, response_type=response_type)
        for item in candidate
    ]


class ExecutionLedger:
    """Persist selected non-secret execution fields into the bot SQLite DB."""

    def __init__(self, db_path: str | Path, *, strategy_name: str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.strategy_name = strategy_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            self._bootstrap_legacy_orders(connection)

    def record_submission(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        result: Any,
        simulation: bool,
    ) -> str:
        """Convenience wrapper used by simulations, tests, and legacy callers."""
        submission_id = self.record_intent(
            token_id=token_id,
            side=side,
            requested_price=requested_price,
            requested_size=requested_size,
            simulation=simulation,
        )
        self.record_submission_result(
            submission_id,
            result=result,
            simulation=simulation,
        )
        return submission_id

    def submit_and_record(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        submit: Callable[[], Any],
        cancel: Callable[[str], Any] | None = None,
    ) -> Mapping[str, Any]:
        """Persist intent, submit once, then persist the response on the same row.

        The intent write happens before the external side effect. If the venue
        accepts an order but the response cannot be persisted, a best-effort
        cancellation is attempted and a fail-closed exception is raised.
        """
        submission_id = self.record_intent(
            token_id=token_id,
            side=side,
            requested_price=requested_price,
            requested_size=requested_size,
            simulation=False,
        )
        try:
            result = submit()
        except Exception as error:
            try:
                response_status = self.record_submission_error(submission_id, error)
            except Exception as ledger_error:
                raise SubmissionEvidenceError(
                    "주문 실패 상태를 execution ledger에 기록하지 못했습니다"
                ) from ledger_error
            if response_status == "SUBMIT_OUTCOME_UNKNOWN":
                raise SubmissionEvidenceError(
                    "주문 POST 결과가 불확실하여 trading cycle을 중단합니다"
                ) from error
            raise

        try:
            result = normalize_clob_response(result, response_type="submission")
        except Exception as error:
            # The POST returned, so an unreadable response cannot be classified
            # as a proven rejection. Persist it as an unknown outcome and keep
            # the restart gate closed until an operator proves what happened.
            try:
                self.record_submission_result(
                    submission_id, result={}, simulation=False
                )
            except _OrderResponseContractError:
                pass
            except Exception as ledger_error:
                raise SubmissionEvidenceError(
                    "해석 불가능한 POST 결과를 execution ledger에 기록하지 못했습니다"
                ) from ledger_error
            raise SubmissionEvidenceError(
                "CLOB order response representation을 해석할 수 없어 cycle을 중단합니다"
            ) from error
        try:
            self.record_submission_result(
                submission_id,
                result=result,
                simulation=False,
            )
        except _OrderResponseContractError as response_error:
            order_id = str(result.get("orderID") or "")
            cancellation_failed = bool(order_id)
            if order_id and cancel is not None:
                try:
                    cancel_result = cancel(order_id)
                    cancellation = normalize_clob_response(
                        cancel_result, response_type="cancellation"
                    )
                    canceled_ids = cancellation.get("canceled", [])
                    cancellation_failed = order_id not in {
                        str(value) for value in canceled_ids
                    }
                except Exception:
                    cancellation_failed = True
            suffix = " cancellation도 실패했습니다" if cancellation_failed else ""
            raise SubmissionEvidenceError(
                f"CLOB order response contract가 불확실하여 cycle을 중단합니다{suffix}"
            ) from response_error
        except Exception as ledger_error:
            order_id = str(result.get("orderID") or "")
            try:
                self.mark_evidence_write_failure(
                    submission_id, order_id=order_id, error=ledger_error
                )
            except Exception:
                pass
            cancellation_failed = bool(order_id)
            if order_id and cancel is not None:
                try:
                    cancel_result = cancel(order_id)
                    cancellation = normalize_clob_response(
                        cancel_result, response_type="cancellation"
                    )
                    canceled_ids = cancellation.get("canceled", [])
                    cancellation_failed = order_id not in {
                        str(value) for value in canceled_ids
                    }
                except Exception:
                    cancellation_failed = True
            suffix = " cancellation도 실패했습니다" if cancellation_failed else ""
            raise SubmissionEvidenceError(
                "접수된 주문 응답을 execution ledger에 기록하지 못해 "
                f"best-effort cancel을 수행했습니다{suffix}"
            ) from ledger_error
        return result

    def record_intent(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        simulation: bool,
    ) -> str:
        """Durably record an order intent before the external POST occurs."""
        if not _valid_fill_price(requested_price):
            raise ValueError("requested_price는 finite 0 < price < 1 이어야 합니다")
        if not _finite_positive(requested_size):
            raise ValueError("requested_size는 finite positive 값이어야 합니다")
        submission_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_submissions (
                    submission_id, run_id, strategy_name, order_id, token_id, side,
                    requested_price, requested_size, submitted_at, simulation,
                    success, response_status, associated_trade_ids_json,
                    needs_reconciliation
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 0, 'INTENT', '[]', 0)
                """,
                (
                    submission_id,
                    current_run_id(),
                    self.strategy_name,
                    str(token_id),
                    str(side).upper(),
                    float(requested_price),
                    float(requested_size),
                    _utc_now(),
                    int(simulation),
                ),
            )
        return submission_id

    def record_submission_result(
        self,
        submission_id: str,
        *,
        result: Any,
        simulation: bool,
    ) -> None:
        """Attach the venue response to a previously persisted intent."""
        result = normalize_clob_response(result, response_type="submission")
        order_id = str(result.get("orderID") or "") or None
        explicit_success = result.get("success")
        response_anomaly: str | None = None
        if not simulation:
            if explicit_success is False and order_id:
                response_anomaly = "success=false response에 orderID가 함께 있습니다"
            elif not order_id and explicit_success is not False:
                response_anomaly = "live success/accepted response에 orderID가 없습니다"
            elif "success" in result and not isinstance(explicit_success, bool):
                response_anomaly = "live response success 필드가 boolean이 아닙니다"
        success = bool(explicit_success or order_id) and response_anomaly is None
        response_status = _normalize_status(
            "SUBMIT_OUTCOME_UNKNOWN"
            if response_anomaly
            else result.get("status")
            or ("SIMULATED" if simulation else "ACCEPTED" if success else "FAILED")
        )
        trade_ids = _string_list(result.get("tradeIDs") or result.get("trade_ids"))
        needs_reconciliation = int(bool(success and order_id and not simulation))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET order_id = ?, simulation = ?, success = ?, response_status = ?,
                    making_amount = ?, taking_amount = ?,
                    associated_trade_ids_json = ?, needs_reconciliation = ?,
                    error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    order_id,
                    int(simulation),
                    int(success),
                    response_status,
                    _fixed_6_number(result.get("makingAmount")),
                    _fixed_6_number(result.get("takingAmount")),
                    json.dumps(trade_ids),
                    needs_reconciliation,
                    None
                    if success
                    else "OrderResponseContractError"
                    if response_anomaly
                    else "OrderSubmissionError",
                    None
                    if success
                    else _safe_error_message(
                        ValueError(
                            str(
                                response_anomaly
                                or result.get("error")
                                or result.get("errorMsg")
                                or "order rejected"
                            )
                        )
                    ),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")
        if response_anomaly:
            raise _OrderResponseContractError(response_anomaly)

    def record_submission_error(
        self, submission_id: str, error: BaseException
    ) -> str:
        """Mark a pre-submit intent failed without creating another ledger row."""
        status_code = getattr(error, "status_code", "missing")
        ambiguous = type(error).__name__ in {
            "ConnectTimeout",
            "ConnectionError",
            "ReadTimeout",
            "Timeout",
        } or (
            status_code != "missing"
            and (
                status_code is None
                or (isinstance(status_code, int) and status_code >= 500)
            )
        )
        response_status = "SUBMIT_OUTCOME_UNKNOWN" if ambiguous else "FAILED"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET success = 0, response_status = ?,
                    needs_reconciliation = 0, error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    response_status,
                    type(error).__name__,
                    _safe_error_message(error),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")
        return response_status

    def mark_evidence_write_failure(
        self,
        submission_id: str,
        *,
        order_id: str,
        error: BaseException,
    ) -> None:
        """Best-effort emergency link for an accepted order after a write failure."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET order_id = NULLIF(?, ''), success = 1,
                    response_status = 'EVIDENCE_WRITE_FAILED',
                    needs_reconciliation = CASE WHEN ? = '' THEN 0 ELSE 1 END,
                    error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    order_id,
                    order_id,
                    type(error).__name__,
                    _safe_error_message(error),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")

    def unresolved_submission_outcomes(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return secret-safe uncertain intents that require operator proof.

        These rows cannot be reconciled automatically because no trustworthy
        venue order ID was persisted. They remain a restart gate until an
        operator records either a proved non-submission or the discovered ID.
        """
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = self._unresolved_outcome_rows(connection, limit=limit)
        return [dict(row) for row in rows]

    def assert_execution_ready(self) -> None:
        """Fail closed when a prior ambiguous POST is unresolved across restart."""
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM order_submissions WHERE {self._unresolved_sql()}"
            ).fetchone()
        count = int(row[0] or 0)
        if count:
            raise UnresolvedSubmissionOutcomeError(count)

    def resolve_uncertain_submission(
        self,
        submission_id: str,
        *,
        resolution: str,
        reason: str,
        order_id: str | None = None,
    ) -> None:
        """Persist explicit operator proof without erasing the original outcome.

        ``NO_ORDER_CREATED`` requires venue/operator evidence that no order was
        accepted. ``ORDER_ID_LINKED`` requires the discovered venue order ID and
        moves the row into the normal reconciliation queue. Resolution is
        immutable; corrections require preserving and reviewing the DB evidence.
        """
        resolution = str(resolution or "").strip().upper()
        if resolution not in {"NO_ORDER_CREATED", "ORDER_ID_LINKED"}:
            raise ValueError(
                "resolution은 NO_ORDER_CREATED 또는 ORDER_ID_LINKED여야 합니다"
            )
        safe_reason = _safe_error_message(RuntimeError(str(reason or "").strip()))
        if not safe_reason:
            raise ValueError("operator resolution reason은 비어 있을 수 없습니다")
        normalized_order_id = str(order_id or "").strip()
        if resolution == "ORDER_ID_LINKED" and not normalized_order_id:
            raise ValueError("ORDER_ID_LINKED에는 venue order_id가 필요합니다")
        if resolution == "NO_ORDER_CREATED" and normalized_order_id:
            raise ValueError("NO_ORDER_CREATED에는 order_id를 지정할 수 없습니다")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT response_status, order_id, outcome_resolution "
                "FROM order_submissions WHERE submission_id = ? AND simulation = 0",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise ValueError("live order intent를 찾을 수 없습니다")
            if row["outcome_resolution"] is not None:
                raise ValueError("order intent outcome은 이미 operator가 해결했습니다")
            unresolved = row["response_status"] in {
                "INTENT",
                "SUBMIT_OUTCOME_UNKNOWN",
            } or (
                row["response_status"] == "EVIDENCE_WRITE_FAILED"
                and row["order_id"] is None
            )
            if not unresolved:
                raise ValueError("불확실한 order intent 상태가 아닙니다")
            if resolution == "NO_ORDER_CREATED" and row["order_id"] is not None:
                raise ValueError("order_id가 존재하는 intent는 NO_ORDER_CREATED로 해결할 수 없습니다")
            if (
                resolution == "ORDER_ID_LINKED"
                and row["order_id"] is not None
                and str(row["order_id"]) != normalized_order_id
            ):
                raise ValueError("기존 response order_id와 operator order_id가 다릅니다")
            connection.execute(
                """
                UPDATE order_submissions
                SET outcome_resolution = ?, outcome_resolved_at = ?,
                    outcome_resolution_reason = ?,
                    order_id = CASE WHEN ? = 'ORDER_ID_LINKED' THEN ? ELSE order_id END,
                    success = CASE WHEN ? = 'ORDER_ID_LINKED' THEN 1 ELSE 0 END,
                    needs_reconciliation = CASE
                        WHEN ? = 'ORDER_ID_LINKED' THEN 1 ELSE 0 END
                WHERE submission_id = ?
                """,
                (
                    resolution,
                    _utc_now(),
                    safe_reason,
                    resolution,
                    normalized_order_id,
                    resolution,
                    resolution,
                    submission_id,
                ),
            )

    def pending_submissions(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            unresolved_count = connection.execute(
                f"SELECT COUNT(*) FROM order_submissions WHERE {self._unresolved_sql()}"
            ).fetchone()[0]
            if unresolved_count:
                raise UnresolvedSubmissionOutcomeError(int(unresolved_count))
            rows = connection.execute(
                """
                SELECT submission_id, order_id, token_id, response_status,
                       associated_trade_ids_json, reconciliation_proof
                FROM order_submissions
                WHERE needs_reconciliation = 1 AND order_id IS NOT NULL
                ORDER BY CASE WHEN last_reconciled_at IS NULL THEN 0 ELSE 1 END,
                         COALESCE(last_reconciled_at, submitted_at) ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def catalog_missing_submissions(
        self, *, limit: int = 500, include_evidence_linked: bool = False
    ) -> list[dict[str, Any]]:
        """Return catalog-missing accepted orders eligible for operator quarantine.

        Eligibility deliberately excludes matched orders, known trade IDs, recorded
        fills, and any previously observed order status. Quarantine acknowledges an
        evidence gap; it never asserts that the order was unfilled.
        """
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = self._catalog_missing_rows(
                connection,
                limit=limit,
                include_evidence_linked=include_evidence_linked,
            )
            results: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                fill_rows = connection.execute(
                    "SELECT trade_id, status, size, domain_error "
                    "FROM order_fills WHERE submission_id = ?",
                    (item["submission_id"],),
                ).fetchall()
                statuses = sorted(
                    {_normalize_status(fill["status"]) for fill in fill_rows}
                )
                confirmed_size = sum(
                    _number(fill["size"]) or 0.0
                    for fill in fill_rows
                    if _normalize_status(fill["status"]) == "CONFIRMED"
                )
                try:
                    associated_payload = json.loads(
                        item["associated_trade_ids_json"] or "[]"
                    )
                    associated = (
                        {str(value) for value in associated_payload}
                        if isinstance(associated_payload, list)
                        else set()
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    associated = set()
                observed_trade_ids = {str(fill["trade_id"]) for fill in fill_rows}
                nonterminal_count = sum(
                    _normalize_status(fill["status"]) not in _TERMINAL_TRADE_STATUSES
                    for fill in fill_rows
                )
                invalid_confirmed_count = sum(
                    _normalize_status(fill["status"]) == "CONFIRMED"
                    and fill["domain_error"] is not None
                    for fill in fill_rows
                )

                order_status = _normalize_status(item["latest_order_status"])
                expected_size = None
                if order_status in _TERMINAL_ORDER_STATUSES:
                    expected_size = _number(item["latest_size_matched"])
                elif (
                    item["reconciliation_proof"]
                    == "AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS"
                ):
                    if str(item["side"] or "").upper() == "BUY":
                        expected_size = _number(item["taking_amount"])
                    elif str(item["side"] or "").upper() == "SELL":
                        expected_size = _number(item["making_amount"])

                blockers: list[str] = []
                if item["latest_status_domain_error"] is not None:
                    blockers.append("latest_order_status_domain_error")
                if not associated:
                    blockers.append("associated_trade_ids_missing")
                if associated != observed_trade_ids:
                    blockers.append("associated_trade_ids_do_not_match_fills")
                if not fill_rows:
                    blockers.append("fills_missing")
                if nonterminal_count:
                    blockers.append("nonterminal_fills_present")
                if invalid_confirmed_count:
                    blockers.append("confirmed_fill_domain_errors_present")
                if expected_size is None:
                    blockers.append("expected_fill_size_missing")
                elif not math.isclose(
                    confirmed_size,
                    expected_size,
                    rel_tol=0.0,
                    abs_tol=_QUANTITY_TOLERANCE,
                ):
                    blockers.append("confirmed_fill_sum_differs_from_expected")

                item["fill_statuses"] = statuses
                item["confirmed_fill_size"] = confirmed_size
                item["expected_fill_size"] = expected_size
                item["nonterminal_fill_count"] = nonterminal_count
                item["invalid_confirmed_fill_count"] = invalid_confirmed_count
                item["fill_domain_errors"] = sorted(
                    self._fill_domain_error_tokens(connection, item["submission_id"])
                )
                item["completion_ready"] = not blockers
                item["completion_blockers"] = blockers
                results.append(item)
        return results

    def quantity_scale_repair_candidates(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return terminal fills exhibiting the proven 10^6 double-scaling shape."""
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            return self._quantity_scale_repair_rows(connection, limit=limit)

    def quantity_scale_diagnostics(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Explain why suspicious 10^6-scale rows are or are not repairable."""
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT submission.submission_id, submission.order_id,
                       submission.submitted_at, submission.response_status,
                       submission.latest_order_status, submission.requested_size,
                       submission.latest_size_matched, submission.quantity_scale,
                       submission.latest_status_domain_error,
                       submission.reconciliation_error,
                       submission.associated_trade_ids_json,
                       COUNT(fill.trade_id) AS fill_count,
                       COUNT(DISTINCT fill.trade_id) AS distinct_fill_trade_count,
                       GROUP_CONCAT(DISTINCT fill.status) AS fill_statuses,
                       SUM(CASE WHEN fill.status = 'CONFIRMED'
                                THEN COALESCE(fill.size, 0) ELSE 0 END)
                           AS confirmed_fill_size,
                       SUM(COALESCE(fill.size, 0)) AS total_fill_size,
                       SUM(CASE WHEN fill.status NOT IN ('CONFIRMED', 'FAILED')
                                THEN 1 ELSE 0 END) AS nonterminal_fill_count,
                       SUM(CASE WHEN fill.domain_error IS NOT NULL THEN 1 ELSE 0 END)
                           AS invalid_fill_count
                FROM order_submissions AS submission
                LEFT JOIN order_fills AS fill
                  ON fill.submission_id = submission.submission_id
                LEFT JOIN quantity_scale_repairs AS repair
                  ON repair.submission_id = submission.submission_id
                WHERE submission.strategy_name = ?
                  AND submission.simulation = 0
                  AND submission.success = 1
                  AND submission.needs_reconciliation = 1
                  AND submission.requested_size > 0
                  AND submission.latest_size_matched > 0
                  AND submission.requested_size / submission.latest_size_matched
                      BETWEEN 900000 AND 1100000
                  AND repair.submission_id IS NULL
                GROUP BY submission.submission_id
                ORDER BY submission.submitted_at, submission.submission_id
                LIMIT ?
                """,
                (self.strategy_name, limit),
            ).fetchall()

            diagnostics: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    associated = set(
                        json.loads(item["associated_trade_ids_json"] or "[]")
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    associated = set()
                fill_trade_ids = {
                    str(fill_row[0])
                    for fill_row in connection.execute(
                        "SELECT DISTINCT trade_id FROM order_fills "
                        "WHERE submission_id = ?",
                        (item["submission_id"],),
                    )
                }
                fill_domain_errors = self._fill_domain_error_tokens(
                    connection, item["submission_id"]
                )
                confirmed_size = _number(item["confirmed_fill_size"])
                latest_size = _number(item["latest_size_matched"])
                repair_mode = None
                if confirmed_size is not None and latest_size is not None:
                    if math.isclose(
                        confirmed_size,
                        latest_size,
                        rel_tol=0.0,
                        abs_tol=_QUANTITY_TOLERANCE,
                    ):
                        repair_mode = "ORDER_AND_FILL_X1000000"
                    elif math.isclose(
                        confirmed_size,
                        latest_size * _FIXED_6_SCALE,
                        rel_tol=0.0,
                        abs_tol=_QUANTITY_TOLERANCE,
                    ):
                        repair_mode = "ORDER_ONLY_X1000000"

                reasons: list[str] = []
                if item["latest_order_status"] != "MATCHED":
                    reasons.append("latest_order_status_not_matched")
                if item["latest_status_domain_error"] is not None:
                    reasons.append("latest_order_status_domain_error")
                if not associated:
                    reasons.append("associated_trade_ids_missing")
                if associated != fill_trade_ids:
                    reasons.append("associated_trade_ids_do_not_match_fills")
                if not item["fill_count"]:
                    reasons.append("fills_missing")
                if item["nonterminal_fill_count"]:
                    reasons.append("nonterminal_fills_present")
                unsupported_fill_errors = self._unsupported_quantity_scale_fill_errors(
                    connection, item["submission_id"]
                )
                if unsupported_fill_errors:
                    reasons.append("unsupported_fill_domain_errors_present")
                if repair_mode is None:
                    reasons.append("confirmed_fill_sum_matches_neither_scale")

                item["associated_trade_count"] = len(associated)
                item["fill_trade_id_count"] = len(fill_trade_ids)
                item["fill_domain_errors"] = sorted(fill_domain_errors)
                item["repair_mode"] = repair_mode
                item["repair_eligible"] = not reasons
                item["rejection_reasons"] = reasons
                item.pop("associated_trade_ids_json", None)
                diagnostics.append(item)
            return diagnostics

    def repair_quantity_scale(
        self,
        *,
        expected_count: int,
        confirmation: str,
        reason: str,
    ) -> dict[str, int]:
        """Repair exact double-scaled order/fill quantities with an audit trail."""
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count < 1
        ):
            raise ValueError("expected_count는 1 이상이어야 합니다")
        expected_confirmation = QUANTITY_SCALE_REPAIR_CONFIRMATION_TEMPLATE.format(
            count=expected_count
        )
        if confirmation != expected_confirmation:
            raise ValueError(f"확인 문구가 일치하지 않습니다: {expected_confirmation}")
        safe_reason = _safe_error_message(RuntimeError(str(reason or "").strip()))
        if not safe_reason:
            raise ValueError("quantity repair reason은 비어 있을 수 없습니다")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            candidates = self._quantity_scale_repair_rows(
                connection, limit=max(500, expected_count + 1)
            )
            if len(candidates) != expected_count:
                raise RuntimeError(
                    "quantity-scale repair 건수가 예상과 다릅니다: "
                    f"expected={expected_count}, actual={len(candidates)}"
                )
            repaired_at = _utc_now()
            for candidate in candidates:
                submission_id = candidate["submission_id"]
                before = self._quantity_repair_snapshot(connection, submission_id)
                connection.execute(
                    """
                    UPDATE order_submissions
                    SET latest_size_matched = latest_size_matched * ?,
                        quantity_scale = 1,
                        reconciliation_error = 'quantity scale repaired; reconciliation pending',
                        error_type = 'QuantityScaleRepaired',
                        error_message = ?
                    WHERE submission_id = ? AND needs_reconciliation = 1
                    """,
                    (_FIXED_6_SCALE, safe_reason, submission_id),
                )
                connection.execute(
                    """
                    UPDATE order_status_events
                    SET original_size = CASE WHEN original_size IS NULL THEN NULL
                                             ELSE original_size * ? END,
                        size_matched = CASE WHEN size_matched IS NULL THEN NULL
                                           ELSE size_matched * ? END
                    WHERE submission_id = ?
                    """,
                    (_FIXED_6_SCALE, _FIXED_6_SCALE, submission_id),
                )
                if candidate["repair_mode"] == "ORDER_AND_FILL_X1000000":
                    connection.execute(
                        """
                        UPDATE order_fills
                        SET size = CASE WHEN size IS NULL THEN NULL ELSE size * ? END
                        WHERE submission_id = ?
                        """,
                        (_FIXED_6_SCALE, submission_id),
                    )
                self._clear_repaired_quantity_scale_fill_errors(
                    connection, submission_id
                )
                after = self._quantity_repair_snapshot(connection, submission_id)
                connection.execute(
                    """
                    INSERT INTO quantity_scale_repairs (
                        repair_id, submission_id, strategy_name, repaired_at,
                        multiplier, reason, before_json, after_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        submission_id,
                        self.strategy_name,
                        repaired_at,
                        _FIXED_6_SCALE,
                        safe_reason,
                        json.dumps(before, sort_keys=True, separators=(",", ":")),
                        json.dumps(after, sort_keys=True, separators=(",", ":")),
                    ),
                )

        completed = sum(
            1
            for candidate in candidates
            if self.finish_reconciliation(candidate["submission_id"])
        )
        return {
            "repaired": expected_count,
            "completed": completed,
            "pending": expected_count - completed,
        }

    def _quantity_scale_repair_rows(
        self, connection: sqlite3.Connection, *, limit: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT submission.submission_id, submission.order_id,
                   submission.submitted_at, submission.side,
                   submission.requested_size, submission.latest_size_matched,
                   submission.associated_trade_ids_json,
                   COUNT(fill.trade_id) AS fill_count,
                   COUNT(DISTINCT fill.trade_id) AS distinct_fill_trade_count,
                   SUM(CASE WHEN fill.status = 'CONFIRMED'
                            THEN COALESCE(fill.size, 0) ELSE 0 END)
                       AS confirmed_fill_size,
                   SUM(CASE WHEN fill.status NOT IN ('CONFIRMED', 'FAILED')
                            THEN 1 ELSE 0 END) AS nonterminal_fill_count,
                   SUM(CASE WHEN fill.domain_error IS NOT NULL THEN 1 ELSE 0 END)
                       AS invalid_fill_count
            FROM order_submissions AS submission
            JOIN order_fills AS fill
              ON fill.submission_id = submission.submission_id
            LEFT JOIN quantity_scale_repairs AS repair
              ON repair.submission_id = submission.submission_id
            WHERE submission.strategy_name = ?
              AND submission.simulation = 0
              AND submission.success = 1
              AND submission.needs_reconciliation = 1
              AND submission.latest_order_status = 'MATCHED'
              AND submission.latest_status_domain_error IS NULL
              AND submission.requested_size > 0
              AND submission.latest_size_matched > 0
              AND submission.requested_size / submission.latest_size_matched
                  BETWEEN 900000 AND 1100000
              AND repair.submission_id IS NULL
            GROUP BY submission.submission_id
            ORDER BY submission.submitted_at, submission.submission_id
            LIMIT ?
            """,
            (self.strategy_name, limit),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                associated = set(json.loads(item["associated_trade_ids_json"] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            fill_trade_ids = {
                str(fill_row[0])
                for fill_row in connection.execute(
                    "SELECT DISTINCT trade_id FROM order_fills "
                    "WHERE submission_id = ?",
                    (item["submission_id"],),
                )
            }
            fill_domain_errors = self._fill_domain_error_tokens(
                connection, item["submission_id"]
            )
            unsupported_fill_errors = self._unsupported_quantity_scale_fill_errors(
                connection, item["submission_id"]
            )
            confirmed_size = _number(item["confirmed_fill_size"])
            latest_size = _number(item["latest_size_matched"])
            if (
                not associated
                or associated != fill_trade_ids
                or item["nonterminal_fill_count"]
                or unsupported_fill_errors
                or confirmed_size is None
                or latest_size is None
            ):
                continue
            if math.isclose(
                confirmed_size,
                latest_size,
                rel_tol=0.0,
                abs_tol=_QUANTITY_TOLERANCE,
            ):
                item["repair_mode"] = "ORDER_AND_FILL_X1000000"
            elif math.isclose(
                confirmed_size,
                latest_size * _FIXED_6_SCALE,
                rel_tol=0.0,
                abs_tol=_QUANTITY_TOLERANCE,
            ):
                item["repair_mode"] = "ORDER_ONLY_X1000000"
            else:
                continue
            item["associated_trade_count"] = len(associated)
            item["fill_domain_errors"] = sorted(fill_domain_errors)
            item.pop("associated_trade_ids_json", None)
            candidates.append(item)
        return candidates

    @staticmethod
    def _fill_domain_error_tokens(
        connection: sqlite3.Connection, submission_id: str
    ) -> set[str]:
        tokens: set[str] = set()
        for row in connection.execute(
            "SELECT domain_error FROM order_fills WHERE submission_id = ?",
            (submission_id,),
        ):
            tokens.update(
                token.strip()
                for token in str(row[0] or "").split(",")
                if token.strip()
            )
        return tokens

    @staticmethod
    def _unsupported_quantity_scale_fill_errors(
        connection: sqlite3.Connection, submission_id: str
    ) -> set[str]:
        unsupported: set[str] = set()
        for domain_error, fee_rate_bps in connection.execute(
            "SELECT domain_error, fee_rate_bps FROM order_fills "
            "WHERE submission_id = ?",
            (submission_id,),
        ):
            for token in str(domain_error or "").split(","):
                normalized = token.strip()
                if not normalized:
                    continue
                if normalized in _REPAIRABLE_QUANTITY_SCALE_FILL_ERRORS:
                    continue
                # Older CLOB responses sometimes supplied a blank optional fee
                # field.  The ledger persisted that as NULL plus
                # fee_rate_invalid.  NULL still preserves missing fee coverage;
                # only the stale invalid-domain marker is repairable.
                if normalized == "fee_rate_invalid" and fee_rate_bps is None:
                    continue
                unsupported.add(normalized)
        return unsupported

    @staticmethod
    def _clear_repaired_quantity_scale_fill_errors(
        connection: sqlite3.Connection, submission_id: str
    ) -> None:
        for rowid, domain_error, fee_rate_bps in connection.execute(
            "SELECT rowid, domain_error, fee_rate_bps FROM order_fills "
            "WHERE submission_id = ?",
            (submission_id,),
        ).fetchall():
            remaining = [
                token.strip()
                for token in str(domain_error or "").split(",")
                if token.strip()
                and token.strip() not in _REPAIRABLE_QUANTITY_SCALE_FILL_ERRORS
                and not (token.strip() == "fee_rate_invalid" and fee_rate_bps is None)
            ]
            connection.execute(
                "UPDATE order_fills SET domain_error = ? WHERE rowid = ?",
                (",".join(remaining) or None, rowid),
            )

    @staticmethod
    def _quantity_repair_snapshot(
        connection: sqlite3.Connection, submission_id: str
    ) -> dict[str, Any]:
        submission = connection.execute(
            """
            SELECT latest_size_matched, quantity_scale, needs_reconciliation,
                   reconciliation_error FROM order_submissions
            WHERE submission_id = ?
            """,
            (submission_id,),
        ).fetchone()
        statuses = connection.execute(
            "SELECT id, original_size, size_matched FROM order_status_events "
            "WHERE submission_id = ? ORDER BY id",
            (submission_id,),
        ).fetchall()
        fills = connection.execute(
            "SELECT trade_id, bucket_index, status, size, domain_error FROM order_fills "
            "WHERE submission_id = ? ORDER BY trade_id, bucket_index",
            (submission_id,),
        ).fetchall()
        return {
            "submission": list(submission) if submission else None,
            "status_events": [list(row) for row in statuses],
            "fills": [list(row) for row in fills],
        }

    def resolve_catalog_missing_submissions(
        self,
        *,
        expected_count: int,
        confirmation: str,
        reason: str,
        include_evidence_linked: bool = False,
    ) -> int:
        """Quarantine an exact, operator-acknowledged set of irrecoverable gaps."""
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count < 1
        ):
            raise ValueError("expected_count는 1 이상이어야 합니다")
        confirmation_template = (
            LINKED_CATALOG_GAP_CONFIRMATION_TEMPLATE
            if include_evidence_linked
            else CATALOG_GAP_CONFIRMATION_TEMPLATE
        )
        expected_confirmation = confirmation_template.format(count=expected_count)
        if confirmation != expected_confirmation:
            raise ValueError(f"확인 문구가 일치하지 않습니다: {expected_confirmation}")
        safe_reason = _safe_error_message(RuntimeError(str(reason or "").strip()))
        if not safe_reason:
            raise ValueError("operator resolution reason은 비어 있을 수 없습니다")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = self._catalog_missing_rows(
                connection,
                limit=max(500, expected_count + 1),
                include_evidence_linked=include_evidence_linked,
            )
            if len(rows) != expected_count:
                raise RuntimeError(
                    "catalog-missing CLOB gap 건수가 예상과 다릅니다: "
                    f"expected={expected_count}, actual={len(rows)}"
                )
            resolved_at = _utc_now()
            outcome_resolution = (
                "EVIDENCE_GAP_WITH_LINKED_EVIDENCE_ACCEPTED"
                if include_evidence_linked
                else "EVIDENCE_GAP_ACCEPTED"
            )
            reconciliation_error = (
                "operator accepted catalog-missing CLOB fill evidence gap "
                "with linked evidence"
                if include_evidence_linked
                else "operator accepted catalog-missing CLOB fill evidence gap"
            )
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE order_submissions
                    SET response_status = 'OPERATOR_EVIDENCE_GAP',
                        needs_reconciliation = 0,
                        outcome_resolution = ?,
                        outcome_resolved_at = ?,
                        outcome_resolution_reason = ?,
                        reconciliation_error = ?,
                        error_type = 'OperatorEvidenceGap',
                        error_message = ?
                    WHERE submission_id = ? AND needs_reconciliation = 1
                    """,
                    (
                        outcome_resolution,
                        resolved_at,
                        safe_reason,
                        reconciliation_error,
                        safe_reason,
                        row["submission_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "catalog-missing CLOB gap을 원자적으로 격리하지 못했습니다"
                    )
        return expected_count

    def _catalog_missing_rows(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int,
        include_evidence_linked: bool = False,
    ) -> list[sqlite3.Row]:
        strict_contract = "" if include_evidence_linked else """
              AND UPPER(submission.response_status) IN ('ACCEPTED', 'LIVE', 'DELAYED')
              AND submission.latest_order_status IS NULL
              AND COALESCE(submission.associated_trade_ids_json, '[]') = '[]'
              AND NOT EXISTS (
                  SELECT 1 FROM order_fills AS strict_fill
                  WHERE strict_fill.submission_id = submission.submission_id
              )
        """
        return connection.execute(
            f"""
            SELECT submission_id, order_id, submitted_at, response_status,
                   side, requested_price, requested_size, last_reconciled_at,
                   latest_order_status, latest_size_matched,
                   latest_status_domain_error, making_amount, taking_amount,
                   reconciliation_proof,
                   associated_trade_ids_json,
                   (SELECT COUNT(*) FROM order_fills AS observed_fill
                    WHERE observed_fill.submission_id = submission.submission_id)
                       AS fill_count
            FROM order_submissions AS submission
            WHERE submission.simulation = 0
              AND submission.strategy_name = ?
              AND submission.success = 1
              AND submission.needs_reconciliation = 1
              AND submission.order_id IS NOT NULL
              AND submission.last_reconciled_at IS NOT NULL
              AND submission.reconciliation_error LIKE
                  'phase=match_authoritative_order_catalogs '
                  || 'error=ClobResponseUnavailableError%'
              {strict_contract}
            ORDER BY submission.submitted_at, submission.submission_id
            LIMIT ?
            """,
            (self.strategy_name, limit),
        ).fetchall()

    def mark_legacy_unavailable(self, submission_id: str) -> None:
        """Atomically close a proved-unavailable pre-ledger evidence gap.

        This is not proof of an unfilled order. The explicit response status
        and reconciliation error preserve the gap for retrospective audits,
        while avoiding an endless live-trading gate after both authoritative
        order catalogs were queried successfully and the exact ID was absent.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET response_status = 'LEGACY_UNAVAILABLE',
                    needs_reconciliation = 0,
                    last_reconciled_at = ?,
                    reconciliation_error = ?,
                    error_type = 'LegacyOrderUnavailable',
                    error_message = ?
                WHERE submission_id = ?
                  AND response_status = 'LEGACY_ASSUMED'
                  AND needs_reconciliation = 1
                """,
                (
                    _utc_now(),
                    "legacy order unavailable; fill evidence gap remains",
                    "normal and pre-migration order catalogs returned no exact ID",
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "LEGACY_ASSUMED reconciliation row를 원자적으로 종결하지 못했습니다"
                )

    @staticmethod
    def _unresolved_sql() -> str:
        return (
            "simulation = 0 AND ("
            "response_status IN ('INTENT', 'SUBMIT_OUTCOME_UNKNOWN') OR "
            "(response_status = 'EVIDENCE_WRITE_FAILED' AND order_id IS NULL)) AND COALESCE(("
            "(outcome_resolution = 'NO_ORDER_CREATED' AND order_id IS NULL "
            "AND outcome_resolved_at IS NOT NULL "
            "AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL) OR "
            "(outcome_resolution = 'ORDER_ID_LINKED' AND order_id IS NOT NULL "
            "AND outcome_resolved_at IS NOT NULL "
            "AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL)), 0) = 0"
        )

    @classmethod
    def _unresolved_outcome_rows(
        cls, connection: sqlite3.Connection, *, limit: int
    ) -> list[sqlite3.Row]:
        return connection.execute(
            f"""
            SELECT submission_id, run_id, submitted_at, response_status,
                   side, requested_price, requested_size, error_type
            FROM order_submissions
            WHERE {cls._unresolved_sql()}
            ORDER BY submitted_at, submission_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def record_order_status(
        self, submission_id: str, detail: Any
    ) -> list[str]:
        detail = normalize_clob_response(detail, response_type="order")
        status = _normalize_status(detail.get("status"))
        associated = _string_list(
            detail.get("associate_trades") or detail.get("associated_trades")
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT associated_trade_ids_json, requested_size, quantity_scale "
                "FROM order_submissions "
                "WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        existing = json.loads(row[0] or "[]") if row else []
        associated = list(dict.fromkeys([*existing, *associated]))
        quantity_scale = _number(row[2]) if row else None
        if quantity_scale not in {1.0, float(_FIXED_6_SCALE)}:
            quantity_scale = _infer_quantity_scale(
                detail.get("original_size"), row[1] if row else None
            )
        if quantity_scale is None:
            quantity_scale = _infer_partial_quantity_scale(
                detail.get("size_matched"), row[1] if row else None
            )
        if quantity_scale is None and _number(detail.get("size_matched")) == 0.0:
            quantity_scale = 1.0
        selected = {
            "status": status,
            "original_size": _quantity_number(
                detail.get("original_size"), quantity_scale
            ),
            "size_matched": _quantity_number(
                detail.get("size_matched"), quantity_scale
            ),
            "price": _number(detail.get("price")),
            "associated": associated,
            "quantity_scale": quantity_scale,
        }
        domain_errors: list[str] = []
        if quantity_scale is None:
            domain_errors.append("quantity_scale_ambiguous")
        if selected["original_size"] is not None and not _finite_nonnegative(
            selected["original_size"]
        ):
            domain_errors.append("original_size_invalid")
        if selected["size_matched"] is not None and not _finite_nonnegative(
            selected["size_matched"]
        ):
            domain_errors.append("size_matched_invalid")
        if (
            selected["original_size"] is not None
            and selected["size_matched"] is not None
            and _finite_nonnegative(selected["original_size"])
            and _finite_nonnegative(selected["size_matched"])
            and selected["size_matched"]
            > selected["original_size"] + _QUANTITY_TOLERANCE
        ):
            domain_errors.append("size_matched_exceeds_original")
        if selected["price"] is not None and not _valid_fill_price(selected["price"]):
            domain_errors.append("order_price_invalid")
        domain_error = ",".join(domain_errors) or None
        selected["domain_error"] = domain_error
        fingerprint = hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO order_status_events (
                    submission_id, observed_at, status, original_size,
                    size_matched, price, associated_trade_ids_json, fingerprint,
                    domain_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    _utc_now(),
                    status,
                    selected["original_size"],
                    selected["size_matched"],
                    selected["price"],
                    json.dumps(associated),
                    fingerprint,
                    domain_error,
                ),
            )
            connection.execute(
                """
                UPDATE order_submissions
                SET latest_order_status = ?, latest_size_matched = ?,
                    associated_trade_ids_json = ?, last_reconciled_at = ?,
                    latest_status_domain_error = ?, reconciliation_error = ?,
                    quantity_scale = ?
                WHERE submission_id = ?
                """,
                (
                    status,
                    selected["size_matched"],
                    json.dumps(associated),
                    _utc_now(),
                    domain_error,
                    domain_error,
                    quantity_scale,
                    submission_id,
                ),
            )
        return associated

    def record_fill(
        self,
        submission_id: str,
        order_id: str,
        trade: Any,
    ) -> None:
        trade = normalize_clob_response(trade, response_type="trade")
        trade_id = str(trade.get("id") or "")
        if not trade_id:
            return
        with self._connect() as connection:
            scale_row = connection.execute(
                "SELECT requested_size, quantity_scale FROM order_submissions "
                "WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        requested_size = _number(scale_row[0]) if scale_row else None
        quantity_scale = _number(scale_row[1]) if scale_row else None
        if quantity_scale not in {1.0, float(_FIXED_6_SCALE)}:
            quantity_scale = None
        status = _normalize_status(trade.get("status"))
        maker_orders = trade.get("maker_orders") or []
        maker_match = next(
            (
                item
                for item in maker_orders
                if isinstance(item, Mapping) and str(item.get("order_id")) == str(order_id)
            ),
            None,
        )
        reported_role = str(trade.get("trader_side") or "").upper()
        taker_match = (
            (
                bool(trade.get("taker_order_id"))
                and str(trade.get("taker_order_id")) == str(order_id)
            )
            or (reported_role == "TAKER" and maker_match is None)
        )
        execution_payload_present = any(
            key in trade
            for key in (
                "maker_orders", "taker_order_id", "trader_side", "size", "price"
            )
        )
        domain_errors: list[str] = []
        if maker_match is not None and taker_match:
            domain_errors.append("order_fill_correlation_conflict")
        if maker_match is not None:
            raw_size = maker_match.get("matched_amount")
            price = _number(maker_match.get("price"))
            side = str(maker_match.get("side") or "").upper()
            liquidity_role = "MAKER"
            fee_rate_raw = maker_match.get("fee_rate_bps")
        elif taker_match:
            raw_size = trade.get("size")
            price = _number(trade.get("price"))
            side = str(trade.get("side") or "").upper()
            liquidity_role = "TAKER"
            fee_rate_raw = trade.get("fee_rate_bps")
        else:
            raw_size = trade.get("size")
            price = _number(trade.get("price"))
            side = str(trade.get("side") or "").upper()
            liquidity_role = "UNKNOWN"
            fee_rate_raw = trade.get("fee_rate_bps")
            if execution_payload_present:
                domain_errors.append("order_fill_correlation_invalid")
        if quantity_scale is None:
            quantity_scale = _infer_partial_quantity_scale(raw_size, requested_size)
            if quantity_scale is not None:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE order_submissions SET quantity_scale = ? "
                        "WHERE submission_id = ? AND quantity_scale IS NULL",
                        (quantity_scale, submission_id),
                    )
        if quantity_scale is None:
            domain_errors.append("quantity_scale_missing")
        size = _quantity_number(raw_size, quantity_scale)
        if (
            reported_role in {"TAKER", "MAKER"}
            and liquidity_role != "UNKNOWN"
            and reported_role != liquidity_role
        ):
            domain_errors.append("liquidity_role_conflict")
        bucket_index, bucket_error = _bucket_index(trade.get("bucket_index"))
        if bucket_error:
            domain_errors.append(bucket_error)
        if status == "CONFIRMED" and execution_payload_present:
            if not _finite_positive(size):
                domain_errors.append("confirmed_size_invalid")
            if not _valid_fill_price(price):
                domain_errors.append("confirmed_price_invalid")
        fee_rate_bps = _number(fee_rate_raw)
        if _numeric_metadata_present(fee_rate_raw) and not _finite_nonnegative(
            fee_rate_bps
        ):
            domain_errors.append("fee_rate_invalid")
        fee_amount_source = maker_match if maker_match is not None else trade
        fee_amount_raw = _first_present(
            fee_amount_source, "fee_amount_usdc", "fee_amount", "fee"
        )
        fee_amount_usdc = _fixed_6_number(fee_amount_raw)
        if _numeric_metadata_present(fee_amount_raw) and not _finite_nonnegative(
            fee_amount_usdc
        ):
            domain_errors.append("fee_amount_invalid")
        domain_error = ",".join(dict.fromkeys(domain_errors)) or None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_fills (
                    submission_id, order_id, trade_id, bucket_index, status, side, size,
                    price, liquidity_role, fee_rate_bps, fee_amount_usdc,
                    matched_at, last_update, transaction_hash, domain_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(submission_id, trade_id, bucket_index) DO UPDATE SET
                    status = CASE WHEN excluded.status = 'UNKNOWN'
                                  THEN order_fills.status ELSE excluded.status END,
                    side = COALESCE(NULLIF(excluded.side, ''), order_fills.side),
                    size = COALESCE(excluded.size, order_fills.size),
                    price = COALESCE(excluded.price, order_fills.price),
                    liquidity_role = CASE WHEN excluded.liquidity_role = 'UNKNOWN'
                                          THEN order_fills.liquidity_role
                                          ELSE excluded.liquidity_role END,
                    fee_rate_bps = COALESCE(excluded.fee_rate_bps, order_fills.fee_rate_bps),
                    fee_amount_usdc = COALESCE(excluded.fee_amount_usdc, order_fills.fee_amount_usdc),
                    matched_at = COALESCE(excluded.matched_at, order_fills.matched_at),
                    last_update = COALESCE(excluded.last_update, order_fills.last_update),
                    transaction_hash = COALESCE(excluded.transaction_hash, order_fills.transaction_hash),
                    domain_error = COALESCE(excluded.domain_error, order_fills.domain_error)
                """,
                (
                    submission_id,
                    str(order_id),
                    trade_id,
                    bucket_index,
                    status,
                    side,
                    size,
                    price,
                    liquidity_role,
                    fee_rate_bps,
                    fee_amount_usdc,
                    str(
                        trade.get("match_time")
                        or trade.get("matchtime")
                        or trade.get("timestamp")
                        or ""
                    )
                    or None,
                    str(trade.get("last_update") or "") or None,
                    str(trade.get("transaction_hash") or "") or None,
                    domain_error,
                ),
            )

    def record_recovered_trade_associations(
        self,
        submission_id: str,
        order_id: str,
        trade_ids: list[str],
    ) -> list[str]:
        """Monotonically attach exact, already-persisted trade evidence.

        This path is reserved for trades discovered through the authenticated
        token trade catalog after an order disappears from both order catalogs.
        It does not infer an order status.  Every supplied ID must already have
        an exact-order-correlated, domain-valid fill row recorded by
        :meth:`record_fill` before it can become canonical submission evidence.
        """
        normalized_trade_ids = [str(value or "").strip() for value in trade_ids]
        if not normalized_trade_ids or any(not value for value in normalized_trade_ids):
            raise ClobResponseContractError(
                "복구할 authenticated trade ID가 비어 있습니다"
            )
        if len(set(normalized_trade_ids)) != len(normalized_trade_ids):
            raise ClobResponseContractError(
                "복구할 authenticated trade ID가 중복되었습니다"
            )
        expected_order_id = str(order_id or "").strip()
        if not expected_order_id:
            raise ClobResponseContractError("복구할 exact order ID가 비어 있습니다")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            submission = connection.execute(
                "SELECT order_id, associated_trade_ids_json "
                "FROM order_submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            if submission is None:
                raise ClobResponseContractError(
                    "authenticated trade를 연결할 submission이 없습니다"
                )
            if str(submission["order_id"] or "") != expected_order_id:
                raise ClobResponseContractError(
                    "authenticated trade의 exact order ID가 submission과 다릅니다"
                )
            try:
                existing = json.loads(
                    submission["associated_trade_ids_json"] or "[]"
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ClobResponseContractError(
                    "기존 associated trade IDs JSON이 유효하지 않습니다"
                ) from error
            if not isinstance(existing, list):
                raise ClobResponseContractError(
                    "기존 associated trade IDs가 list가 아닙니다"
                )
            existing_ids = [str(value or "").strip() for value in existing]
            if any(not value for value in existing_ids) or len(set(existing_ids)) != len(
                existing_ids
            ):
                raise ClobResponseContractError(
                    "기존 associated trade IDs가 비어 있거나 중복되었습니다"
                )

            placeholders = ",".join("?" for _ in normalized_trade_ids)
            rows = connection.execute(
                "SELECT trade_id, order_id, liquidity_role, domain_error "
                "FROM order_fills WHERE submission_id = ? "
                f"AND trade_id IN ({placeholders})",
                (submission_id, *normalized_trade_ids),
            ).fetchall()
            observed_ids = {str(row["trade_id"]) for row in rows}
            if observed_ids != set(normalized_trade_ids):
                raise ClobResponseContractError(
                    "authenticated trade ID의 persisted fill evidence가 완전하지 않습니다"
                )
            if any(
                str(row["order_id"] or "") != expected_order_id
                or str(row["liquidity_role"] or "") not in {"TAKER", "MAKER"}
                or row["domain_error"] is not None
                for row in rows
            ):
                raise ClobResponseContractError(
                    "authenticated trade fill의 exact order 상관관계가 유효하지 않습니다"
                )

            merged = list(dict.fromkeys([*existing_ids, *normalized_trade_ids]))
            cursor = connection.execute(
                "UPDATE order_submissions "
                "SET associated_trade_ids_json = ?, last_reconciled_at = ?, "
                "reconciliation_error = NULL, reconciliation_proof = "
                "'AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS' "
                "WHERE submission_id = ? AND order_id = ?",
                (json.dumps(merged), _utc_now(), submission_id, expected_order_id),
            )
            if cursor.rowcount != 1:
                raise ClobResponseContractError(
                    "authenticated trade association을 원자적으로 기록하지 못했습니다"
                )
        return merged

    def finish_reconciliation(self, submission_id: str) -> bool:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            submission = connection.execute(
                """
                SELECT latest_order_status, latest_size_matched,
                       associated_trade_ids_json, latest_status_domain_error,
                       requested_price, requested_size, side, making_amount,
                       taking_amount, reconciliation_proof
                FROM order_submissions WHERE submission_id = ?
                """,
                (submission_id,),
            ).fetchone()
            if submission is None:
                return False
            order_status = _normalize_status(submission["latest_order_status"])
            trade_ids = json.loads(submission["associated_trade_ids_json"] or "[]")
            complete = False
            if (
                submission["latest_status_domain_error"] is not None
                or not _valid_fill_price(submission["requested_price"])
                or not _finite_positive(submission["requested_size"])
                or (
                    submission["latest_size_matched"] is not None
                    and not _finite_nonnegative(submission["latest_size_matched"])
                )
            ):
                connection.execute(
                    "UPDATE order_submissions SET reconciliation_error = ? "
                    "WHERE submission_id = ?",
                    ("order/submission domain invalid", submission_id),
                )
                return False
            authenticated_trade_proof = (
                submission["reconciliation_proof"]
                == "AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS"
            )
            if order_status in _TERMINAL_ORDER_STATUSES or (
                trade_ids and authenticated_trade_proof
            ):
                trade_rows: dict[str, list[tuple[str, float | None]]] = {}
                invalid_confirmed_domain = False
                for row in connection.execute(
                    "SELECT trade_id, status, size, price, bucket_index, domain_error "
                    "FROM order_fills "
                    "WHERE submission_id = ?",
                    (submission_id,),
                ):
                    fill_status = _normalize_status(row[1])
                    trade_rows.setdefault(str(row[0]), []).append(
                        (fill_status, _number(row[2]))
                    )
                    if fill_status == "CONFIRMED" and (
                        not _finite_positive(row[2])
                        or not _valid_fill_price(row[3])
                        or type(row[4]) is not int
                        or row[4] < 0
                        or row[5] is not None
                    ):
                        invalid_confirmed_domain = True
                if invalid_confirmed_domain:
                    connection.execute(
                        "UPDATE order_submissions SET reconciliation_error = ? "
                        "WHERE submission_id = ?",
                        ("confirmed fill domain invalid", submission_id),
                    )
                    return False
                if trade_ids:
                    # Every trade advertised by the order endpoint must reach a
                    # terminal state. A single confirmed partial fill is not
                    # enough to close a multi-fill order.
                    every_bucket_terminal = all(
                        str(trade_id) in trade_rows
                        and all(
                            status in _TERMINAL_TRADE_STATUSES
                            for status, _ in trade_rows[str(trade_id)]
                        )
                        for trade_id in trade_ids
                    )
                    every_bucket_confirmed = all(
                        str(trade_id) in trade_rows
                        and all(
                            status == "CONFIRMED"
                            for status, _ in trade_rows[str(trade_id)]
                        )
                        for trade_id in trade_ids
                    )
                    confirmed_size = sum(
                        size or 0.0
                        for trade_id in trade_ids
                        for status, size in trade_rows.get(str(trade_id), [])
                        if status == "CONFIRMED"
                    )
                    matched_size = _number(submission["latest_size_matched"])
                    if order_status in _TERMINAL_ORDER_STATUSES:
                        expected_size = matched_size
                    elif str(submission["side"] or "").upper() == "BUY":
                        expected_size = _number(submission["taking_amount"])
                    elif str(submission["side"] or "").upper() == "SELL":
                        expected_size = _number(submission["making_amount"])
                    else:
                        expected_size = None
                    if authenticated_trade_proof and not _finite_positive(
                        expected_size
                    ):
                        connection.execute(
                            "UPDATE order_submissions SET reconciliation_error = ? "
                            "WHERE submission_id = ?",
                            (
                                "authenticated trade full-fill proof missing "
                                "submission token amount",
                                submission_id,
                            ),
                        )
                        return False
                    complete = (
                        (
                            every_bucket_confirmed
                            if authenticated_trade_proof
                            and order_status not in _TERMINAL_ORDER_STATUSES
                            else every_bucket_terminal
                        )
                        and expected_size is not None
                        and math.isclose(
                            confirmed_size,
                            expected_size,
                            rel_tol=0.0,
                            abs_tol=_QUANTITY_TOLERANCE,
                        )
                    )
                    if (
                        expected_size is not None
                        and confirmed_size > expected_size + _QUANTITY_TOLERANCE
                    ):
                        connection.execute(
                            "UPDATE order_submissions SET reconciliation_error = ? "
                            "WHERE submission_id = ?",
                            (
                                (
                                    "confirmed fill quantity exceeds "
                                    "latest_size_matched"
                                    if order_status in _TERMINAL_ORDER_STATUSES
                                    else "confirmed fill quantity exceeds "
                                    "submission token amount"
                                ),
                                submission_id,
                            ),
                        )
                elif order_status != "MATCHED":
                    # A canceled/invalid order is provably unfilled only when
                    # the venue explicitly reports zero matched size. Missing
                    # size evidence remains pending (fail closed).
                    matched_size = _number(submission["latest_size_matched"])
                    complete = matched_size == 0.0
            if complete:
                connection.execute(
                    "UPDATE order_submissions SET needs_reconciliation = 0, "
                    "reconciliation_error = NULL, reconciliation_proof = CASE "
                    "WHEN reconciliation_proof = "
                    "'AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS' THEN "
                    "'AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL' "
                    "ELSE reconciliation_proof END WHERE submission_id = ?",
                    (submission_id,),
                )
            return complete

    def record_reconciliation_error(
        self, submission_id: str, error: BaseException
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE order_submissions
                SET last_reconciled_at = ?, reconciliation_error = ?
                WHERE submission_id = ?
                """,
                (_utc_now(), _safe_error_message(error), submission_id),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_schema(
        connection: sqlite3.Connection,
        *,
        migration_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Create or migrate the ledger in one explicit SQLite transaction.

        ``sqlite3.Connection.executescript`` commits an already-open transaction
        before running its script. Schema migration therefore uses individual DDL
        statements so a copy/drop/rename failure rolls back as one unit. The hook
        is deliberately private test injection for proving mid-migration rollback.
        """
        if connection.in_transaction:
            raise RuntimeError("execution ledger schema migration requires no transaction")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polybot_schema_versions (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_submissions (
                    submission_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    strategy_name TEXT NOT NULL,
                    order_id TEXT,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_price REAL NOT NULL,
                    requested_size REAL NOT NULL,
                    submitted_at TEXT NOT NULL,
                    simulation INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    response_status TEXT NOT NULL,
                    making_amount REAL,
                    taking_amount REAL,
                    associated_trade_ids_json TEXT NOT NULL DEFAULT '[]',
                    latest_order_status TEXT,
                    latest_size_matched REAL,
                    quantity_scale REAL,
                    latest_status_domain_error TEXT,
                    reconciliation_proof TEXT,
                    last_reconciled_at TEXT,
                    needs_reconciliation INTEGER NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    reconciliation_error TEXT,
                    outcome_resolution TEXT,
                    outcome_resolved_at TEXT,
                    outcome_resolution_reason TEXT
                )
                """
            )
            submission_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(order_submissions)")
            }
            for column_name in (
                "outcome_resolution",
                "outcome_resolved_at",
                "outcome_resolution_reason",
                "latest_status_domain_error",
                "reconciliation_proof",
            ):
                if column_name not in submission_columns:
                    connection.execute(
                        f"ALTER TABLE order_submissions ADD COLUMN {column_name} TEXT"
                    )
            if "quantity_scale" not in submission_columns:
                connection.execute(
                    "ALTER TABLE order_submissions ADD COLUMN quantity_scale REAL"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_status_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id TEXT NOT NULL
                        REFERENCES order_submissions(submission_id),
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_size REAL,
                    size_matched REAL,
                    price REAL,
                    associated_trade_ids_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    domain_error TEXT,
                    UNIQUE(submission_id, fingerprint)
                )
                """
            )
            status_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(order_status_events)")
            }
            if "domain_error" not in status_columns:
                connection.execute(
                    "ALTER TABLE order_status_events ADD COLUMN domain_error TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_submissions_reconcile_idx "
                "ON order_submissions(needs_reconciliation, submitted_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_submissions_order_idx "
                "ON order_submissions(order_id)"
            )

            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "order_fills" not in tables and "order_fills_v2" in tables:
                # Recover the only surviving table from an interrupted legacy
                # migration before CREATE IF NOT EXISTS can hide its rows.
                connection.execute("ALTER TABLE order_fills_v2 RENAME TO order_fills")
            elif "order_fills" not in tables:
                ExecutionLedger._create_order_fills_table(connection, "order_fills")

            fill_info = list(connection.execute("PRAGMA table_info(order_fills)"))
            fill_columns = {str(row[1]) for row in fill_info}
            for column_name, column_type in (
                ("liquidity_role", "TEXT"),
                ("fee_amount_usdc", "REAL"),
                ("bucket_index", "INTEGER NOT NULL DEFAULT 0"),
                ("domain_error", "TEXT"),
            ):
                if column_name not in fill_columns:
                    connection.execute(
                        f"ALTER TABLE order_fills ADD COLUMN {column_name} {column_type}"
                    )

            fill_info = list(connection.execute("PRAGMA table_info(order_fills)"))
            fill_columns = {str(row[1]) for row in fill_info}
            migration_columns = {
                "submission_id", "order_id", "trade_id", "bucket_index", "status",
                "side", "size", "price", "liquidity_role", "fee_rate_bps",
                "fee_amount_usdc", "matched_at", "last_update", "transaction_hash",
                "domain_error",
            }
            missing_migration_columns = migration_columns - fill_columns
            if missing_migration_columns:
                raise RuntimeError(
                    "order_fills schema를 안전하게 migration할 수 없습니다: "
                    f"{sorted(missing_migration_columns)}"
                )
            primary_key = [
                str(row[1])
                for row in sorted(fill_info, key=lambda item: item[5])
                if row[5]
            ]
            if primary_key != ["submission_id", "trade_id", "bucket_index"]:
                connection.execute("DROP TABLE IF EXISTS order_fills_v2")
                ExecutionLedger._create_order_fills_table(connection, "order_fills_v2")
                connection.execute(
                    """
                    INSERT INTO order_fills_v2 (
                        submission_id, order_id, trade_id, bucket_index, status,
                        side, size, price, liquidity_role, fee_rate_bps,
                        fee_amount_usdc, matched_at, last_update, transaction_hash,
                        domain_error
                    )
                    SELECT submission_id, order_id, trade_id,
                           COALESCE(bucket_index, 0), status, side, size, price,
                           liquidity_role, fee_rate_bps, fee_amount_usdc,
                           matched_at, last_update, transaction_hash, domain_error
                    FROM order_fills
                    """
                )
                if migration_hook is not None:
                    migration_hook("after_order_fills_copy")
                connection.execute("DROP TABLE order_fills")
                connection.execute("ALTER TABLE order_fills_v2 RENAME TO order_fills")
            else:
                # Both names can remain after the old non-atomic migration failed.
                connection.execute("DROP TABLE IF EXISTS order_fills_v2")

            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_fills_order_idx "
                "ON order_fills(order_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quantity_scale_repairs (
                    repair_id TEXT PRIMARY KEY,
                    submission_id TEXT NOT NULL UNIQUE
                        REFERENCES order_submissions(submission_id),
                    strategy_name TEXT NOT NULL,
                    repaired_at TEXT NOT NULL,
                    multiplier REAL NOT NULL,
                    reason TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL
                )
                """
            )
            required_columns = {
                "order_submissions": {
                    "submission_id", "run_id", "order_id", "requested_price",
                    "requested_size", "simulation", "success", "response_status",
                    "needs_reconciliation", "outcome_resolution",
                    "outcome_resolved_at", "outcome_resolution_reason",
                    "latest_status_domain_error", "quantity_scale",
                    "reconciliation_proof",
                },
                "order_status_events": {
                    "submission_id", "status", "original_size", "size_matched",
                    "domain_error",
                },
                "order_fills": {
                    "submission_id", "order_id", "trade_id", "bucket_index",
                    "status", "size", "price", "fee_rate_bps", "domain_error",
                },
                "quantity_scale_repairs": {
                    "repair_id", "submission_id", "strategy_name", "repaired_at",
                    "multiplier", "reason", "before_json", "after_json",
                },
            }
            for table, required in required_columns.items():
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                missing = required - columns
                if missing:
                    raise RuntimeError(
                        f"{table} schema에 필수 컬럼이 없습니다: {sorted(missing)}"
                    )
            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
            if foreign_key_errors:
                raise RuntimeError("execution ledger foreign key 검증에 실패했습니다")
            connection.execute(
                """
                INSERT INTO polybot_schema_versions(component, version, updated_at)
                VALUES ('execution_ledger', 8, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version, updated_at = excluded.updated_at
                """,
                (_utc_now(),),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _create_order_fills_table(
        connection: sqlite3.Connection, table_name: str
    ) -> None:
        if table_name not in {"order_fills", "order_fills_v2"}:
            raise ValueError("unexpected order_fills table name")
        connection.execute(
            f"""
            CREATE TABLE {table_name} (
                submission_id TEXT NOT NULL
                    REFERENCES order_submissions(submission_id),
                order_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                bucket_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                side TEXT,
                size REAL,
                price REAL,
                liquidity_role TEXT,
                fee_rate_bps REAL,
                fee_amount_usdc REAL,
                matched_at TEXT,
                last_update TEXT,
                transaction_hash TEXT,
                domain_error TEXT,
                PRIMARY KEY(submission_id, trade_id, bucket_index)
            )
            """
        )

    def _bootstrap_legacy_orders(self, connection: sqlite3.Connection) -> None:
        """Register recent pre-ledger trade order IDs for best-effort reconciliation."""
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "trades" not in tables:
            return
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
        required = {
            "token_id",
            "buy_order_id",
            "buy_price",
            "buy_shares",
            "buy_timestamp",
            "status",
        }
        if not required.issubset(columns):
            return

        select_columns = [
            "token_id",
            "buy_order_id",
            "buy_price",
            "buy_shares",
            "buy_timestamp",
            "sell_order_id" if "sell_order_id" in columns else "NULL AS sell_order_id",
            "sell_price" if "sell_price" in columns else "NULL AS sell_price",
            "sell_shares" if "sell_shares" in columns else "NULL AS sell_shares",
            "sell_timestamp" if "sell_timestamp" in columns else "NULL AS sell_timestamp",
            "status" if "status" in columns else "NULL AS status",
        ]
        rows = connection.execute(
            f"SELECT {', '.join(select_columns)} FROM trades "
            "WHERE (datetime(buy_timestamp) >= datetime('now', '-90 days') "
            "OR status IN ('HOLDING', 'PENDING_BUY', 'PENDING_SELL'))"
        ).fetchall()
        now = _utc_now()
        for row in rows:
            token_id = str(row[0])
            candidates = (
                ("BUY", row[1], row[2], row[3], row[4]),
                ("SELL", row[5], row[6], row[7] or row[3], row[8]),
            )
            for side, order_id, price, size, submitted_at in candidates:
                if not order_id or str(order_id).startswith("SIM"):
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM order_submissions WHERE order_id = ? LIMIT 1",
                    (str(order_id),),
                ).fetchone()
                if exists:
                    continue
                submission_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"polybot:{order_id}"))
                connection.execute(
                    """
                    INSERT INTO order_submissions (
                        submission_id, run_id, strategy_name, order_id, token_id,
                        side, requested_price, requested_size, submitted_at,
                        simulation, success, response_status,
                        associated_trade_ids_json, needs_reconciliation
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, 1,
                              'LEGACY_ASSUMED', '[]', 1)
                    """,
                    (
                        submission_id,
                        self.strategy_name,
                        str(order_id),
                        token_id,
                        side,
                        float(price or 0),
                        float(size or 0),
                        str(submitted_at or now),
                    ),
                )

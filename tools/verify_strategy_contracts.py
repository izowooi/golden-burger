#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Validate the repository-level contract shared by every golden-* bot."""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_STRATEGIES = {
    "golden-apple",
    "golden-banana",
    "golden-cherry",
    "golden-date",
    "golden-elderberry",
    "golden-fig",
    "golden-grape",
    "golden-honeydew",
    "golden-lime",
    "golden-mango",
    "golden-nectarine",
    "golden-orange",
    "golden-papaya",
}
PRE_L3_STRATEGIES = {"golden-apple", "golden-banana", "golden-cherry"}


@dataclass(frozen=True)
class Finding:
    strategy: str
    check: str
    detail: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _require_file(findings: list[Finding], strategy: str, path: Path) -> str:
    if not path.is_file():
        findings.append(Finding(strategy, "missing_file", str(path.relative_to(ROOT))))
        return ""
    return _read(path)


def _require_tokens(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    content: str,
    tokens: tuple[str, ...],
) -> None:
    for token in tokens:
        if token not in content:
            findings.append(
                Finding(strategy, "missing_contract", f"{relative_path}: {token}")
            )


def _parse_python(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> ast.Module | None:
    try:
        return ast.parse(content, filename=relative_path)
    except SyntaxError as error:
        findings.append(Finding(strategy, "invalid_python", f"{relative_path}: {error}"))
        return None


def _function(
    tree: ast.AST, name: str, *, class_name: str | None = None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    scope: ast.AST = tree
    if class_name is not None:
        class_node = next(
            (
                node
                for node in getattr(tree, "body", [])
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ),
            None,
        )
        if class_node is None:
            return None
        scope = class_node
    return next(
        (
            node
            for node in getattr(scope, "body", [])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _call_name(call: ast.Call) -> str:
    parts: list[str] = []
    node: ast.AST = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _expression_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _keyword_value(call: ast.Call, name: str) -> ast.AST | None:
    return next(
        (keyword.value for keyword in call.keywords if keyword.arg == name),
        None,
    )


def _is_none_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _calls(node: ast.AST) -> list[tuple[str, ast.Call]]:
    return [
        (_call_name(child), child)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    ]


def _mode_comparison(test: ast.AST, mode: str) -> str | None:
    """Return ``eq``/``ne`` when *test* compares lifecycle_mode to *mode*."""
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        operands = [node.left, *node.comparators]
        has_lifecycle = any(
            (isinstance(operand, ast.Name) and operand.id == "lifecycle_mode")
            or (isinstance(operand, ast.Attribute) and operand.attr == "lifecycle_mode")
            for operand in operands
        )
        has_mode = any(
            isinstance(operand, ast.Constant) and operand.value == mode
            for operand in operands
        )
        if not has_lifecycle or not has_mode:
            continue
        if isinstance(node.ops[0], ast.Eq):
            return "eq"
        if isinstance(node.ops[0], ast.NotEq):
            return "ne"
    return None


def _instance_mode_comparison(test: ast.AST, mode: str) -> str | None:
    """Return ``eq``/``ne`` when *test* compares ``self.mode`` to *mode*."""
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        operands = [node.left, *node.comparators]
        has_instance_mode = any(
            isinstance(operand, ast.Attribute)
            and operand.attr == "mode"
            and isinstance(operand.value, ast.Name)
            and operand.value.id == "self"
            for operand in operands
        )
        has_mode = any(
            isinstance(operand, ast.Constant) and operand.value == mode
            for operand in operands
        )
        if not has_instance_mode or not has_mode:
            continue
        if isinstance(node.ops[0], ast.Eq):
            return "eq"
        if isinstance(node.ops[0], ast.NotEq):
            return "ne"
    return None


def _update_calls_with_simulation_guard(
    node: ast.AST,
    *,
    simulation_guarded: bool = False,
) -> list[tuple[ast.Call, bool]]:
    """Collect ``update_trade`` calls and whether their branch requires simulation."""
    collected: list[tuple[ast.Call, bool]] = []

    if isinstance(node, ast.Call) and _call_name(node).endswith("update_trade"):
        collected.append((node, simulation_guarded))

    if isinstance(node, ast.If):
        comparison = _instance_mode_comparison(node.test, "sim")
        body_guarded = simulation_guarded or comparison == "eq"
        else_guarded = simulation_guarded or comparison == "ne"
        collected.extend(
            item
            for child in node.body
            for item in _update_calls_with_simulation_guard(
                child, simulation_guarded=body_guarded
            )
        )
        collected.extend(
            item
            for child in node.orelse
            for item in _update_calls_with_simulation_guard(
                child, simulation_guarded=else_guarded
            )
        )
        collected.extend(
            _update_calls_with_simulation_guard(
                node.test, simulation_guarded=simulation_guarded
            )
        )
        return collected

    for child in ast.iter_child_nodes(node):
        collected.extend(
            _update_calls_with_simulation_guard(
                child, simulation_guarded=simulation_guarded
            )
        )
    return collected


def _guarded_calls(
    node: ast.AST,
    suffixes: tuple[str, ...],
    *,
    active_guarded: bool = False,
) -> list[tuple[str, ast.Call, bool]]:
    """Collect calls and whether their control path requires active mode."""
    collected: list[tuple[str, ast.Call, bool]] = []

    if isinstance(node, ast.Call):
        name = _call_name(node)
        if any(name.endswith(suffix) for suffix in suffixes):
            collected.append((name, node, active_guarded))

    if isinstance(node, ast.If):
        comparison = _mode_comparison(node.test, "active")
        body_guarded = active_guarded or comparison == "eq"
        else_guarded = active_guarded or comparison == "ne"
        collected.extend(
            item
            for child in node.body
            for item in _guarded_calls(
                child, suffixes, active_guarded=body_guarded
            )
        )
        collected.extend(
            item
            for child in node.orelse
            for item in _guarded_calls(
                child, suffixes, active_guarded=else_guarded
            )
        )
        collected.extend(
            _guarded_calls(node.test, suffixes, active_guarded=active_guarded)
        )
        return collected

    for child in ast.iter_child_nodes(node):
        collected.extend(
            _guarded_calls(child, suffixes, active_guarded=active_guarded)
        )
    return collected


def _require_function(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    tree: ast.AST,
    name: str,
    *,
    class_name: str | None = None,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    function = _function(tree, name, class_name=class_name)
    if function is None:
        qualified = f"{class_name}.{name}" if class_name else name
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: {qualified}")
        )
    return function


def _require_call_order(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    function: ast.AST,
    expected: tuple[str, ...],
) -> None:
    locations = {
        name: min(
            (
                call.lineno
                for call_name, call in _calls(function)
                if call_name.endswith(name)
            ),
            default=None,
        )
        for name in expected
    }
    if any(value is None for value in locations.values()):
        missing = [name for name, value in locations.items() if value is None]
        findings.append(
            Finding(
                strategy,
                "missing_call",
                f"{relative_path}: {', '.join(missing)}",
            )
        )
        return
    ordered = [int(locations[name]) for name in expected]
    if ordered != sorted(ordered):
        findings.append(
            Finding(
                strategy,
                "unsafe_call_order",
                f"{relative_path}: {' -> '.join(expected)}",
            )
        )


def _validate_config_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    validator = _require_function(
        findings, strategy, relative_path, tree, "_validate_config"
    )
    loader = _require_function(findings, strategy, relative_path, tree, "load_config")
    number_loader = _require_function(
        findings, strategy, relative_path, tree, "_get_config_value"
    )
    lifecycle_loader = _require_function(
        findings, strategy, relative_path, tree, "_get_lifecycle_mode"
    )
    if validator is not None:
        calls = {name for name, _ in _calls(validator)}
        if not any(name.endswith("math.isfinite") for name in calls):
            findings.append(
                Finding(strategy, "missing_validation", f"{relative_path}: finite numbers")
            )
        if not any(isinstance(node, ast.Raise) for node in ast.walk(validator)):
            findings.append(
                Finding(strategy, "missing_validation", f"{relative_path}: fail closed")
            )
    if loader is not None:
        loader_calls = {name for name, _ in _calls(loader)}
        required_loader_calls = (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "_validate_config",
            "_get_lifecycle_mode",
        )
        missing_loader_calls = [
            name
            for name in required_loader_calls
            if not any(call.endswith(name) for call in loader_calls)
        ]
        if missing_loader_calls:
            findings.append(
                Finding(
                    strategy,
                    "missing_call",
                    f"{relative_path}: {', '.join(missing_loader_calls)}",
                )
            )
    if number_loader is not None:
        has_boolean_type_guard = any(
            isinstance(node, ast.Call)
            and _call_name(node).endswith("isinstance")
            and any(
                isinstance(child, ast.Name) and child.id == "bool"
                for child in ast.walk(node)
            )
            for node in ast.walk(number_loader)
        )
        has_integer_type_guard = any(
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "value_type"
            and any(isinstance(operator, ast.Is) for operator in node.ops)
            and any(
                isinstance(comparator, ast.Name) and comparator.id == "int"
                for comparator in node.comparators
            )
            for node in ast.walk(number_loader)
        )
        if not has_boolean_type_guard or not has_integer_type_guard:
            findings.append(
                Finding(
                    strategy,
                    "missing_validation",
                    f"{relative_path}: strict YAML numeric types",
                )
            )
    if lifecycle_loader is not None:
        lifecycle_source = ast.get_source_segment(content, lifecycle_loader) or ""
        _require_tokens(
            findings,
            strategy,
            relative_path,
            lifecycle_source,
            (
                "POLYBOT_LIFECYCLE_MODE",
                "active",
                "close_only",
                "archive_only",
                "replace",
            ),
        )


def _validate_bot_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    run = _require_function(
        findings, strategy, relative_path, tree, "run", class_name="PolymarketBot"
    )
    if run is None:
        return
    _require_call_order(
        findings,
        strategy,
        relative_path,
        run,
        (
            "RunAudit.start",
            "reconcile_order_ledger",
            "log_reconciliation_continuity",
            "run_cycle",
            "audit.succeed",
        ),
    )
    calls = {name for name, _ in _calls(run)}
    if any(
        name.endswith("reconciliation.get")
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "errors"
        for name, call in _calls(run)
    ):
        findings.append(
            Finding(
                strategy,
                "unsafe_global_gate",
                f"{relative_path}: per-order reconciliation errors must stay local",
            )
        )
    if not any(name.endswith("audit.fail") for name in calls):
        findings.append(Finding(strategy, "missing_call", f"{relative_path}: audit.fail"))
    if not any(isinstance(node, ast.Raise) for node in ast.walk(run)):
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: reconciliation fail closed")
        )
    for required_suffix in ("sweep_attestations.clear", "get_sweep_summaries"):
        if not any(name.endswith(required_suffix) for name in calls):
            findings.append(
                Finding(
                    strategy,
                    "missing_contract",
                    f"{relative_path}: {required_suffix}",
                )
            )

    run_cycle = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "run_cycle",
        class_name="PolymarketBot",
    )
    if run_cycle is None:
        return
    _require_call_order(
        findings,
        strategy,
        relative_path,
        run_cycle,
        ("get_holding_trades", "midpoint_snapshot", "execute_sell"),
    )
    midpoint_contexts = [
        item.context_expr
        for node in ast.walk(run_cycle)
        if isinstance(node, ast.With)
        for item in node.items
        if any(
            name.endswith("self.clob.midpoint_snapshot")
            for name, _ in _calls(item.context_expr)
        )
    ]
    if not midpoint_contexts:
        findings.append(
            Finding(
                strategy,
                "missing_contract",
                f"{relative_path}: Phase 1 scoped midpoint_snapshot",
            )
        )

    entry_calls = _guarded_calls(
        run_cycle, ("scan_buy_candidates", "execute_buy")
    )
    unguarded_entries = [name for name, _, guarded in entry_calls if not guarded]
    if unguarded_entries:
        findings.append(
            Finding(
                strategy,
                "unsafe_lifecycle_path",
                f"{relative_path}: active guard missing for "
                + ", ".join(sorted(set(unguarded_entries))),
            )
        )
    if _mode_comparison(run_cycle, "archive_only") is None:
        findings.append(
            Finding(
                strategy,
                "missing_contract",
                f"{relative_path}: archive_only order guard",
            )
        )
    sell_calls = _guarded_calls(run_cycle, ("execute_sell",))
    if sell_calls and all(guarded for _, _, guarded in sell_calls):
        findings.append(
            Finding(
                strategy,
                "unsafe_lifecycle_path",
                f"{relative_path}: close_only must retain execute_sell",
            )
        )


def _validate_papaya_bot_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    run_cycle = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "run_cycle",
        class_name="PolymarketBot",
    )
    if run_cycle is None:
        return
    _require_call_order(
        findings,
        strategy,
        relative_path,
        run_cycle,
        (
            "get_pending_sell_trades",
            "reconcile_pending_sell",
            "get_holding_trades",
        ),
    )


def _validate_clob_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    place = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "place_limit_order",
        class_name="ClobClientWrapper",
    )
    reconcile = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "reconcile_order_ledger",
        class_name="ClobClientWrapper",
    )
    cancel = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "cancel_order",
        class_name="ClobClientWrapper",
    )
    get_midpoint = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "get_midpoint",
        class_name="ClobClientWrapper",
    )
    get_midpoints = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "get_midpoints",
        class_name="ClobClientWrapper",
    )
    midpoint_snapshot = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "midpoint_snapshot",
        class_name="ClobClientWrapper",
    )
    if place is not None:
        names = {name for name, _ in _calls(place)}
        required = {
            "self.execution_ledger.submit_and_record",
            "self.client.create_order",
            "self.client.post_order",
            "self.client.cancel_orders",
        }
        missing = sorted(required - names)
        if missing:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_submission_path",
                    f"{relative_path}: missing {', '.join(missing)}",
                )
            )
        evidence_handlers = [
            handler
            for handler in ast.walk(place)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "SubmissionEvidenceError"
        ]
        if not evidence_handlers or not any(
            isinstance(node, ast.Raise)
            for handler in evidence_handlers
            for node in ast.walk(handler)
        ):
            findings.append(
                Finding(
                    strategy,
                    "unsafe_submission_path",
                    f"{relative_path}: SubmissionEvidenceError must propagate",
                )
            )
    if reconcile is not None:
        names = {name for name, _ in _calls(reconcile)}
        required_suffixes = (
            "pending_submissions",
            "get_order",
            "get_open_orders",
            "get_pre_migration_orders",
            "normalize_clob_response",
            "safe_clob_response_shape",
            "record_order_status",
            "get_trades",
            "normalize_clob_response_list",
            "record_fill",
            "mark_legacy_unavailable",
            "finish_reconciliation",
            "record_reconciliation_error",
        )
        missing = [
            suffix
            for suffix in required_suffixes
            if not any(name.endswith(suffix) for name in names)
        ]
        if missing:
            findings.append(
                Finding(
                    strategy,
                    "incomplete_reconciliation",
                    f"{relative_path}: {', '.join(missing)}",
                )
            )
        constants = {
            node.value
            for node in ast.walk(reconcile)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if "LEGACY_ASSUMED" not in constants:
            findings.append(
                Finding(
                    strategy,
                    "incomplete_reconciliation",
                    f"{relative_path}: missing LEGACY_ASSUMED fallback gate",
                )
            )
    if cancel is not None:
        names = {name for name, _ in _calls(cancel)}
        required = {
            "self.client.cancel_orders",
            "self.client.get_order",
            "normalize_clob_response",
        }
        missing = sorted(required - names)
        has_raise = any(isinstance(node, ast.Raise) for node in ast.walk(cancel))
        required_tokens = (
            "_PROVABLY_UNFILLED_ORDER_STATUSES",
            "returned_order_id",
            "size_matched",
            "verified_order_status",
        )
        if missing or not has_raise or any(
            token not in content for token in required_tokens
        ):
            findings.append(
                Finding(
                    strategy,
                    "unsafe_cancellation_path",
                    f"{relative_path}: exact terminal zero-fill evidence required",
                )
            )
    if get_midpoints is not None:
        names = {name for name, _ in _calls(get_midpoints)}
        required_suffixes = (
            "self.client.get_midpoints",
            "BookParams",
            "self._normalize_midpoint_value",
        )
        missing = [
            suffix
            for suffix in required_suffixes
            if not any(name.endswith(suffix) for name in names)
        ]
        if missing:
            findings.append(
                Finding(
                    strategy,
                    "missing_contract",
                    f"{relative_path}: batch midpoint {', '.join(missing)}",
                )
            )
    if midpoint_snapshot is not None:
        has_finally = any(
            isinstance(node, ast.Try) and bool(node.finalbody)
            for node in ast.walk(midpoint_snapshot)
        )
        if not has_finally:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_cache_scope",
                    f"{relative_path}: midpoint snapshot must restore in finally",
                )
            )
    if get_midpoint is not None:
        midpoint_source = ast.get_source_segment(content, get_midpoint) or ""
        if (
            "_midpoint_snapshot" not in midpoint_source
            or "ClobResponseUnavailableError" not in midpoint_source
        ):
            findings.append(
                Finding(
                    strategy,
                    "missing_contract",
                    f"{relative_path}: fail-closed midpoint snapshot lookup",
                )
            )
    _require_tokens(
        findings,
        strategy,
        relative_path,
        content,
        (
            "MAX_MIDPOINT_BATCH_SIZE = 500",
            "@contextmanager",
            "fallback",
        ),
    )


def _validate_trader_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    mark_unfilled = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "_mark_unfilled",
        class_name="Trader",
    )
    if mark_unfilled is None:
        return
    evidence_handlers = [
        handler
        for handler in ast.walk(mark_unfilled)
        if isinstance(handler, ast.ExceptHandler)
        and isinstance(handler.type, ast.Name)
        and handler.type.id == "SubmissionEvidenceError"
    ]
    if not evidence_handlers or not any(
        isinstance(node, ast.Return)
        for handler in evidence_handlers
        for node in ast.walk(handler)
    ):
        findings.append(
            Finding(
                strategy,
                "unsafe_phantom_position_path",
                f"{relative_path}: unproved cancellation must keep HOLDING",
            )
        )


def _validate_papaya_trader_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    """Enforce Papaya's accepted-SELL versus confirmed-fill state boundary."""
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return

    execute_sell = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "execute_sell",
        class_name="Trader",
    )
    reconcile = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "reconcile_pending_sell",
        class_name="Trader",
    )
    fill_ready = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "_actual_fill_ready",
        class_name="Trader",
    )

    if execute_sell is not None:
        update_calls = _update_calls_with_simulation_guard(execute_sell)
        pending_updates = [
            call
            for call, _ in update_calls
            if _expression_name(_keyword_value(call, "status") or ast.Constant())
            == "TradeStatus.PENDING_SELL"
        ]
        if not pending_updates:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_sell_acceptance_path",
                    f"{relative_path}: live accepted SELL must become PENDING_SELL",
                )
            )
        for call in pending_updates:
            if not _is_none_constant(_keyword_value(call, "realized_pnl")):
                findings.append(
                    Finding(
                        strategy,
                        "unsafe_sell_acceptance_path",
                        f"{relative_path}: accepted SELL realized_pnl must remain None",
                    )
                )
            if not _is_none_constant(_keyword_value(call, "hypothetical_pnl")):
                findings.append(
                    Finding(
                        strategy,
                        "unsafe_sell_acceptance_path",
                        f"{relative_path}: live accepted SELL cannot record hypothetical P&L",
                    )
                )

        unguarded_completed = [
            call
            for call, simulation_guarded in update_calls
            if _expression_name(_keyword_value(call, "status") or ast.Constant())
            == "TradeStatus.COMPLETED"
            and not simulation_guarded
        ]
        if unguarded_completed:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_sell_acceptance_path",
                    f"{relative_path}: accepted live SELL cannot become COMPLETED",
                )
            )

    if fill_ready is not None:
        fill_ready_source = ast.get_source_segment(content, fill_ready) or ""
        _require_tokens(
            findings,
            strategy,
            relative_path,
            fill_ready_source,
            (
                "has_reconciled_full_fill",
                "fee_complete",
                "confirmed_size",
                "confirmed_vwap",
                "confirmed_fee_usdc",
            ),
        )

    if reconcile is None:
        return

    calls = _calls(reconcile)
    required_suffixes = (
        "get_exact_sell_fill_evidence",
        "get_exact_buy_fill_evidence",
        "_actual_fill_ready",
        "math.isclose",
        "update_trade",
    )
    missing = [
        suffix
        for suffix in required_suffixes
        if not any(name.endswith(suffix) for name, _ in calls)
    ]
    if missing:
        findings.append(
            Finding(
                strategy,
                "incomplete_pending_sell_reconciliation",
                f"{relative_path}: {', '.join(missing)}",
            )
        )

    completed_updates = [
        call
        for name, call in calls
        if name.endswith("update_trade")
        and _expression_name(_keyword_value(call, "status") or ast.Constant())
        == "TradeStatus.COMPLETED"
    ]
    if not completed_updates:
        findings.append(
            Finding(
                strategy,
                "incomplete_pending_sell_reconciliation",
                f"{relative_path}: confirmed fill path must finalize COMPLETED",
            )
        )
    else:
        completed = min(completed_updates, key=lambda call: call.lineno)
        if _is_none_constant(_keyword_value(completed, "realized_pnl")) or (
            _keyword_value(completed, "realized_pnl") is None
        ):
            findings.append(
                Finding(
                    strategy,
                    "incomplete_pending_sell_reconciliation",
                    f"{relative_path}: confirmed BUY/SELL fills must calculate realized_pnl",
                )
            )
        pnl_basis = _keyword_value(completed, "pnl_basis")
        if not (
            isinstance(pnl_basis, ast.Constant)
            and pnl_basis.value
            == "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
        ):
            findings.append(
                Finding(
                    strategy,
                    "incomplete_pending_sell_reconciliation",
                    f"{relative_path}: exact confirmed-fill net-fee P&L basis required",
                )
            )

        sell_lines = sorted(
            call.lineno
            for name, call in calls
            if name.endswith("get_exact_sell_fill_evidence")
        )
        buy_lines = sorted(
            call.lineno
            for name, call in calls
            if name.endswith("get_exact_buy_fill_evidence")
        )
        ready_lines = sorted(
            call.lineno
            for name, call in calls
            if name.endswith("_actual_fill_ready")
        )
        size_check_lines = sorted(
            call.lineno for name, call in calls if name.endswith("math.isclose")
        )
        ordered_evidence = (
            bool(sell_lines)
            and bool(buy_lines)
            and len(ready_lines) >= 2
            and bool(size_check_lines)
            and sell_lines[0]
            < ready_lines[0]
            < buy_lines[0]
            < ready_lines[1]
            < size_check_lines[0]
            < completed.lineno
        )
        if not ordered_evidence:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_pending_sell_reconciliation",
                    f"{relative_path}: SELL proof -> BUY proof -> size match -> COMPLETED required",
                )
            )

    holding_updates = [
        call
        for name, call in calls
        if name.endswith("update_trade")
        and _expression_name(_keyword_value(call, "status") or ast.Constant())
        == "TradeStatus.HOLDING"
    ]
    if not holding_updates or not all(
        _is_none_constant(_keyword_value(call, "realized_pnl"))
        for call in holding_updates
    ):
        findings.append(
            Finding(
                strategy,
                "unsafe_pending_sell_reconciliation",
                f"{relative_path}: terminal zero-fill SELL must return to HOLDING without P&L",
            )
        )


def _validate_gamma_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    sweep = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "get_all_tradable_markets",
        class_name="GammaClient",
    )
    if sweep is None:
        return
    bounded_get = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "_get",
        class_name="GammaClient",
    )
    page_fetch = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "_get_keyset_page",
        class_name="GammaClient",
    )
    if not any(isinstance(node, ast.While) for node in ast.walk(sweep)):
        findings.append(
            Finding(strategy, "incomplete_pagination", f"{relative_path}: no keyset loop")
        )
    calls = _calls(sweep)
    page_calls = _calls(page_fetch) if page_fetch is not None else []
    bounded_calls = [call for name, call in page_calls if name.endswith("self._get")]
    direct_get_calls = _calls(bounded_get) if bounded_get is not None else []
    session_get_calls = [
        call for name, call in direct_get_calls if name.endswith("session.get")
    ]
    if (
        not bounded_calls
        or not session_get_calls
        or not all(
            any(keyword.arg == "timeout" for keyword in call.keywords)
            for call in session_get_calls
        )
    ):
        findings.append(
            Finding(strategy, "missing_timeout", f"{relative_path}: Gamma request")
        )
    if not any(name.endswith("self._get_keyset_page") for name, _ in calls):
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: page-level retry")
        )
    page_decorators = (
        [call for decorator in page_fetch.decorator_list for call in _calls(decorator)]
        if page_fetch is not None
        else []
    )
    if not any(name.endswith("rate_limit_handler") for name, _ in page_decorators):
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: page retry handler")
        )
    if not any(
        name.endswith("rate_limit_handler")
        and any(
            keyword.arg == "retry_forbidden"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
        for name, call in page_decorators
    ):
        findings.append(
            Finding(
                strategy,
                "missing_contract",
                f"{relative_path}: transient Gamma 403 page retry",
            )
        )
    sweep_decorators = [
        call for decorator in sweep.decorator_list for call in _calls(decorator)
    ]
    if any(name.endswith("rate_limit_handler") for name, _ in sweep_decorators):
        findings.append(
            Finding(strategy, "unsafe_retry_scope", f"{relative_path}: full Gamma sweep")
        )
    if not any(name.endswith("raise_for_status") for name, _ in page_calls):
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: HTTP status check")
        )
    _require_tokens(
        findings,
        strategy,
        relative_path,
        content,
        (
            "/markets/keyset",
            "after_cursor",
            "next_cursor",
            "liquidity_num_min",
            "volume_num_min",
            "enableOrderBook",
            "acceptingOrders",
            "sweep_attestation",
            "CONNECT_TIMEOUT_SECONDS",
            "READ_TIMEOUT_SECONDS",
            "@rate_limit_handler(max_retries=3)",
            "membership_digest_sha256",
            '"membership_digest_scope": "qualified_only"',
            "raw_seen_count",
            "cursor_complete",
            "excluded_condition_count",
            "exclusion_counts",
        ),
    )


def _validate_retry_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return
    handler = _require_function(
        findings, strategy, relative_path, tree, "rate_limit_handler"
    )
    if handler is None:
        return
    _require_tokens(
        findings,
        strategy,
        relative_path,
        content,
        (
            "MAX_RETRY_DELAY_SECONDS",
            "_retry_after_seconds",
            "parsedate_to_datetime",
            "attempt + 1 < max_retries",
            "retry_forbidden",
            "status_code == 403",
        ),
    )


def _validate_pyproject(
    findings: list[Finding], strategy: str, path: Path, content: str
) -> None:
    try:
        payload = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        findings.append(Finding(strategy, "invalid_toml", f"{path.name}: {error}"))
        return

    project = payload.get("project", {})
    dependencies = project.get("dependencies", [])
    if not any(str(value).split("[")[0] == "polybot-observability" for value in dependencies):
        findings.append(
            Finding(strategy, "missing_dependency", "polybot-observability")
        )
    if project.get("scripts", {}).get("polybot") != "polybot.main:main":
        findings.append(Finding(strategy, "missing_entrypoint", "polybot.main:main"))
    source = payload.get("tool", {}).get("uv", {}).get("sources", {}).get(
        "polybot-observability", {}
    )
    if source.get("path") != "../polybot-observability":
        findings.append(
            Finding(strategy, "invalid_uv_source", "../polybot-observability")
        )
    if payload.get("build-system", {}).get("build-backend") != "hatchling.build":
        findings.append(Finding(strategy, "missing_build", "hatchling.build"))
    packages = (
        payload.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("packages", [])
    )
    if "src/polybot" not in packages:
        findings.append(Finding(strategy, "missing_package", "src/polybot"))


def validate_strategy(directory: Path) -> list[Finding]:
    strategy = directory.name
    findings: list[Finding] = []
    required = ("README.md", ".env.example", "config.yaml", "uv.lock")
    for relative_path in required:
        _require_file(findings, strategy, directory / relative_path)

    if strategy not in PRE_L3_STRATEGIES:
        for relative_path in ("AGENTS.md", "STRATEGY.md"):
            _require_file(findings, strategy, directory / relative_path)

    pyproject_path = directory / "pyproject.toml"
    pyproject = _require_file(findings, strategy, pyproject_path)
    if pyproject:
        _validate_pyproject(findings, strategy, pyproject_path, pyproject)

    config = _require_file(findings, strategy, directory / "src/polybot/config.py")
    _validate_config_source(findings, strategy, "src/polybot/config.py", config)
    _require_tokens(
        findings,
        strategy,
        "src/polybot/config.py",
        config,
        (
            "excluded_categories must be a list",
            "simulation_mode must be a boolean",
            "LIFECYCLE_MODES",
            "lifecycle_mode: str = \"active\"",
            "POLYBOT_LIFECYCLE_MODE",
        ),
    )

    bot = _require_file(findings, strategy, directory / "src/polybot/bot.py")
    _validate_bot_source(findings, strategy, "src/polybot/bot.py", bot)
    if strategy == "golden-papaya":
        _validate_papaya_bot_source(
            findings, strategy, "src/polybot/bot.py", bot
        )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/bot.py",
        bot,
        ("lifecycle_mode", "active", "archive_only"),
    )

    main_source = _require_file(
        findings, strategy, directory / "src/polybot/main.py"
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/main.py",
        main_source,
        ("Lifecycle Mode", "lifecycle_mode"),
    )

    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        ("POLYBOT_LIFECYCLE_MODE=active",),
    )

    readme = _read(directory / "README.md")
    _require_tokens(
        findings,
        strategy,
        "README.md",
        readme,
        (
            "POLYBOT_LIFECYCLE_MODE",
            "close_only",
            "archive_only",
            "strategy-wind-down-playbook.md",
        ),
    )

    lifecycle_test = _require_file(
        findings, strategy, directory / "tests/test_lifecycle_mode.py"
    )
    _require_tokens(
        findings,
        strategy,
        "tests/test_lifecycle_mode.py",
        lifecycle_test,
        (
            "active",
            "close_only",
            "archive_only",
            "scan_buy_candidates",
            "execute_buy",
            "execute_sell",
        ),
    )

    clob = _require_file(
        findings, strategy, directory / "src/polybot/api/clob_client.py"
    )
    _validate_clob_source(findings, strategy, "src/polybot/api/clob_client.py", clob)

    trader = _require_file(
        findings, strategy, directory / "src/polybot/strategy/trader.py"
    )
    _validate_trader_source(
        findings, strategy, "src/polybot/strategy/trader.py", trader
    )
    if strategy == "golden-papaya":
        _validate_papaya_trader_source(
            findings, strategy, "src/polybot/strategy/trader.py", trader
        )

    gamma = _require_file(
        findings, strategy, directory / "src/polybot/api/gamma_client.py"
    )
    _validate_gamma_source(findings, strategy, "src/polybot/api/gamma_client.py", gamma)

    retry = _require_file(
        findings, strategy, directory / "src/polybot/utils/retry.py"
    )
    _validate_retry_source(findings, strategy, "src/polybot/utils/retry.py", retry)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )

    simulation = directory / "scripts/simulate.py"
    if simulation.is_file():
        simulation_content = _read(simulation)
        _require_tokens(
            findings,
            strategy,
            "scripts/simulate.py",
            simulation_content,
            ("simulation_mode=True", "trades_sim.db"),
        )
    return findings


def main() -> int:
    discovered = {
        path.name
        for path in ROOT.glob("golden-*")
        if path.is_dir() and (path / "src/polybot").is_dir()
    }
    findings = [
        Finding(strategy, "missing_strategy", strategy)
        for strategy in sorted(CURRENT_STRATEGIES - discovered)
    ]
    for strategy in sorted(discovered):
        findings.extend(validate_strategy(ROOT / strategy))

    if findings:
        print(f"strategy contract: FAIL ({len(findings)} finding(s))")
        for finding in findings:
            print(f"- {finding.strategy}: {finding.check}: {finding.detail}")
        return 1

    extras = sorted(discovered - CURRENT_STRATEGIES)
    print(f"strategy contract: PASS ({len(discovered)} strategies)")
    if extras:
        print("new strategies discovered: " + ", ".join(extras))
    return 0


if __name__ == "__main__":
    sys.exit(main())

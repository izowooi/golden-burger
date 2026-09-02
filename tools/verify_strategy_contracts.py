#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Validate the repository-level contract shared by every golden-* bot."""

from __future__ import annotations

import ast
import hashlib
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_STRATEGIES = {
    "golden-apple",
    "golden-banana",
    "golden-black",
    "golden-blueberry",
    "golden-cherry",
    "golden-coconut",
    "golden-date",
    "golden-elderberry",
    "golden-fig",
    "golden-grape",
    "golden-honeydew",
    "golden-kiwi",
    "golden-lime",
    "golden-mango",
    "golden-melon",
    "golden-nectarine",
    "golden-orange",
    "golden-papaya",
    "golden-peach",
    "golden-plum",
    "golden-pomegranate",
    "golden-queen",
    "golden-quince",
    "golden-raspberry",
    "golden-strawberry",
    "golden-tangerine",
    "golden-watermelon",
    "golden-watermelon-live",
}
RESEARCH_ONLY_STRATEGIES = {
    "golden-black",
    "golden-coconut",
    "golden-pomegranate",
    "golden-raspberry",
    "golden-strawberry",
    "golden-watermelon",
}
# L3 AGENTS.md 없이 오래 운영된 전략만 검사에서 면제한다.
# golden-cherry는 2026-07-28에 L3를 갖췄으므로 더 이상 면제 대상이 아니다.
PRE_L3_STRATEGIES = {"golden-apple", "golden-banana"}


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


def _require_token_alternatives(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    content: str,
    token_groups: tuple[tuple[str, ...], ...],
) -> None:
    """Require one token from each semantic group.

    Dedicated research collectors do not need to use one exact identifier for
    every evidence field.  This helper keeps the contract strict about the
    evidence concept while accepting a small set of reasonable exact names.
    """

    for alternatives in token_groups:
        if not any(token in content for token in alternatives):
            findings.append(
                Finding(
                    strategy,
                    "missing_contract",
                    f"{relative_path}: one of {' | '.join(alternatives)}",
                )
            )


def _require_one_of_files(
    findings: list[Finding],
    strategy: str,
    directory: Path,
    relative_paths: tuple[str, ...],
) -> str:
    for relative_path in relative_paths:
        path = directory / relative_path
        if path.is_file():
            return _read(path)
    findings.append(
        Finding(
            strategy,
            "missing_file",
            "one of: " + ", ".join(relative_paths),
        )
    )
    return ""


def _normalized_literal(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(_normalized_literal(item) for item in value)
    return value


def _require_literal_assignment(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    tree: ast.Module,
    names: tuple[str, ...],
    expected: object,
) -> None:
    values: list[tuple[str, object]] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value_node = node.value
        if value_node is None:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in names:
                continue
            try:
                values.append((target.id, ast.literal_eval(value_node)))
            except (ValueError, TypeError):
                values.append((target.id, "<non-literal>"))

    if not values:
        findings.append(
            Finding(
                strategy,
                "missing_contract",
                f"{relative_path}: one of {' | '.join(names)}",
            )
        )
        return

    normalized_expected = _normalized_literal(expected)
    invalid = [
        (name, value)
        for name, value in values
        if _normalized_literal(value) != normalized_expected
    ]
    if invalid:
        findings.append(
            Finding(
                strategy,
                "invalid_contract",
                f"{relative_path}: {invalid!r} != {expected!r}",
            )
        )


def _simple_yaml_values(content: str, key: str) -> list[object]:
    values: list[object] = []
    prefix = f"{key}:"
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line.startswith(prefix):
            continue
        raw_value = line[len(prefix) :].strip()
        lowered = raw_value.lower()
        if lowered == "true":
            values.append(True)
            continue
        if lowered == "false":
            values.append(False)
            continue
        if lowered in {"null", "none", "~"}:
            values.append(None)
            continue
        try:
            values.append(ast.literal_eval(raw_value))
        except (SyntaxError, ValueError):
            values.append(raw_value.strip("'\""))
    return values


def _require_yaml_value(
    findings: list[Finding],
    strategy: str,
    relative_path: str,
    content: str,
    key: str,
    expected: object,
) -> None:
    values = _simple_yaml_values(content, key)
    if not values:
        findings.append(
            Finding(strategy, "missing_contract", f"{relative_path}: {key}")
        )
        return
    normalized_expected = _normalized_literal(expected)
    if len(values) != 1 or _normalized_literal(values[0]) != normalized_expected:
        findings.append(
            Finding(
                strategy,
                "invalid_contract",
                f"{relative_path}: {key}={values!r}, expected {expected!r}",
            )
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
    if strategy == "golden-kiwi":
        # Kiwi deliberately prices each timed exit from one fresh executable
        # bid.  Its entry uses one CLOB order book for midpoint, spread and
        # depth, so forcing the fleet's batch-midpoint context here would
        # reintroduce a second, stale price observation.
        _require_call_order(
            findings,
            strategy,
            relative_path,
            run_cycle,
            ("get_holding_trades", "execute_sell"),
        )
    else:
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
    """Enforce the strict accepted-SELL versus confirmed-fill state boundary."""
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


def _validate_kiwi_trader_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    """Enforce Kiwi's single-book decision and permanent research stop."""
    tree = _parse_python(findings, strategy, relative_path, content)
    if tree is None:
        return

    execute_buy = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "execute_buy",
        class_name="Trader",
    )
    drawdown = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "evaluate_drawdown_stop",
        class_name="Trader",
    )
    if execute_buy is not None:
        _require_call_order(
            findings,
            strategy,
            relative_path,
            execute_buy,
            (
                "get_buy_book_depth",
                "evaluate_entry",
                "place_limit_order",
                "create_trade",
            ),
        )
        execute_source = ast.get_source_segment(content, execute_buy) or ""
        _require_tokens(
            findings,
            strategy,
            relative_path,
            execute_source,
            (
                "trend_decision_timestamps_json",
                "trend_decision_gap_minutes_json",
                "decision_observed_at_at_entry",
                "clob_single_order_book_midpoint",
                "best_bid_at_buy",
                "best_ask_at_buy",
                "book_depth_shares_at_buy",
            ),
        )
        execute_calls = {name for name, _ in _calls(execute_buy)}
        if any(
            name.endswith(("get_midpoint", "_fresh_book"))
            for name in execute_calls
        ):
            findings.append(
                Finding(
                    strategy,
                    "unsafe_price_lineage",
                    f"{relative_path}: Kiwi BUY must use one get_buy_book_depth response",
                )
            )

    if drawdown is not None:
        _require_call_order(
            findings,
            strategy,
            relative_path,
            drawdown,
            (
                "get_drawdown_kill_switch",
                "current_run_id",
                "strict_terminal_economic_path",
                "stage_drawdown_kill_switch",
            ),
        )
        _require_tokens(
            findings,
            strategy,
            relative_path,
            ast.get_source_segment(content, drawdown) or "",
            (
                "experiment_capital_usdc",
                "max_drawdown_stop",
                "source_terminal_run_id",
                "candidate-independent first crossing",
            ),
        )


def _validate_kiwi_bot_source(
    findings: list[Finding], strategy: str, relative_path: str, content: str
) -> None:
    """Enforce candidate-independent drawdown evaluation and two-phase latch."""
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
    run = _require_function(
        findings,
        strategy,
        relative_path,
        tree,
        "run",
        class_name="PolymarketBot",
    )
    if run_cycle is not None:
        _require_call_order(
            findings,
            strategy,
            relative_path,
            run_cycle,
            (
                "evaluate_drawdown_stop",
                "fetch_markets",
                "scan_buy_candidates",
            ),
        )
    if run is not None:
        _require_call_order(
            findings,
            strategy,
            relative_path,
            run,
            (
                "reconcile_staged_drawdown_kill_switch",
                "run_cycle",
                "succeed",
                "finalize_staged_drawdown_kill_switch",
            ),
        )
        _require_tokens(
            findings,
            strategy,
            relative_path,
            ast.get_source_segment(content, run) or "",
            (
                "invalidate_non_successful_run_evidence",
                "discard_staged_drawdown_kill_switch",
            ),
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
    sweep_nodes = [sweep]
    public_calls = _calls(sweep)
    if any(
        name.endswith("self._get_all_tradable_markets_uncached")
        for name, _ in public_calls
    ):
        delegated_sweep = _require_function(
            findings,
            strategy,
            relative_path,
            tree,
            "_get_all_tradable_markets_uncached",
            class_name="GammaClient",
        )
        if delegated_sweep is not None:
            sweep_nodes.append(delegated_sweep)
    if not any(
        isinstance(node, ast.While)
        for sweep_node in sweep_nodes
        for node in ast.walk(sweep_node)
    ):
        findings.append(
            Finding(strategy, "incomplete_pagination", f"{relative_path}: no keyset loop")
        )
    calls = [call for sweep_node in sweep_nodes for call in _calls(sweep_node)]
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


def _validate_research_only_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate a source-level accountless collector without fake order code.

    Trading bots and market research instruments have opposite safety
    contracts.  Requiring a ``Trader`` or an ``ExecutionLedger`` here would
    manufacture an order path solely to satisfy a structural checker.  The
    collector branch instead proves that live execution is impossible and
    that its raw evidence is complete, append-only, and discoverable.
    """

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/api/data_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/utils/retry.py",
        "src/polybot/source_digest.py",
    )
    sources: dict[str, str] = {}
    for relative_path in required_sources:
        sources[relative_path] = _require_file(
            findings, strategy, directory / relative_path
        )

    config = sources["src/polybot/config.py"]
    config_tree = _parse_python(
        findings, strategy, "src/polybot/config.py", config
    )
    if config_tree is not None:
        for function_name in (
            "_validate_config",
            "load_config",
            "_get_config_value",
            "_get_lifecycle_mode",
            "assert_no_credentials",
        ):
            _require_function(
                findings,
                strategy,
                "src/polybot/config.py",
                config_tree,
                function_name,
            )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/config.py",
        config,
        (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "simulation_mode must be a boolean",
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_FUNDER_ADDRESS",
            "POLYMARKET_SIGNATURE_TYPE",
            "research-full-v1",
            "math.isfinite",
            "archive_only",
        ),
    )

    main_source = sources["src/polybot/main.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/main.py",
        main_source,
        ("--live", "--simulate", "config", "status", "health"),
    )

    bot = sources["src/polybot/bot.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/bot.py",
        bot,
        (
            "ResearchRunAudit.start",
            "exclusive_job_run_lock",
            "record_storage_metric",
            "assert_no_credentials",
        ),
    )

    gamma = sources["src/polybot/api/gamma_client.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/gamma_client.py",
        gamma,
        (
            "/markets/keyset",
            "after_cursor",
            "next_cursor",
            "include_tag",
            "received_at",
        ),
    )
    retry = sources["src/polybot/utils/retry.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/utils/retry.py",
        retry,
        ("RequestException", "ChunkedEncodingError", "Retry-After"),
    )

    clob = sources["src/polybot/api/clob_client.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/clob_client.py",
        clob,
        (
            "/books",
            "token_id",
            "sampler_slot",
            "rotation_offset",
            "long_run_coverage_basis",
        ),
    )

    data_api = sources["src/polybot/api/data_client.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/data_client.py",
        data_api,
        (
            "/trades",
            "takerOnly",
            "safety_lag_seconds",
            "overlap_seconds",
            "possible_gap",
            "occurrence_index",
            "sanitize_trade",
        ),
    )

    repository = sources["src/polybot/db/repository.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/db/repository.py",
        repository,
        (
            "market_sweep_memberships",
            "research_config_versions",
            "research_run_events",
            "market_observations",
            "outcome_observations",
            "market_metadata_versions",
            "api_requests",
            "orderbook_snapshots",
            "orderbook_token_attempts",
            "resolution_observations",
            "trade_tape_sweeps",
            "trade_tape_windows",
            "trade_tape_memberships",
            "trade_observations",
            "data_quality_issues",
            "storage_metrics",
            "append-only evidence",
        ),
    )

    run_audit = sources["src/polybot/run_audit.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/run_audit.py",
        run_audit,
        (
            "class ResearchRunAudit",
            "record_research_run_start",
            "record_research_run_event",
            "STARTED",
            "SUCCEEDED",
            "FAILED",
        ),
    )

    combined = "\n".join(sources.values())
    forbidden = (
        "ExecutionLedger",
        "submit_and_record",
        "post_order",
        "place_limit_order",
        "POLYMARKET_PRIVATE_KEY=",
    )
    for token in forbidden:
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    f"research-only source contains {token}",
                )
            )

    readme = _read(directory / "README.md")
    _require_tokens(
        findings,
        strategy,
        "README.md",
        readme,
        (
            "research-full-v1",
            "trades_sim.db",
            "trades_sim_YYYYMMDD.db",
            "OPERATIONS.md",
            "--simulate",
            "--live",
            "compact-v1",
        ),
    )
    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        (
            "POLYBOT_LIFECYCLE_MODE=archive_only",
            "POLYBOT_CADENCE_MINUTES=15",
            "POLYBOT_GAMMA_MIN_LIQUIDITY=10000",
            "POLYBOT_GAMMA_MIN_TOTAL_VOLUME=2000",
            "POLYBOT_GAMMA_MAX_END_HORIZON_DAYS=120",
            "POLYBOT_MIN_FREE_GIB=150",
        ),
    )

    for relative_path in (
        "tests/test_config.py",
        "tests/test_research_safety.py",
        "tests/test_gamma_client.py",
        "tests/test_repository.py",
        "tests/test_lifecycle_mode.py",
        "tests/test_trade_tape.py",
        "tests/test_storage.py",
        "tests/test_run_audit.py",
    ):
        _require_file(findings, strategy, directory / relative_path)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_queue_echo_research_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Raspberry's accountless preregistered hypothesis collector."""

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/collector.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/utils/retry.py",
        "src/polybot/source_digest.py",
    )
    sources = {
        relative_path: _require_file(findings, strategy, directory / relative_path)
        for relative_path in required_sources
    }

    config = sources["src/polybot/config.py"]
    config_tree = _parse_python(findings, strategy, "src/polybot/config.py", config)
    if config_tree is not None:
        for function_name in (
            "_validate_config",
            "load_config",
            "_get_config_value",
            "_get_lifecycle_mode",
            "assert_no_credentials",
        ):
            _require_function(
                findings,
                strategy,
                "src/polybot/config.py",
                config_tree,
                function_name,
            )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/config.py",
        config,
        (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "simulation_mode must be a boolean",
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_FUNDER_ADDRESS",
            "POLYMARKET_SIGNATURE_TYPE",
            "queue-echo-v1",
            "CANONICAL_JOBS",
            "FROZEN_EXPERIMENT_START",
            "FROZEN_EXPERIMENT_END",
            "active frozen preregistration",
            "math.isfinite",
            "archive_only",
        ),
    )

    _require_tokens(
        findings,
        strategy,
        "src/polybot/main.py",
        sources["src/polybot/main.py"],
        ("--live", "--simulate", "config", "status", "health"),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/bot.py",
        sources["src/polybot/bot.py"],
        (
            "ResearchRunAudit.start",
            "exclusive_job_run_lock",
            "record_storage_metric",
            "assert_no_credentials",
            "archive_only",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/gamma_client.py",
        sources["src/polybot/api/gamma_client.py"],
        (
            "/markets/keyset",
            "after_cursor",
            "next_cursor",
            "cursor_complete",
            "received_at",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/clob_client.py",
        sources["src/polybot/api/clob_client.py"],
        (
            "/books",
            "token_id",
            "atomic_pairs",
            "EMPTY_BOOK",
            "MISSING",
            "MALFORMED",
            "RawBookPayload",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/collector.py",
        sources["src/polybot/collector.py"],
        (
            '("DO", 1)',
            '("RE", 2)',
            '("MI", 3)',
            "pair_received_skew_seconds",
            "history_gaps_minutes_json",
            "matched_control_snapshot_id",
            "WINDOW_EXPIRED",
            "int(winner.event_selection_hash, 16)",
            "gzip.compress",
            "payload_sha256",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/db/repository.py",
        sources["src/polybot/db/repository.py"],
        (
            "experiment_contracts",
            "research_config_versions",
            "research_run_events",
            "api_requests",
            "market_sweeps",
            "market_observations",
            "raw_payloads",
            "orderbook_token_attempts",
            "orderbook_snapshots",
            "signal_decisions",
            "research_cases",
            "followup_attempts",
            'terminal = "SELECT 1 FROM followup_attempts f WHERE f.case_id=c.case_id"',
            "cycle_stats",
            "data_quality_issues",
            "storage_metrics",
            "append-only evidence",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/run_audit.py",
        sources["src/polybot/run_audit.py"],
        (
            "class ResearchRunAudit",
            "record_research_run_event",
            "STARTED",
            "SUCCEEDED",
            "FAILED",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/utils/retry.py",
        sources["src/polybot/utils/retry.py"],
        ("RequestException", "ChunkedEncodingError", "Retry-After"),
    )

    combined = "\n".join(sources.values())
    for token in (
        "ExecutionLedger",
        "submit_and_record",
        "post_order",
        "place_limit_order",
        "POLYMARKET_PRIVATE_KEY=",
    ):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    f"research-only source contains {token}",
                )
            )

    readme = _read(directory / "README.md")
    _require_tokens(
        findings,
        strategy,
        "README.md",
        readme,
        (
            "queue-echo-v1",
            "trades_sim.db",
            "OPERATIONS.md",
            "PREREGISTRATION.md",
            "--simulate",
            "--live",
            "polybot-do",
            "polybot-re",
            "polybot-mi",
        ),
    )
    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        (
            "POLYBOT_LIFECYCLE_MODE=archive_only",
            "POLYBOT_SIMULATION_MODE=true",
            "POLYBOT_SHARD_COUNT=3",
            "POLYBOT_EXPERIMENT_START_UTC=2026-08-13T12:00:00Z",
            "POLYBOT_EXPERIMENT_END_UTC=2026-09-12T12:00:00Z",
        ),
    )
    analyzer = _require_file(
        findings, strategy, directory / "scripts/analyze_experiment.py"
    )
    _require_tokens(
        findings,
        strategy,
        "scripts/analyze_experiment.py",
        analyzer,
        (
            "queue-echo-analyzer-v1",
            "BOOTSTRAP_DRAWS = 20_000",
            "BOOTSTRAP_SEED = 20260813",
            "SHADOW_REVIEW_ONLY",
            "STOP_UNRESEARCHABLE",
            "mode=ro&immutable=1",
            "WITH first_attempt AS",
            "same_request_pair_coverage",
            "MI_MINUS_DO_DIAGNOSTIC",
            "mi_minus_do_severe_lower_positive",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/source_digest.py",
        sources["src/polybot/source_digest.py"],
        (
            '"pyproject.toml"',
            '"uv.lock"',
            '"scripts/analyze_experiment.py"',
            '"scripts/verify_external_workspace.py"',
            '"src/polybot/main.py"',
            '"src/polybot/bot.py"',
            '"src/polybot/run_audit.py"',
            '"src/polybot/utils/retry.py"',
        ),
    )
    workspace_preflight = _require_file(
        findings, strategy, directory / "scripts/verify_external_workspace.py"
    )
    _require_tokens(
        findings,
        strategy,
        "scripts/verify_external_workspace.py",
        workspace_preflight,
        (
            "golden-raspberry-apfs-v1",
            "FilesystemType",
            "MountPoint",
            "VolumeUUID",
            '"Internal"',
            ".daily-rsync-workspace.json",
            "workspace canonical path does not match",
            "trusted UUID pin is not stored off-volume",
        ),
    )

    for relative_path in (
        "tests/test_config.py",
        "tests/test_research_safety.py",
        "tests/test_gamma_client.py",
        "tests/test_clob_client.py",
        "tests/test_collector.py",
        "tests/test_repository.py",
        "tests/test_lifecycle_mode.py",
        "tests/test_run_audit.py",
        "tests/test_analyzer.py",
        "tests/test_external_workspace.py",
        "research/frozen-2026-08-13/PREREGISTRATION.md",
        "research/frozen-2026-08-13/MANIFEST.sha256",
        "research/frozen-2026-08-13-external-v2/PREREGISTRATION.md",
        "research/frozen-2026-08-13-external-v2/MANIFEST.sha256",
        "OPERATIONS.md",
    ):
        _require_file(findings, strategy, directory / relative_path)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_last_mile_research_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Strawberry's frozen, accountless Last Mile experiment."""

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/collector.py",
        "src/polybot/api/sampling_client.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/utils/retry.py",
        "src/polybot/source_digest.py",
    )
    sources = {
        relative_path: _require_file(findings, strategy, directory / relative_path)
        for relative_path in required_sources
    }

    config_path = "src/polybot/config.py"
    config = sources[config_path]
    config_tree = _parse_python(findings, strategy, config_path, config)
    if config_tree is not None:
        for function_name in ("_validate_config", "load_config", "assert_no_credentials"):
            _require_function(
                findings,
                strategy,
                config_path,
                config_tree,
                function_name,
            )
        _require_literal_assignment(
            findings,
            strategy,
            config_path,
            config_tree,
            ("ENTRY_THRESHOLDS", "ENTRY_THRESHOLD_GRID", "ENTRY_GRID"),
            (0.90, 0.92, 0.95, 0.97),
        )
        _require_literal_assignment(
            findings,
            strategy,
            config_path,
            config_tree,
            ("STOP_THRESHOLDS", "STOP_THRESHOLD_GRID", "STOP_GRID"),
            (0.80, 0.85, 0.90),
        )
        _require_literal_assignment(
            findings,
            strategy,
            config_path,
            config_tree,
            ("TARGET_THRESHOLDS", "TARGET_THRESHOLD_GRID", "TARGET_GRID"),
            (0.98, 0.99),
        )
        _require_literal_assignment(
            findings,
            strategy,
            config_path,
            config_tree,
            ("PRIMARY_ENTRY_THRESHOLD", "PRIMARY_ENTRY"),
            0.95,
        )
        _require_literal_assignment(
            findings,
            strategy,
            config_path,
            config_tree,
            ("PRIMARY_STOP_THRESHOLD", "PRIMARY_STOP"),
            0.85,
        )
    _require_tokens(
        findings,
        strategy,
        config_path,
        config,
        (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "last-mile-clob-v1",
            "archive_only",
            "math.isfinite",
            "CANONICAL_JOB",
            "FROZEN_ENTRY_START",
            "FROZEN_ENTRY_END",
            "FROZEN_FOLLOWUP_END",
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_FUNDER_ADDRESS",
            "POLYMARKET_SIGNATURE_TYPE",
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_API_PASSPHRASE",
            "CLOB_API_KEY",
            "CLOB_SECRET",
            "CLOB_PASSPHRASE",
        ),
    )
    _require_token_alternatives(
        findings,
        strategy,
        config_path,
        config,
        (
            ("_CREDENTIAL_ENV_KEYS", "CREDENTIAL_ENV_KEYS"),
            ("_ALLOWED_POLYBOT_ENV_KEYS", "ALLOWED_POLYBOT_ENV_KEYS"),
            ("can never run live", "never run live", "live mode is forbidden"),
        ),
    )

    yaml_path = "config.yaml"
    yaml_config = _read(directory / yaml_path)
    for key, expected in (
        ("simulation_mode", True),
        ("lifecycle_mode", "archive_only"),
        ("data_contract", "last-mile-clob-v1"),
        ("cadence_minutes", 10),
        ("cadence_offset_minute", 7),
        ("entry_start_utc", "2026-08-15T04:00:00Z"),
        ("entry_end_utc", "2026-08-22T04:00:00Z"),
        ("followup_end_utc", "2026-09-21T04:00:00Z"),
        ("page_size", 1000),
        ("max_pages", 100),
        ("entry_thresholds", (0.90, 0.92, 0.95, 0.97)),
        ("stop_thresholds", (0.80, 0.85, 0.90)),
        ("target_thresholds", (0.98, 0.99)),
        ("primary_entry_threshold", 0.95),
        ("primary_stop_threshold", 0.85),
        ("simulated_notional_usdc", 5),
    ):
        _require_yaml_value(
            findings,
            strategy,
            yaml_path,
            yaml_config,
            key,
            expected,
        )

    main_source = sources["src/polybot/main.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/main.py",
        main_source,
        (
            ("--live",),
            ("--simulate",),
            ("status",),
            ("health",),
            ("refuses --live", "rejects --live", "--live is forbidden", "never run live"),
        ),
    )

    bot = sources["src/polybot/bot.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/bot.py",
        bot,
        (
            ("ResearchRunAudit.start", "ResearchRunAudit("),
            ("exclusive_job_run_lock", "job_run_lock"),
            ("record_storage_metric", "storage_metric"),
            ("assert_no_credentials",),
            ("archive_only",),
            ("run_cycle", "collect_cycle", "collector.collect"),
        ),
    )

    sampling = sources["src/polybot/api/sampling_client.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/api/sampling_client.py",
        sampling,
        (
            ("/sampling-markets",),
            ("next_cursor", "nextCursor"),
            ("next_cursor", "nextCursor"),
            ("cursor_complete", "terminal_cursor", "cursor_terminal"),
            ("LTE=",),
            ("page-size contract", "page_size"),
            ("repeated cursor",),
            ("received_at", "source_received_at"),
            ("raw_payload", "raw_body", "response.raw", "raw: bytes"),
            ("payload_sha256", "raw_sha256", "response_sha256"),
        ),
    )

    gamma = sources["src/polybot/api/gamma_client.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/api/gamma_client.py",
        gamma,
        (
            ("/markets",),
            ("fetch_metadata",),
            ("fetch_resolutions",),
            ("condition_ids",),
            ("closed",),
            ("include_tag", "includeTag"),
        ),
    )

    clob = sources["src/polybot/api/clob_client.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/api/clob_client.py",
        clob,
        (
            ("/books",),
            ("token_id", "asset_id"),
            ("asks", "ask_levels"),
            ("bids", "bid_levels"),
            ("OBSERVED", "observed"),
            ("MISSING", "missing"),
            ("MALFORMED", "malformed"),
            ("EMPTY", "empty"),
            ("ERROR", "error"),
            ("RawBookPayload", "raw_payload", "raw_body"),
        ),
    )

    collector = sources["src/polybot/collector.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/collector.py",
        collector,
        (
            ("LEFT_CENSORED",),
            ("GAP_CENSORED",),
            (
                "first_observed",
                "first_crossing",
                "first crossing",
                "episode_exists",
                "NEW_CROSSING",
            ),
            ("outcome_token_id", "token_id"),
            ("entry_thresholds", "ENTRY_THRESHOLDS"),
            (
                "prior_received_at",
                "previous_received_at",
                "prior_observed_at",
                "interval_start",
            ),
            ("current_received_at", "observed_at", "interval_end"),
            ("simulated_notional_usdc", "notional_usdc"),
            ("displayed_book_counterfactual", "displayed-book counterfactual"),
            ("ask_vwap", "entry_vwap"),
            ("bid_vwap", "exit_vwap"),
            ("terminal_payout", "resolution_payout", "resolution_observation"),
        ),
    )

    repository = sources["src/polybot/db/repository.py"]
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/db/repository.py",
        repository,
        (
            ("experiment_contracts",),
            ("research_config_versions",),
            ("research_run_events",),
            ("api_requests",),
            ("raw_payloads",),
            ("market_sweeps", "gamma_sweeps"),
            ("market_sweep_memberships", "market_membership_blobs", "gamma_membership_blobs"),
            ("market_sweep_page_lineage", "market_page_lineage", "gamma_page_lineage"),
            ("market_observations", "market_catalog_versions"),
            ("outcome_observations", "outcome_token_observations"),
            ("latest_outcome_state", "crossing_states", "threshold_states"),
            ("crossing_decisions", "threshold_decisions", "signal_decisions"),
            ("candidate_metadata_observations",),
            ("crossing_episodes", "entry_episodes", "hypothetical_episodes"),
            ("orderbook_token_attempts", "book_attempts", "clob_token_attempts"),
            ("orderbook_snapshots", "book_snapshots", "clob_snapshots"),
            ("orderbook_levels", "book_levels", "clob_levels"),
            (
                "episode_path_observations",
                "counterfactual_path_observations",
                "path_observations",
            ),
            ("resolution_observations",),
            ("cycle_stats",),
            ("data_quality_issues",),
            ("storage_metrics",),
            ("append-only evidence", "append_only", "append only"),
            ("_append_only_triggers", "CREATE TRIGGER"),
            ("BEFORE UPDATE",),
            ("BEFORE DELETE",),
            ("latest-state cache", "sole mutable table", "mutable_cache_table"),
            ("UNIQUE (token_id, entry_threshold)", "UNIQUE(token_id,entry_threshold)"),
            ("category", "category_slug"),
            ("sports", "sport", "game_start_time"),
            ("negRisk", "neg_risk"),
            ("multi_outcome", "multioutcome", "outcome_count", "outcomes_json"),
            ("interval_censored", "interval_start", "prior_received_at"),
        ),
    )

    evidence_sources = "\n".join((sampling, gamma, collector, repository))
    _require_token_alternatives(
        findings,
        strategy,
        "Last Mile evidence sources",
        evidence_sources,
        (
            ("category", "category_slug"),
            ("sports", "sport", "game_start_time"),
            ("negRisk", "neg_risk"),
            ("multi_outcome", "multioutcome", "outcome_count", "outcomes_json"),
            ("interval_censored", "interval_start", "prior_received_at"),
            ("condition_id",),
            ("event_id", "event_cluster"),
            (
                "atomic",
                "Atomic",
                "publish_cycle",
                "publish_complete_sweep",
                "commit_complete_sweep",
            ),
        ),
    )

    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/run_audit.py",
        sources["src/polybot/run_audit.py"],
        (
            ("class ResearchRunAudit",),
            ("record_research_run_start", "ResearchRunAudit.start", "def start("),
            ("record_research_run_event",),
            ("STARTED",),
            ("SUCCEEDED",),
            ("FAILED",),
        ),
    )
    _require_token_alternatives(
        findings,
        strategy,
        "src/polybot/utils/retry.py",
        sources["src/polybot/utils/retry.py"],
        (
            ("RequestException",),
            ("ChunkedEncodingError",),
            ("Retry-After", "retry_after"),
        ),
    )

    source_digest = sources["src/polybot/source_digest.py"]
    _require_tokens(
        findings,
        strategy,
        "src/polybot/source_digest.py",
        source_digest,
        (
            "pyproject.toml",
            "uv.lock",
            "config.yaml",
            "STRATEGY.md",
            "research/frozen-2026-08-15-clob/PREREGISTRATION.md",
            "scripts/analyze_experiment.py",
            "scripts/verify_external_workspace.py",
            "src/polybot/main.py",
            "src/polybot/bot.py",
            "src/polybot/config.py",
            "src/polybot/run_audit.py",
            "src/polybot/source_digest.py",
            "src/polybot/api/sampling_client.py",
            "src/polybot/api/gamma_client.py",
            "src/polybot/api/clob_client.py",
            "src/polybot/collector.py",
            "src/polybot/db/repository.py",
            "src/polybot/utils/retry.py",
        ),
    )
    if (directory / "src/polybot/analyzer.py").is_file():
        _require_tokens(
            findings,
            strategy,
            "src/polybot/source_digest.py",
            source_digest,
            ("src/polybot/analyzer.py",),
        )

    forbidden_path_parts = {"execution", "fill", "order", "trader", "wallet"}
    forbidden_source_tokens = (
        "from py_clob_client",
        "import py_clob_client",
        "ExecutionLedger",
        "OrderArgs",
        "MarketOrderArgs",
        "ApiCreds",
        "Trader(",
        "Wallet(",
        "set_api_creds(",
        "submit_and_record(",
        "submit_order(",
        "post_order(",
        "place_order(",
        "place_limit_order(",
        "create_market_order(",
        "create_order(",
        "build_order(",
        "sign_order(",
        "execute_order(",
        "cancel_order(",
        "cancel_all(",
        "get_balance_allowance(",
        "get_api_keys(",
        "execute_buy(",
        "execute_sell(",
        "record_fill(",
        "order_submissions",
        "order_status_events",
        "order_fills",
        "confirmed_fill",
        "realized_pnl",
        '"/order"',
        '"/orders"',
        '"/cancel"',
        '"/balance-allowance"',
        '"/auth/api-key"',
        "'/order'",
        "'/orders'",
        "'/cancel'",
        "'/balance-allowance'",
        "'/auth/api-key'",
        "wallet_address",
        "private_key=",
        "funder_address=",
        "POLYMARKET_PRIVATE_KEY=",
    )
    python_paths = set((directory / "src/polybot").rglob("*.py"))
    python_paths.update((directory / "scripts").glob("*.py"))
    if (directory / "main.py").is_file():
        python_paths.add(directory / "main.py")
    for path in sorted(python_paths):
        relative_path = path.relative_to(directory)
        path_stems = {Path(part).stem.lower() for part in relative_path.parts}
        has_unsafe_path = any(
            any(token in stem for token in forbidden_path_parts - {"order"})
            or (
                "order" in stem
                and "orderbook" not in stem
                and "order_book" not in stem
            )
            for stem in path_stems
        )
        if has_unsafe_path:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    str(relative_path),
                )
            )
        content = _read(path)
        for token in forbidden_source_tokens:
            if token in content:
                findings.append(
                    Finding(
                        strategy,
                        "unsafe_research_order_path",
                        f"{relative_path}: {token}",
                    )
                )
    if "py-clob-client" in _read(directory / "pyproject.toml").lower():
        findings.append(
            Finding(
                strategy,
                "unsafe_research_order_path",
                "pyproject.toml: py-clob-client",
            )
        )

    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        (
            "POLYBOT_LIFECYCLE_MODE=archive_only",
            "POLYBOT_SIMULATION_MODE=true",
        ),
    )
    for credential_key in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY",
        "CLOB_SECRET",
        "CLOB_PASSPHRASE",
    ):
        if f"{credential_key}=" in env_example:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_credentials",
                    f".env.example: {credential_key}",
                )
            )

    readme = _read(directory / "README.md")
    _require_token_alternatives(
        findings,
        strategy,
        "README.md",
        readme,
        (
            ("Last Mile",),
            ("last-mile-clob-v1",),
            ("accountless",),
            ("research-only", "research only"),
            ("10 minute", "10-minute", "10분"),
            ("trades_sim.db",),
            ("OPERATIONS.md",),
            ("--simulate",),
            ("--live",),
            ("$5",),
        ),
    )
    strategy_doc = _read(directory / "STRATEGY.md")
    _require_token_alternatives(
        findings,
        strategy,
        "STRATEGY.md",
        strategy_doc,
        (
            ("Last Mile",),
            ("0.95",),
            ("0.85",),
            ("terminal", "resolution"),
            ("LEFT_CENSORED",),
            ("GAP_CENSORED",),
            ("one-week", "one week", "7-day", "7 day", "7일"),
            ("health",),
            (
                "no live",
                "live 금지",
                "--live",
                "live-deployment approval",
                "live deployment approval",
            ),
        ),
    )

    operations = _require_file(findings, strategy, directory / "OPERATIONS.md")
    _require_token_alternatives(
        findings,
        strategy,
        "OPERATIONS.md",
        operations,
        (
            ("/Volumes/t7",),
            ("verify_external_workspace.py",),
            ("host", "off-volume"),
            ("UUID", "uuid"),
            ("before", "먼저"),
            ("analyze_experiment.py", "polybot analyze"),
            ("health",),
        ),
    )

    prereg_relative = "research/frozen-2026-08-15-clob/PREREGISTRATION.md"
    manifest_relative = "research/frozen-2026-08-15-clob/MANIFEST.sha256"
    prereg_path = directory / prereg_relative
    manifest_path = directory / manifest_relative
    preregistration = _require_file(findings, strategy, prereg_path)
    manifest = _require_file(findings, strategy, manifest_path)
    _require_token_alternatives(
        findings,
        strategy,
        prereg_relative,
        preregistration,
        (
            ("2026-08-15",),
            ("last-mile-clob-v1",),
            ("archive_only",),
            ("accountless",),
            ("10 minutes", "10-minute", "10분"),
            ("/sampling-markets",),
            ("cursor",),
            ("No liquidity", "no liquidity", "no volume", "liquidity"),
            ("category",),
            ("sports",),
            ("negRisk", "neg_risk"),
            ("outcome", "token"),
            ("LEFT_CENSORED",),
            ("GAP_CENSORED",),
            ("interval",),
            ("$5",),
            ("[0.90, 0.92, 0.95, 0.97]", "`0.90`, `0.92`, `0.95`, and `0.97`"),
            ("[none, 0.80, 0.85, 0.90]", "`0.80` and `0.90` stops"),
            ("[none, 0.98, 0.99]", "`0.98`/`0.99`"),
            ("entry `0.95`", "primary entry 0.95"),
            ("stop `0.85`", "primary stop 0.85"),
            ("terminal Gamma payout", "terminal resolution"),
            ("HEALTH_ONLY",),
            ("one-week", "one week", "7-day", "7 day", "7일"),
            ("50 executable episodes", "at least 50 executable episodes"),
            (
                "30 resolved independent event clusters",
                "at least 30 resolved independent event clusters",
                "30 resolved known event clusters",
            ),
            ("90% episode-path coverage", "90% path coverage"),
            ("90% resolution coverage",),
        ),
    )
    if preregistration and manifest:
        try:
            preregistration_sha256 = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
        except OSError as error:
            findings.append(
                Finding(strategy, "invalid_manifest", f"{prereg_relative}: {error}")
            )
        else:
            pinned = False
            for line in manifest.splitlines():
                fields = line.strip().split()
                if len(fields) < 2:
                    continue
                target = fields[-1].lstrip("*")
                if target.endswith("PREREGISTRATION.md"):
                    pinned = fields[0].lower() == preregistration_sha256
                    break
            if not pinned:
                findings.append(
                    Finding(
                        strategy,
                        "invalid_manifest",
                        f"{manifest_relative}: current PREREGISTRATION.md SHA-256",
                    )
                )

    analyzer = _require_file(
        findings, strategy, directory / "scripts/analyze_experiment.py"
    )
    analyzer_module = _read(directory / "src/polybot/analyzer.py")
    analyzer_contract = "\n".join((analyzer, analyzer_module))
    _require_token_alternatives(
        findings,
        strategy,
        "Last Mile analyzer",
        analyzer_contract,
        (
            ("last-mile-analyzer-v1", "golden-strawberry-analysis-v1"),
            ("mode=ro&immutable=1",),
            ("PRAGMA quick_check",),
            ("expected_slots",),
            ("runtime_p95", "p95_runtime", "p95_cycle", '"p95": p95'),
            ("runtime_max", "max_runtime", '"max": maximum'),
            ("raw_linkage", "raw_payload_coverage", "raw_request_linkage"),
            (
                "storage_forecast",
                "forecast_storage",
                "projected_storage",
                "storage_growth_and_forecast",
            ),
            ("crossing_clob_coverage", "crossing_book_coverage", "episode_clob_coverage"),
            ("path_coverage",),
            ("resolution_coverage",),
            ("LEFT_CENSORED", "left_censored"),
            ("GAP_CENSORED", "gap_censored"),
            ("HEALTH_ONLY",),
            ("PILOT_UNDERPOWERED",),
            ("PILOT_CANDIDATE",),
            ("ENTRY_THRESHOLDS", "entry_thresholds"),
            ("STOP_THRESHOLDS", "stop_thresholds"),
            ("TARGET_THRESHOLDS", "target_thresholds"),
            ("for stop in [None]", "stop_options: list[float | None] = [None]"),
            ("for target in [None]", "target_options: list[float | None] = [None]"),
            ("if value < entry_threshold", "stop < entry_threshold"),
            ("if value > entry_threshold", "target > entry_threshold"),
            ("PRIMARY_ENTRY_THRESHOLD", "primary_entry_threshold"),
            ("PRIMARY_STOP_THRESHOLD", "primary_stop_threshold"),
            ("target_threshold=None", "target_threshold is None", '"target_threshold": None'),
            (
                "terminal_payout",
                "resolution_payout",
                "terminal_resolution",
                "TERMINAL_RESOLUTION",
            ),
            (
                "MIN_EXECUTABLE_EPISODES",
                "minimum_executable_episodes",
                "executable_episodes_at_least_50",
            ),
            (
                "MIN_RESOLVED_EVENT_CLUSTERS",
                "minimum_resolved_event_clusters",
                "resolved_independent_event_clusters_at_least_30",
            ),
            (
                "MIN_PATH_COVERAGE",
                "minimum_path_coverage",
                "episode_path_coverage_at_least_90pct",
            ),
            (
                "MIN_RESOLUTION_COVERAGE",
                "minimum_resolution_coverage",
                "resolution_coverage_at_least_90pct",
            ),
            ('"profitability_claim_allowed": False',),
            ('"parameter_winner_selection_allowed": False',),
            ('"target_0_99_is_resolution": False',),
            ("stop-before-target-before-resolution", "STOP_FIRST"),
        ),
    )

    workspace_preflight = _require_file(
        findings, strategy, directory / "scripts/verify_external_workspace.py"
    )
    # Strawberry intentionally reuses Raspberry's already trusted T7 sentinel
    # and off-volume UUID pin instead of creating a second trust identity.
    _require_token_alternatives(
        findings,
        strategy,
        "scripts/verify_external_workspace.py",
        workspace_preflight,
        (
            ("golden-raspberry-apfs-v1",),
            ("FilesystemType",),
            ("MountPoint",),
            ("VolumeUUID",),
            ('"Internal"', "'Internal'"),
            (".daily-rsync-workspace.json",),
            ("host_uuid_pin", "host-uuid-pin"),
            ("off-volume", "stored off-volume"),
            ("st_dev", "_device_id"),
            ("canonical",),
            ("symlink",),
        ),
    )

    test_groups = (
        ("tests/test_config.py",),
        (
            "tests/test_research_safety.py",
            "tests/test_accountless_safety.py",
            "tests/test_safety.py",
            "tests/test_safety_cli.py",
        ),
        ("tests/test_gamma_client.py", "tests/test_gamma.py"),
        (
            "tests/test_clob_client.py",
            "tests/test_orderbook_client.py",
            "tests/test_clob.py",
        ),
        ("tests/test_collector.py", "tests/test_last_mile_collector.py"),
        ("tests/test_repository.py", "tests/test_db_repository.py"),
        (
            "tests/test_lifecycle_mode.py",
            "tests/test_lifecycle.py",
            "tests/test_main.py",
            "tests/test_cli.py",
            "tests/test_safety_cli.py",
        ),
        ("tests/test_run_audit.py", "tests/test_audit.py"),
        ("tests/test_analyzer.py", "tests/test_analyze_experiment.py"),
        ("tests/test_external_workspace.py", "tests/test_workspace.py"),
    )
    test_contents: list[str] = []
    for relative_paths in test_groups:
        content = _require_one_of_files(findings, strategy, directory, relative_paths)
        test_contents.append(content)
    _require_token_alternatives(
        findings,
        strategy,
        "accountless safety tests",
        "\n".join(test_contents),
        (
            ("POLYMARKET_PRIVATE_KEY",),
            ("POLYMARKET_FUNDER_ADDRESS",),
            ("POLYMARKET_SIGNATURE_TYPE",),
            ("--live", "simulation_mode=False", "simulation_mode = False"),
        ),
    )

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_sports_resolution_research_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Golden Black's paired accountless sports experiment."""

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/collector.py",
        "src/polybot/analyzer.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/utils/retry.py",
        "src/polybot/source_digest.py",
    )
    sources = {
        relative: _require_file(findings, strategy, directory / relative)
        for relative in required_sources
    }

    _require_tokens(
        findings,
        strategy,
        "src/polybot/config.py",
        sources["src/polybot/config.py"],
        (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_FUNDER_ADDRESS",
            "POLYMARKET_SIGNATURE_TYPE",
            "sports-resolution-paired-v1",
            "ENTRY_THRESHOLDS = (0.92, 0.94)",
            "STOP_LEVELS = (0.80, 0.70, 0.60)",
            "archive_only",
            "Golden Black can never run live",
            "math.isfinite",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/main.py",
        sources["src/polybot/main.py"],
        ("--live", "--simulate", "config", "run", "status", "health", "analyze"),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/bot.py",
        sources["src/polybot/bot.py"],
        (
            "ResearchRunAudit",
            "exclusive_job_run_lock",
            "record_storage_metric",
            "assert_no_credentials",
            "storage safety gate",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/gamma_client.py",
        sources["src/polybot/api/gamma_client.py"],
        (
            "/events/keyset",
            "after_cursor",
            "next_cursor",
            "liquidity_min",
            "volume_min",
            "end_date_min",
            "cursor_complete",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/api/clob_client.py",
        sources["src/polybot/api/clob_client.py"],
        (
            "/books", "/markets/", "walk_asks", "walk_bids",
            "walk_bids_partial", "remaining_shares", "winner",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/collector.py",
        sources["src/polybot/collector.py"],
        (
            "walk_asks",
            "walk_bids",
            "EPISODE_ALREADY_EXISTS",
            "resolution_due",
            "GAMMA_CURSOR_INCOMPLETE",
            "feeSchedule",
            "HOLD_TO_RESOLUTION",
            "STOP_",
            "PARTIAL_FILL",
            "gap_from_stop",
            "NEG_RISK_UNKNOWN",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/db/repository.py",
        sources["src/polybot/db/repository.py"],
        (
            "research_config_versions",
            "research_run_events",
            "api_requests",
            "raw_payloads",
            "market_sweeps",
            "market_observations",
            "neg_risk",
            "outcome_observations",
            "orderbook_token_attempts",
            "orderbook_snapshots",
            "orderbook_levels",
            "signal_decisions",
            "hypothetical_episodes",
            "episode_path_observations",
            "counterfactual_exit_policies",
            "stop_execution_attempts",
            "counterfactual_stop_exits",
            "resolution_attempts",
            "resolution_observations",
            "data_quality_issues",
            "storage_metrics",
            "append-only evidence",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/analyzer.py",
        sources["src/polybot/analyzer.py"],
        (
            "sports-resolution-paired-analyzer-v1",
            "SHADOW_REVIEW_ONLY",
            "win_rate_wilson_95ci_pct",
            "event_equal_fee_plus_1c_roi_pct",
            "event_equal_fee_plus_1c_roi_bootstrap_95ci_pct",
            "stop_policy_comparison",
            "gap_below_stop_p95",
        ),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/utils/retry.py",
        sources["src/polybot/utils/retry.py"],
        ("RequestException", "ChunkedEncodingError", "trust_env = False"),
    )
    _require_tokens(
        findings,
        strategy,
        "src/polybot/source_digest.py",
        sources["src/polybot/source_digest.py"],
        ('"uv.lock"', '"scripts/analyze_experiment.py"', '"scripts/verify_external_workspace.py"'),
    )

    combined = "\n".join(sources.values())
    for token in (
        "ExecutionLedger",
        "submit_and_record",
        "post_order",
        "place_limit_order",
        "POLYMARKET_PRIVATE_KEY=",
    ):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    f"research-only source contains {token}",
                )
            )

    readme = _read(directory / "README.md")
    _require_tokens(
        findings,
        strategy,
        "README.md",
        readme,
        ("sports-resolution-paired-v1", "trades_sim.db", "OPERATIONS.md", "--simulate", "--live", "0.92", "0.94"),
    )
    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        ("POLYBOT_LIFECYCLE_MODE=archive_only", "POLYBOT_SIMULATION_MODE=true"),
    )
    preregistration = _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-20/PREREGISTRATION.md",
    )
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-20/PREREGISTRATION.md",
        preregistration,
        (
            "[0.92,0.93]", "[0.94,0.95]",
            "STOP_0.80", "STOP_0.70", "STOP_0.60",
            "full displayed bid", "2026-09-19T14:08:00Z", "Accountless only",
        ),
    )
    _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-20/MANIFEST.sha256",
    )
    for relative in (
        "tests/test_config.py",
        "tests/test_gamma_client.py",
        "tests/test_clob_client.py",
        "tests/test_collector.py",
        "tests/test_repository.py",
        "tests/test_safety_cli.py",
        "tests/test_analyzer.py",
        "tests/test_external_workspace.py",
    ):
        _require_file(findings, strategy, directory / relative)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_major_sports_research_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Golden Coconut's accountless five-family observatory."""

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/collector.py",
        "src/polybot/analyzer.py",
        "src/polybot/classifier.py",
        "src/polybot/lifecycle.py",
        "src/polybot/crossings.py",
        "src/polybot/registry.py",
        "src/polybot/run_audit.py",
        "src/polybot/source_digest.py",
        "src/polybot/api/transport.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/api/sports_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/db/migrations/0002_major_sports_lifecycle_v2.sql",
        "src/polybot/db/migrations/0003_major_sports_lifecycle_v3.sql",
        "src/polybot/db/migrations/0004_major_sports_lifecycle_v4.sql",
        "src/polybot/db/migrations/0005_major_sports_lifecycle_v5.sql",
        "src/polybot/db/migrations/0006_major_sports_lifecycle_v6.sql",
        "scripts/verify_external_workspace.py",
    )
    sources = {
        relative: _require_file(findings, strategy, directory / relative)
        for relative in required_sources
    }
    contracts = {
        "src/polybot/config.py": (
            "coconut-major-sports-lifecycle-5m-v7",
            "major-sports-lifecycle-census-v7",
            "major-sports-five-family-lifecycle-2026-08-v6",
            "major-sports-exact-identity-lifecycle-v6",
            "POLYMARKET_",
            "CLOB_",
            "archive_only",
            "threshold grid must be exactly 0.75 through 0.99 by 0.01",
            "database_name must remain daily-rsync canonical trades_sim.db",
            "minimum free space must remain 150 GiB",
            "storage warn/stop ratios must remain 70/80 percent",
            "1000.0",
        ),
        "src/polybot/main.py": (
            "assert_safe_environment",
            "--live",
            "--simulate",
            "--shadow",
            "config",
            "run",
            "status",
            "health",
            "analyze",
            "before argparse, config, logs",
        ),
        "src/polybot/api/transport.py": (
            "CycleBudget",
            "cooperative_seconds",
            "hard_seconds",
            "Retry-After",
            "trust_env = False",
            "public transport requires a credential-free HTTPS URL",
            "AttemptWallTimeout",
            "stream=True",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset",
            "fetch_event",
            "start_time_min",
            "start_time_max",
            "after_cursor",
            "next_cursor",
            "cursor_complete",
            '"closed": "false"',
            '"include_children": "false"',
            '"related_tags": "false"',
            "query_tag_ids",
            "GammaFamilyPool",
            "ThreadPoolExecutor",
        ),
        "src/polybot/lifecycle.py": (
            "DISCOVERED_OPEN",
            "PREGAME",
            "IN_PLAY",
            "POSTPONED",
            "RESOLVED",
            "wall time is intentionally not an input",
            "minutes_to_scheduled_start",
        ),
        "src/polybot/classifier.py": (
            "PRESEASON",
            "moneyline",
            "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED",
            "ESPORTS",
            "EVENT_SEASON_SERIES_SCHEDULE_YEAR_MISMATCH",
            "TEAM_LEAGUE_MISMATCH",
            "draw_descriptors",
        ),
        "src/polybot/registry.py": (
            "schema_version must be 6",
            "discovery tags differ from v6",
            "query tags differ from v6",
            "event series policy differs",
            "101962",
            "101787",
        ),
        "src/polybot/crossings.py": (
            "LEFT_CENSORED",
            "GAP_CENSORED",
            "UPWARD_CROSSING",
        ),
        "src/polybot/collector.py": (
            "all_complete",
            "followup_complete",
            "DISCOVERY_SCHEDULE_MISSING",
            "DISCOVERY_SCHEDULE_INVALID",
            "DISCOVERY_SCHEDULE_OUTSIDE_WINDOW",
            "DISCOVERED_OPEN",
            "liquidity_gate",
            "volume_gate",
            "threshold_vectors",
            '"episodes": episodes',
            '"paths": paths',
            '"anchors": anchors',
            '"sports_clock": sports_clock_rows',
            "resolution_observations",
            "storage safety gate reached STOP",
        ),
        "src/polybot/db/repository.py": (
            "trades_sim_????????.db",
            "threshold_state_carryovers",
            "episode_carryovers",
            "tracked_game_carryovers",
            "append-only",
            "daily_rsync_canonical_filename",
            "PRAGMA quick_check",
        ),
        "src/polybot/analyzer.py": (
            "major-sports-lifecycle-health-v6",
            "config_hash x strategy_source_digest x mode x job_name cohort",
            "NOT_UNIQUELY_SUCCEEDED",
            "FIVE_FAMILY_CURSOR_INCOMPLETE",
            "PRESEASON",
            "threshold_state_strata_by_notional",
            "query_tag_accounting",
            "schedule_window_accounting",
            "schedule_anchor_health",
            "liquidity_discovery_gate",
            "volume_discovery_gate",
            "profitability_conclusion",
        ),
        "src/polybot/source_digest.py": (
            "research/EPOCHS.json",
            "frozen-2026-08-28-v7",
            "research/frozen-2026-08-28-v6/SPORTS_REGISTRY.json",
            "SPORTS_REGISTRY.json",
            "MANIFEST.sha256",
            "verify_external_workspace.py",
            "0006_major_sports_lifecycle_v6.sql",
            "verify_frozen_manifest",
        ),
        "scripts/verify_external_workspace.py": (
            "/Volumes/t7/jenkins/polybot-gold",
            "coconut-major-sports-lifecycle-5m-v7",
            ".daily-rsync-workspace.json",
            '"schema_version": 1',
            '"job": "polybot-gold"',
            "external volume UUID differs from both pins",
        ),
    }
    for relative, tokens in contracts.items():
        _require_tokens(findings, strategy, relative, sources[relative], tokens)

    gamma_source = sources["src/polybot/api/gamma_client.py"]
    for forbidden in (
        "start_date_min",
        "start_date_max",
        "liquidity_num_min",
        "volume_num_min",
        "liquidity_min",
        "volume_min",
        '"offset"',
        "/markets/keyset",
    ):
        if forbidden in gamma_source:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_selection_gate",
                    f"Gamma collector contains forbidden selector {forbidden}",
                )
            )

    combined = "\n".join(sources.values())
    for token in (
        "py_clob_client",
        "OrderArgs",
        "ApiCreds",
        "post_order",
        "place_limit_order",
        "POLYMARKET_PRIVATE_KEY=",
    ):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    f"research-only source contains {token}",
                )
            )

    for relative, tokens in {
        "README.md": (
            "coconut-major-sports-lifecycle-5m-v7",
            "polybot-gold",
            "soccer",
            "MLB",
            "NBA",
            "NFL",
            "NHL",
            "PRESEASON",
            "trades_sim.db",
            "0.75",
            "0.99",
            "$500",
            "$1000",
            "query tag",
            "--live",
        ),
        "STRATEGY.md": (
            "LEFT_CENSORED",
            "GAP_CENSORED",
            "UPWARD_CROSSING",
            "150 GiB/70%/80%",
            "health-only",
            "event-by-ID follow-up",
        ),
        "OPERATIONS.md": (
            "/Volumes/t7/jenkins/polybot-gold",
            "H/5 * * * *",
            "scan/plan/sync/verify",
            "profitability",
            "coconut-major-sports-lifecycle-5m-v7",
        ),
        ".env.example": (
            "POLYBOT_LIFECYCLE_MODE=archive_only",
            "POLYBOT_SIMULATION_MODE=true",
        ),
        "research/frozen-2026-08-28-v6/PREREGISTRATION.md": (
            "soccer, MLB",
            "NBA, NFL",
            "NHL",
            "0.75",
            "0.99",
            "PRESEASON",
            "1,000 USDC",
            "displayed-book research",
            "query tags",
            "semantic event series",
        ),
        "research/frozen-2026-08-28-v6/DATA_CONTRACT.md": (
            "major-sports-lifecycle-census-v6",
            "trades_sim_YYYYMMDD.db",
            "$1000",
            "unique `SUCCEEDED`",
            "query tags",
            "root or season series",
        ),
        "research/frozen-2026-08-28-v7/PREREGISTRATION.md": (
            "coconut-major-sports-lifecycle-5m-v7",
            "isolated Gamma workers",
            "109 seconds",
            "90 seconds",
            "Profitability",
        ),
        "research/frozen-2026-08-28-v7/DATA_CONTRACT.md": (
            "major-sports-lifecycle-census-v7",
            "parallel_family_workers",
            "PRAGMA user_version=6",
            "unique `research_run_events.event_type='SUCCEEDED'`",
        ),
    }.items():
        content = _require_file(findings, strategy, directory / relative)
        _require_tokens(findings, strategy, relative, content, tokens)

    for relative in (
        "research/EPOCHS.json",
        "research/frozen-2026-08-27-v1/PREREGISTRATION.md",
        "research/frozen-2026-08-27-v1/DATA_CONTRACT.md",
        "research/frozen-2026-08-27-v1/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-27-v1/MANIFEST.sha256",
        "research/frozen-2026-08-27-v2/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-27-v2/MANIFEST.sha256",
        "research/frozen-2026-08-27-v2/PREREGISTRATION.md",
        "research/frozen-2026-08-27-v2/DATA_CONTRACT.md",
        "research/frozen-2026-08-28-v3/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-28-v3/MANIFEST.sha256",
        "research/frozen-2026-08-28-v3/PREREGISTRATION.md",
        "research/frozen-2026-08-28-v3/DATA_CONTRACT.md",
        "research/frozen-2026-08-28-v4/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-28-v4/MANIFEST.sha256",
        "research/frozen-2026-08-28-v4/PREREGISTRATION.md",
        "research/frozen-2026-08-28-v4/DATA_CONTRACT.md",
        "research/frozen-2026-08-28-v5/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-28-v5/MANIFEST.sha256",
        "research/frozen-2026-08-28-v5/PREREGISTRATION.md",
        "research/frozen-2026-08-28-v5/DATA_CONTRACT.md",
        "research/frozen-2026-08-28-v6/SPORTS_REGISTRY.json",
        "research/frozen-2026-08-28-v6/MANIFEST.sha256",
        "research/frozen-2026-08-28-v6/PREREGISTRATION.md",
        "research/frozen-2026-08-28-v6/DATA_CONTRACT.md",
        "research/frozen-2026-08-28-v7/MANIFEST.sha256",
        "research/frozen-2026-08-28-v7/PREREGISTRATION.md",
        "research/frozen-2026-08-28-v7/DATA_CONTRACT.md",
        "tests/fixtures/major_sports_lifecycle_cases.json",
        "tests/test_config_safety.py",
        "tests/test_registry_classifier.py",
        "tests/test_gamma_client.py",
        "tests/test_gamma_family_pool.py",
        "tests/test_books_crossings_resolution.py",
        "tests/test_repository.py",
        "tests/test_collector_analyzer.py",
        "tests/test_budget_storage_skew.py",
        "tests/test_workspace_contract.py",
        "tests/test_document_static_contract.py",
    ):
        _require_file(findings, strategy, directory / relative)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_inplay_match_winner_research_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Golden Watermelon's accountless cadence experiment."""

    required_sources = (
        "src/polybot/config.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/collector.py",
        "src/polybot/league_classifier.py",
        "src/polybot/analyzer.py",
        "src/polybot/api/gamma_client.py",
        "src/polybot/api/clob_client.py",
        "src/polybot/api/sports_client.py",
        "src/polybot/db/repository.py",
        "src/polybot/utils/retry.py",
        "src/polybot/source_digest.py",
    )
    sources = {
        relative: _require_file(findings, strategy, directory / relative)
        for relative in required_sources
    }
    contracts = {
        "src/polybot/config.py": (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "POLYMARKET_PRIVATE_KEY",
            "watermelon-soccer-mlb-nhl-inplay-match-winner-v5",
            "soccer-inplay-elite-competition-match-winner-v3",
            "soccer-inplay-major-league-match-winner-v1",
            "watermelon-white-1m-v4a",
            "watermelon-grey-5m-v4a",
            "watermelon-white-1m-v3d",
            "watermelon-grey-5m-v3d",
            "MAJOR_SOCCER_LEAGUES",
            "FROZEN_CUP_IDENTITIES",
            "FROZEN_DIRECT_SPORT_IDENTITIES",
            "MLB_TAG_ID",
            "NHL_TAG_ID",
            "UEFA Champions League",
            "UEFA Europa League",
            "FAST_1M",
            "CONTROL_5M",
            "ENTRY_THRESHOLDS = (0.95, 0.96, 0.97, 0.98, 0.99)",
            "STOP_LEVELS = (0.95, 0.93, 0.90, 0.85, 0.80, 0.70)",
            "LATE_ENTRY_MINUTE_FLOORS = (75, 80, 85)",
            "NOTIONAL_LADDER_USDC",
            "archive_only",
            "can never run live",
            "math.isfinite",
        ),
        "src/polybot/main.py": (
            "--live", "--simulate", "config", "run", "status", "health",
            "analyze", "analyze_databases",
        ),
        "src/polybot/bot.py": (
            "ResearchRunAudit", "exclusive_job_run_lock",
            "record_storage_metric", "assert_no_credentials",
            "storage safety gate",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset", "tag_slug", '"live": "true"', "events",
            "after_cursor", "next_cursor", "cursor_complete",
        ),
        "src/polybot/api/clob_client.py": (
            "/books", "/markets/", "walk_asks", "walk_bids",
            "walk_bids_partial", "remaining_shares", "winner",
        ),
        "src/polybot/collector.py": (
            "classify_match_winner", "classify_soccer_league",
            "ESPORTS_EXCLUDED", "LEAGUE_NOT_ALLOWED",
            "NOT_TOP_LEVEL_MONEYLINE",
            "ALIGNED_TWO_TEAM_MONEYLINE", "NEGRISK_TEAM_WIN_YES",
            "DIRECT_TWO_TEAM_MONEYLINE",
            "FIRST_FULL_DEPTH_ABOVE",
            "UPWARD_CROSS", "HOLD_TO_RESOLUTION", "PARTIAL_FILL",
            "gap_from_stop", "resolution_due", "GAMMA_CURSOR_INCOMPLETE",
            "SPORTS_WEBSOCKET_COVERAGE_GAP", "SPORTS_CLOCK_UPDATE",
            "SOURCE_CLOCK_COVERAGE_GAP", "SOURCE_CLOCK_MINUTE_FIELD_GAP",
            "RESULT_TRIAD_COVERAGE_GAP",
            "late_entry_minute_floors", "notional_ladder_usdc",
            "Entry and exit cannot use the same displayed book",
        ),
        "src/polybot/api/sports_client.py": (
            "SportsClockClient", "SportsClockBatch", "elapsed", "period",
            "sports_clock_websocket_snapshot", 'websocket.send("pong")',
        ),
        "src/polybot/league_classifier.py": (
            "classify_sports_event", "US_DIRECT_TWO_OUTCOME",
            "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED", "ESPORTS_EXCLUDED",
        ),
        "src/polybot/db/repository.py": (
            "research_config_versions", "research_run_events", "api_requests",
            "raw_payloads", "market_sweeps", "market_observations",
            "sport_family", "league_code", "league_name", "series_slug",
            "match_winner_class", "eligible_outcome_indices_json",
            "outcome_observations", "orderbook_snapshots", "orderbook_levels",
            "signal_decisions", "hypothetical_episodes",
            "episode_path_observations", "counterfactual_exit_policies",
            "stop_execution_attempts", "resolution_observations",
            "storage_metrics", "append-only evidence",
        ),
        "src/polybot/analyzer.py": (
            "watermelon-major-sports-analyzer-v4a",
            "watermelon-major-sports-cadence-pair-v4a",
            "soccer-elite-competition-analyzer-v3d",
            "soccer-elite-competition-cadence-pair-v3d", "league_coverage",
            "cursor_complete_pct", "observed_book_pct", "entry_thresholds",
            "stop_policy_comparison", "matched_episode_keys",
            "sports_clock_evidence", "result_triad_evidence",
            "notional_depth_evidence",
            "DISPLAYED_BOOK_COUNTERFACTUAL_ONLY", "cohort_runs",
        ),
        "src/polybot/utils/retry.py": (
            "RequestException", "ChunkedEncodingError", "trust_env = False",
        ),
        "src/polybot/source_digest.py": (
            '"uv.lock"', '"scripts/analyze_experiment.py"',
            '"scripts/verify_external_workspace.py"',
        ),
    }
    for relative, tokens in contracts.items():
        _require_tokens(findings, strategy, relative, sources[relative], tokens)

    gamma_source = sources["src/polybot/api/gamma_client.py"]
    for forbidden in (
        "liquidity_num_min", "volume_num_min", "liquidity_min",
        "volume_min", '"offset"', "/markets/keyset",
    ):
        if forbidden in gamma_source:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_selection_gate",
                    f"Gamma collector contains forbidden selector {forbidden}",
                )
            )

    combined = "\n".join(sources.values())
    for token in (
        "ExecutionLedger", "submit_and_record", "post_order",
        "place_limit_order", "POLYMARKET_PRIVATE_KEY=",
    ):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_research_order_path",
                    f"research-only source contains {token}",
                )
            )

    readme = _read(directory / "README.md")
    _require_tokens(
        findings,
        strategy,
        "README.md",
        readme,
        (
            "watermelon-soccer-mlb-nhl-inplay-match-winner-v5",
            "watermelon-white-1m-v4a", "watermelon-grey-5m-v4a",
            "Soccer", "MLB", "NHL", "World Series", "Stanley Cup Final",
            "MiLB", "AHL", "NCAA", "--simulate", "--live", "e-sports",
            "UEFA Champions League", "UEFA Europa League",
            "75/80/85", "$1000", "actual fill",
        ),
    )
    env_example = _read(directory / ".env.example")
    _require_tokens(
        findings,
        strategy,
        ".env.example",
        env_example,
        ("POLYBOT_LIFECYCLE_MODE=archive_only", "POLYBOT_SIMULATION_MODE=true"),
    )
    preregistration = _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-29-major-sports-v4a/PREREGISTRATION.md",
    )
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-29-major-sports-v4a/PREREGISTRATION.md",
        preregistration,
        (
            "2026-08-29T04:00:00Z", "2026-09-05T04:00:00Z",
            "0.95/0.96/0.97/0.98/0.99", "0.95/0.93/0.90/0.85/0.80/0.70",
            "FAST_1M", "CONTROL_5M", "displayed-book counterfactual",
            "Soccer", "MLB", "NHL", "World Series", "Stanley Cup Final",
            "75/80/85", "$1000", "HIGH",
        ),
    )
    _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-29-major-sports-v4a/MANIFEST.sha256",
    )
    _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-26-uefa-clock-scale-v3c/PREREGISTRATION.md",
    )
    _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-23/PREREGISTRATION.md",
    )
    for relative in (
        "tests/test_config.py", "tests/test_gamma_client.py",
        "tests/test_clob_client.py", "tests/test_collector.py",
        "tests/test_repository.py", "tests/test_safety_cli.py",
        "tests/test_analyzer.py", "tests/test_external_workspace.py",
        "tests/test_document_contract.py",
        "tests/test_sports_client.py",
    ):
        _require_file(findings, strategy, directory / relative)

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_tangerine_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate the bounded event-keyset live companion contract."""

    contracts = {
        "README.md": (
            "polybot-orange",
            "polybot-fox",
            "tangerine-live-a-94",
            "tangerine-live-b-92",
            "수동 포지션",
        ),
        "STRATEGY.md": (
            "[0.94,0.95]",
            "[0.92,0.93]",
            "FOK BUY",
            "HOLD_TO_RESOLUTION",
            "entry_episodes",
        ),
        "OPERATIONS.md": (
            "H/5 * * * *",
            "Clean before checkout",
            "polybot-black",
            "/Volumes/t7/jenkins/polybot-black",
            "daily-rsync verify",
        ),
        "src/polybot/config.py": (
            "FROZEN_ARMS",
            "FROZEN_START_UTC",
            "strategy_source_digest",
            "preregistration_sha256",
            "Golden Tangerine live notional must remain exactly $5",
            "exposure limits are frozen at 3/1/1",
            "must evaluate both binary outcomes",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset",
            "tag_slug",
            "liquidity_min",
            "volume_min",
            "end_date_min",
            "end_date_max",
            "after_cursor",
            "cursor_complete",
            "membership_digest_sha256",
        ),
        "src/polybot/api/clob_client.py": (
            "BuyBookWalk",
            "full $5 displayed ask depth is unavailable",
            "get_tick_size",
            "OrderType.FOK",
        ),
        "src/polybot/strategy/scanner.py": (
            "get_aligned_binary_outcomes",
            "claim_entry_episode",
            "not_first_in_arm_observation",
            "get_buy_book_walks",
        ),
        "src/polybot/strategy/trader.py": (
            "entry_episode_id",
            "link_entry_episode_trade",
            "hold to resolution",
            "payouts_by_outcome",
            "place_fok_buy",
        ),
        "src/polybot/source_digest.py": (
            "compute_strategy_source_digest",
            "ACTIVE_PREREGISTRATION",
            "polybot_observability",
        ),
        "tests/test_scanner.py": (
            "test_arm_a_claims_only_first_exact_book_observation",
            "test_arm_b_can_select_no_without_yes_only_bias",
        ),
        "tests/test_trader.py": (
            "test_existing_manual_wallet_positions_are_never_adopted_or_sold",
            "test_named_outcome_resolution_uses_selected_payout_without_synthetic_sell",
        ),
    }
    for relative_path, tokens in contracts.items():
        content = _require_file(findings, strategy, directory / relative_path)
        _require_tokens(findings, strategy, relative_path, content, tokens)

    for relative_path in (
        "tests/test_api_contracts.py",
        "tests/test_config.py",
        "tests/test_filters_signals.py",
        "tests/test_lifecycle_mode.py",
        "src/polybot/source_digest.py",
        "research/frozen-2026-08-20/PREREGISTRATION.md",
        "research/frozen-2026-08-20/MANIFEST.sha256",
    ):
        _require_file(findings, strategy, directory / relative_path)

    trader_content = _read(directory / "src/polybot/strategy/trader.py")
    trader_tree = _parse_python(
        findings, strategy, "src/polybot/strategy/trader.py", trader_content
    )
    if trader_tree is not None:
        execute_sell = _function(trader_tree, "execute_sell", class_name="Trader")
        if execute_sell is None:
            findings.append(
                Finding(strategy, "missing_contract", "Trader.execute_sell")
            )
        elif any(
            name.endswith("place_limit_order") for name, _call in _calls(execute_sell)
        ):
            findings.append(
                Finding(
                    strategy,
                    "unsafe_contract",
                    "Trader.execute_sell must not submit a pre-resolution order",
                )
            )

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_watermelon_live_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate the shared Soccer/MLB/NHL in-play live A/B contract."""

    contracts = {
        "README.md": (
            "polybot-cat",
            "polybot-dog",
            "watermelon-live-cat-96-1m-v2h",
            "watermelon-live-dog-99-1m-v2h",
            "watermelon-live-bear-mlb-96-1m-v3a",
            "watermelon-live-tiger-mlb-99-1m-v3a",
            "watermelon-live-lion-nhl-96-1m-v3a",
            "watermelon-live-wolf-nhl-99-1m-v3a",
            "EPL",
            "Bundesliga",
            "Ligue 1",
            "LaLiga",
            "MLS",
            "Serie A",
            "UEFA Champions League",
            "UEFA Europa League",
            "World Series",
            "Stanley Cup Final",
            "MiLB",
            "AHL",
            "close_only",
            "archive_only",
            "strategy-wind-down-playbook.md",
        ),
        "STRATEGY.md": (
            "[0.96,0.999]",
            "[0.99,0.999]",
            "HOME/DRAW/AWAY",
            "FOK BUY",
            "FOK SELL",
            "full-depth",
            "0.70",
            "SELL-only intent",
            "180분",
            "QUARANTINED",
        ),
        "OPERATIONS.md": (
            "* * * * *",
            "Clean before checkout",
            "watermelon-live-cat-96-1m-v2h",
            "watermelon-live-dog-99-1m-v2h",
            "watermelon-live-bear-mlb-96-1m-v3a",
            "watermelon-live-tiger-mlb-99-1m-v3a",
            "watermelon-live-lion-nhl-96-1m-v3a",
            "watermelon-live-wolf-nhl-99-1m-v3a",
            "daily-rsync verify",
        ),
        "src/polybot/bot.py": (
            "pending_buy_event_isolated",
            "untracked_buy_exposure_isolated",
            "unresolved_buy_outcome_isolated",
            "buy_reconciliation_error_isolated",
            "pending_sell_event_isolated",
            "stop_sell_unknown_exposure_isolated",
            "sell_reconciliation_error_isolated",
            "quarantined_position",
            "open_buy_fill_or_fee_evidence_gap",
            "league_identity_metadata_drift",
            "unresolved_sell_outcome",
            "entry_blocked_candidates",
            "get_entry_capacity_state",
            "Release every cycle-scoped resource even if one cleanup fails",
            "database_engine",
        ),
        "src/polybot/db/repository.py": (
            "get_untracked_buy_reservation_count",
            "get_open_buy_evidence_gap_count",
            "create_recovered_orphan_trade",
            "untracked_buy_reservations",
            "incomplete membership checkpoint",
            "fee_taker_only",
            "legacy fee_rate_bps=0 placeholder",
            "self.session.rollback()",
            "response_status",
        ),
        "src/polybot/db/models.py": (
            "sport_family",
            "league_code",
            "market_tags_json",
            "target_buy_amount_usdc",
            "selected_buy_amount_usdc",
            "max_executable_buy_notional_usdc",
            "buy_notional_fallback_reason",
        ),
        "src/polybot/config.py": (
            "FROZEN_ARMS",
            "FROZEN_START_UTC",
            "FROZEN_ENTRY_END_UTC",
            "FROZEN_FOLLOWUP_END_UTC",
            "FROZEN_LEAGUE_IDENTITIES",
            "FROZEN_CUP_IDENTITIES",
            "DIRECT_SPORT_IDENTITIES",
            "SPORT_FAMILY_TAG_IDS",
            "LEAGUE_MAPPING_SHA256",
            "strategy_source_digest",
            "preregistration_sha256",
            "target notional must be $5-$1000",
            "exposure limits are frozen at 20/1/5",
            "emergency stop_price is frozen at 0.70",
            "failed stop SELL quarantine timeout is frozen at 180 minutes",
            "YES tokens / direct winner tokens must remain winner-only",
        ),
        "src/polybot/league_classifier.py": (
            "classify_soccer_event",
            "ESPORTS_TAG_ID",
            "LEAGUE_MAPPING_SHA256",
            "UEFA_CUP",
            "US_DIRECT_TWO_OUTCOME",
            "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset",
            '"live": "true"',
            "tag_id",
            "related_tags",
            "after_cursor",
            "next_cursor",
            "cursor_complete",
            "membership_digest_sha256",
            "outside_in_play_window",
            "max_retries=1",
            "READ_TIMEOUT_SECONDS = 5.0",
            '"liquidity_min"',
            '"volume_min"',
            "self.session.close()",
        ),
        "src/polybot/api/clob_client.py": (
            "AdaptiveBuySelection",
            "_select_adaptive_buy_from_book",
            "BuyBookWalk",
            "SellBookWalk",
            "Gamma and CLOB dynamic fee parameters do not match",
            "resolve_dynamic_fee_evidence",
            "fee_amount_usdc",
            "full $5 displayed ask depth is unavailable",
            "full displayed bid depth for stop shares is unavailable",
            "get_sell_book_walk",
            "OrderType.FOK",
            "signed limit order share quantity drift",
            "signed FOK BUY does not preserve exact maker USDC",
            '"intent_autoresolved": 0',
            "process-global HTTP/2 pool",
            'getattr(helpers, "_http_client", None)',
        ),
        "src/polybot/strategy/filters.py": (
            "match_result_reason",
            "get_match_result_yes",
            "sportsMarketType",
            "parentEventId",
            "HOME",
            "DRAW",
            "AWAY",
        ),
        "src/polybot/strategy/scanner.py": (
            "claim_entry_episode",
            "not_first_in_arm_observation",
            "multiple_result_tokens_above_threshold",
            "get_buy_book_walks",
            "entry_period_open",
        ),
        "src/polybot/strategy/trader.py": (
            "ADAPTIVE_BUY_NOTIONAL_LADDER_USDC",
            "selected_buy_amount_usdc",
            "place_fok_buy",
            "get_sell_book_walk",
            'order_type="FOK"',
            "absolute_stop_pending_confirmed_fill",
            "stop_sell_terminal_zero_fill",
            "get_exact_buy_fill_evidence",
            "get_exact_sell_fill_evidence",
            "recover_orphan_buys",
            "sell_residual_shares",
            "_resolution_fill_ready",
            "_sdk_sellable_shares",
            "_orphan_catalog_identity_matches",
            "live gap-stop allowed after dual lifecycle proof",
            "STOP_SELL_QUARANTINE_REASON",
            "_STOP_SELL_FAILURE_RETRY_REASON",
        ),
        "src/polybot/source_digest.py": (
            "compute_strategy_source_digest",
            "ACTIVE_PREREGISTRATION",
            "polybot_observability",
        ),
        "tests/test_scanner.py": (
            "test_cat_claims_only_first_exact_yes_book_observation",
            "test_dog_99_arm_accepts_draw_yes_and_never_no_token",
            "test_multiple_results_above_threshold_for_one_event_fail_closed",
            "test_detail_checkpoint_keeps_excluded_identity_and_repairs_legacy_gap",
        ),
        "tests/test_trader.py": (
            "test_buy_revalidates_baseline_and_submits_adaptive_fok",
            "test_stop_uses_fresh_bid_and_submits_fok_sell",
            "test_stop_walk_uses_sdk_sellable_size_and_records_residual_dust",
            "test_orphan_catalog_identity_requires_yes_token_event_and_snapshot_alignment",
            "test_clob_one_hot_resolution_fallback_settles_confirmed_own_trade",
            "test_live_gap_beyond_normal_stop_limit_uses_first_full_depth_book",
            "test_continuous_stop_failure_is_quarantined_after_three_hours",
            "test_sell_ledger_failure_is_immediately_isolated_without_raising",
        ),
        "tests/test_api_contracts.py": (
            "test_adaptive_buy_uses_largest_fully_executable_ladder_amount",
            "test_full_share_sell_walk_uses_deeper_bids_and_market_limit",
            "test_shallow_stop_book_is_censored_not_partially_sold",
            "test_gamma_exclusion_bucket_preserves_rejected_sport_identity",
            "test_gamma_rate_limit_fails_fast_without_in_process_retry",
            "test_order_reconciliation_reports_health_without_unsafe_intent_autoresolve",
            "test_order_reconciliation_attributes_sell_error_without_hiding_total",
            "test_live_sell_ledger_uses_signed_two_decimal_share_quantity",
            "test_gamma_accepts_exact_cross_league_uefa_identity",
            "test_gamma_rejects_uefa_advancement_scope_before_trading",
            "test_live_exact_usdc_fok_buy_rejects_excess_taker_precision_before_post",
            "test_gamma_close_releases_per_cycle_keepalive_pool",
            "test_clob_close_releases_sdk_process_global_http2_pool",
        ),
        "src/polybot/utils/deadline.py": (
            "elapsed_time_can_suppress_requests",
            "fixed finite socket timeouts",
            "without installing or adopting a process alarm",
        ),
        "src/polybot/utils/run_lock.py": (
            "exclusive_job_run_lock",
            "LOCK_NB",
        ),
        "tests/test_cycle_deadline.py": (
            "test_cycle_runtime_never_suppresses_network_after_target",
        ),
        "tests/test_run_lock.py": (
            "test_second_process_skips_while_runtime_lock_is_held",
        ),
        "src/polybot/main.py": (
            "cycle resources closed",
            "bot.close()",
        ),
    }
    for relative_path, tokens in contracts.items():
        content = _require_file(findings, strategy, directory / relative_path)
        _require_tokens(findings, strategy, relative_path, content, tokens)

    for relative_path in (
        ".env.example",
        "config.yaml",
        "tests/test_config.py",
        "tests/test_filters_signals.py",
        "tests/test_lifecycle_mode.py",
        "tests/test_source_digest.py",
        "tests/test_cycle_deadline.py",
        "tests/test_run_lock.py",
        "research/frozen-2026-08-24/PREREGISTRATION.md",
        "research/frozen-2026-08-24/MANIFEST.sha256",
        "research/frozen-2026-08-24-1m-v2a/PREREGISTRATION.md",
        "research/frozen-2026-08-24-1m-v2a/MANIFEST.sha256",
        "research/frozen-2026-08-25-safety-v2b/PREREGISTRATION.md",
        "research/frozen-2026-08-25-safety-v2b/MANIFEST.sha256",
        "research/frozen-2026-08-25-fee-v2c/PREREGISTRATION.md",
        "research/frozen-2026-08-25-fee-v2c/MANIFEST.sha256",
        "research/frozen-2026-08-25-safety-v2d/PREREGISTRATION.md",
        "research/frozen-2026-08-25-safety-v2d/MANIFEST.sha256",
        "research/frozen-2026-08-26-uefa-v2h/PREREGISTRATION.md",
        "research/frozen-2026-08-26-uefa-v2h/MANIFEST.sha256",
        "research/frozen-2026-08-29-major-sports-v3a/PREREGISTRATION.md",
        "research/frozen-2026-08-29-major-sports-v3a/MANIFEST.sha256",
        "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/PREREGISTRATION.md",
        "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/MANIFEST.sha256",
    ):
        _require_file(findings, strategy, directory / relative_path)

    config_yaml = _read(directory / "config.yaml")
    for key, expected in (
        ("buy_amount_usdc", 5.0),
        ("min_liquidity", 5000),
        ("min_volume_24h", 0),
        ("min_cumulative_volume", 5000),
        ("max_positions", 20),
        ("max_event_positions", 1),
        ("max_new_positions_per_cycle", 5),
        ("stop_price", 0.70),
        ("max_entry_drawdown", 0.30),
    ):
        _require_yaml_value(
            findings, strategy, "config.yaml", config_yaml, key, expected
        )

    trader_content = _read(directory / "src/polybot/strategy/trader.py")
    if "_place_sell_with_balance_retry" in trader_content:
        findings.append(
            Finding(
                strategy,
                "unsafe_contract",
                "stop must not shrink a SELL and strand a residual position",
            )
        )
    combined = "\n".join(
        _read(path)
        for path in (
            directory / "src/polybot/bot.py",
            directory / "src/polybot/strategy/scanner.py",
            directory / "src/polybot/strategy/trader.py",
        )
    )
    for token in ("get_positions(", "wallet_position", "account_wide"):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_wallet_adoption_path",
                    f"live runtime contains {token}",
                )
            )

    prereg_path = (
        directory
        / "research/frozen-2026-08-25-safety-v2f/PREREGISTRATION.md"
    )
    manifest_path = (
        directory
        / "research/frozen-2026-08-25-safety-v2f/MANIFEST.sha256"
    )
    preregistration = _read(prereg_path)
    manifest = _read(manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-25-safety-v2f/PREREGISTRATION.md",
        preregistration,
        (
            "2026-08-24T13:00:00Z",
            "2026-08-31T13:00:00Z",
            "2026-09-07T13:00:00Z",
            "[0.98,0.999]",
            "[0.99,0.999]",
            "0.70",
            "full-holding FOK stop SELL",
            "does not select a threshold winner",
            "watermelon-live-cat-98-1m-v2f",
            "watermelon-live-dog-99-1m-v2f",
            "QUARANTINED",
            "operator proof of no order",
            "signed maker/taker amounts",
            "0.5/0.5",
            "does not select a threshold winner",
            "Session rollback",
            "SDK SELL dust",
            "catalog identity",
            "60-second Retry-After",
        ),
    )
    if preregistration and manifest:
        digest = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-08-25-safety-v2d/MANIFEST.sha256",
                )
            )

    active_prereg_path = directory / (
        "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/PREREGISTRATION.md"
    )
    active_manifest_path = (
        directory
        / "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/MANIFEST.sha256"
    )
    active_preregistration = _read(active_prereg_path)
    active_manifest = _read(active_manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/PREREGISTRATION.md",
        active_preregistration,
        (
            "2026-08-29T04:00:00Z", "2026-09-05T04:00:00Z",
            "watermelon-live-cat-96-1m-v2h",
            "watermelon-live-dog-99-1m-v2h",
            "watermelon-live-bear-mlb-96-1m-v3a",
            "watermelon-live-tiger-mlb-99-1m-v3a",
            "watermelon-live-lion-nhl-96-1m-v3a",
            "watermelon-live-wolf-nhl-99-1m-v3a",
            "[0.96,0.999]", "[0.99,0.999]",
            "post_only_mode", "trading is disabled", "NO_ORDER_CREATED",
            "max(0.70, confirmed entry VWAP-0.30)",
            "$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000",
            "sport_family", "selected_buy_amount_usdc", "50초",
        ),
    )
    if active_preregistration and active_manifest:
        digest = hashlib.sha256(active_prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in active_manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/MANIFEST.sha256",
                )
            )

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_peach_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate the soccer kickoff direct-six-book live/shadow contract."""

    contracts = {
        "README.md": (
            "polybot-eco",
            "polybot-fruit",
            "polybot-grey",
            "peach-live-eco-3pp-1m-v1",
            "peach-live-fruit-5pp-1m-v1",
            "peach-shadow-1m-v1",
            "직접 YES/NO 6개",
            "0~10분",
            "+0.03",
            "+0.05",
            "-0.10",
            "2026-09-13T00:00:00Z",
        ),
        "STRATEGY.md": (
            "HOME/DRAW/AWAY",
            "direct token",
            "0.60",
            "0.94",
            "FOK",
            "source minute 80",
            "event당 filled/uncertain entry 한 번",
            "180분",
            "QUARANTINED",
            "actual fill",
        ),
        "OPERATIONS.md": (
            "/Volumes/t7/jenkins/polybot-eco",
            "/Volumes/t7/jenkins/polybot-fruit",
            "/Volumes/t7/jenkins/polybot-grey",
            "* * * * *",
            "Concurrent build",
            "clean build는 사용하지 않는다",
            "daily-rsync verify",
        ),
        "src/polybot/config.py": (
            "FROZEN_JOB_TAKE_PROFIT",
            "peach-live-eco-3pp-1m-v1",
            "peach-live-fruit-5pp-1m-v1",
            "peach-shadow-1m-v1",
            "Golden Peach is frozen to soccer",
            "Golden Peach notional must remain exactly $5",
            "Golden Peach must inspect direct YES and NO books",
            "failed stop SELL quarantine timeout is frozen at 180 minutes",
            "strategy_source_digest",
            "preregistration_sha256",
        ),
        "src/polybot/strategy/scanner.py": (
            "get_source_regulation_minute",
            "six_direct_executable_books_required",
            "leader_margin_too_small",
            "claim_entry_episode",
            "event_token_ids",
            "direct YES/NO snapshots",
        ),
        "src/polybot/strategy/trader.py": (
            "fresh_six_token_leader_changed",
            "place_fok_buy",
            "late_half_target",
            "CURRENT_SOURCE_CLOCK_UNPROVEN",
            "continuous {exit_signal} failure remained triggered",
            "STOP_SELL_QUARANTINE_REASON",
            "get_exact_buy_fill_evidence",
            "get_exact_sell_fill_evidence",
            'order_type="FOK"',
            "live gap-stop allowed after dual lifecycle proof",
        ),
        "src/polybot/db/repository.py": (
            "event_already_traded",
            "PROVEN_ZERO_FILL_RETRYABLE",
            "get_entry_capacity_state",
            "get_isolated_stop_sell_trades",
            "QUARANTINED means economic exposure is unknown",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset",
            '"live": "true"',
            "after_cursor",
            "cursor_complete",
            "membership_digest_sha256",
            '"liquidity_min"',
            '"volume_min"',
        ),
        "src/polybot/api/clob_client.py": (
            "BuyBookWalk",
            "SellBookWalk",
            "get_buy_book_walks",
            "get_cached_book_evidence",
            "signed FOK BUY does not preserve exact maker USDC",
            "signed limit order share quantity drift",
            "resolve_dynamic_fee_evidence",
        ),
        "tests/test_scanner.py": (
            "test_complete_six_token_event_selects_direct_no_leader",
            "test_entry_requires_actual_source_clock_within_first_ten_minutes",
            "test_missing_one_direct_book_fails_closed",
            "test_tied_leader_margin_fails_closed",
        ),
        "tests/test_trader.py": (
            "test_buy_refuses_any_prior_event_trade",
            "test_continuous_stop_failure_is_quarantined_after_three_hours",
            "test_missing_source_clock_cannot_create_a_late_stop",
            "test_minute_eighty_allows_half_target_but_disables_new_stop",
            "test_unrelated_event_exits_are_not_blocked_by_first_sell",
        ),
        "tests/test_lifecycle_mode.py": (
            "test_active_caps_one_cycle_at_five_new_positions",
            "test_active_isolates_sell_intent_without_blocking_unrelated_buy",
            "test_active_isolated_stop_quarantine_reserves_capacity_but_not_global_gate",
        ),
        "scripts/replay_watermelon_kickoff_leader.py": (
            "mode=ro",
            "synthetic NO book",
            "NO books are synthetic complements",
            "parameter grid is exploratory",
            "PRAGMA quick_check",
        ),
        "src/polybot/source_digest.py": (
            "compute_strategy_source_digest",
            "ACTIVE_PREREGISTRATION",
            "polybot_observability",
        ),
    }
    for relative_path, tokens in contracts.items():
        content = _require_file(findings, strategy, directory / relative_path)
        _require_tokens(findings, strategy, relative_path, content, tokens)

    for relative_path in (
        ".env.example",
        "config.yaml",
        "tests/test_api_contracts.py",
        "tests/test_config.py",
        "tests/test_fill_evidence.py",
        "tests/test_fill_evidence_repository.py",
        "tests/test_reentry_policy.py",
        "tests/test_source_digest.py",
        "tests/test_cycle_deadline.py",
        "tests/test_run_lock.py",
        "research/frozen-2026-08-30-kickoff-leader-v1/PREREGISTRATION.md",
        "research/frozen-2026-08-30-kickoff-leader-v1/HISTORICAL_REPLAY.md",
        "research/frozen-2026-08-30-kickoff-leader-v1/MANIFEST.sha256",
    ):
        _require_file(findings, strategy, directory / relative_path)

    config_yaml = _read(directory / "config.yaml")
    for key, expected in (
        ("buy_amount_usdc", 5.0),
        ("min_liquidity", 5000),
        ("min_cumulative_volume", 5000),
        ("max_positions", 10),
        ("max_event_positions", 1),
        ("max_new_positions_per_cycle", 5),
        ("max_emergency_sells_per_cycle", 10),
        ("yes_only_mode", False),
        ("prob_max", 0.94),
        ("max_source_minute", 10),
        ("stop_loss_delta", 0.10),
        ("late_exit_minute", 80),
        ("stop_cutoff_minute", 80),
        ("stop_sell_quarantine_timeout_minutes", 180),
    ):
        _require_yaml_value(
            findings, strategy, "config.yaml", config_yaml, key, expected
        )

    combined = "\n".join(
        _read(path)
        for path in (
            directory / "src/polybot/bot.py",
            directory / "src/polybot/strategy/scanner.py",
            directory / "src/polybot/strategy/trader.py",
        )
    )
    for token in ("get_positions(", "wallet_position", "account_wide"):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_wallet_adoption_path",
                    f"live runtime contains {token}",
                )
            )

    prereg_path = (
        directory
        / "research/frozen-2026-08-30-kickoff-leader-v1/PREREGISTRATION.md"
    )
    manifest_path = (
        directory
        / "research/frozen-2026-08-30-kickoff-leader-v1/MANIFEST.sha256"
    )
    preregistration = _read(prereg_path)
    manifest = _read(manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-30-kickoff-leader-v1/PREREGISTRATION.md",
        preregistration,
        (
            "2026-08-30T00:00:00Z",
            "2026-09-13T00:00:00Z",
            "2026-09-20T00:00:00Z",
            "직접 YES와",
            "source 경기 시간이 0~10분",
            "exact `$5`",
            "+0.03",
            "+0.05",
            "-0.10",
            "180분",
            "QUARANTINED",
        ),
    )
    if preregistration and manifest:
        digest = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-08-30-kickoff-leader-v1/MANIFEST.sha256",
                )
            )

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


def _validate_plum_strategy(
    findings: list[Finding], strategy: str, directory: Path
) -> None:
    """Validate Golden Plum's sport-profiled full-game live/shadow contract."""

    contracts = {
        "README.md": (
            "polybot-king",
            "polybot-queen",
            "polybot-silver",
            "polybot-gold",
            "plum-live-king-90-1m-v1",
            "plum-live-queen-95-1m-v1",
            "plum-shadow-silver-1m-v1",
            "plum-shadow-gold-mlb-1m-v1",
            "[0.75,0.78]",
            "시간 강제 청산은 없고",
            "$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000",
            "baseline `$5`",
            "MLB",
        ),
        "STRATEGY.md": (
            "HOME/DRAW/AWAY",
            "direct YES",
            "0.75",
            "0.90",
            "0.95",
            "최근 3개 snapshot",
            "누적 상승이 0.02",
            "시간 강제 청산: 없음",
            "execution_capacity_json",
            "MLB",
            "NBA·NFL·NHL",
            "event당 실제 체결",
            "180분",
            "QUARANTINED",
            "confirmed BUY VWAP",
        ),
        "OPERATIONS.md": (
            "/Volumes/t7/jenkins/polybot-silver",
            "/Volumes/t7/jenkins/polybot-gold",
            "* * * * *",
            "동시 빌드",
            "clean",
            "daily-rsync verify",
        ),
        "src/polybot/config.py": (
            "FROZEN_JOB_TAKE_PROFIT",
            "RuntimeSpec",
            "RUNTIME_SPECS",
            "plum-live-king-90-1m-v1",
            "plum-live-queen-95-1m-v1",
            "plum-shadow-silver-1m-v1",
            "plum-shadow-gold-mlb-1m-v1",
            "non-soccer Golden Plum live execution is not enabled",
            "Golden Plum target notional must be $5-$1000",
            "Golden Plum must inspect direct YES and NO books",
            "failed stop SELL quarantine timeout is frozen at 180 minutes",
            "strategy_source_digest",
            "preregistration_sha256",
        ),
        "src/polybot/strategy/scanner.py": (
            "get_source_progress",
            "trend_snapshot_cadence_gap",
            "trend_snapshot_ids",
            "full_game_first_cross_trend",
            "claim_entry_episode",
            "direct",
        ),
        "src/polybot/strategy/trader.py": (
            "ADAPTIVE_BUY_NOTIONAL_LADDER_USDC",
            "selected_buy_amount_usdc",
            "fresh_direct_book_leader_changed",
            "fresh_direct_book_coverage_gap",
            "get_snapshots_by_ids",
            "place_fok_buy",
            "no time exit",
            "STOP_SELL_QUARANTINE_REASON",
            "get_exact_buy_fill_evidence",
            "get_exact_sell_fill_evidence",
            'order_type="FOK"',
        ),
        "src/polybot/db/repository.py": (
            "event_already_traded",
            "PROVEN_ZERO_FILL_RETRYABLE",
            "get_entry_capacity_state",
            "get_isolated_stop_sell_trades",
            "QUARANTINED means economic exposure is unknown",
            "get_snapshots_by_ids",
        ),
        "src/polybot/api/gamma_client.py": (
            "/events/keyset",
            '"live": "true"',
            "after_cursor",
            "cursor_complete",
            "membership_digest_sha256",
            '"liquidity_min"',
            '"volume_min"',
        ),
        "src/polybot/api/clob_client.py": (
            "AdaptiveBuySelection",
            "select_adaptive_buy_from_book_evidence",
            "BuyBookWalk",
            "SellBookWalk",
            "get_buy_book_walks",
            "get_cached_book_evidence",
            "build_execution_capacity_evidence",
            "signed FOK BUY does not preserve exact maker USDC",
            "signed limit order share quantity drift",
            "resolve_dynamic_fee_evidence",
        ),
        "tests/test_scanner.py": (
            "test_three_fresh_snapshots_confirm_direct_no_first_cross",
            "test_entry_accepts_explicit_live_source_clock_after_minute_seventy_five",
            "test_simulation_scaling_ladder_is_persisted_without_extra_book_reads",
            "test_mlb_direct_two_team_collection_and_trend_need_no_fake_minute",
            "test_missing_one_direct_book_fails_closed",
            "test_tied_current_leader_fails_closed_after_history",
        ),
        "tests/test_trader.py": (
            "test_buy_revalidates_exact_five_and_submits_fok",
            "test_buy_refuses_any_prior_event_trade",
            "test_continuous_stop_failure_is_quarantined_after_three_hours",
            "test_minute_eighty_does_not_force_exit_and_stop_remains_active",
            "test_unrelated_event_exits_are_not_blocked_by_first_sell",
            "test_mlb_simulation_revalidates_two_direct_books_without_source_minute",
        ),
        "tests/test_lifecycle_mode.py": (
            "test_active_caps_one_cycle_at_five_new_positions",
            "test_pre_submission_failure_does_not_skip_later_candidate",
            "test_active_pending_buy_reserves_capacity_but_does_not_block_other_event",
            "test_active_isolated_stop_quarantine_reserves_capacity_but_not_global_gate",
        ),
        "tests/test_api_contracts.py": (
            "test_adaptive_buy_uses_cached_book_and_largest_safe_ladder_amount",
        ),
        "src/polybot/db/models.py": (
            "sport_family",
            "league_code",
            "market_tags_json",
            "target_buy_amount_usdc",
            "selected_buy_amount_usdc",
            "max_executable_buy_notional_usdc",
            "buy_notional_fallback_reason",
        ),
        "tests/test_replay_direct_six_book.py": (
            "test_full_depth_walks_use_all_levels_and_fail_if_shallow",
            "test_trend_requires_same_token_first_cross_and_bounded_pullback",
            "test_replay_defaults_to_full_match_without_time_exit",
            "test_mlb_replay_uses_timestamp_cadence_without_inventing_source_minutes",
        ),
        "src/polybot/source_digest.py": (
            "compute_strategy_source_digest",
            "ACTIVE_PREREGISTRATION",
            "polybot_observability",
        ),
    }
    for relative_path, tokens in contracts.items():
        content = _require_file(findings, strategy, directory / relative_path)
        _require_tokens(findings, strategy, relative_path, content, tokens)

    for relative_path in (
        ".env.example",
        "config.yaml",
        "tests/test_api_contracts.py",
        "tests/test_config.py",
        "tests/test_fill_evidence.py",
        "tests/test_fill_evidence_repository.py",
        "tests/test_reentry_policy.py",
        "tests/test_source_digest.py",
        "tests/test_cycle_deadline.py",
        "tests/test_run_lock.py",
        "research/frozen-2026-08-31-midgame-confirmation-v1/PREREGISTRATION.md",
        "research/frozen-2026-08-31-midgame-confirmation-v1/HISTORICAL_REPLAY.md",
        "research/frozen-2026-08-31-midgame-confirmation-v1/MANIFEST.sha256",
        "research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md",
        "research/frozen-2026-08-31-full-match-no-time-exit-v2/MANIFEST.sha256",
        "research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md",
        "research/frozen-2026-09-01-multisport-mlb-shadow-v3/MANIFEST.sha256",
        "research/frozen-2026-09-02-execution-metadata-v5/PREREGISTRATION.md",
        "research/frozen-2026-09-02-execution-metadata-v5/MANIFEST.sha256",
    ):
        _require_file(findings, strategy, directory / relative_path)

    config_yaml = _read(directory / "config.yaml")
    for key, expected in (
        ("buy_amount_usdc", 5.0),
        ("min_liquidity", 5000),
        ("min_cumulative_volume", 5000),
        ("max_positions", 10),
        ("max_event_positions", 1),
        ("max_new_positions_per_cycle", 5),
        ("max_emergency_sells_per_cycle", 10),
        ("yes_only_mode", False),
        ("prob_max", 0.78),
        ("min_source_minute", 0),
        ("max_source_minute", None),
        ("trend_observations", 3),
        ("trend_min_cumulative_move", 0.02),
        ("trend_max_pullback", 0.01),
        ("trend_max_gap_seconds", 90),
        ("stop_loss_delta", 0.15),
        ("force_exit_minute", None),
        ("stop_sell_quarantine_timeout_minutes", 180),
    ):
        _require_yaml_value(
            findings, strategy, "config.yaml", config_yaml, key, expected
        )
    _require_tokens(
        findings,
        strategy,
        "config.yaml",
        config_yaml,
        ("prob_min: 0.75",),
    )

    combined = "\n".join(
        _read(path)
        for path in (
            directory / "src/polybot/bot.py",
            directory / "src/polybot/strategy/scanner.py",
            directory / "src/polybot/strategy/trader.py",
        )
    )
    for token in ("get_positions(", "wallet_position", "account_wide"):
        if token in combined:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_wallet_adoption_path",
                    f"live runtime contains {token}",
                )
            )

    prereg_path = (
        directory
        / "research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md"
    )
    manifest_path = (
        directory
        / "research/frozen-2026-08-31-full-match-no-time-exit-v2/MANIFEST.sha256"
    )
    preregistration = _read(prereg_path)
    manifest = _read(manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md",
        preregistration,
        (
            "2026-08-31T00:00:00Z",
            "2026-09-14T00:00:00Z",
            "2026-09-21T00:00:00Z",
            "직접 YES",
            "source minute `0`",
            "시간 강제 청산: 없음",
            "execution_capacity_json",
            "exact `$5`",
            "0.75",
            "0.90",
            "0.95",
            "180분",
            "QUARANTINED",
        ),
    )
    if preregistration and manifest:
        digest = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-08-31-full-match-no-time-exit-v2/MANIFEST.sha256",
                )
            )

    mlb_prereg_path = (
        directory
        / "research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md"
    )
    mlb_manifest_path = (
        directory
        / "research/frozen-2026-09-01-multisport-mlb-shadow-v3/MANIFEST.sha256"
    )
    mlb_preregistration = _read(mlb_prereg_path)
    mlb_manifest = _read(mlb_manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md",
        mlb_preregistration,
        (
            "plum-shadow-gold-mlb-1m-v1",
            "MLB",
            "NBA·NFL·NHL",
            "exact `$5`",
            "$5/$10/$25/$50/$100/$250/$500",
            "source_elapsed_minutes`는 NULL",
            "시간 청산은 없음",
            "50초",
            "해결까지 관측된 MLB event 100개",
        ),
    )
    if mlb_preregistration and mlb_manifest:
        digest = hashlib.sha256(mlb_prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in mlb_manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-09-01-multisport-mlb-shadow-v3/MANIFEST.sha256",
                )
            )

    active_prereg_path = (
        directory
        / "research/frozen-2026-09-02-execution-metadata-v5/PREREGISTRATION.md"
    )
    active_manifest_path = (
        directory
        / "research/frozen-2026-09-02-execution-metadata-v5/MANIFEST.sha256"
    )
    active_preregistration = _read(active_prereg_path)
    active_manifest = _read(active_manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-09-02-execution-metadata-v5/PREREGISTRATION.md",
        active_preregistration,
        (
            "[0.75,0.78]",
            "0.90/0.95",
            "confirmed BUY VWAP-0.15",
            "sport_family",
            "league_code",
            "market_tags_json",
            "baseline exact `$5`",
            "$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000",
            "target_buy_amount_usdc",
            "selected_buy_amount_usdc",
            "FOK",
            "50초",
        ),
    )
    if active_preregistration and active_manifest:
        digest = hashlib.sha256(active_prereg_path.read_bytes()).hexdigest()
        pinned = any(
            len(fields := line.strip().split()) >= 2
            and fields[0].lower() == digest
            and fields[-1].lstrip("*").endswith("PREREGISTRATION.md")
            for line in active_manifest.splitlines()
        )
        if not pinned:
            findings.append(
                Finding(
                    strategy,
                    "invalid_manifest",
                    "research/frozen-2026-09-02-execution-metadata-v5/MANIFEST.sha256",
                )
            )

    retro = ROOT / "docs/retro" / f"{strategy}.md"
    retro_content = _require_file(findings, strategy, retro)
    _require_tokens(
        findings,
        strategy,
        f"docs/retro/{strategy}.md",
        retro_content,
        ("EVIDENCE_CONTRACT.md", "REVIEW_START", "REVIEW_END"),
    )


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

    if strategy in RESEARCH_ONLY_STRATEGIES:
        if strategy == "golden-black":
            _validate_sports_resolution_research_strategy(
                findings, strategy, directory
            )
        elif strategy == "golden-coconut":
            _validate_major_sports_research_strategy(
                findings, strategy, directory
            )
        elif strategy == "golden-pomegranate":
            _validate_research_only_strategy(findings, strategy, directory)
        elif strategy == "golden-raspberry":
            _validate_queue_echo_research_strategy(findings, strategy, directory)
        elif strategy == "golden-strawberry":
            _validate_last_mile_research_strategy(findings, strategy, directory)
        elif strategy == "golden-watermelon":
            _validate_inplay_match_winner_research_strategy(
                findings, strategy, directory
            )
        return findings

    if strategy == "golden-tangerine":
        _validate_tangerine_strategy(findings, strategy, directory)
        return findings

    if strategy == "golden-peach":
        _validate_peach_strategy(findings, strategy, directory)
        return findings

    if strategy == "golden-plum":
        _validate_plum_strategy(findings, strategy, directory)
        return findings

    if strategy == "golden-watermelon-live":
        _validate_watermelon_live_strategy(findings, strategy, directory)
        return findings

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
    if strategy in {"golden-blueberry", "golden-kiwi", "golden-melon", "golden-papaya", "golden-queen", "golden-quince"}:
        _validate_papaya_bot_source(
            findings, strategy, "src/polybot/bot.py", bot
        )
    if strategy == "golden-kiwi":
        _validate_kiwi_bot_source(
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
    if strategy in {"golden-blueberry", "golden-kiwi", "golden-melon", "golden-papaya", "golden-queen", "golden-quince"}:
        _validate_papaya_trader_source(
            findings, strategy, "src/polybot/strategy/trader.py", trader
        )
    if strategy == "golden-kiwi":
        _validate_kiwi_trader_source(
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

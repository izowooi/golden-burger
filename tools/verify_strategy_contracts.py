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
    contracts = {
        "src/polybot/config.py": (
            "get_trading_config_mapping",
            "validate_yaml_config_shape",
            "POLYMARKET_PRIVATE_KEY",
            "soccer-inplay-major-league-match-winner-v1",
            "watermelon-white-1m-v3",
            "watermelon-grey-5m-v3",
            "MAJOR_SOCCER_LEAGUES",
            "FAST_1M",
            "CONTROL_5M",
            "ENTRY_THRESHOLDS = (0.95, 0.96, 0.97, 0.98, 0.99)",
            "STOP_LEVELS = (0.95, 0.93, 0.90, 0.85, 0.80, 0.70)",
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
            "DRAW_OUTCOME_EXCLUDED", "FIRST_FULL_DEPTH_ABOVE",
            "UPWARD_CROSS", "HOLD_TO_RESOLUTION", "PARTIAL_FILL",
            "gap_from_stop", "resolution_due", "GAMMA_CURSOR_INCOMPLETE",
            "Entry and exit cannot use the same displayed book",
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
            "inplay-match-winner-analyzer-v2",
            "inplay-match-winner-cadence-pair-v2", "league_coverage",
            "cursor_complete_pct", "observed_book_pct", "entry_thresholds",
            "stop_policy_comparison", "matched_episode_keys",
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
            "soccer-inplay-major-league-match-winner-v1",
            "watermelon-white-1m-v3", "watermelon-grey-5m-v3",
            "child_moneyline", "--simulate", "--live", "e-sports",
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
        directory / "research/frozen-2026-08-24-soccer/PREREGISTRATION.md",
    )
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-24-soccer/PREREGISTRATION.md",
        preregistration,
        (
            "2026-08-23T15:00:00Z", "2026-08-30T15:00:00Z",
            "0.95, 0.96, 0.97, 0.98, 0.99", "STOP_0.95", "STOP_0.70",
            "FAST_1M", "CONTROL_5M", "displayed-book counterfactual",
            "epl", "bun", "fl1", "lal", "mls", "e-sports",
        ),
    )
    _require_file(
        findings,
        strategy,
        directory / "research/frozen-2026-08-24-soccer/MANIFEST.sha256",
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
    """Validate the isolated Cat/Dog in-play soccer live pilot."""

    contracts = {
        "README.md": (
            "polybot-cat",
            "polybot-dog",
            "watermelon-live-cat-98-1m-v2d",
            "watermelon-live-dog-99-1m-v2d",
            "EPL",
            "Bundesliga",
            "Ligue 1",
            "LaLiga",
            "MLS",
            "close_only",
            "archive_only",
            "strategy-wind-down-playbook.md",
        ),
        "STRATEGY.md": (
            "[0.98, 0.999]",
            "[0.99, 0.999]",
            "HOME/DRAW/AWAY",
            "FOK BUY",
            "FOK SELL",
            "full depth",
            "0.70",
            "PENDING_SELL",
        ),
        "OPERATIONS.md": (
            "* * * * *",
            "Clean before checkout",
            "watermelon-live-cat-98-1m-v2d",
            "watermelon-live-dog-99-1m-v2d",
            "daily-rsync verify",
        ),
        "src/polybot/bot.py": (
            "pending_buy_unresolved",
            "pending_sell_unresolved",
            "quarantined_position",
            "open_buy_fill_or_fee_evidence_gap",
            "league_identity_metadata_drift",
            "unresolved_sell_outcome",
            "entry_blocked_candidates",
            "get_entry_capacity_state",
        ),
        "src/polybot/db/repository.py": (
            "get_untracked_buy_reservation_count",
            "get_open_buy_evidence_gap_count",
            "create_recovered_orphan_trade",
            "untracked_buy_reservations",
            "incomplete membership checkpoint",
            "fee_taker_only",
            "legacy fee_rate_bps=0 placeholder",
        ),
        "src/polybot/config.py": (
            "FROZEN_ARMS",
            "FROZEN_START_UTC",
            "FROZEN_ENTRY_END_UTC",
            "FROZEN_FOLLOWUP_END_UTC",
            "FROZEN_LEAGUE_IDENTITIES",
            "LEAGUE_MAPPING_SHA256",
            "strategy_source_digest",
            "preregistration_sha256",
            "live notional must remain exactly $5",
            "exposure limits are frozen at 20/1/20",
            "emergency stop_price is frozen at 0.70",
            "only YES tokens of home/draw/away",
        ),
        "src/polybot/league_classifier.py": (
            "classify_soccer_event",
            "ESPORTS_TAG_ID",
            "LEAGUE_MAPPING_SHA256",
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
        ),
        "src/polybot/api/clob_client.py": (
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
            '"intent_autoresolved": 0',
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
            "test_buy_revalidates_exact_five_and_submits_fok",
            "test_stop_uses_fresh_bid_and_submits_fok_sell",
            "test_clob_one_hot_resolution_fallback_settles_confirmed_own_trade",
        ),
        "tests/test_api_contracts.py": (
            "test_full_share_sell_walk_uses_deeper_bids_and_market_limit",
            "test_shallow_stop_book_is_censored_not_partially_sold",
            "test_gamma_exclusion_bucket_preserves_rejected_sport_identity",
            "test_order_reconciliation_reports_health_without_unsafe_intent_autoresolve",
            "test_live_sell_ledger_uses_signed_two_decimal_share_quantity",
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
    ):
        _require_file(findings, strategy, directory / relative_path)

    config_yaml = _read(directory / "config.yaml")
    for key, expected in (
        ("buy_amount_usdc", 5.0),
        ("min_liquidity", 0),
        ("min_volume_24h", 0),
        ("min_cumulative_volume", 0),
        ("max_positions", 20),
        ("max_event_positions", 1),
        ("max_new_positions_per_cycle", 20),
        ("stop_price", 0.70),
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
        / "research/frozen-2026-08-25-safety-v2d/PREREGISTRATION.md"
    )
    manifest_path = (
        directory
        / "research/frozen-2026-08-25-safety-v2d/MANIFEST.sha256"
    )
    preregistration = _read(prereg_path)
    manifest = _read(manifest_path)
    _require_tokens(
        findings,
        strategy,
        "research/frozen-2026-08-25-safety-v2d/PREREGISTRATION.md",
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
            "watermelon-live-cat-98-1m-v2d",
            "watermelon-live-dog-99-1m-v2d",
            "QUARANTINED",
            "operator proof of no order",
            "signed maker/taker amounts",
            "0.5/0.5",
            "does not select a threshold winner",
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

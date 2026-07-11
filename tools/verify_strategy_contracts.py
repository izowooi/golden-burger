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


def _calls(node: ast.AST) -> list[tuple[str, ast.Call]]:
    return [
        (_call_name(child), child)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    ]


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
        ("RunAudit.start", "reconcile_order_ledger", "run_cycle", "audit.succeed"),
    )
    calls = {name for name, _ in _calls(run)}
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
            "normalize_clob_response",
        }
        missing = sorted(required - names)
        has_raise = any(isinstance(node, ast.Raise) for node in ast.walk(cancel))
        if missing or not has_raise:
            findings.append(
                Finding(
                    strategy,
                    "unsafe_cancellation_path",
                    f"{relative_path}: exact cancel evidence must raise on failure",
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
    required = ("README.md", "config.yaml", "uv.lock")
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
        ("excluded_categories must be a list", "simulation_mode must be a boolean"),
    )

    bot = _require_file(findings, strategy, directory / "src/polybot/bot.py")
    _validate_bot_source(findings, strategy, "src/polybot/bot.py", bot)

    clob = _require_file(
        findings, strategy, directory / "src/polybot/api/clob_client.py"
    )
    _validate_clob_source(findings, strategy, "src/polybot/api/clob_client.py", clob)

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

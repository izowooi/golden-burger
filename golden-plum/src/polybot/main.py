"""CLI entry point for Golden Plum."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from polybot_observability import RunAudit

from .bot import PolymarketBot
from .config import RUNTIME_SPECS, load_config
from .utils.deadline import enforced_cycle_deadline
from .utils.logger import setup_logger
from .utils.run_lock import exclusive_job_run_lock


class OverlappingCycleSkipped(RuntimeError):
    """A simulation trigger arrived while the prior cycle held the job lock."""


def _verify_external_collector_workspace(runtime_job: str) -> Path | None:
    """Run the strict T7 preflight before config creates the DB directory."""
    runtime_spec = RUNTIME_SPECS.get(runtime_job)
    if runtime_spec is None or runtime_spec.external_workspace_path is None:
        return None
    if not runtime_spec.simulation_mode:
        raise RuntimeError("external Golden Plum runtime must remain simulation-only")
    jenkins_job = runtime_spec.jenkins_job
    expected_workspace = Path(runtime_spec.external_workspace_path)
    raw_workspace = (os.environ.get("WORKSPACE") or "").strip()
    if not raw_workspace:
        raise RuntimeError(
            f"{jenkins_job} requires Jenkins WORKSPACE for external-volume proof"
        )
    expected_database = (
        expected_workspace
        / "golden-plum"
        / "data"
        / runtime_job
        / "trades_sim.db"
    )
    project_root = Path(__file__).resolve().parents[2]
    verifier = project_root / "scripts" / "verify_external_workspace.py"
    result = subprocess.run(
        [
            sys.executable,
            str(verifier),
            "--job",
            jenkins_job,
            "--workspace",
            raw_workspace,
            "--database",
            str(expected_database),
            "--write-daily-rsync-marker",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{jenkins_job} external workspace verification failed"
        )
    logging.info(
        "external workspace verified - jenkins_job=%s runtime_job=%s",
        jenkins_job,
        runtime_job,
    )
    return expected_database


def _resolved_database_path(path: Path) -> Path:
    """Resolve config's repository-relative DB path against the current checkout."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _validate_resolved_external_contract(
    config, expected_database: Path | None
) -> None:
    if expected_database is None:
        return
    runtime_spec = RUNTIME_SPECS.get(config.job_name)
    if runtime_spec is None:
        raise RuntimeError("resolved runtime is absent from the atomic registry")
    if (
        config.trading.external_workspace_path
        != runtime_spec.external_workspace_path
        or config.trading.cycle_hard_deadline_seconds
        != runtime_spec.hard_deadline_seconds
        or config.trading.cadence_seconds != runtime_spec.cadence_seconds
        or config.simulation_mode is not runtime_spec.simulation_mode
        or runtime_spec.jenkins_job
        not in {"polybot-gold", "polybot-silver"}
        or _resolved_database_path(config.db_path) != expected_database.resolve()
    ):
        raise RuntimeError(
            "resolved config differs from the atomic external workspace contract"
        )


def _record_simulation_failure(config, error: BaseException) -> None:
    """Best-effort audit for failures before ``PolymarketBot.run`` starts."""
    if not config.simulation_mode:
        return
    try:
        audit = RunAudit.start(config, strategy_name="golden-plum")
        audit.fail(error)
    except Exception as audit_error:
        logging.warning(
            "pre-run simulation failure audit could not be recorded - "
            "job=%s error=%s",
            config.job_name,
            type(audit_error).__name__,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Plum - full-game direct-book first-cross confirmation"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run one archive/trading cycle")
    run.add_argument("--config", "-c", default="config.yaml")
    run.add_argument("--job", "-j", default="default")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument(
        "--live",
        action="store_true",
        help="Explicitly enable real CLOB orders (default is simulation)",
    )
    run.add_argument("--verbose", "-v", action="store_true")
    status = commands.add_parser("status", help="Show DB status")
    status.add_argument("--config", "-c", default="config.yaml")
    status.add_argument("--job", "-j", default="default")
    _add_mode_flags(status)
    config = commands.add_parser("config", help="Show resolved configuration")
    config.add_argument("--config", "-c", default="config.yaml")
    config.add_argument("--job", "-j", default="default")
    _add_mode_flags(config)
    return parser


def _add_mode_flags(command: argparse.ArgumentParser) -> None:
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument("--live", action="store_true")


def _load(args, simulation_override=None):
    try:
        return load_config(
            args.config,
            args.job,
            simulation_mode=simulation_override,
        )
    except ValueError as error:
        print(f"Configuration error: {error}")
        sys.exit(1)


def _run_simulation_override(args: argparse.Namespace) -> bool:
    """실주문은 매번 명시적인 ``--live``를 요구한다 (queen과 같은 안전 기본값).

    config.yaml의 ``simulation_mode: true``는 그대로 두고, 실거래는 Jenkins에서
    ``--live``를 붙여야만 켜진다. 플래그 없이 실행하면 시뮬레이션이다.
    """
    return not bool(args.live)


def _inspection_simulation_override(args: argparse.Namespace):
    if args.live:
        return False
    if args.simulate:
        return True
    return None


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        try:
            expected_external_database = _verify_external_collector_workspace(
                args.job
            )
        except Exception as error:
            print(f"External workspace error: {error}", file=sys.stderr)
            sys.exit(1)
        config = _load(
            args,
            simulation_override=_run_simulation_override(args),
        )
        try:
            _validate_resolved_external_contract(
                config, expected_external_database
            )
        except RuntimeError as error:
            print(
                f"External workspace error: {error}",
                file=sys.stderr,
            )
            sys.exit(1)
        setup_logger(config.job_name, verbose=args.verbose)
        try:
            lock_path = config.db_path.parent / ".cycle-run.lock"
            with exclusive_job_run_lock(lock_path) as acquired:
                if not acquired:
                    _record_simulation_failure(
                        config,
                        OverlappingCycleSkipped(
                            f"overlapping cycle skipped for {config.job_name}"
                        ),
                    )
                    logging.warning(
                        "이전 %s cycle이 아직 실행 중이므로 중복 실행을 안전하게 건너뜁니다",
                        config.job_name,
                    )
                    return
                configured_deadline = (
                    config.trading.cycle_hard_deadline_seconds
                )
                if config.simulation_mode and configured_deadline is None:
                    raise RuntimeError(
                        "simulation runtime requires a frozen hard deadline"
                    )
                with enforced_cycle_deadline(
                    hard_limit_seconds=configured_deadline,
                    enforce_deadline=(
                        config.simulation_mode
                        and configured_deadline is not None
                    ),
                ) as cycle_budget:
                    try:
                        bot = PolymarketBot(config, cycle_budget=cycle_budget)
                    except Exception as error:
                        _record_simulation_failure(config, error)
                        raise
                    try:
                        bot.run()
                    finally:
                        cleanup_failures = bot.close()
                        if cleanup_failures:
                            logging.error(
                                "cycle resource cleanup incomplete - job=%s failures=%s",
                                config.job_name,
                                ",".join(cleanup_failures),
                            )
                        else:
                            logging.info(
                                "cycle resources closed - job=%s", config.job_name
                            )
        except KeyboardInterrupt:
            print("\n사용자에 의해 중단됨")
            sys.exit(0)
        except Exception as error:
            logging.exception("Bot 실패: %s", error)
            sys.exit(1)
        return

    config = _load(args, simulation_override=_inspection_simulation_override(args))
    if args.command == "status":
        setup_logger(config.job_name, level=logging.WARNING)
        bot = PolymarketBot(config)
        try:
            print(json.dumps(bot.get_status(), indent=2, default=str))
        finally:
            cleanup_failures = bot.close()
            if cleanup_failures:
                logging.error(
                    "status resource cleanup incomplete - job=%s failures=%s",
                    config.job_name,
                    ",".join(cleanup_failures),
                )
        return

    trading = config.trading
    print("=== Golden Plum / Full-Game Direct-Book Confirmation ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation: {config.simulation_mode}")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    print(f"Sport Family: {trading.sport_family}")
    print(f"DB: {config.db_path}")
    print(
        f"Book shape: {trading.book_shape}; expected "
        f"{trading.expected_market_count} market(s) / "
        f"{trading.expected_token_count} token(s) per event"
    )
    print(
        f"Sport profile: {trading.sport_profile_version}; source clock required: "
        f"{trading.source_clock_required}"
    )
    print(
        "Cohort source/preregistration: "
        f"{trading.strategy_source_digest[:12]}/"
        f"{trading.preregistration_sha256[:12]}"
    )
    print(
        "Baseline $5 ask VWAP band: "
        f"[{trading.entry.prob_min:.3f}, {trading.entry.prob_max:.3f}]"
    )
    in_play_max = (
        "match end"
        if trading.entry.hours_max is None
        else f"{trading.entry.hours_max:.1f} hours"
    )
    source_max = (
        "match end"
        if trading.entry.max_source_minute is None
        else f"minute {trading.entry.max_source_minute:.0f}"
    )
    print(f"In-play age: from kickoff to {in_play_max}")
    print(
        f"Trend: {trading.entry.trend_observations} fresh observations, "
        f"move >= {trading.entry.trend_min_cumulative_move:.2f}, pullback <= "
        f"{trading.entry.trend_max_pullback:.2f}; source window "
        f"[minute {trading.entry.min_source_minute:.0f}, {source_max}]"
    )
    print(
        f"Exit: absolute TP {trading.entry.take_profit_price:.2f}; "
        f"SL entry-{trading.entry.stop_loss_delta:.2f}; no time-forced exit; "
        "otherwise proven resolution"
    )
    print(
        f"Adaptive FOK target: ${trading.buy_amount_usdc:.2f} (floor $5.00), min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        f"Server universe: live {trading.sport_family}; cumulative volume >= "
        f"${trading.min_cumulative_volume:.0f}, liquidity >= "
        f"${trading.min_liquidity:.0f}; fresh exact-$5 CLOB depth is final gate"
    )
    print(
        f"Limits: {trading.max_positions} total, "
        f"{trading.max_event_positions} per event, "
        f"{trading.max_new_positions_per_cycle} new/cycle, "
        f"{trading.max_emergency_sells_per_cycle} exits/cycle; no second "
        "filled or uncertain BUY for the same event"
    )
    print(
        "Delayed FOK reconciliation timeout: "
        f"{trading.fok_reconciliation_timeout_minutes:.0f} minutes"
    )
    print(
        "Failed BUY/SELL containment: unrelated events continue within the "
        "remaining capacity; unresolved exposure auto-quarantines after "
        f"{trading.stop_sell_quarantine_timeout_minutes:.0f} minutes"
    )
    print(
        "Economic drawdown entry guard: "
        f"-${trading.experiment_capital_usdc * trading.max_drawdown_stop:.2f} "
        "(confirmed SELL + proven resolution P&L)"
    )
    print(
        "Entry period: "
        f"[{trading.experiment_start_utc}, "
        f"{trading.experiment_entry_end_utc})"
    )
    print(
        f"Archive: {trading.expected_token_count} direct books for the full "
        "explicitly live match, "
        f"{trading.archive.retention_days}d retention"
    )
    if trading.scaling_notionals_usdc:
        print(
            "Simulation-only displayed-depth scaling ladder: "
            + ", ".join(f"${value:g}" for value in trading.scaling_notionals_usdc)
        )
        print(
            "Simulation-only replay grid: entries="
            + ",".join(f"{value:.2f}" for value in trading.analysis_entry_thresholds)
            + "; targets="
            + ",".join(f"{value:.2f}" for value in trading.analysis_target_prices)
            + "; stops="
            + ",".join(f"{value:.2f}" for value in trading.analysis_stop_deltas)
        )


if __name__ == "__main__":
    main()

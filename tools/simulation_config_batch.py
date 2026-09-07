#!/usr/bin/env python3
"""Print registered simulation configs using one interpreter and the existing CLI.

Run from the strategy directory with that strategy's virtualenv. This replaces
only repeated ``polybot config --simulate`` processes; it never runs a cycle,
constructs a bot, overrides configuration environment values, or replaces workspace preflight.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib
import io
import logging
import sys
from types import ModuleType
from typing import Sequence


def _forbid_runtime(*_args, **_kwargs):
    raise RuntimeError("config batch cannot construct a trading runtime")


@contextmanager
def _buffer_console_diagnostics(target):
    previous_stderr = sys.stderr

    def console_handlers():
        loggers = [
            logging.getLogger(),
            *(
                logger
                for logger in logging.Logger.manager.loggerDict.values()
                if isinstance(logger, logging.Logger)
            ),
        ]
        return {
            handler
            for logger in loggers
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        }

    previous_streams = {handler: handler.stream for handler in console_handlers()}
    try:
        for handler in previous_streams:
            handler.setStream(target)
        with redirect_stderr(target):
            yield
    finally:
        for handler in console_handlers():
            if handler in previous_streams:
                handler.setStream(previous_streams[handler])
            elif handler.stream is target:
                handler.setStream(previous_stderr)


def render_configs(
    jobs: Sequence[str],
    *,
    config_path: str = "config.yaml",
    entry_module: ModuleType | None = None,
    config_module: ModuleType | None = None,
) -> str:
    """Buffer Python stdout/stderr and console logging until the batch passes."""
    output, diagnostics = io.StringIO(), io.StringIO()
    with redirect_stdout(output), _buffer_console_diagnostics(diagnostics):
        _run_existing_config_cli(
            jobs,
            config_path=config_path,
            entry_module=entry_module,
            config_module=config_module,
        )
    # Successful diagnostics retain their original content. Any exception above
    # discards both buffers before main emits the safe exception type only.
    sys.stderr.write(diagnostics.getvalue())
    return output.getvalue()


def _run_existing_config_cli(
    jobs: Sequence[str],
    *,
    config_path: str = "config.yaml",
    entry_module: ModuleType | None = None,
    config_module: ModuleType | None = None,
) -> None:
    """Return complete stdout only after every registered simulation config passes."""
    if not jobs or len(jobs) != len(set(jobs)):
        raise ValueError("simulation runtime list must be nonempty and unique")
    config_module = config_module or importlib.import_module("polybot.config")
    entry_module = entry_module or importlib.import_module("polybot.main")
    allowed = getattr(config_module, "SIMULATION_RUNTIME_JOBS", None)
    if allowed is None:
        allowed = getattr(config_module, "FROZEN_SIMULATION_JOBS", None)
    if not isinstance(allowed, (set, frozenset)) or any(
        job not in allowed for job in jobs
    ):
        raise ValueError("only registered simulation runtimes are accepted")
    if not callable(getattr(entry_module, "main", None)) or not hasattr(
        entry_module, "PolymarketBot"
    ):
        raise ValueError("unsupported strategy configuration CLI")

    previous_argv = sys.argv
    previous_bot = entry_module.PolymarketBot
    try:
        entry_module.PolymarketBot = _forbid_runtime
        for job in jobs:
            sys.argv = [
                "polybot",
                "config",
                "--simulate",
                "--job",
                job,
                "--config",
                config_path,
            ]
            entry_module.main()
    finally:
        sys.argv = previous_argv
        entry_module.PolymarketBot = previous_bot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("jobs", nargs="+")
    args = parser.parse_args(argv)
    try:
        output = render_configs(args.jobs, config_path=args.config)
    except (Exception, SystemExit) as error:
        # Configuration validators can include sensitive values in their error
        # text. Preserve the failure type without echoing that text or partial
        # config stdout into a Jenkins console.
        print(
            f"simulation config batch failed: {type(error).__name__}", file=sys.stderr
        )
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

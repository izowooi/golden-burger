"""Registered sequential processes; independent envs/pools, no POST time kills."""
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from . import config as configuration
from .account import account_paths
from .utils.run_lock import exclusive_job_run_lock


def run_account(account, config_path="config.yaml", *, run_process=subprocess.run):
    if account not in configuration.ACCOUNT_RUNTIMES:
        raise ValueError("unregistered account")
    jobs = configuration.ACCOUNT_RUNTIMES[account]
    # Validate account/Jenkins/credentials/frozen soccer policy before children.
    configuration.load_config(config_path, jobs[0], simulation_mode=False)
    _, account_lock = account_paths(account)
    failures = []
    with exclusive_job_run_lock(account_lock.with_name(f".{account}-orchestrator.lock")) as acquired:
        if not acquired:
            logging.warning("account orchestrator overlaps; skipped: %s", account)
            return 0
        # Parent never owns the child account lock. Ledger rolling-60s quota
        # covers this runner and independently invoked registered single runs.
        ordered = jobs if int(time.time() // 60) % 2 == 0 else tuple(reversed(jobs))
        for job in ordered:
            env = configuration.profile_environment(job, os.environ)
            if failures:
                env["POLYBOT_ACCOUNT_DISABLE_BUYS"] = "1"
            command = [sys.executable, "-m", "polybot.main", "run", "--live", "--job", job,
                       "--config", str(Path(config_path).resolve())]
            try:
                result = run_process(command, cwd=configuration.SOURCE_PROJECT_ROOT,
                                     env=env, check=False)
                if result.returncode:
                    failures.append(job)
            except Exception:
                failures.append(job)
                logging.exception("account child failed: %s", job)
            # Always attempt the other sport, including close_only management.
        if failures:
            logging.error("account child failures: %s", ",".join(failures))
    return int(bool(failures))

import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "simulation_config_batch",
    Path(__file__).resolve().parents[1] / "simulation_config_batch.py",
)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


class SimulationConfigBatchTests(unittest.TestCase):
    def fixture(self, *, peach=False):
        calls = []
        original_bot = object()
        entry = SimpleNamespace(PolymarketBot=original_bot)

        def main():
            calls.append(list(sys.argv))
            print("Job:", sys.argv[4])
            print("Simulation: True")

        entry.main = main
        field = "FROZEN_SIMULATION_JOBS" if peach else "SIMULATION_RUNTIME_JOBS"
        config = SimpleNamespace(**{field: frozenset({"shadow-a", "shadow-b"})})
        return entry, config, original_bot, calls

    def test_order_stdout_and_explicit_simulation_match_existing_cli(self):
        for peach in (False, True):
            entry, config, original, calls = self.fixture(peach=peach)
            argv = sys.argv
            result = batch.render_configs(
                ["shadow-b", "shadow-a"], entry_module=entry, config_module=config
            )
            self.assertEqual(
                result,
                "Job: shadow-b\nSimulation: True\nJob: shadow-a\nSimulation: True\n",
            )
            self.assertEqual(
                calls,
                [
                    [
                        "polybot",
                        "config",
                        "--simulate",
                        "--job",
                        "shadow-b",
                        "--config",
                        "config.yaml",
                    ],
                    [
                        "polybot",
                        "config",
                        "--simulate",
                        "--job",
                        "shadow-a",
                        "--config",
                        "config.yaml",
                    ],
                ],
            )
            self.assertIs(sys.argv, argv)
            self.assertIs(entry.PolymarketBot, original)

    def test_live_unknown_duplicate_and_empty_lists_are_rejected_before_execution(self):
        for jobs in (["live-a"], ["unknown"], ["shadow-a", "shadow-a"], []):
            entry, config, _, calls = self.fixture()
            with self.assertRaises(ValueError):
                batch.render_configs(jobs, entry_module=entry, config_module=config)
            self.assertEqual(calls, [])

    def test_runtime_construction_is_blocked_and_globals_restored(self):
        entry, config, original, _ = self.fixture()
        entry.main = lambda: entry.PolymarketBot()
        argv = sys.argv
        with self.assertRaisesRegex(RuntimeError, "cannot construct"):
            batch.render_configs(["shadow-a"], entry_module=entry, config_module=config)
        self.assertIs(entry.PolymarketBot, original)
        self.assertIs(sys.argv, argv)

    def test_failed_late_config_does_not_leak_partial_stdout(self):
        entry, config, original, _ = self.fixture()
        count = 0

        def fail_later():
            nonlocal count
            count += 1
            print("unpublished config output")
            if count == 2:
                raise ValueError("sensitive-value-example")

        entry.main = fail_later
        exposed = io.StringIO()
        with redirect_stdout(exposed), self.assertRaises(ValueError):
            batch.render_configs(
                ["shadow-a", "shadow-b"], entry_module=entry, config_module=config
            )
        self.assertEqual(exposed.getvalue(), "")
        self.assertIs(entry.PolymarketBot, original)

    def test_cli_requires_simulation_and_rejects_live_flag(self):
        for argv in (["shadow-a"], ["--live", "shadow-a"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                batch.main(argv)

    def test_cli_error_output_has_type_only(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(
            batch, "render_configs", side_effect=ValueError("secret-example")
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = batch.main(["--simulate", "shadow-a"])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ValueError", stderr.getvalue())
        self.assertNotIn("secret-example", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()


class ConfigDiagnosticsTests(unittest.TestCase):
    fixture = SimulationConfigBatchTests.fixture

    def test_print_and_preexisting_console_logger_do_not_leak_on_failure(self):
        import logging

        entry, config, original, _ = self.fixture()
        leaked_console = io.StringIO()
        logger = logging.getLogger("simulation-config-batch-test")
        handler = logging.StreamHandler(leaked_console)
        logger.addHandler(handler)
        logger.propagate = False
        old_level = logger.level
        logger.setLevel(logging.WARNING)

        def fail():
            print("sensitive-stderr-example", file=sys.stderr)
            logger.warning("sensitive-log-example")
            raise ValueError("sensitive-exception-example")

        entry.main = fail
        exposed = io.StringIO()
        try:
            with redirect_stderr(exposed), self.assertRaises(ValueError):
                batch.render_configs(
                    ["shadow-a"], entry_module=entry, config_module=config
                )
            self.assertEqual(exposed.getvalue(), "")
            self.assertEqual(leaked_console.getvalue(), "")
            self.assertIs(handler.stream, leaked_console)
            self.assertIs(entry.PolymarketBot, original)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            handler.close()

    def test_successful_stderr_diagnostics_are_preserved(self):
        entry, config, _, _ = self.fixture()
        entry.main = lambda: print("configuration warning", file=sys.stderr)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = batch.render_configs(
                ["shadow-a"], entry_module=entry, config_module=config
            )
        self.assertEqual(result, "")
        self.assertEqual(stderr.getvalue(), "configuration warning\n")

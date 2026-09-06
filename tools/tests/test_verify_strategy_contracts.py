"""Guava's staged contract gate must fail real source mutations, not just keywords.

Tests copy only static source/docs/fixtures into TemporaryDirectory. They never
import a strategy, invoke its clients, touch live DBs or alter the real projects.
"""
import ast
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("strategy_contract_gate_test", REPO / "tools/verify_strategy_contracts.py")
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)

EXISTING_28 = {
    "golden-apple", "golden-banana", "golden-black", "golden-blueberry", "golden-cherry",
    "golden-coconut", "golden-date", "golden-elderberry", "golden-fig", "golden-grape",
    "golden-honeydew", "golden-kiwi", "golden-lime", "golden-mango", "golden-melon",
    "golden-nectarine", "golden-orange", "golden-papaya", "golden-peach", "golden-plum",
    "golden-pomegranate", "golden-queen", "golden-quince", "golden-raspberry",
    "golden-strawberry", "golden-tangerine", "golden-watermelon", "golden-watermelon-live",
}


class GuavaGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="guava-contract-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "golden-guava"
        source = REPO / "golden-guava"
        for relative in ("src", "tests", "research"):
            shutil.copytree(source / relative, self.directory / relative,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        for relative in ("README.md", "STRATEGY.md", "AGENTS.md", "OPERATIONS.md", ".env.example", "config.yaml", "pyproject.toml", "uv.lock"):
            shutil.copy2(source / relative, self.directory / relative)
        (self.root / "docs/retro").mkdir(parents=True)
        shutil.copy2(REPO / "docs/retro/golden-guava.md", self.root / "docs/retro/golden-guava.md")
        self.root_patch = patch.object(gate, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def findings(self):
        return gate.validate_strategy(self.directory)

    @contextmanager
    def changed(self, relative, transform):
        path = self.directory / relative
        original = path.read_text()
        altered = transform(original)
        self.assertNotEqual(original, altered, relative)
        path.write_text(altered)
        try:
            yield
        finally:
            path.write_text(original)

    def assert_rejected(self, check=None):
        findings = self.findings()
        self.assertTrue(findings, "mutation silently passed the research gate")
        if check:
            self.assertTrue(any(check in f.check for f in findings), findings)

    def test_actual_research_structure_passes_without_fake_legacy_trader(self):
        self.assertFalse((self.directory / "src/polybot/bot.py").exists())
        self.assertFalse((self.directory / "src/polybot/api/clob_client.py").exists())
        self.assertEqual(self.findings(), [])

    def test_all_required_modules_and_research_fixtures_are_required(self):
        files = ["src/polybot/" + name + ".py" for name in (
            "config", "main", "public_clients", "evidence", "runtime", "execution", "workspace",
            "budget", "source_digest", "identity", "official_news", "streams", "book", "hypotheses")]
        files += ["tests/" + name for name in (
            "test_runtime.py", "test_config_budget.py", "test_public_clients.py", "test_evidence.py",
            "test_workspace.py", "test_execution.py", "test_identity.py", "test_book_hypotheses.py", "test_official_news.py")]
        files += ["OPERATIONS.md", "research/2026-09-06-guava-v1/PREREGISTRATION.md"]
        for relative in files:
            with self.subTest(relative=relative):
                path = self.directory / relative
                saved = path.read_bytes()
                path.unlink()
                try:
                    self.assert_rejected("missing_file")
                finally:
                    path.write_bytes(saved)

    def test_missing_retro_is_not_silently_accepted(self):
        (self.root / "docs/retro/golden-guava.md").unlink()
        self.assert_rejected("missing_file")

    def test_live_unknown_false_and_missing_release_stage_fail(self):
        for value in ("'live'", "'small_live'", "False", "None"):
            with self.subTest(stage=value), self.changed("src/polybot/config.py", lambda s: s.replace("RELEASE_STAGE='research_only'", "RELEASE_STAGE=" + value)):
                self.assert_rejected("release_stage")
        with self.changed("src/polybot/config.py", lambda s: s.replace("RELEASE_STAGE='research_only'", "# RELEASE_STAGE='research_only'")):
            self.assert_rejected("release_stage")

    def test_deleted_live_cli_rejection_fails_even_with_research_label(self):
        class RemoveGuard(ast.NodeTransformer):
            def visit_Raise(self, node):
                if isinstance(node.exc, ast.Call) and gate._call_name(node.exc) == "RuntimeError":
                    return ast.Pass()
                return node
        with self.changed("src/polybot/main.py", lambda s: ast.unparse(RemoveGuard().visit(ast.parse(s)))):
            self.assert_rejected("live_cli_enabled")

    def test_research_defaults_cannot_become_active_or_live_yaml(self):
        for old,new in (("lifecycle_mode: archive_only", "lifecycle_mode: active"), ("simulation_mode: true", "simulation_mode: false")):
            with self.subTest(old=old), self.changed("config.yaml", lambda s: s.replace(old,new)):
                self.assert_rejected()

    def test_required_core_calls_cannot_be_deleted_or_left_only_in_comments(self):
        cases = {
            "config": ("validate_yaml_config_shape", "validate", "compute_strategy_source_digest"),
            "runtime": ("verify_workspace", "fcntl.flock", "repo.start_run", "repo.record_request",
                        "client.fetch_events", "client.fetch_event", "client.fetch_books", "repo.publish_cycle", "repo.fail_run", "budget.require_commit"),
            "evidence": ("_triggers", "_safe_json", "_raw"),
            "public_clients": ("self.session.request", "self.receipt_sink"),
            "source_digest": ("digest.update", "path.read_bytes"),
        }
        for module,names in cases.items():
            for target in names:
                class DeleteCall(ast.NodeTransformer):
                    def visit_Call(self, node):
                        if gate._call_name(node) == target:
                            return ast.Constant(None)
                        return self.generic_visit(node)
                with self.subTest(module=module,call=target), self.changed(
                        f"src/polybot/{module}.py", lambda s: ast.unparse(DeleteCall().visit(ast.parse(s))) + "\n# " + target + "()\n"):
                    self.assert_rejected()

    def test_dead_branch_cannot_fake_durable_start(self):
        with self.changed("src/polybot/runtime.py", lambda s:s.replace(
                "repo.start_run(run_id,utc(now),config.public_snapshot())",
                "None\n            if False:\n                repo.start_run(run_id,utc(now),config.public_snapshot())")):
            self.assert_rejected("missing_call")

    def test_atomic_publication_cannot_use_a_nontransaction_context(self):
        with self.changed("src/polybot/evidence.py", lambda s:s.replace(
                "with self._transaction(budget=self._budget):", "with nullcontext():")):
            self.assert_rejected("atomic_publication")

    def test_deleted_success_publication_fails(self):
        with self.changed("src/polybot/evidence.py", lambda s:s.replace(
                'self._insert("run_events", {"run_id": run_id, "status": "SUCCEEDED", "occurred_at": observed_at})',
                'None # self._insert("run_events", {"status":"SUCCEEDED"})')):
            self.assert_rejected("atomic_publication")

    def test_actual_schema_foreign_keys_and_cohort_cache_are_required(self):
        with self.changed("src/polybot/evidence.py", lambda s:s.replace(
                "PRIMARY KEY(cohort_key,event_id)", "PRIMARY KEY(event_id)")):
            self.assert_rejected("research_schema")
        with self.changed("src/polybot/evidence.py", lambda s:s.replace(
                "WHERE c.cohort_key=?", "WHERE 1=?")):
            self.assert_rejected("cohort_cache")

    def test_nonresearch_import_and_sdk_cannot_hide_in_new_runtime(self):
        for statement in ("from .execution import Broker", "import py_clob_client_v2", "from eth_account import Account"):
            with self.subTest(import_=statement), self.changed("src/polybot/runtime.py", lambda s:statement+"\n"+s):
                self.assert_rejected("research_import")

    def test_dynamic_import_is_not_an_import_graph_escape(self):
        with self.changed("src/polybot/runtime.py", lambda s:s+"\n__import__('py_clob_client_v2')\n"):
            self.assert_rejected("dynamic_research_code")

    def test_public_allowlist_cannot_allow_orders_or_all_routes(self):
        for before,after in (("allowed = (", "allowed = True or ("), ('path == "/books"', 'path in {"/books", "/order"}'), (r'/events/[0-9]+', r'/events/.*')):
            with self.subTest(change=after), self.changed("src/polybot/public_clients.py",lambda s:s.replace(before,after)):
                self.assert_rejected("public_allowlist")

    def test_news_and_websocket_cannot_switch_to_private_or_other_endpoints(self):
        with self.changed("src/polybot/official_news.py",lambda s:s.replace('https://site.api.espn.com','https://evil.invalid')):
            self.assert_rejected("official_routes")
        with self.changed("src/polybot/official_news.py",lambda s:s.replace('self.session.request("GET",','self.session.request("POST",')):
            self.assert_rejected("official_http")
        with self.changed("src/polybot/streams.py",lambda s:s.replace('/ws/market','/ws/user')):
            self.assert_rejected("public_stream")

    def test_default_dependencies_cannot_import_live_sdk_by_default(self):
        with self.changed("pyproject.toml",lambda s:s.replace('dependencies = ["polybot-observability",','dependencies = ["py-clob-client-v2>=1", "polybot-observability",',1)):
            self.assert_rejected("research_dependencies")

    def test_books_cannot_drop_their_side_identity(self):
        with self.changed("src/polybot/evidence.py",lambda s:s.replace('PRIMARY KEY(run_id,event_id,token_id)', 'PRIMARY KEY(run_id,event_id)')):
            self.assert_rejected("research_schema")

    def test_allowed_check_cannot_be_bypassed_at_http_call(self):
        with self.changed("src/polybot/public_clients.py",lambda s:s.replace(
                "self.session.request(method, HOSTS[source] + path, **kwargs)",
                'self.session.request("POST", HOSTS[source] + "/order", **kwargs)')):
            self.assert_rejected("public_http_binding")

    def test_redirect_and_environment_opt_in_fail(self):
        for before,after,check in (("self.session.trust_env = False", "self.session.trust_env = True", "public_environment"),
                ('"allow_redirects": False', '"allow_redirects": True', "public_redirects")):
            with self.subTest(change=after), self.changed("src/polybot/public_clients.py",lambda s:s.replace(before,after)):
                self.assert_rejected(check)

    def test_budget_position_cap_and_full_integrity_regressions_fail(self):
        with self.changed("src/polybot/runtime.py",lambda s:s.replace("phase='census'", "position_cap=config.trading.max_positions\n            phase='census'")):
            self.assert_rejected("research_position_gate")
        with self.changed("src/polybot/evidence.py",lambda s:s.replace(
                '"SELECT schema_version,contract_name,data_contract,database_utc_date,strategy_name,job_name,mode "',
                '"PRAGMA quick_check; SELECT schema_version,contract_name,data_contract,database_utc_date,strategy_name,job_name,mode "')):
            self.assert_rejected("unbounded_status")

    def test_fixture_names_and_keywords_without_assertions_do_not_pass(self):
        def fake(s):
            tree=ast.parse(s)
            for node in ast.walk(tree):
                if isinstance(node,ast.FunctionDef) and node.name.startswith('test_'):
                    node.body=[ast.Assert(ast.Constant(True))]
            return ast.unparse(tree)
        with self.changed("tests/test_runtime.py",fake):
            self.assert_rejected("test_fixture")


class LegacyIsolationTests(unittest.TestCase):
    def test_existing_28_strategy_set_and_exemptions_are_unchanged(self):
        self.assertEqual(gate.CURRENT_STRATEGIES, EXISTING_28)
        self.assertEqual(gate.PRE_L3_STRATEGIES, {"golden-apple","golden-banana"})
        self.assertNotIn("golden-guava", gate.RESEARCH_ONLY_STRATEGIES)

    def test_existing_28_real_contracts_still_pass_their_original_branches(self):
        for name in sorted(EXISTING_28):
            with self.subTest(strategy=name):
                self.assertEqual(gate.validate_strategy(REPO/name), [])

    def test_missing_legacy_trader_still_fails(self):
        with tempfile.TemporaryDirectory(prefix="legacy-contract-test-") as temp:
            root=Path(temp);target=root/'golden-cherry'
            shutil.copytree(REPO/'golden-cherry'/'src',target/'src',ignore=shutil.ignore_patterns('__pycache__'))
            (target/'src/polybot/strategy/trader.py').unlink()
            with patch.object(gate,'ROOT',root):
                findings=gate.validate_strategy(target)
            self.assertTrue(any(f.check=='missing_file' and 'strategy/trader.py' in f.detail for f in findings))


if __name__=='__main__':
    unittest.main()

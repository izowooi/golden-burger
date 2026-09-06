from __future__ import annotations

import pytest

from polybot.utils.retry import (
    CycleBudget,
    CycleBudgetExceeded,
    NetworkBudgetExceeded,
    PublicJsonTransport,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class NoNetworkSession:
    def __init__(self) -> None:
        self.trust_env = True
        self.headers = {}
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("network must not start after the cooperative cutoff")

    def close(self) -> None:
        pass


def test_network_cutoff_records_explicit_incomplete_receipt_without_http() -> None:
    clock = Clock()
    budget = CycleBudget(0, network_seconds=42, cycle_seconds=50, monotonic=clock)
    session = NoNetworkSession()
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3,
        read_timeout_seconds=30,
        max_retries=0,
        retry_base_seconds=0,
        retry_max_seconds=0,
        receipt_sink=receipts.append,
        session=session,
        budget=budget,
    )
    clock.value = 42

    with pytest.raises(NetworkBudgetExceeded):
        transport.request_json(
            "GET",
            "https://gamma-api.polymarket.com/events/keyset",
            request_kind="gamma_live_events_keyset:nba",
            run_id="run",
        )

    assert session.calls == 0
    assert receipts[0]["status"] == "SKIPPED_NETWORK_BUDGET"
    assert receipts[0]["error_type"] == "NetworkBudgetExceeded"
    assert budget.incomplete_reasons


def test_cycle_budget_is_cooperative_and_has_eight_second_persistence_reserve() -> None:
    clock = Clock()
    budget = CycleBudget(0, network_seconds=42, cycle_seconds=50, monotonic=clock)
    clock.value = 41
    connect, read = budget.request_timeouts(3, 30)
    assert connect + read <= 1.0 + 1e-9
    clock.value = 49.9
    budget.assert_cycle_available("persistence")
    clock.value = 50
    with pytest.raises(CycleBudgetExceeded):
        budget.assert_cycle_available("completion")


def test_time_spent_in_storage_cannot_be_published_as_success(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from polybot import bot as module
    from polybot.config import load_config

    config = load_config(str(Path(__file__).resolve().parents[1] / 'config.yaml'),
                         'watermelon-white-1m-v4b')
    config = replace(config, db_path=tmp_path / 'runtime' / 'trades_sim.db')
    clock = Clock()
    budget = CycleBudget(0, network_seconds=42, cycle_seconds=50, monotonic=clock)
    monkeypatch.setattr(module.CycleBudget, 'start', lambda **kw: budget)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda path: SimpleNamespace(total=10**12, free=9*10**11))
    repo, audit = MagicMock(), MagicMock()
    repo.record_storage_metric.return_value = {'db_bytes': 0}
    def health(*args, **kwargs):
        clock.value = 52.38
        return {'full_check_performed': False}
    repo.scheduled_database_check.side_effect = health
    monkeypatch.setattr(module, 'ResearchRepository', lambda *a, **kw: repo)
    monkeypatch.setattr(module, 'ResearchRunAudit', lambda *a, **kw: audit)
    for name in ('PublicJsonTransport', 'GammaClient', 'ClobClient', 'SportsClockClient'):
        monkeypatch.setattr(module, name, MagicMock())
    collector = MagicMock()
    collector.collect.return_value = {}
    monkeypatch.setattr(module, 'Collector', lambda *a, **kw: collector)
    with pytest.raises(CycleBudgetExceeded, match='post-health'):
        module.ResearchBot(config).run()
    audit.succeed.assert_not_called()
    audit.fail.assert_called_once()
    repo.close.assert_called_once()

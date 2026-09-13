from types import SimpleNamespace
import hashlib
import inspect

from polybot.bot import PolymarketBot
from polybot.db.models import init_database


def test_live_bot_never_runs_compact_maintenance_in_one_minute_cycle(monkeypatch):
    import polybot.bot as bot_module

    observed = {}
    clob_observed = {}

    def fake_init_database(path, requirements, **kwargs):
        observed.update(path=path, requirements=requirements, **kwargs)
        return SimpleNamespace(kw={})

    monkeypatch.setattr(bot_module, "init_database", fake_init_database)
    monkeypatch.setattr(bot_module, "GammaClient", lambda **kwargs: object())
    def fake_clob(*args, **kwargs):
        clob_observed.update(kwargs)
        return object()

    monkeypatch.setattr(bot_module, "ClobClientWrapper", fake_clob)
    config = SimpleNamespace(
        simulation_mode=False,
        db_path="live.db",
        api=object(),
        job_name="apricot-live-test",
        trading=SimpleNamespace(
            max_snapshot_gap_minutes=2.0,
            drawdown_loss_limit_usdc=300.0,
            archive=SimpleNamespace(retention_days=60),
            sport_family="mlb",
            sport_profile_version="profile-v1",
            yes_only_mode=False,
            strategy_source_digest="a" * 64,
            preregistration_sha256="b" * 64,
            lifecycle_mode="active",
        ),
    )

    monkeypatch.setattr(bot_module.Path, "exists", lambda self: True)
    monkeypatch.setattr(bot_module.Path, "stat", lambda self: SimpleNamespace(st_size=1))
    bot_module.PolymarketBot(config)

    assert observed["maintenance_on_start"] is False
    assert observed["schema_on_start"] is False
    assert observed["enable_research_raw"] is False
    assert clob_observed["execution_ledger_schema_on_start"] is False
    assert clob_observed["execution_ledger_bootstrap_legacy_orders"] is False


def test_new_live_database_runs_execution_ledger_preflight(monkeypatch, tmp_path):
    import polybot.bot as bot_module

    observed = {}

    monkeypatch.setattr(
        bot_module,
        "init_database",
        lambda *args, **kwargs: SimpleNamespace(kw={}),
    )
    monkeypatch.setattr(bot_module, "GammaClient", lambda **kwargs: object())

    def fake_clob(*args, **kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(bot_module, "ClobClientWrapper", fake_clob)
    config = SimpleNamespace(
        simulation_mode=False,
        db_path=tmp_path / "new.db",
        api=object(),
        job_name="apricot-live-test",
        trading=SimpleNamespace(
            max_snapshot_gap_minutes=2.0,
            archive=SimpleNamespace(retention_days=60),
            sport_family="mlb",
            sport_profile_version="profile-v1",
            yes_only_mode=False,
            strategy_source_digest="a" * 64,
            preregistration_sha256="b" * 64,
            lifecycle_mode="active",
        ),
    )

    bot_module.PolymarketBot(config)

    assert observed["execution_ledger_schema_on_start"] is True
    assert observed["execution_ledger_bootstrap_legacy_orders"] is True


def test_existing_live_schema_fast_open_is_read_only(tmp_path):
    db_path = tmp_path / "existing.db"
    init_database(str(db_path), maintenance_on_start=False)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    init_database(
        str(db_path),
        maintenance_on_start=False,
        schema_on_start=False,
    )

    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_one_minute_cycle_defers_snapshot_retention_scan():
    cycle_source = inspect.getsource(PolymarketBot.run_cycle)

    assert "repo.cleanup_old_snapshots" not in cycle_source
    assert "DEFERRED_OUTSIDE_ONE_MINUTE_COLLECTION" in cycle_source

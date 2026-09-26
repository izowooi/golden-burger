"""Deployed minute runtimes must retain evidence guards without DB maintenance."""
from types import SimpleNamespace
import hashlib
import sqlite3

import pytest
from sqlalchemy import event
from polybot_observability import SubmissionEvidenceError
from polybot.db.models import init_database
from polybot.submission_identity import PlumExecutionLedger


def test_existing_schema_fast_open_does_not_write_or_migrate(tmp_path, monkeypatch):
    import polybot.db.models as models
    path = tmp_path / 'deployed.db'
    init_database(str(path), maintenance_on_start=False, enable_research_raw=True).kw['bind'].dispose()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        pytest.fail('minute runtime attempted schema/maintenance writes')
    monkeypatch.setattr(models, '_ensure_columns', forbidden)
    monkeypatch.setattr(models, 'prepare_database', forbidden)
    statements = []
    original = models.create_engine
    def traced(*args, **kwargs):
        engine = original(*args, **kwargs)
        event.listen(engine, 'before_cursor_execute',
            lambda conn, cursor, statement, params, context, many: statements.append(statement))
        return engine
    monkeypatch.setattr(models, 'create_engine', traced)
    session = init_database(str(path), maintenance_on_start=False,
        schema_on_start=False, enable_research_raw=True)
    session.kw['bind'].dispose()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert statements and not any(s.lstrip().upper().startswith(
        ('CREATE', 'ALTER', 'INSERT', 'DELETE', 'UPDATE', 'VACUUM')) for s in statements)


@pytest.mark.parametrize('raw', [False, True])
def test_fast_open_refuses_missing_append_only_guard(tmp_path, raw):
    path = tmp_path / 'broken.db'
    init_database(str(path), maintenance_on_start=False, enable_research_raw=raw).kw['bind'].dispose()
    trigger = 'raw_book_observations_forbid_update' if raw else 'resolution_observations_forbid_delete'
    with sqlite3.connect(path) as con:
        con.execute('DROP TRIGGER '+trigger)
    with pytest.raises(RuntimeError, match='missing evidence guards'):
        init_database(str(path), maintenance_on_start=False,
            schema_on_start=False, enable_research_raw=raw)


def test_fast_open_refuses_incomplete_columns(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY)')
    with pytest.raises(RuntimeError, match='missing columns'):
        init_database(str(path), maintenance_on_start=False, schema_on_start=False)
    with sqlite3.connect(path) as con:
        assert len(list(con.execute('PRAGMA table_info(trades)'))) == 1


def test_fast_sell_ledger_does_not_bootstrap_and_rejects_missing_identity(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.db'
    ledger = PlumExecutionLedger(path, strategy_name='golden-plum')
    monkeypatch.setattr(PlumExecutionLedger, '_bootstrap_legacy_orders',
        lambda *a: pytest.fail('legacy bootstrap repeated'))
    PlumExecutionLedger(path, strategy_name='golden-plum',
        schema_on_start=False, bootstrap_legacy_orders=False)
    with sqlite3.connect(path) as con:
        con.execute('DROP TABLE plum_sell_request_identity')
    with pytest.raises(SubmissionEvidenceError, match='deployment preflight'):
        PlumExecutionLedger(path, strategy_name='golden-plum',
            schema_on_start=False, bootstrap_legacy_orders=False)


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('simulation', [False, True])
def test_bot_initialization_uses_deployed_ledger_only_when_database_exists(tmp_path, monkeypatch, existing, simulation):
    import polybot.bot as bot_module
    path = tmp_path / 'bot.db'
    if existing:
        path.write_bytes(b'already deployed')
    observed = {}
    def init(*a, **kw):
        observed['db'] = kw
        return SimpleNamespace(kw={})
    def clob(*a, **kw):
        observed['ledger'] = kw
        return object()
    monkeypatch.setattr(bot_module, 'init_database', init)
    monkeypatch.setattr(bot_module, 'GammaClient', lambda **kw: object())
    monkeypatch.setattr(bot_module, 'ClobClientWrapper', clob)
    config = SimpleNamespace(db_path=path, simulation_mode=simulation, api=object(),job_name='test',
        trading=SimpleNamespace(max_snapshot_gap_minutes=2,archive=SimpleNamespace(retention_days=60),
            sport_family='nfl',sport_profile_version='test',yes_only_mode=False,
            strategy_source_digest='a'*64,preregistration_sha256='b'*64,lifecycle_mode='active'))
    bot_module.PolymarketBot(config)
    assert observed['db']['maintenance_on_start'] is False
    assert observed['db']['schema_on_start'] is False
    assert observed['db']['enable_research_raw'] is simulation
    assert observed['ledger']['execution_ledger_schema_on_start'] is (not existing)
    assert observed['ledger']['execution_ledger_bootstrap_legacy_orders'] is (not existing)

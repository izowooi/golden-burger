import json
import sqlite3
import fcntl

import pytest
from polybot_observability import ExecutionLedger
from polybot.db.models import Trade,TradeStatus,init_database
from polybot.db.repository import TradeRepository
from scripts.admin_close_trade import BASIS,close_trade,ledger_digest

RUNTIME='plum-live-king-90-1m-v1'


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'trades.db';S=init_database(str(path),maintenance_on_start=False)
    ExecutionLedger(path,strategy_name='golden-plum')
    with S() as session:
        session.add(Trade(id=85,event_id='972649',condition_id='condition',token_id='token',
            outcome='No',mode='live',status=TradeStatus.QUARANTINED,
            buy_confirmed_size=13.698629,buy_confirmed_vwap=.73,buy_confirmed_fee_usdc=.135,
            exit_reason='stop_sell_reconciliation_timeout_3h_unknown_exposure'))
        session.commit()
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE run_audits(job_name TEXT,mode TEXT,started_at TEXT)')
        con.execute('INSERT INTO run_audits VALUES(?,?,?)',(RUNTIME,'live','2026-09-26T10:00:00Z'))
    yield path,S
    S.kw['bind'].dispose()


def action(path,**kwargs):
    return close_trade(path,runtime=RUNTIME,trade_id=85,expected_event='972649',
        reason='explicit user-directed legacy management close',**kwargs)


def test_dry_run_does_not_change_state_or_create_audit(db):
    path,S=db;result=action(path)
    assert result['status']=='DRY_RUN' and result['actual_pnl'] is None
    with sqlite3.connect(path) as con:
        assert con.execute('select status from trades where id=85').fetchone()[0]=='QUARANTINED'
        assert not con.execute("select name from sqlite_master where name='user_trade_closures'").fetchone()


def test_apply_preserves_economic_fields_and_ledger_and_excludes_closed_sample(db):
    path,S=db
    with sqlite3.connect(path) as con:
        con.row_factory=sqlite3.Row;digest=ledger_digest(con)
    result=action(path,apply=True)
    assert result['status']=='ADMINISTRATIVELY_CLOSED' and result['actual_pnl'] is None
    with sqlite3.connect(path) as con:
        con.row_factory=sqlite3.Row;assert ledger_digest(con)==digest
        row=dict(con.execute('select * from trades where id=85').fetchone())
        assert row['status']=='COMPLETED' and row['pnl_basis']==BASIS
        assert row['realized_pnl'] is None and row['sell_confirmed_size'] is None
        assert row['resolution_value'] is None and row['buy_confirmed_size']==13.698629
        audit=con.execute('select * from user_trade_closures').fetchone()
        assert json.loads(audit['before_json'])['status']=='QUARANTINED'
        with pytest.raises(sqlite3.IntegrityError):con.execute("delete from user_trade_closures")
    with S() as session:
        stats=TradeRepository(session).get_stats()
        assert stats['quarantined']==0 and stats['completed']==0 and stats['administrative_closed']==1
    assert action(path,apply=True)['status']=='ALREADY_ADMINISTRATIVELY_CLOSED'


def test_refuses_wrong_identity_and_proven_economic_close(db):
    path,S=db
    with pytest.raises(ValueError,match='runtime mismatch'):
        close_trade(path,runtime='wrong',trade_id=85,expected_event='972649',reason='user',apply=True)
    with S() as session:
        t=session.get(Trade,85);t.sell_confirmed_size=10;session.commit()
    with pytest.raises(ValueError,match='economic evidence'):action(path,apply=True)


def test_cannot_race_a_running_cycle(db):
    path,S=db
    with (path.parent/'.cycle-run.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):action(path,apply=True)

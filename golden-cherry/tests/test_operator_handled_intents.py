import sqlite3
from types import SimpleNamespace

import pytest

from polybot.config import TradingConfig
from polybot.db.models import init_database, Trade, TradeStatus
from polybot.db.operator_controls import TABLE, register_operator_handled
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader
from polybot_observability import ExecutionLedger
from tests.test_exposure_reservations import _submission
from tests.test_time_and_order_safety import make_candidate


def setup(tmp_path):
    path=tmp_path/'trades.db';Session=init_database(str(path))
    ExecutionLedger(path,strategy_name='golden-cherry')
    with sqlite3.connect(path) as c:_submission(c,'old')
    return path,Session


def acknowledge(path, ids=None):
    with sqlite3.connect(path) as c:
        c.execute('BEGIN IMMEDIATE')
        return register_operator_handled(c,ids or ['old'],reason='owner accepted prior handling assumption',approval_id='user-approval')


def test_acknowledgement_preserves_venue_evidence_and_protects_token(tmp_path):
    path,Session=setup(tmp_path)
    with sqlite3.connect(path) as c:
        original=c.execute('SELECT * FROM order_submissions').fetchall()
    assert acknowledge(path)['created']==1
    assert acknowledge(path)['created']==0
    with Session() as s:
        repo=TradeRepository(s)
        assert repo.get_exposure_summary()['reserved_position_count']==0
        assert repo.get_exposure_summary()['operator_handled_unknown_buy_count']==1
        assert repo.get_entry_guard(-200)['entry_allowed'] is True
        assert repo.is_operator_protected_token('token-old') is True
        assert repo.is_operator_protected_token('other') is False
        bot=Trader(repo,SimpleNamespace(simulation_mode=False),TradingConfig(entry_drawdown_floor_usdc=-200))
        candidate=make_candidate();candidate['token_id']='token-old'
        assert bot.execute_buy(candidate) is None
        assert bot.execute_sell(SimpleNamespace(token_id='token-old',condition_id='c')) is False
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT * FROM order_submissions').fetchall()==original
        assert c.execute('SELECT count(*) FROM order_fills').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM trades').fetchone()[0]==0
        for operation in (f'DELETE FROM {TABLE}',f"UPDATE {TABLE} SET reason='edited'"):
            with pytest.raises(sqlite3.IntegrityError):c.execute(operation)


def test_fresh_unknown_still_blocks_and_loss_is_not_reset(tmp_path):
    path,Session=setup(tmp_path);acknowledge(path)
    with Session() as s:
        s.add(Trade(condition_id='prior-loss',token_id='loss',status=TradeStatus.COMPLETED,
            realized_pnl=-142.51849,pnl_basis='exact_reconciled_buy_sell_confirmed_fills_net_known_fees'))
        s.commit();repo=TradeRepository(s)
        assert not repo.get_entry_guard(-30)['entry_allowed']
        assert repo.get_entry_guard(-200)['entry_allowed']
        assert repo.get_entry_guard(-200)['exact_economic_pnl_usdc']==-142.51849
    with sqlite3.connect(path) as c:_submission(c,'fresh')
    with Session() as s:
        assert TradeRepository(s).get_entry_guard(-200)['unknown_buy_evidence_count']==1


def test_acknowledgement_rejects_known_order_and_managed_position(tmp_path):
    path,Session=setup(tmp_path)
    with sqlite3.connect(path) as c:_submission(c,'known',order_id='known-order',response_status='ACCEPTED')
    with pytest.raises(ValueError):acknowledge(path,['old','known'])
    with sqlite3.connect(path) as c:assert c.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0]==0
    with Session() as s:
        s.add(Trade(condition_id='managed',token_id='token-old',status=TradeStatus.HOLDING));s.commit()
    with pytest.raises(ValueError,match='managed trade'):acknowledge(path)


def test_source_identity_drift_fails_closed(tmp_path):
    path,Session=setup(tmp_path);acknowledge(path)
    with sqlite3.connect(path) as c:c.execute("UPDATE order_submissions SET requested_size=99 WHERE submission_id='old'")
    with Session() as s:
        with pytest.raises(RuntimeError,match='identity mismatch'):TradeRepository(s).get_entry_guard(-200)


def test_cli_dry_run_and_backed_up_apply(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    path,Session=setup(tmp_path)
    script=Path(__file__).resolve().parents[1]/'scripts/acknowledge_handled_intents.py'
    args=[sys.executable,str(script),'--db',str(path),'--submission-id','old',
          '--approval-id','user-approval','--reason','operator assumes handled']
    dry=json.loads(subprocess.check_output(args,text=True))
    assert dry['applied'] is False
    with sqlite3.connect(path) as c:assert c.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0]==0
    # A sibling directory, not the strategy's data directory, is required.
    backup_dir=tmp_path.parent/(tmp_path.name+'-operator-backups')
    result=json.loads(subprocess.check_output(args+['--apply','--expected-set-sha256',
        dry['submission_set_sha256'],'--backup-dir',str(backup_dir)],text=True))
    assert result['applied'] is True and result['venue_outcome_proven'] is False
    backup=Path(result['backup'])
    assert backup.exists() and backup.with_suffix('.manifest.json').exists()
    with sqlite3.connect(backup) as c:
        assert c.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0]==0
        assert c.execute("SELECT response_status FROM order_submissions WHERE submission_id='old'").fetchone()[0]=='SUBMIT_OUTCOME_UNKNOWN'

"""User-authorized management closure; never manufacture fills or P&L.

Run with the Jenkins timer paused. The original trade and ledger digest are
retained in an append-only audit; venue orders and wallet assets are untouched.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3

BASIS = "USER_DIRECTED_ADMIN_CLOSE_UNKNOWN_EXECUTION"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ledger_digest(con):
    value = {}
    for table in ("order_submissions", "order_status_events", "order_fills"):
        value[table] = [dict(r) for r in con.execute('SELECT * FROM "'+table+'" ORDER BY rowid')]
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def close_trade(db, *, runtime, trade_id, expected_event, reason, apply=False):
    path = Path(db)
    if path.is_symlink() or not path.is_file() or not reason.strip():
        raise ValueError("existing standalone DB and explicit reason required")
    with (path.parent / ".cycle-run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        con = sqlite3.connect(path.resolve().as_uri()+"?mode=rw", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            run = con.execute("SELECT job_name,mode FROM run_audits ORDER BY started_at DESC LIMIT 1").fetchone()
            if not run or run['job_name'] != runtime or run['mode'] != 'live':
                raise ValueError("exact live runtime mismatch")
            row = con.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if not row or str(row['event_id']) != str(expected_event) or row['mode'] != 'live':
                raise ValueError("exact live trade/event mismatch")
            before = dict(row)
            if before.get('pnl_basis') == BASIS and before['status'] == 'COMPLETED':
                audit = con.execute("SELECT * FROM user_trade_closures WHERE trade_id=?",(trade_id,)).fetchone()
                if not audit:raise ValueError("administrative closure audit missing")
                con.rollback()
                return {'status':'ALREADY_ADMINISTRATIVELY_CLOSED','trade_id':trade_id,'actual_pnl':None}
            if before['status'] != 'QUARANTINED':
                raise ValueError("only explicitly quarantined trades may be waived")
            if any(before.get(k) is not None for k in ('realized_pnl','settlement_pnl_assumption','sell_confirmed_size','resolution_value')):
                raise ValueError("existing economic evidence requires normal reconciliation")
            digest = ledger_digest(con)
            result = {'status':'DRY_RUN','trade_id':trade_id,'event_id':str(expected_event),
                'old_status':before['status'],'new_status':'COMPLETED','pnl_basis':BASIS,
                'actual_pnl':None,'venue_orders_changed':False,'wallet_changed':False,
                'ledger_sha256':digest}
            if not apply:
                con.rollback();return result
            con.execute("""CREATE TABLE IF NOT EXISTS user_trade_closures (
                trade_id INTEGER PRIMARY KEY REFERENCES trades(id), runtime TEXT NOT NULL,
                authorized_at TEXT NOT NULL, authorization TEXT NOT NULL, reason TEXT NOT NULL,
                before_json TEXT NOT NULL, before_sha256 TEXT NOT NULL,
                after_json TEXT NOT NULL, after_sha256 TEXT NOT NULL, ledger_sha256 TEXT NOT NULL)""")
            for op in ('UPDATE','DELETE'):
                con.execute("CREATE TRIGGER IF NOT EXISTS user_trade_closures_forbid_"+op.lower()+
                    " BEFORE "+op+" ON user_trade_closures BEGIN SELECT RAISE(ABORT, 'append-only user closure'); END")
            con.execute("CREATE TRIGGER IF NOT EXISTS user_trade_closures_forbid_replace BEFORE INSERT ON user_trade_closures "
                "WHEN EXISTS(SELECT 1 FROM user_trade_closures WHERE trade_id=NEW.trade_id) BEGIN SELECT RAISE(ABORT, 'append-only user closure'); END")
            con.execute("UPDATE trades SET status='COMPLETED',exit_reason='user_directed_administrative_close_unknown_execution',pnl_basis=? WHERE id=?",(BASIS,trade_id))
            after = dict(con.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone())
            if ledger_digest(con) != digest:raise RuntimeError("execution ledger changed during administrative closure")
            b,a=canonical(before),canonical(after)
            con.execute("INSERT INTO user_trade_closures VALUES(?,?,?,?,?,?,?,?,?,?)",
                (trade_id,runtime,datetime.now(timezone.utc).isoformat(),'EXPLICIT_USER_INSTRUCTION',reason,b,
                 hashlib.sha256(b.encode()).hexdigest(),a,hashlib.sha256(a.encode()).hexdigest(),digest))
            con.commit();result['status']='ADMINISTRATIVELY_CLOSED';return result
        except Exception:
            con.rollback();raise
        finally:
            con.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,required=True);p.add_argument('--runtime',required=True)
    p.add_argument('--trade-id',type=int,required=True);p.add_argument('--expected-event',required=True)
    p.add_argument('--reason',required=True);p.add_argument('--apply',action='store_true')
    a=p.parse_args();print(json.dumps(close_trade(a.db,runtime=a.runtime,trade_id=a.trade_id,
        expected_event=a.expected_event,reason=a.reason,apply=a.apply),ensure_ascii=False))


if __name__=='__main__':main()

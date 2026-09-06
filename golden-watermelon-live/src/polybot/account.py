"""Ledger-only Cat/Dog admission under one full-child account lock.

No claims, central state DB, wallet-position import or peer mutation. Existing
INTENT-before-POST rows are the sole durable reservation/attempt authority.
Account 20 includes both DBs' open positions and untracked BUY reservations;
BUY5 counts ALL ledger attempts (even failed) in rolling 60 seconds. Cash checks
reserve pending principal; actual fee/allowance validation remains with CLOB.
Missing/unreadable peer state blocks BUY, not management of the readable member.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import os
import json
import sqlite3

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from . import config as configuration
from .db.repository import TradeRepository
from .utils.run_lock import exclusive_job_run_lock


class AccountGuardError(RuntimeError):
    def __init__(self, message, *, code="account_state_unavailable", evidence=None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def number(value):
    if isinstance(value, bool):
        raise AccountGuardError("invalid account amount")
    try:
        result = Decimal(str(value))
    except Exception:
        raise AccountGuardError("invalid account amount") from None
    if not result.is_finite() or result < 0:
        raise AccountGuardError("invalid account amount")
    return result


def account_paths(account):
    if account not in configuration.ACCOUNT_RUNTIMES:
        raise AccountGuardError("unregistered account")
    root = configuration.SOURCE_PROJECT_ROOT.resolve()
    paths = {job: root / "data" / job / "trades.db" for job in configuration.ACCOUNT_RUNTIMES[account]}
    return paths, root / "data" / f".{account}-account.lock"


def verify_member(path, runtime):
    if not path.is_file() or path.is_symlink():
        raise AccountGuardError("member DB absent; use prepare-account, never auto-create")
    try:
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.05)) as con:
            con.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"trades", "order_submissions", "order_fills"} <= tables:
                raise AccountGuardError("account member schema unavailable")
            if "run_audits" in tables and con.execute("SELECT 1 FROM run_audits WHERE job_name!=? OR strategy_name!='golden-watermelon-live' LIMIT 1", (runtime,)).fetchone():
                raise AccountGuardError("foreign runtime DB; copying wallet trades is forbidden")
            nonempty = con.execute("SELECT 1 FROM trades LIMIT 1").fetchone() or con.execute("SELECT 1 FROM order_submissions LIMIT 1").fetchone()
            if nonempty and ("run_audits" not in tables or con.execute("SELECT 1 FROM run_audits WHERE job_name=? AND strategy_name='golden-watermelon-live' LIMIT 1", (runtime,)).fetchone() is None):
                raise AccountGuardError("nonempty peer has no attributable runtime history")
            family = configuration.RUNTIME_SPECS[runtime].sport_family
            if con.execute("SELECT 1 FROM trades WHERE strategy_name!='golden-watermelon-live' OR (sport_family IS NOT NULL AND sport_family!=?) LIMIT 1", (family,)).fetchone():
                raise AccountGuardError("foreign strategy/sport trades in member DB")
    except AccountGuardError:
        raise
    except Exception:
        raise AccountGuardError("current member identity unavailable") from None


def read_database(path, runtime, *, now=None):
    """Reuse existing repository reservation semantics through read-only SQLite."""
    if not path.is_file() or path.is_symlink():
        raise AccountGuardError("account member database missing or symlinked")
    verify_member(path, runtime)
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(seconds=60)).isoformat(timespec="microseconds")
    def connect():
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.05)
        con.execute("PRAGMA query_only=ON")
        return con
    engine = create_engine("sqlite://", creator=connect, poolclass=NullPool)
    try:
        with Session(engine, autoflush=False) as session:
            tables = {r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            if not {"trades", "order_submissions", "order_fills"} <= tables:
                raise AccountGuardError("member schema missing; prepare-account required")
            if "run_audits" in tables:
                foreign = session.execute(text("SELECT 1 FROM run_audits WHERE job_name != :job OR strategy_name != 'golden-watermelon-live' LIMIT 1"), {"job": runtime}).first()
                if foreign:
                    raise AccountGuardError("member runtime ownership mismatch")
            if session.execute(text("SELECT 1 FROM order_submissions WHERE strategy_name!='golden-watermelon-live' OR simulation!=0 LIMIT 1")).first():
                raise AccountGuardError("foreign/simulation execution in account member")
            invalid_time = session.execute(text("SELECT 1 FROM order_submissions WHERE UPPER(side)='BUY' AND (julianday(submitted_at) IS NULL OR julianday(submitted_at)>julianday(:now)) LIMIT 1"),
                                           {"now": (now+timedelta(seconds=2)).isoformat()}).first()
            if invalid_time:
                raise AccountGuardError("BUY attempt timestamp is unbounded")
            attempts = session.execute(text("SELECT count(*) FROM order_submissions WHERE UPPER(side)='BUY' AND julianday(submitted_at)>julianday(:since)"), {"since": since}).scalar_one()
            repo = TradeRepository(session)
            capacity = repo.get_entry_capacity_state()
            # Unknown/orphan positive fills stay reserved exactly as in the
            # existing repository; linked pending rows must reserve cash too.
            pending = {r["submission_id"]: r for r in repo.get_untracked_buy_submissions()}
            for row in session.execute(text("""SELECT s.* FROM order_submissions s
                JOIN trades t ON t.buy_order_id=s.order_id
                WHERE UPPER(s.side)='BUY' AND t.status IN ('PENDING_BUY','HOLDING','PENDING_SELL','QUARANTINED')
                  AND (s.needs_reconciliation=1 OR s.response_status='INTENT' OR t.status='PENDING_BUY')""")).mappings():
                pending[row["submission_id"]] = dict(row)
            cash = Decimal(0)
            for row in pending.values():
                price, size = number(row["requested_price"]), number(row["requested_size"])
                if not 0 < price < 1 or size <= 0:
                    raise AccountGuardError("invalid pending BUY price/size")
                bound = (price*size).quantize(Decimal(".01"), rounding=ROUND_CEILING)
                if row.get("making_amount") is not None:
                    bound = max(bound, number(row["making_amount"]))
                if not 0 < bound <= 1000:
                    raise AccountGuardError("pending BUY principal is unbounded")
                cash += bound
            cash_failed = bool(session.execute(text("SELECT 1 FROM order_submissions WHERE UPPER(side)='BUY' AND julianday(submitted_at)>julianday(:since) AND (LOWER(COALESCE(error_message,'')) LIKE '%balance%' OR LOWER(COALESCE(error_message,'')) LIKE '%allowance%') LIMIT 1"), {"since": since}).first())
            if "run_audits" in tables:
                for row in session.execute(text("SELECT cycle_stats_json FROM run_audits WHERE julianday(finished_at)>julianday(:since) AND cycle_stats_json IS NOT NULL"), {"since": since}):
                    stats = json.loads(row[0])
                    cash_failed |= stats.get("account_cash_failed") is True
            return {"slots": int(capacity["total_reserved"]), "reserved_cash": cash,
                    "attempts": int(attempts), "cash_failed": cash_failed,
                    "buy_evidence_gaps": repo.get_open_buy_evidence_gap_count()}
    except AccountGuardError:
        raise
    except Exception:
        raise AccountGuardError("account peer state unavailable") from None
    finally:
        engine.dispose()


class AccountGuard:
    def __init__(self, config):
        self.account = configuration.account_for_runtime(config.job_name)
        self.paths, self.lock_path = account_paths(self.account)
        self.config = config
        self.active = False
        self.pid = os.getpid()
        self.cash_failed = False
        self.block_buys = os.environ.get("POLYBOT_ACCOUNT_DISABLE_BUYS") == "1"

    def assert_active(self, config):
        if (not self.active or self.pid != os.getpid()
                or configuration.account_for_runtime(config.job_name) != self.account
                or config.api.funder_address != self.config.api.funder_address
                or config.api.signature_type != self.config.api.signature_type
                or config.db_path.resolve() != self.paths[config.job_name].resolve()):
            raise AccountGuardError("registered live runtime requires its account lock and exact DB")
        # Never inspect a failed peer here: own existing management must proceed.
        path = self.paths[config.job_name]
        if not path.is_file() or path.is_symlink():
            raise AccountGuardError("current member DB absent; never auto-create; use prepare-account")
        verify_member(path, config.job_name)

    def check_buy_budget(self):
        self.assert_active(self.config)
        if self.block_buys or self.cash_failed:
            raise AccountGuardError("account cash/profile failure blocks new BUY only",
                                    code="account_cash_or_profile_failure")
        now = datetime.now(timezone.utc)
        members = [read_database(path, job, now=now) for job, path in self.paths.items()]
        result = {"slots": sum(m["slots"] for m in members),
                  "reserved_cash": sum((m["reserved_cash"] for m in members), Decimal(0)),
                  "attempts": sum(m["attempts"] for m in members),
                  "buy_evidence_gaps": sum(m["buy_evidence_gaps"] for m in members)}
        evidence = {"total_reserved": result["slots"], "max_positions": 20,
                    "buy_attempts_last_60s": result["attempts"], "max_buy_attempts_last_60s": 5,
                    "reserved_cash": str(result["reserved_cash"]),
                    "buy_evidence_gaps": result["buy_evidence_gaps"]}
        reasons = []
        if result["slots"] >= 20:
            reasons.append("account_max_positions")
        if result["attempts"] >= 5:
            reasons.append("account_buy_rate_limit")
        if result["buy_evidence_gaps"]:
            reasons.append("account_buy_evidence_gap")
        if any(m["cash_failed"] for m in members):
            reasons.append("account_recent_cash_failure")
        if reasons:
            evidence["blocking_reasons"] = reasons
            raise AccountGuardError("combined account 20/5 cash/evidence guard",
                                    code=reasons[0], evidence=evidence)
        return result

    def approve_buy(self, amount, clob):
        state = self.check_buy_budget()
        try:
            balance = number(clob.get_collateral_balance())
        except Exception:
            self.cash_failed = True
            raise AccountGuardError("account balance unavailable", code="account_balance_unavailable") from None
        if balance - state["reserved_cash"] < number(amount):
            self.cash_failed = True
            raise AccountGuardError("account pending cash or insufficient collateral",
                                    code="account_insufficient_unreserved_cash")
        return state


@contextmanager
def account_session(config):
    if configuration.account_for_runtime(config.job_name) is None:
        yield None
        return
    guard = AccountGuard(config)
    with exclusive_job_run_lock(guard.lock_path) as acquired:
        if not acquired:
            raise AccountGuardError("account cycle already running")
        guard.active = True
        try:
            guard.assert_active(config)
            yield guard
        finally:
            guard.active = False


def prepare_account(config):
    """Explicit NEW MLB schema only; existing soccer database is never written."""
    from polybot_observability import ExecutionLedger
    from .db.models import init_database
    account = configuration.account_for_runtime(config.job_name)
    paths, lock = account_paths(account)
    soccer, mlb = configuration.ACCOUNT_RUNTIMES[account]
    with exclusive_job_run_lock(lock) as acquired:
        if not acquired:
            raise AccountGuardError("account busy")
        if config.job_name != soccer or config.db_path.resolve() != paths[soccer].resolve():
            raise AccountGuardError("prepare using the registered soccer runtime")
        read_database(paths[soccer], soccer)
        if paths[mlb].exists():
            raise AccountGuardError("new MLB target exists; refuse copy/adoption/reset")
        paths[mlb].parent.mkdir(parents=True, exist_ok=True)
        session = init_database(str(paths[mlb]))
        session.kw["bind"].dispose()
        ExecutionLedger(paths[mlb], strategy_name="golden-watermelon-live")
    return {"account": account, "new_mlb_runtime": mlb, "status": "PREPARED_NO_ORDERS"}

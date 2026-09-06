"""Immutable, policy-free PRE-POST plans in an EXISTING Guava live ledger DB.

PlanStore(path, job_name=..., account_fingerprint=...) pins the Lion/Wolf runtime
and opaque account namespace. Caller MUST derive/verify that fingerprint from
chain + funder + signature type against actual broker credentials, NEVER from a
private key. No address, credential, Broker, SDK or order execution belongs here.
Caller also owns workspace validation, single-writer and coherent inventory reads.

bind_run verifies RUNNING shared RunAudit and canonical Guava config separately.
Shared redaction is intentionally lossy (e.g. max_tokens_per_cycle); trusted
in-memory public_snapshot is required, not an arbitrary UI reconstruction. Both
config hashes and the complete public snapshot hash are retained, not equated.
record_plan preserves origin run forever; load_for_execution checks half-open
UTC validity and a registered compatible RUNNING attempt run. No Attempt table.
Historical bindings ignore expiry, retain unknown POSTs, and never infer parents
from tokens. All ledger IDs must have exact preexisting plan/envelope evidence.

This is NOT policy, freshness of venue quotes, cash, fee, position or order
approval. A returned plan cannot authorize a POST; caller must recheck execution
conditions/clock at dispatch. Reads never synthesize fills or alter ledger rows.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

STRATEGY = "golden-guava"
JOBS = {"guava-live-lion-a-v1": ("polybot-lion", "a"), "guava-live-wolf-b-v1": ("polybot-wolf", "b")}
PLAN_COLUMNS = ("decision_id", "side", "parent_buy_decision_id", "event_id", "condition_id", "token_id",
                "requested_quantity", "requested_limit_price", "run_id", "observed_at", "valid_until")
PROVENANCE = ("guava_config_hash", "shared_config_hash", "strategy_source_digest")
PUBLIC_FIELDS = {"strategy_name", "job_name", "jenkins_job", "mode", "simulation_mode", "release_stage", "data_contract",
                 "config_hash", "strategy_source_digest", "trading", "shard_index", "shard_count", "arm"}


class PlanStoreError(ValueError):
    """Fail closed; never substitute an empty plan/order inventory."""


def _check(condition, message):
    if not condition:
        raise PlanStoreError(message)


def _dump(value, *, ascii=True):
    from .evidence import _safe_json
    _safe_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii, allow_nan=False)


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _id(value):
    _check(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value), "public identifier required")
    return value


def _sha(value):
    _check(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "64-hex fingerprint/hash required")
    return value


def _number(value):
    _check(not isinstance(value, bool) and isinstance(value, (str, int, float, Decimal)), "decimal type required")
    result = Decimal(str(value))
    _check(result.is_finite() and 0 < result <= Decimal("1e18"), "finite positive decimal required")
    _check(result == result.normalize(), "decimal precision cannot be rounded")
    return result


def _time(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    _check(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "explicit UTC time required")
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now():
    return _time(datetime.now(timezone.utc))


class PlanStore:
    def __init__(self, path, *, job_name, account_fingerprint):
        _check(isinstance(job_name, str) and job_name in JOBS, "explicit Lion/Wolf runtime required")
        self.job_name, self.account_fingerprint = job_name, _sha(account_fingerprint)
        self.jenkins_job, self.arm = JOBS[job_name]
        self._closed = False
        try:
            self.path = Path(path).expanduser().resolve(strict=True)
            _check(self.path.is_file(), "existing ledger DB required")
            self._file_id = self._stat()
        except OSError:
            raise PlanStoreError("existing ledger DB required; never create missing DB") from None
        with self._transaction(write=True, initializing=True) as con:
            self._scope(con)
            con.execute("""CREATE TABLE IF NOT EXISTS guava_plan_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL, job_name TEXT NOT NULL, jenkins_job TEXT NOT NULL,
                account_fingerprint TEXT NOT NULL, data_contract TEXT NOT NULL, mode TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS guava_plan_runs (
                run_id TEXT PRIMARY KEY NOT NULL REFERENCES run_audits(run_id), guava_config_hash TEXT NOT NULL,
                shared_config_hash TEXT NOT NULL, strategy_source_digest TEXT NOT NULL,
                public_snapshot_json TEXT NOT NULL, public_snapshot_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS guava_position_plans (
                decision_id TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                parent_buy_decision_id TEXT, event_id TEXT NOT NULL, condition_id TEXT NOT NULL, token_id TEXT NOT NULL,
                requested_quantity TEXT NOT NULL, requested_limit_price TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES guava_plan_runs(run_id), observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
                plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(decision_id,side), CHECK((side='BUY' AND parent_buy_decision_id IS NULL)
                    OR (side='SELL' AND parent_buy_decision_id IS NOT NULL)))""")
            for table, key in (("guava_plan_identity", "singleton"), ("guava_plan_runs", "run_id"), ("guava_position_plans", "decision_id")):
                for operation in ("UPDATE", "DELETE"):
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable plan evidence'); END")
                match = f"{key}=NEW.{key}" + (" AND side=NEW.side" if table == "guava_position_plans" else "")
                con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {match}) BEGIN SELECT RAISE(ABORT,'immutable plan key collision'); END")
            if con.execute("SELECT 1 FROM guava_plan_identity").fetchone() is None:
                con.execute("INSERT INTO guava_plan_identity VALUES (1,1,?,?,?,?,?,?)",
                            (STRATEGY, job_name, self.jenkins_job, account_fingerprint, "guava-live-v1", "live"))
            self._identity(con)

    def _stat(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino

    @contextmanager
    def _transaction(self, *, write=False, initializing=False):
        con = None
        try:
            _check(not self._closed and self._stat() == self._file_id, "closed/replaced DB")
            con = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=.2)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            if write:
                con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            _check(self._stat() == self._file_id, "DB identity changed")
            if not initializing:
                self._scope(con); self._identity(con)
            with localcontext() as ctx:
                ctx.prec = 60
                yield con
            con.commit()
        except PlanStoreError:
            if con is not None: con.rollback()
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
            if con is not None: con.rollback()
            raise PlanStoreError("invalid/unavailable plan evidence; fail closed") from None
        finally:
            if con is not None: con.close()

    def _scope(self, con):
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _check("collection_contracts" not in tables and {"order_submissions", "run_audits", "strategy_configs"} <= tables, "existing shared live ledger/audit schema required")
        _check(con.execute("SELECT 1 FROM run_audits LIMIT 1").fetchone(), "live RunAudit required")
        for table, predicate, args in (
            ("run_audits", "strategy_name IS NOT ? OR job_name IS NOT ? OR mode IS NOT 'live'", (STRATEGY, self.job_name)),
            ("strategy_configs", "strategy_name IS NOT ? OR mode IS NOT 'live'", (STRATEGY,)),
            ("order_submissions", "strategy_name IS NOT ? OR simulation IS NOT 0", (STRATEGY,)),
        ):
            _check(con.execute(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1", args).fetchone() is None, "research/foreign runtime evidence forbidden")

    def _identity(self, con):
        expected = dict(singleton=1, schema_version=1, strategy_name=STRATEGY, job_name=self.job_name,
                        jenkins_job=self.jenkins_job, account_fingerprint=self.account_fingerprint, data_contract="guava-live-v1", mode="live")
        rows = list(con.execute("SELECT * FROM guava_plan_identity"))
        _check(len(rows) == 1 and dict(rows[0]) == expected, "immutable runtime/account fingerprint mismatch")

    def _public(self, public):
        p = json.loads(_dump(public))  # Secret check + detached immutable input bytes.
        _check(isinstance(p, dict) and set(p) == PUBLIC_FIELDS, "canonical public snapshot fields required; account identity is separate")
        _check(p["strategy_name"] == STRATEGY and p["job_name"] == self.job_name and p["jenkins_job"] == self.jenkins_job
               and p["mode"] == "live" and p["simulation_mode"] is False and p["data_contract"] == "guava-live-v1"
               and p["arm"] == self.arm and p["shard_index"] is None and type(p["shard_count"]) is int
               and p["shard_count"] == 4 and isinstance(p["trading"], dict), "public snapshot identity mismatch")
        _id(p["release_stage"]); _sha(p["strategy_source_digest"]); _sha(p["config_hash"])
        spec = dict(job_name=self.job_name, jenkins_job=self.jenkins_job, mode="live", shard=None, arm=self.arm)
        canonical = dict(strategy_name=STRATEGY, spec=spec, trading=p["trading"], strategy_source_digest=p["strategy_source_digest"])
        _check(_hash(_dump(canonical)) == p["config_hash"], "Guava canonical config hash mismatch")
        return p

    def _audit(self, con, run_id, public, *, running=False):
        from polybot_observability.run_audit import _canonical_json, _resolved_config
        row = con.execute("SELECT r.*, c.config_json, c.strategy_name AS config_strategy, c.mode AS config_mode FROM run_audits r JOIN strategy_configs c USING(config_hash) WHERE r.run_id=?", (_id(run_id),)).fetchone()
        _check(row is not None and row["strategy_name"] == row["config_strategy"] == STRATEGY
               and row["job_name"] == self.job_name and row["mode"] == row["config_mode"] == "live", "registered live RunAudit required")
        if running:
            _check(row["status"] == "RUNNING" and row["finished_at"] is None, "RUNNING live RunAudit required")
        expected = _canonical_json(_resolved_config(SimpleNamespace(trading=public["trading"], simulation_mode=False), STRATEGY))
        _check(expected == row["config_json"] and _hash(row["config_json"]) == row["config_hash"], "RunAudit resolved trading/redaction mismatch")
        return row

    def bind_run(self, run_id, public_snapshot):
        with self._transaction(write=True) as con:
            p = self._public(public_snapshot)
            audit = self._audit(con, run_id, p, running=True)
            candidate = dict(run_id=run_id, guava_config_hash=p["config_hash"], shared_config_hash=audit["config_hash"],
                             strategy_source_digest=p["strategy_source_digest"], public_snapshot_json=_dump(p), public_snapshot_sha256=_hash(_dump(p)))
            old = con.execute("SELECT * FROM guava_plan_runs WHERE run_id=?", (run_id,)).fetchone()
            if old:
                _check(all(old[k] == v for k, v in candidate.items()), "conflicting run binding")
                return dict(old)
            candidate["created_at"] = _now()
            con.execute(f"INSERT INTO guava_plan_runs ({','.join(candidate)}) VALUES ({','.join('?' for _ in candidate)})", tuple(candidate.values()))
            return candidate

    def _binding(self, con, run_id, *, running=False):
        row = con.execute("SELECT * FROM guava_plan_runs WHERE run_id=?", (_id(run_id),)).fetchone()
        _check(row is not None, "registered public run binding required")
        row = dict(row); p = self._public(json.loads(row["public_snapshot_json"]))
        audit = self._audit(con, run_id, p, running=running)
        _check(row["guava_config_hash"] == p["config_hash"] and row["shared_config_hash"] == audit["config_hash"]
               and row["strategy_source_digest"] == p["strategy_source_digest"]
               and row["public_snapshot_sha256"] == _hash(_dump(p)), "run binding hash mismatch")
        return row

    def _plan(self, con, decision_id, side):
        row = con.execute("SELECT * FROM guava_position_plans WHERE decision_id=? AND side=?", (_id(decision_id), side)).fetchone()
        _check(row is not None, "plan not found")
        payload = json.loads(row["plan_json"])
        _check(_hash(_dump(payload)) == row["plan_sha256"] and all(payload[k] == row[k] for k in PLAN_COLUMNS), "plan hash/columns mismatch")
        binding = self._binding(con, payload["run_id"])
        _check(all(payload[k] == binding[k] for k in PROVENANCE), "plan provenance mismatch")
        return dict(payload, plan_sha256=row["plan_sha256"], created_at=row["created_at"])

    @staticmethod
    def _has_envelopes(con):
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guava_execution_envelopes'").fetchone() is not None

    def _already_enveloped(self, con, decision_id, side):
        return self._has_envelopes(con) and con.execute("SELECT 1 FROM guava_execution_envelopes WHERE decision_id=? AND side=?", (decision_id, side)).fetchone() is not None

    def record_plan(self, *, run_id, decision_id, side, parent_buy_decision_id=None, event_id, condition_id, token_id,
                    requested_quantity, requested_limit_price, sport_family, league_code, hypothesis_id,
                    evidence_ref, evidence_sha256, observed_at, valid_until):
        with self._transaction(write=True) as con:
            binding = self._binding(con, run_id, running=True)
            _check(side in {"BUY", "SELL"}, "BUY/SELL side required")
            quantity, price = _number(requested_quantity), _number(requested_limit_price)
            _check(price < 1 and (5 <= quantity <= 100 and quantity % Decimal('.01') == 0 if side == "BUY" else quantity % Decimal('.000001') == 0), "plan quantity/price domain")
            _check(sport_family in {"soccer", "mlb", "nba", "nfl", "nhl"}, "unsupported sports family")
            _check(isinstance(evidence_ref, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,512}", evidence_ref), "public evidence reference required")
            p = dict(decision_id=_id(decision_id), side=side, parent_buy_decision_id=parent_buy_decision_id,
                     event_id=_id(event_id), condition_id=_id(condition_id), token_id=_id(token_id),
                     requested_quantity=format(quantity.normalize(), 'f'), requested_limit_price=format(price.normalize(), 'f'), run_id=_id(run_id),
                     observed_at=_time(observed_at), valid_until=_time(valid_until), sport_family=sport_family,
                     league_code=_id(league_code), hypothesis_id=_id(hypothesis_id), evidence_ref=evidence_ref,
                     evidence_sha256=_sha(evidence_sha256), **{k: binding[k] for k in PROVENANCE})
            _check(p["observed_at"] < p["valid_until"], "empty plan validity interval")
            if side == "SELL":
                parent = self._plan(con, _id(parent_buy_decision_id), "BUY")
                _check(all(p[k] == parent[k] for k in ("event_id", "condition_id", "token_id")), "SELL parent market identity mismatch")
            else:
                _check(parent_buy_decision_id is None, "BUY cannot have parent")
            raw = _dump(p)
            exists = con.execute("SELECT 1 FROM guava_position_plans WHERE decision_id=? AND side=?", (decision_id, side)).fetchone()
            if exists:
                old = self._plan(con, decision_id, side)
                _check(old["plan_sha256"] == _hash(raw), "conflicting immutable plan")
                return old
            _check(not self._already_enveloped(con, decision_id, side), "cannot backfill plan after envelope")
            created = _now()
            _check(p["observed_at"] <= created < p["valid_until"], "new plan is future-observed or already expired")
            con.execute(f"INSERT INTO guava_position_plans ({','.join(PLAN_COLUMNS)},plan_json,plan_sha256,created_at) VALUES ({','.join('?' for _ in range(len(PLAN_COLUMNS)+3))})",
                        (*[p[k] for k in PLAN_COLUMNS], raw, _hash(raw), created))
            return dict(p, plan_sha256=_hash(raw), created_at=created)

    def get_plan(self, decision_id, side):
        with self._transaction() as con:
            return self._plan(con, decision_id, side)

    def load_for_execution(self, decision_id, side, *, run_id, now):
        """Temporal/provenance guard only; original run_id is never rewritten."""
        with self._transaction() as con:
            p = self._plan(con, decision_id, side)
            attempt = self._binding(con, run_id, running=True)
            _check(all(p[k] == attempt[k] for k in PROVENANCE), "incompatible attempt source/config")
            _check(p["observed_at"] <= _time(now) < p["valid_until"], "plan outside validity interval")
            _check(not self._already_enveloped(con, decision_id, side), "plan already has envelope; reconcile via bindings")
            return dict(p, attempt_run_id=run_id, order_authorized=False)

    def bindings(self):
        """Complete independent ledger ID set + explicit SELL -> BUY links.

        Return keys: expected_submission_ids (frozenset), submission_by_plan
        ((decision_id,side)->submission_id), sell_to_buy, unbound_plans (tuple).
        Even failed/unknown/orphan ledger rows are included or cause failure.
        Expiry is irrelevant to historical attribution; no POST/order-ID needed.
        """
        with self._transaction() as con:
            submissions = {_id(r["submission_id"]): dict(r) for r in con.execute("SELECT * FROM order_submissions")}
            plans = {(r[0], r[1]): self._plan(con, r[0], r[1]) for r in con.execute("SELECT decision_id,side FROM guava_position_plans")}
            envs = list(con.execute("SELECT * FROM guava_execution_envelopes")) if self._has_envelopes(con) else []
            _check({r["submission_id"] for r in envs} == set(submissions), "orphan ledger/envelope inventory")
            by_plan = {}
            for row in envs:
                env, sid = json.loads(row["envelope_json"]), row["submission_id"]
                _check(_hash(_dump(env)) == row["envelope_sha256"], "envelope hash mismatch")
                key = (row["decision_id"], row["side"])
                _check(key in plans and key not in by_plan, "missing/ambiguous pre-POST plan")
                p, submission = plans[key], submissions[sid]
                _check(env["strategy_name"] == STRATEGY and env["schema"] == "guava-signed-envelope-v1" and env["order_type"] == "FOK"
                       and all(env[k] == p[k] for k in ("decision_id", "side", "event_id", "condition_id", "token_id"))
                       and submission["token_id"] == p["token_id"] and submission["side"] == p["side"], "plan/envelope market identity mismatch")
                _check(_number(env["requested_quantity"]) == _number(p["requested_quantity"])
                       and _number(env["requested_limit_price"]) == _number(p["requested_limit_price"]), "plan/envelope requested amount mismatch")
                _check(_number(env["signed_shares"]) == _number(submission["requested_size"])
                       and _number(env["limit_price"]) == _number(submission["requested_price"]), "ledger/envelope signed linkage mismatch")
                attempt = self._binding(con, submission["run_id"])
                _check(all(p[k] == attempt[k] for k in PROVENANCE), "incompatible submitted run/source/config")
                _check(p["created_at"] <= _time(submission["submitted_at"]), "plan did not preexist intent")
                by_plan[key] = sid
            links = {}
            for key, sid in by_plan.items():
                if key[1] == "SELL":
                    parent = (plans[key]["parent_buy_decision_id"], "BUY")
                    _check(parent in by_plan, "SELL parent BUY envelope missing")
                    _check(all(plans[key][k] == plans[parent][k] for k in ("event_id", "condition_id", "token_id")), "SELL parent market identity mismatch")
                    links[sid] = by_plan[parent]
            return dict(expected_submission_ids=frozenset(submissions), submission_by_plan=by_plan,
                        sell_to_buy=links, unbound_plans=tuple(sorted(set(plans) - set(by_plan))))

    def close(self):
        self._closed = True

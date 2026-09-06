"""Live-only, policy-free FOK broker. Importing this module does not import an SDK.

The caller owns the run audit, single-writer/workspace guard, position policy and
cycle Budget. Constructing Broker is an explicit live action; there is no sim
submission path. Monetary result fields are decimal strings, not assumed fills.

Only public quantities/IDs enter the DB. An immutable envelope links the ledger
intent to an append-only POST boundary. A crash at that boundary is ambiguous,
never permission to retry. decision_id + side is an enduring idempotency key.

The runner MUST read signed_reservations() before every BUY and account for ALL
reservation_required rows across ALL events, including unknown POSTs and fee
gaps. BUY maker caps reserve cash/position capacity; SELL caps reserve shares,
never provisional sale proceeds. Join to positions by submission_id to avoid
double counting, and release a filled BUY reservation only with the runner's
independent position/exit evidence. Broker event-local permission is NOT a
portfolio-cap, fee-budget or loss-limit approval.
"""
from __future__ import annotations

from contextvars import copy_context
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .budget import BudgetExceeded


STRATEGY = "golden-guava"
MICROS = Decimal(1_000_000)
SHARE_QUANTUM = Decimal("0.0001")
TICKS = {"0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"}


class BrokerContractError(ValueError):
    """Public, credential-free contract failure before submission."""


class BrokerEvidenceError(RuntimeError):
    """A durable write/read failed; the caller must fail closed."""


class BrokerCallError(RuntimeError):
    """SDK error with deliberately no original message/body/credentials."""


def _decimal(value: Any, *, zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise BrokerContractError("boolean is not an execution quantity")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise BrokerContractError("invalid decimal quantity") from None
    if not result.is_finite() or (result < 0 if zero else result <= 0):
        raise BrokerContractError("non-finite or out-of-domain quantity")
    if result > Decimal("1e30"):
        raise BrokerContractError("quantity exceeds supported precision")
    return result


def _integer(value: Any, *, zero: bool = False) -> int:
    number = _decimal(value, zero=zero)
    if number != number.to_integral_value():
        raise BrokerContractError("integer base units required")
    return int(number)


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
        raise BrokerContractError("public identifier required")
    return value


def _field(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _mapping(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    raise BrokerContractError("SDK response must be an object")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _status(value: Any) -> str:
    status = str(value or "UNKNOWN").upper()
    for prefix in ("ORDER_STATUS_", "TRADE_STATUS_"):
        status = status.removeprefix(prefix)
    allowed = {"UNKNOWN", "LIVE", "ACCEPTED", "DELAYED", "MATCHED", "MINED",
               "CONFIRMED", "FAILED", "RETRYING", "CANCELED", "CANCELLED",
               "CANCELED_MARKET_RESOLVED", "INVALID", "UNMATCHED"}
    return status if status in allowed else "UNKNOWN"


@dataclass(frozen=True, repr=False)
class Credentials:
    private_key: str = field(repr=False)
    funder: str = field(repr=False)
    signature_type: int = field(repr=False)
    api_key: str | None = field(default=None, repr=False)
    api_secret: str | None = field(default=None, repr=False)
    api_passphrase: str | None = field(default=None, repr=False)

    def __repr__(self):
        return "Credentials([REDACTED])"

    def __post_init__(self):
        if not isinstance(self.private_key, str) or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", self.private_key):
            raise BrokerContractError("invalid signing credentials")
        if not isinstance(self.funder, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.funder):
            raise BrokerContractError("invalid signing credentials")
        if type(self.signature_type) is not int or self.signature_type not in {0, 1, 2, 3}:
            raise BrokerContractError("invalid signing credentials")
        parts = (self.api_key, self.api_secret, self.api_passphrase)
        if any(x is not None for x in parts) and not all(isinstance(x, str) and x for x in parts):
            raise BrokerContractError("complete API credentials required")

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        try:
            return cls(env["POLYMARKET_PRIVATE_KEY"], env["POLYMARKET_FUNDER_ADDRESS"],
                       int(env["POLYMARKET_SIGNATURE_TYPE"]),
                       env.get("POLYMARKET_API_KEY"), env.get("POLYMARKET_API_SECRET"),
                       env.get("POLYMARKET_API_PASSPHRASE"))
        except (KeyError, ValueError, TypeError):
            raise BrokerContractError("complete live credentials required") from None


@dataclass(frozen=True)
class BrokerSettings:
    max_buy_notional: Decimal = Decimal("5")
    attempt_seconds: float = 12.0
    socket_seconds: float = 3.0
    max_book_age_seconds: float = 10.0
    max_catalog_pages: int = 50
    delayed_zero_fill_minutes: float = 2.0

    def __post_init__(self):
        cap = _decimal(self.max_buy_notional)
        if cap < 5 or cap > 100 or cap % Decimal("0.01"):
            raise BrokerContractError("BUY cap must be cent-aligned in [5, 100]")
        for number in (self.attempt_seconds, self.socket_seconds, self.max_book_age_seconds,
                       self.delayed_zero_fill_minutes):
            _decimal(number)
        if self.attempt_seconds > 45 or self.socket_seconds > self.attempt_seconds:
            raise BrokerContractError("finite bounded attempt required")
        if self.delayed_zero_fill_minutes < 1 or type(self.max_catalog_pages) is not int or not 1 <= self.max_catalog_pages <= 100:
            raise BrokerContractError("invalid recovery limits")


def _live_client(credentials: Credentials, settings: BrokerSettings):
    """Per-instance transport: no SDK-global monkeypatch, retry, or response logging.

    Public SDK _get/_post/_delete hooks are overridden so each socket and streamed
    body share the caller's whole-attempt deadline. The outer bounded wait also
    covers DNS, signing and SQLite C calls that cannot be interrupted cooperatively.
    """
    import httpx
    from py_clob_client_v2 import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds

    class DeadlineClient(ClobClient):
        def __init__(self):
            creds = (ApiCreds(credentials.api_key, credentials.api_secret, credentials.api_passphrase)
                     if credentials.api_key is not None else None)
            super().__init__(host="https://clob.polymarket.com", chain_id=137,
                             key=credentials.private_key, funder=credentials.funder,
                             signature_type=credentials.signature_type, creds=creds,
                             retry_on_error=False, use_server_time=False)
            self.transport = httpx.Client(http2=True, follow_redirects=False,
                                          transport=httpx.HTTPTransport(retries=0), trust_env=False)
            self.deadline_context = threading.local()

        def _request(self, method, endpoint, headers=None, data=None, params=None):
            deadline = getattr(self.deadline_context, "deadline", 0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BudgetExceeded("SDK attempt deadline exhausted")
            kwargs = {"content": data.encode()} if isinstance(data, str) else {"json": data}
            try:
                with self.transport.stream(method, endpoint, headers=headers, params=params,
                                           timeout=min(settings.socket_seconds, remaining), **kwargs) as response:
                    if response.status_code != 200:
                        raise BrokerCallError("CLOB HTTP request failed")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if time.monotonic() >= deadline or size > 8_000_000:
                            raise BudgetExceeded("CLOB body deadline/size exceeded")
                        chunks.append(chunk)
                    return json.loads(b"".join(chunks))
            except (BudgetExceeded, BrokerCallError):
                raise
            except Exception:
                raise BrokerCallError("CLOB transport failed") from None

        def _get(self, endpoint, headers=None, data=None, params=None):
            return self._request("GET", endpoint, headers, data, params)

        def _post(self, endpoint, headers=None, data=None, params=None):
            return self._request("POST", endpoint, headers, data, params)

        def _delete(self, endpoint, headers=None, data=None, params=None):
            return self._request("DELETE", endpoint, headers, data, params)

        def close(self):
            self.transport.close()

    return DeadlineClient()


class Broker:
    def __init__(self, ledger, db_path, budget, credentials=None, sdk_client=None, *, settings=None):
        # A live client without a ledger is never constructed, even for tests.
        from polybot_observability import ExecutionLedger

        if not isinstance(ledger, ExecutionLedger) or ledger.strategy_name != STRATEGY:
            raise BrokerContractError("golden-guava ExecutionLedger is mandatory")
        self.db_path = Path(db_path).expanduser().resolve()
        if self.db_path != ledger.db_path or not self.db_path.is_file():
            raise BrokerContractError("broker and ledger must use the same existing DB")
        self.ledger, self.budget = ledger, budget
        self.settings = settings or BrokerSettings()
        self._lock = threading.RLock()
        self._worker = None
        self._closed = False
        self._real_sdk = sdk_client is None
        self._client = None
        self._signature_type = credentials.signature_type if credentials else -1
        self._schema()
        self._deadline()
        if self._real_sdk:
            credentials = credentials or Credentials.from_environment()
            self._signature_type = credentials.signature_type
            try:
                self._client = _live_client(credentials, self.settings)
            except Exception:
                raise BrokerContractError("live SDK initialization failed") from None
        else:
            self._client = sdk_client

    def __repr__(self):
        return "Broker(strategy_name='golden-guava', live=True)"

    def _connect(self):
        # Never create a missing/replaced file; workspace validation is caller-owned.
        con = sqlite3.connect(self.db_path.as_uri() + "?mode=rw", uri=True, timeout=0.05)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _schema(self):
        con = self._connect()
        try:
            foreign = con.execute("SELECT 1 FROM order_submissions WHERE strategy_name != ? OR simulation != 0 LIMIT 1", (STRATEGY,)).fetchone()
            if foreign:
                raise BrokerContractError("foreign strategy/simulation ledger is forbidden")
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS guava_execution_envelopes (
                submission_id TEXT PRIMARY KEY REFERENCES order_submissions(submission_id),
                decision_id TEXT NOT NULL, side TEXT NOT NULL, envelope_json TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(decision_id, side))""")
            con.execute("""CREATE TABLE IF NOT EXISTS guava_execution_events (
                sequence INTEGER PRIMARY KEY, submission_id TEXT NOT NULL REFERENCES order_submissions(submission_id),
                phase TEXT NOT NULL, evidence_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(submission_id, phase, fingerprint))""")
            for table in ("guava_execution_envelopes", "guava_execution_events"):
                for operation in ("UPDATE", "DELETE"):
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable execution evidence'); END")
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _deadline(self):
        if self._closed:
            raise BrokerContractError("broker is closed")
        return time.monotonic() + min(float(self.budget.require()), self.settings.attempt_seconds)

    def _check(self, deadline, *, cleanup=False):
        remaining = (self.budget.require_commit() if cleanup else self.budget.require())
        remaining = min(float(remaining), deadline - time.monotonic())
        if remaining <= 0:
            raise BudgetExceeded("whole broker attempt exhausted")
        return remaining

    def _invoke(self, function, deadline, *, cleanup=False):
        """Bound the caller, never kill the process or retry an in-flight operation.

        A timed-out worker may finish its ONE already-started call. No worker can
        advance from signing/DB to POST. Until it exits this broker cannot dispatch
        another SDK/ledger call; its durable POST_STARTED remains unknown on crash.
        """
        remaining = self._check(deadline, cleanup=cleanup)
        if self._worker is not None and self._worker.is_alive():
            raise BrokerCallError("previous call still in flight")
        box, done = [], threading.Event()
        context = copy_context()  # Preserve RunAudit current_run_id in ledger calls.

        def run():
            try:
                if self._client is not None and self._real_sdk:
                    self._client.deadline_context.deadline = deadline
                box.append((True, context.run(function)))
            except Exception:
                box.append((False, None))  # Never retain/print SDK error bodies.
            finally:
                try:
                    if self._closed and self._real_sdk and self._client is not None:
                        self._client.close()
                finally:
                    done.set()

        self._worker = threading.Thread(target=run, daemon=True, name="guava-broker-call")
        self._worker.start()
        if not done.wait(remaining):
            raise BudgetExceeded("broker call outcome unavailable at deadline")
        if not box or not box[0][0]:
            raise BrokerCallError("broker call failed")
        self._check(deadline, cleanup=cleanup)
        return box[0][1]

    def _args(self, name, **values):
        if not self._real_sdk:
            return SimpleNamespace(**values)
        from py_clob_client_v2 import clob_types
        return getattr(clob_types, name)(**values)

    def _event(self, submission_id, phase, evidence):
        con = self._connect()
        try:
            con.execute("INSERT OR IGNORE INTO guava_execution_events (submission_id,phase,evidence_json,fingerprint,created_at) VALUES (?,?,?,?,?)",
                        (submission_id, phase, _json(evidence), _hash(evidence), time.time()))
            con.commit()
        finally:
            con.close()

    def _envelope(self, submission_id, envelope):
        con = self._connect()
        try:
            con.execute("INSERT INTO guava_execution_envelopes VALUES (?,?,?,?,?,?)",
                        (submission_id, envelope["decision_id"], envelope["side"], _json(envelope), _hash(envelope), time.time()))
            con.commit()
        finally:
            con.close()

    def _row(self, submission_id):
        con = self._connect()
        try:
            row = con.execute("SELECT s.*, e.envelope_json, e.envelope_sha256, e.decision_id AS envelope_decision_id, e.side AS envelope_side FROM order_submissions s JOIN guava_execution_envelopes e USING(submission_id) WHERE s.submission_id=? AND s.strategy_name=? AND s.simulation=0", (submission_id, STRATEGY)).fetchone()
            if row is None:
                raise BrokerContractError("submission has no Guava signed envelope; no backfill")
            result = dict(row)
            try:
                result["envelope"] = json.loads(result["envelope_json"])
            except (TypeError, ValueError):
                raise BrokerEvidenceError("invalid immutable envelope JSON") from None
            if _hash(result["envelope"]) != result["envelope_sha256"]:
                raise BrokerEvidenceError("immutable envelope hash mismatch")
            self._validate_reservation_envelope(result)
            return result
        finally:
            con.close()

    @staticmethod
    def _validate_reservation_envelope(row):
        """Only attributable, finite signed bounds may be isolated by event.

        Validate original amounts, never today's signer/config. A historic $100
        envelope remains a $100 reservation even when today's BUY cap is $5.
        """
        try:
            env = row["envelope"]
            for key in ("event_id", "condition_id", "decision_id", "token_id"):
                _identifier(env[key])
            if (env["schema"] != "guava-signed-envelope-v1" or env["strategy_name"] != STRATEGY
                    or env["order_type"] != "FOK" or env["side"] not in {"BUY", "SELL"}
                    or env["side"] != row["side"] or env["side"] != row["envelope_side"]
                    or env["token_id"] != row["token_id"] or env["decision_id"] != row["envelope_decision_id"]):
                raise BrokerContractError("reservation ownership mismatch")
            requested, price = _decimal(env["requested_quantity"]), _decimal(env["limit_price"])
            limit, tick = _decimal(env["requested_limit_price"]), _decimal(env["tick_size"])
            maker, taker = _integer(env["maker_amount"]), _integer(env["taker_amount"])
            shares, residual = _decimal(env["signed_shares"]), _decimal(env["sell_residual_shares"], zero=True)
            if (str(tick) not in TICKS or not tick <= price <= 1 - tick or price % tick
                    or limit >= 1 or _decimal(row["requested_price"]) != price
                    or _decimal(row["requested_size"]) != shares):
                raise BrokerContractError("invalid reservation amount/tick linkage")
            if env["side"] == "BUY":
                expected = (requested / price).quantize(SHARE_QUANTUM, rounding=ROUND_FLOOR)
                if (not 5 <= requested <= 100 or requested % Decimal("0.01") or price > limit
                        or maker != int(requested * MICROS) or maker % 10000 or taker % 100
                        or shares != Decimal(taker) / MICROS or shares != expected or residual != 0):
                    raise BrokerContractError("invalid signed BUY reservation")
            elif (price < limit or maker % 10000 or shares != Decimal(maker) / MICROS
                  or requested - shares != residual or not 0 <= residual < Decimal("0.01")
                  or not 0 <= Decimal(taker) / MICROS - shares * price < Decimal("0.000001")):
                raise BrokerContractError("invalid signed SELL reservation")
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise BrokerContractError("invalid signed reservation ownership/amount evidence") from None

    def _duplicate(self, decision, side):
        con = self._connect()
        try:
            row = con.execute("SELECT submission_id FROM guava_execution_envelopes WHERE decision_id=? AND side=?", (decision, side)).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _snapshot(self, submission_id):
        row = self._row(submission_id)
        con = self._connect()
        try:
            events = list(con.execute("SELECT phase,evidence_json FROM guava_execution_events WHERE submission_id=? ORDER BY sequence", (submission_id,)))
            fills = [dict(r) for r in con.execute("SELECT trade_id,bucket_index,status,side,size,price,liquidity_role,fee_amount_usdc,transaction_hash,domain_error FROM order_fills WHERE submission_id=?", (submission_id,))]
            status_evidence = con.execute("SELECT 1 FROM order_status_events WHERE submission_id=? LIMIT 1", (submission_id,)).fetchone()
        finally:
            con.close()
        confirmed = [f for f in fills if f["status"] == "CONFIRMED" and not f["domain_error"]]
        quantity = sum((_decimal(f["size"]) for f in confirmed), Decimal(0))
        gross = sum((_decimal(f["size"]) * _decimal(f["price"]) for f in confirmed), Decimal(0))
        fee_complete = bool(confirmed) and all(f["fee_amount_usdc"] is not None for f in confirmed)
        fee = sum((_decimal(f["fee_amount_usdc"], zero=True) for f in confirmed), Decimal(0)) if fee_complete else None
        proofs = [json.loads(e[1]) for e in events if e[0] == "RECONCILED"]
        proof = proofs[-1] if proofs else {}
        complete = bool(proof) and not row["needs_reconciliation"] and not row["reconciliation_error"]
        phases = {e[0] for e in events}
        no_post = "POST_STARTED" not in phases
        no_post_proven = (
            no_post and not any(phase.startswith("POST_") for phase in phases)
            and not fills and status_evidence is None and not row["order_id"]
            and not row["success"] and not row["needs_reconciliation"]
            and row["latest_order_status"] is None and row["latest_size_matched"] is None
            and row["response_status"] == "INTENT"
            and row["outcome_resolution"] in {None, "NO_ORDER_CREATED"}
            and json.loads(row["associated_trade_ids_json"]) == []
            and any(e[0] == "NO_POST" and json.loads(e[1]) == {
                "reason": "PRE_POST_ABORT", "envelope_sha256": row["envelope_sha256"]}
                for e in events)
        )
        zero_proven = complete and proof.get("zero_fill_proven") is True
        env = row["envelope"]
        reservation = {
            "submission_id": submission_id, "event_id": env["event_id"],
            "condition_id": env["condition_id"], "token_id": env["token_id"],
            "decision_id": env["decision_id"], "side": env["side"],
            "envelope_sha256": row["envelope_sha256"],
            "buy_notional_cap_usdc": str(Decimal(env["maker_amount"]) / MICROS) if env["side"] == "BUY" else None,
            "sell_shares_cap": env["signed_shares"] if env["side"] == "SELL" else None,
            "signed_shares": env["signed_shares"], "no_post_proven": no_post_proven,
            "reservation_required": not (no_post_proven or zero_proven),
            "runner_must_account": True, "portfolio_approval": False,
            "fee_budget_included": False, "fee_status": "KNOWN" if fee_complete or zero_proven else "UNKNOWN",
            "execution_complete": complete,
            "event_or_token_blocked": not (no_post_proven or (complete and (fee_complete or zero_proven))),
        }
        return {"submission_id": submission_id, "order_id": row["order_id"],
                "status": row["latest_order_status"] or row["response_status"],
                "phase": events[-1][0] if events else "ENVELOPE_COMMITTED",
                "no_post_proven": no_post_proven, "reservation": reservation,
                "no_post": no_post, "submission_outcome_unknown": not no_post and (not row["order_id"] or ("POST_UNKNOWN" in phases and not complete)),
                "envelope_sha256": row["envelope_sha256"], "envelope": env,
                "signed_maker_amount": env["maker_amount"], "signed_taker_amount": env["taker_amount"],
                "signed_shares": env["signed_shares"], "sell_residual_shares": env["sell_residual_shares"],
                "confirmed_shares": str(quantity) if confirmed or zero_proven else None,
                "confirmed_notional_usdc": str(gross) if confirmed or zero_proven else None,
                "confirmed_vwap": str(gross / quantity) if quantity else None,
                "fee_usdc": str(fee) if fee is not None else ("0" if zero_proven else None),
                "fee_status": "KNOWN" if fee_complete or zero_proven else "UNKNOWN",
                "fills": fills, "execution_complete": complete,
                "partial_terminal": complete and proof.get("partial_terminal", False),
                "remaining_order_shares": proof.get("remaining_order_shares") if complete else None,
                "exposure_unknown": not no_post and not complete,
                "new_risk_allowed": not reservation["event_or_token_blocked"],
                "risk_permission_scope": "EVENT_OR_TOKEN_ONLY_RUNNER_MUST_ACCOUNT_ALL_RESERVATIONS",
                "zero_fill_proven": zero_proven}

    def _reservation_snapshots(self, deadline):
        con = self._connect()
        try:
            orphan = con.execute("""SELECT 1 FROM order_submissions s
                LEFT JOIN guava_execution_envelopes e USING(submission_id)
                WHERE e.submission_id IS NULL OR s.strategy_name != ? OR s.simulation != 0 LIMIT 1""", (STRATEGY,)).fetchone()
            if orphan:
                raise BrokerContractError("orphan or unknown ownership blocks all execution")
            rows = con.execute("SELECT submission_id FROM guava_execution_envelopes ORDER BY submission_id")
            snapshots = []
            for row in rows:
                self._check(deadline)
                snapshots.append(self._snapshot(row[0]))  # Validates every original cap.
            self._check(deadline)
            return snapshots
        finally:
            con.close()

    def signed_reservations(self):
        """Read-only, COMPLETE portfolio reservation inventory or fail closed.

        Runner MUST account for every reservation_required row before each BUY,
        across all events, joining tracked positions by submission_id. Do not
        subtract an unknown SELL, or drop a BUY because its fee is unknown or its
        event is blocked. Caps exclude fees: runner must budget those separately.
        Completed fills remain listed until runner position/exit evidence accounts
        for them; execution_complete alone does not mean a position was closed.
        False reservation_required is reserved for proven NO_POST/zero-fill only.
        Orphan, invalid amount/ownership, DB/budget failure never returns a partial
        list, zero portfolio, or permission. This method does not submit or sign.
        """
        with self._lock:
            try:
                return [s["reservation"] for s in self._reservation_snapshots(self._deadline())]
            except sqlite3.Error:
                raise BrokerEvidenceError("complete reservation inventory unavailable") from None

    def _resolve_no_post(self, snapshot, deadline):
        if not snapshot["no_post_proven"]:
            return
        sid = snapshot["submission_id"]
        row = self._row(sid)
        if row["outcome_resolution"] == "NO_ORDER_CREATED":
            return
        # The caller's single-writer guard + broker lock prohibit another attempt
        # from progressing this intent. No in-flight worker may be reclassified.
        if self._worker is not None and self._worker.is_alive():
            raise BrokerCallError("prior call still in flight; preserve intent")
        self._invoke(lambda: self.ledger.resolve_uncertain_submission(
            sid, resolution="NO_ORDER_CREATED",
            reason="GUAVA_DURABLE_PRE_POST_ABORT envelope_sha256=" + row["envelope_sha256"]), deadline)

    def _risk_gate(self, token, side, event_id, deadline):
        snapshots = self._reservation_snapshots(deadline)
        for snapshot in snapshots:
            self._resolve_no_post(snapshot, deadline)
        self._invoke(lambda: self.ledger.assert_submission_allowed(token_id=token, side=side), deadline)
        if side != "BUY":
            return
        for snapshot in snapshots:
            reservation = snapshot["reservation"]
            if reservation["event_or_token_blocked"] and (
                reservation["event_id"] == event_id or reservation["token_id"] == token
            ):
                raise BrokerContractError("incomplete execution blocks this event/token only")

    def _authenticate(self, deadline):
        if getattr(self._client, "retry_on_error", False):
            raise BrokerContractError("SDK POST retries are forbidden")
        if getattr(self._client, "builder_config", None):
            raise BrokerContractError("builder-fee orders are unsupported")
        if self._real_sdk and self._client.creds is None:
            creds = self._invoke(self._client.derive_api_key, deadline)
            self._client.set_api_creds(creds)
        self._invoke(self._client.assert_level_2_auth, deadline)

    def _book(self, token, deadline):
        raw = self._invoke(lambda: self._client.get_order_book(token), deadline)
        if str(_field(raw, "asset_id", _field(raw, "token_id", ""))) != token:
            raise BrokerContractError("book token mismatch")
        timestamp = _decimal(_field(raw, "timestamp"))
        if timestamp > Decimal("1e11"):
            timestamp /= 1000
        age = Decimal(str(time.time())) - timestamp
        if age < -2 or age > Decimal(str(self.settings.max_book_age_seconds)):
            raise BrokerContractError("book timestamp is stale or future")
        return raw

    def fresh_book(self, token_ids):
        """Fresh SDK book objects, including empty sides; no signing or fee inference."""
        with self._lock:
            deadline = self._deadline()
            return {token: self._book(token, deadline) for token in dict.fromkeys(_identifier(t) for t in token_ids)}

    def market_state(self, condition_id):
        with self._lock:
            deadline = self._deadline()
            condition = _identifier(condition_id)
            raw = _mapping(self._invoke(lambda: self._client.get_market(condition), deadline))
            if raw.get("condition_id") != condition:
                raise BrokerContractError("market condition mismatch")
            return raw

    def _balance(self, token, deadline):
        self._authenticate(deadline)
        args = self._args("BalanceAllowanceParams", asset_type="CONDITIONAL" if token else "COLLATERAL",
                          token_id=token, signature_type=self._signature_type)
        raw = _mapping(self._invoke(lambda: self._client.get_balance_allowance(args), deadline))
        return Decimal(_integer(raw.get("balance"), zero=True)) / MICROS

    def collateral_balance(self):
        with self._lock:
            return self._balance(None, self._deadline())

    def token_balance(self, token):
        with self._lock:
            return self._balance(_identifier(token), self._deadline())

    def _fee_preflight(self, token, condition, deadline):
        raw = _mapping(self._invoke(lambda: self._client.get_clob_market_info(condition), deadline))
        tokens = raw.get("t", raw.get("tokens", []))
        if raw.get("c", raw.get("condition_id")) != condition or token not in {
            str(_field(t, "t", _field(t, "token_id", ""))) for t in tokens
        }:
            raise BrokerContractError("fee market/token identity mismatch")
        details = _mapping(raw.get("fd", raw.get("fee_details")))
        rate, exponent = _decimal(details.get("r"), zero=True), _integer(details.get("e"), zero=True)
        if rate > 1 or exponent > 10 or type(details.get("to")) is not bool:
            raise BrokerContractError("invalid dynamic fee schedule")
        # This is preflight metadata, NEVER backfilled as a historical actual fee.
        tick = _decimal(raw.get("mts"))
        if str(tick) not in TICKS or type(raw.get("nr")) is not bool:
            raise BrokerContractError("fresh market tick/neg-risk evidence missing")
        return {"condition_id": condition, "token_id": token, "rate": str(rate),
                "tick_size": str(tick), "neg_risk": raw["nr"],
                "exponent": exponent, "taker_only": details["to"], "observed_at": time.time()}

    def _prepare(self, token, quantity, limit, side, context, deadline):
        self._authenticate(deadline)  # Signer failure cannot create an INTENT.
        raw = self._book(token, deadline)
        if str(_field(raw, "market", "")) != context["condition_id"]:
            raise BrokerContractError("book condition mismatch")
        tick = _decimal(_field(raw, "tick_size"))
        if str(tick) not in TICKS:
            raise BrokerContractError("unsupported venue tick")
        # User limit is a ceiling for BUY / floor for SELL, never widened.
        price = (limit / tick).to_integral_value(rounding=ROUND_FLOOR if side == "BUY" else ROUND_CEILING) * tick
        if not tick <= price <= 1 - tick:
            raise BrokerContractError("limit outside venue tick domain")
        fee = self._fee_preflight(token, context["condition_id"], deadline)
        if fee["tick_size"] != str(tick) or fee["neg_risk"] != _field(raw, "neg_risk"):
            raise BrokerContractError("fresh book/market tick or neg-risk disagreement")
        balance = self._balance(None if side == "BUY" else token, deadline)
        shares = quantity if side == "BUY" else quantity.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        if shares <= 0 or quantity > balance:
            raise BrokerContractError("insufficient balance or sub-quantum SELL")
        levels = _field(raw, "asks" if side == "BUY" else "bids")
        if not isinstance(levels, (tuple, list)) or not levels:
            raise BrokerContractError("empty executable book")
        depth = Decimal(0)
        for level in levels:
            p, q = _decimal(_field(level, "price")), _decimal(_field(level, "size"))
            if p >= 1 or p % tick:
                raise BrokerContractError("book price outside tick domain")
            if (p <= price if side == "BUY" else p >= price):
                depth += p * q if side == "BUY" else q
        if depth < shares:
            raise BrokerContractError("full FOK displayed depth unavailable")
        opposite = _field(raw, "bids" if side == "BUY" else "asks")
        if not isinstance(opposite, (list, tuple)):
            raise BrokerContractError("opposite book side missing")
        if opposite:
            asks = [_decimal(_field(level, "price")) for level in _field(raw, "asks")]
            bids = [_decimal(_field(level, "price")) for level in _field(raw, "bids")]
            if bids and asks and max(bids) > min(asks):
                raise BrokerContractError("crossed book")
        options = self._args("PartialCreateOrderOptions", tick_size=str(tick), neg_risk=_field(raw, "neg_risk"))
        if type(options.neg_risk) is not bool:
            raise BrokerContractError("neg-risk evidence missing")
        if side == "BUY":
            args = self._args("MarketOrderArgs", token_id=token, amount=float(quantity), side=side, price=float(price), order_type="FOK")
            signed = self._invoke(lambda: self._client.create_market_order(args, options=options), deadline)
        else:
            args = self._args("OrderArgs", token_id=token, size=float(shares), side=side, price=float(price))
            signed = self._invoke(lambda: self._client.create_order(args, options=options), deadline)
        maker, taker = _integer(_field(signed, "makerAmount")), _integer(_field(signed, "takerAmount"))
        signed_side = _field(signed, "side")
        if (isinstance(signed_side, bool) or signed_side not in ({0, "BUY"} if side == "BUY" else {1, "SELL"})
                or str(_field(signed, "tokenId")) != token):
            raise BrokerContractError("signed token/side mismatch")
        if not _field(signed, "timestamp") or not isinstance(_field(signed, "signature"), str) or not _field(signed, "signature"):
            raise BrokerContractError("v2 signed order required")
        _integer(_field(signed, "timestamp"))
        if self._real_sdk:
            if (_field(signed, "signatureType") != self._signature_type
                    or str(_field(signed, "maker")).lower() != str(self._client.builder.funder).lower()):
                raise BrokerContractError("signed credential identity mismatch")
        if str(_field(signed, "builder", "0x" + "0" * 64)) != "0x" + "0" * 64:
            raise BrokerContractError("signed builder fee is forbidden")
        if side == "BUY":
            expected = (quantity / price).quantize(SHARE_QUANTUM, rounding=ROUND_FLOOR)
            if maker != int(quantity * MICROS) or maker % 10000 or taker % 100 or Decimal(taker) / MICROS != expected:
                raise BrokerContractError("signed BUY exact-notional/precision mismatch")
            signed_shares, residual = Decimal(taker) / MICROS, Decimal(0)
        else:
            signed_shares, residual = Decimal(maker) / MICROS, quantity - Decimal(maker) / MICROS
            if maker % 10000 or signed_shares != shares or not 0 <= residual < Decimal("0.01"):
                raise BrokerContractError("signed SELL exceeds safe quantity")
            # Never accept rounded-down proceeds that violate the user's floor.
            if Decimal(taker) / MICROS < shares * price or Decimal(taker) / MICROS - shares * price >= Decimal("0.000001"):
                raise BrokerContractError("signed SELL limit/amount precision mismatch")
        if signed_shares < _decimal(_field(raw, "min_order_size")):
            raise BrokerContractError("signed size below venue minimum")
        timestamp = _decimal(_field(raw, "timestamp"))
        if timestamp > Decimal("1e11"):
            timestamp /= 1000
        if Decimal(str(time.time())) - timestamp > Decimal(str(self.settings.max_book_age_seconds)):
            raise BrokerContractError("book stale after signer preflight")
        envelope = dict(context, strategy_name=STRATEGY, token_id=token, side=side, order_type="FOK",
                        schema="guava-signed-envelope-v1", requested_quantity=str(quantity),
                        requested_limit_price=str(limit),
                        limit_price=str(price), tick_size=str(tick), maker_amount=str(maker),
                        taker_amount=str(taker), signed_shares=str(signed_shares),
                        sell_residual_shares=str(residual), fee_preflight=fee,
                        book_timestamp=str(_field(raw, "timestamp")))
        return signed, envelope

    def buy_fok(self, token, notional, limit_price, context):
        return self._submit(token, notional, limit_price, context, "BUY")

    def sell_fok(self, token, shares, limit_price, context):
        return self._submit(token, shares, limit_price, context, "SELL")

    def _submit(self, token, quantity, limit_price, context, side):
        with self._lock:
            submission_id = None
            try:
                deadline = self._deadline()
                token, quantity, limit = _identifier(token), _decimal(quantity), _decimal(limit_price)
                if limit >= 1 or (side == "BUY" and (quantity < 5 or quantity > _decimal(self.settings.max_buy_notional) or quantity % Decimal("0.01"))):
                    raise BrokerContractError("price/notional outside execution limits")
                if not isinstance(context, Mapping) or set(context) != {"event_id", "condition_id", "decision_id"}:
                    raise BrokerContractError("exact event/condition/decision context required")
                context = {k: _identifier(v) for k, v in context.items()}
                previous = self._duplicate(context["decision_id"], side)
                if previous:
                    snapshot = self._snapshot(previous)
                    env = snapshot["envelope"]
                    if any(env[k] != v for k, v in context.items()) or env["token_id"] != token or _decimal(env["requested_quantity"]) != quantity or _decimal(env["requested_limit_price"]) != limit:
                        raise BrokerContractError("idempotency key reused with different request")
                    return dict(snapshot, duplicate=True)
                self._risk_gate(token, side, context["event_id"], deadline)
                signed, envelope = self._prepare(token, quantity, limit, side, context, deadline)
            except sqlite3.Error:
                raise BrokerEvidenceError("pre-POST DB read failed; no POST") from None
            except BrokerEvidenceError:
                raise
            except (BrokerContractError, BrokerCallError, BudgetExceeded) as error:
                return {"status": "NO_POST", "phase": "PREFLIGHT", "no_post": True,
                        "submission_id": None, "confirmed_shares": None, "fee_usdc": None,
                        "exposure_unknown": False, "new_risk_allowed": False,
                        "error_type": type(error).__name__}
            try:
                submission_id = self._invoke(lambda: self.ledger.record_intent(
                    token_id=token, side=side, requested_price=float(envelope["limit_price"]),
                    requested_size=float(envelope["signed_shares"]), simulation=False), deadline)
                self._envelope(submission_id, envelope)
                self._event(submission_id, "ENVELOPE_COMMITTED", {"envelope_sha256": _hash(envelope)})
                self._check(deadline)
            except Exception:
                # No worker in this block has permission to POST. A late ledger
                # commit remains an unresolved orphan; never auto-backfill/retry it.
                if submission_id:
                    try:
                        self._event(submission_id, "NO_POST", {"reason": "PRE_POST_ABORT", "envelope_sha256": _hash(envelope)})
                    except Exception:
                        pass
                raise BrokerEvidenceError("intent/envelope pre-POST failure; no POST") from None
            try:
                self._event(submission_id, "POST_STARTED", {"envelope_sha256": _hash(envelope)})
            except Exception:
                try:
                    self._event(submission_id, "NO_POST", {"reason": "PRE_POST_ABORT", "envelope_sha256": _hash(envelope)})
                except Exception:
                    pass  # Missing/conflicting proof stays reserved; no guessing.
                raise BrokerEvidenceError("POST boundary write failed; no POST") from None
            response = None
            observed_order_id = ""
            try:
                response = self._invoke(lambda: self._client.post_order(signed, "FOK"), deadline)
                response = _mapping(response)
                # All POST errors (including malformed/HTTP rejection) are kept
                # ambiguous without exact venue proof; never guessed as zero fill.
                order_id = _identifier(response.get("orderID"))
                observed_order_id = order_id
                self._event(submission_id, "POST_RESPONSE", {"order_id": order_id,
                            "status": _status(response.get("status"))})
                if response.get("success") is not True:
                    raise BrokerContractError("ambiguous submission response")
                trade_ids = response.get("tradeIDs", [])
                if not isinstance(trade_ids, list):
                    raise BrokerContractError("invalid submission trade IDs")
                public = {"success": True, "orderID": order_id, "status": _status(response.get("status")),
                          "tradeIDs": [_identifier(t) for t in trade_ids]}
            except Exception:
                try:
                    self._event(submission_id, "POST_UNKNOWN", {"reason": "POST_OUTCOME_UNAVAILABLE", "envelope_sha256": _hash(envelope)})
                    cleanup = time.monotonic() + min(1.0, float(self.budget.require_commit()))
                    # Avoid shared-ledger exception classification heuristics: this
                    # is explicitly an unknown outcome after the POST boundary.
                    self._invoke(lambda: self.ledger.mark_evidence_write_failure(
                        submission_id, order_id=observed_order_id, error=RuntimeError("POST_OUTCOME_UNKNOWN")), cleanup, cleanup=True)
                except Exception:
                    pass  # durable INTENT + POST_STARTED already reserves exposure
                return self._snapshot(submission_id)
            try:
                cleanup = time.monotonic() + min(2.0, float(self.budget.require_commit()))
                self._event(submission_id, "POST_RETURNED", public)
                # These are the ORIGINAL signed bounds, not actual POST fills.
                # Shared Ledger uses them to validate later confirmed quantities.
                ledger_result = dict(public, makingAmount=envelope["maker_amount"],
                                     takingAmount=envelope["taker_amount"])
                self._invoke(lambda: self.ledger.record_submission_result(submission_id, result=ledger_result, simulation=False), cleanup, cleanup=True)
            except Exception:
                # Exact ID remains recoverable from POST_RETURNED if its commit
                # succeeded; no cancel-all, no second POST, no success-shaped stub.
                raise BrokerEvidenceError("POST result evidence incomplete; do not resubmit") from None
            return self._snapshot(submission_id)

    def _trades(self, token, deadline):
        cursor, seen, rows = "MA==", set(), []
        params = self._args("TradeParams", asset_id=token)
        for _ in range(self.settings.max_catalog_pages):
            self._check(deadline)
            if cursor in seen:
                raise BrokerContractError("repeated trade cursor")
            seen.add(cursor)
            page = _mapping(self._invoke(lambda: self._client.get_trades_paginated(params, next_cursor=cursor), deadline))
            items = page.get("trades")
            if not isinstance(items, list):
                raise BrokerContractError("trade catalog shape invalid")
            rows.extend(_mapping(item) for item in items)
            cursor = page.get("next_cursor")
            if cursor == "LTE=":
                return rows
            if not isinstance(cursor, str) or not cursor:
                raise BrokerContractError("trade cursor completion missing")
        raise BrokerContractError("trade catalog incomplete at page budget")

    def _exact_fill(self, raw, row):
        order_id, env = row["order_id"], row["envelope"]
        makers = raw.get("maker_orders") or []
        if not isinstance(makers, list):
            raise BrokerContractError("invalid maker association")
        maker_match = any(_field(m, "order_id") == order_id for m in makers)
        taker_match = raw.get("taker_order_id") == order_id
        if not maker_match and not taker_match:
            return None
        if maker_match or raw.get("trader_side", "TAKER") != "TAKER":
            raise BrokerContractError("FOK exact taker correlation required")
        if str(raw.get("asset_id", "")) != env["token_id"] or raw.get("side") != env["side"] or raw.get("market") != env["condition_id"]:
            raise BrokerContractError("trade identity mismatch")
        size, price = _decimal(raw.get("size")), _decimal(raw.get("price"))
        if price >= 1:
            raise BrokerContractError("invalid actual fill price")
        trade = {"id": _identifier(raw.get("id")), "bucket_index": _integer(raw.get("bucket_index", 0), zero=True),
                 "status": _status(raw.get("status")), "taker_order_id": order_id,
                 "trader_side": "TAKER", "side": env["side"], "size": str(size), "price": str(price),
                 "fee_rate_bps": None}
        if raw.get("transaction_hash"):
            trade["transaction_hash"] = _identifier(raw["transaction_hash"])
        # V2 fee amounts are explicit fixed-6 on the exact authenticated fill.
        # Neither legacy zero bps nor today's market fee schedule is actual proof.
        if raw.get("fee_amount_usdc") is not None:
            trade["fee_amount_usdc"] = str(_integer(raw["fee_amount_usdc"], zero=True))
        return trade

    def reconcile(self, submission_ids):
        """Exact owned IDs only; no migration, amount reconstruction or resubmission."""
        with self._lock:
            deadline = self._deadline()
            results = {}
            self._authenticate(deadline)
            for submission_id in dict.fromkeys(submission_ids):
                submission_id = _identifier(submission_id)
                row = self._row(submission_id)
                if not row["order_id"]:
                    row = self._recover_response(row, deadline)
                if not row["order_id"]:
                    results[submission_id] = self._snapshot(submission_id)
                    continue
                try:
                    self._reconcile_one(row, deadline)
                except sqlite3.Error:
                    raise BrokerEvidenceError("reconciliation DB evidence failed") from None
                except (BrokerContractError, BrokerCallError, BudgetExceeded):
                    try:
                        cleanup = time.monotonic() + min(1.0, float(self.budget.require_commit()))
                        self._invoke(lambda: self.ledger.record_reconciliation_error(submission_id, RuntimeError("GUAVA_EXACT_EXECUTION_EVIDENCE_GAP")), cleanup, cleanup=True)
                    except Exception:
                        pass
                results[submission_id] = self._snapshot(submission_id)
            return results

    def _recover_response(self, row, deadline):
        """Recover an interrupted ledger write from original durable public facts."""
        sid = row["submission_id"]
        con = self._connect()
        try:
            response = con.execute("SELECT evidence_json FROM guava_execution_events WHERE submission_id=? AND phase='POST_RESPONSE' ORDER BY sequence DESC LIMIT 1", (sid,)).fetchone()
            accepted = con.execute("SELECT evidence_json FROM guava_execution_events WHERE submission_id=? AND phase='POST_RETURNED' ORDER BY sequence DESC LIMIT 1", (sid,)).fetchone()
        finally:
            con.close()
        if accepted:
            result = json.loads(accepted[0])
            result.update(makingAmount=row["envelope"]["maker_amount"], takingAmount=row["envelope"]["taker_amount"])
            self._invoke(lambda: self.ledger.record_submission_result(sid, result=result, simulation=False), deadline)
        elif response:
            oid = _identifier(json.loads(response[0])["order_id"])
            self._invoke(lambda: self.ledger.mark_evidence_write_failure(sid, order_id=oid, error=RuntimeError("ORIGINAL_POST_RESPONSE_RECOVERED")), deadline)
        return self._row(sid)

    def _reconcile_one(self, row, deadline):
        sid, order_id, env = row["submission_id"], row["order_id"], row["envelope"]
        detail = self._invoke(lambda: self._client.get_order(order_id), deadline)
        if not isinstance(detail, Mapping) or not detail:
            raise BrokerContractError("missing order detail is not zero fill")
        if detail.get("id") != order_id or detail.get("asset_id") != env["token_id"] or detail.get("market") != env["condition_id"] or detail.get("side") != env["side"]:
            raise BrokerContractError("order detail identity mismatch")
        original, matched = _decimal(detail.get("original_size")), _decimal(detail.get("size_matched"), zero=True)
        if abs(original - _decimal(env["signed_shares"])) > SHARE_QUANTUM:
            raise BrokerContractError("detail disagrees with persisted signed amount")
        if env["side"] == "SELL" and matched > original:
            raise BrokerContractError("SELL matched exceeds signed amount")
        trades = [t for raw in self._trades(env["token_id"], deadline) if (t := self._exact_fill(raw, row)) is not None]
        advertised = detail.get("associate_trades", detail.get("associated_trades", []))
        if not isinstance(advertised, list):
            raise BrokerContractError("invalid order trade associations")
        ids = list(dict.fromkeys([*[_identifier(t) for t in advertised], *[t["id"] for t in trades]]))
        status = _status(detail.get("status"))
        public_detail = {"id": order_id, "status": status, "original_size": str(original),
                         "size_matched": str(matched), "price": env["limit_price"], "associate_trades": ids}
        self._invoke(lambda: self.ledger.record_order_status(sid, public_detail, quantity_tolerance=0.0001), deadline)
        for trade in trades:
            # Never regress already-confirmed evidence or silently change its qty.
            con = self._connect()
            try:
                old = con.execute("SELECT status,size,price,fee_amount_usdc FROM order_fills WHERE submission_id=? AND trade_id=? AND bucket_index=?", (sid, trade["id"], trade["bucket_index"])).fetchone()
            finally:
                con.close()
            if old and old["status"] == "CONFIRMED":
                if trade["status"] != "CONFIRMED" or _decimal(old["size"]) != _decimal(trade["size"]) or _decimal(old["price"]) != _decimal(trade["price"]):
                    raise BrokerContractError("confirmed fill evidence regression/conflict")
                if old["fee_amount_usdc"] is not None and trade.get("fee_amount_usdc") is not None and _decimal(old["fee_amount_usdc"], zero=True) != _decimal(trade["fee_amount_usdc"], zero=True) / MICROS:
                    raise BrokerContractError("confirmed fee conflict")
            self._invoke(lambda t=trade: self.ledger.record_fill(sid, order_id, t), deadline)
        if not trades or any(t["status"] != "CONFIRMED" or not t.get("transaction_hash") for t in trades) or set(ids) != {t["id"] for t in trades}:
            # Even explicit CANCELED size=0 is insufficient without independent
            # exact cancellation + complete catalogs; cancel_exact owns that path.
            raise BrokerContractError("terminal exact confirmed trade proof incomplete")
        actual = sum((_decimal(t["size"]) for t in trades), Decimal(0))
        gross = sum((_decimal(t["size"]) * _decimal(t["price"]) for t in trades), Decimal(0))
        if abs(actual - matched) > Decimal("0.000001"):
            raise BrokerContractError("confirmed/matched quantity mismatch")
        signed_tokens = _decimal(env["signed_shares"])
        if env["side"] == "BUY" and gross > Decimal(env["maker_amount"]) / MICROS + Decimal("0.000001"):
            raise BrokerContractError("confirmed BUY exceeds signed maker cap")
        terminal = status in {"MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID"}
        if not terminal or (status == "MATCHED" and actual + SHARE_QUANTUM < signed_tokens):
            raise BrokerContractError("partial MATCHED is not terminal full fill")
        # Shared ledger finalizes quantity separately from fee coverage. Our risk
        # gate still blocks when an exact CONFIRMED fee is absent.
        if not self._invoke(lambda: self.ledger.finish_reconciliation(sid), deadline):
            raise BrokerContractError("ledger terminal coverage incomplete")
        self._event(sid, "RECONCILED", {"zero_fill_proven": False,
                    "partial_terminal": actual < signed_tokens - SHARE_QUANTUM,
                    "remaining_order_shares": str(max(Decimal(0), signed_tokens - actual)),
                    "proof": "EXACT_ORDER_AND_CONFIRMED_TRADES"})

    def cancel_exact(self, order_id):
        """Cancel one broker-owned order. An ACK alone never releases exposure.

        Missing-detail zero-fill recovery is deliberately limited to stale DELAYED
        FOK orders under the shared ledger's conjunction proof. Other missing
        catalog cases remain unknown and require operator evidence.
        """
        with self._lock:
            deadline, order_id = self._deadline(), _identifier(order_id)
            con = self._connect()
            try:
                rows = list(con.execute("SELECT s.submission_id FROM order_submissions s JOIN guava_execution_envelopes e USING(submission_id) WHERE s.order_id=?", (order_id,)))
            finally:
                con.close()
            if len(rows) != 1:
                raise BrokerContractError("cancellation requires one exact broker-owned ID")
            sid = rows[0][0]
            self._authenticate(deadline)
            self._event(sid, "CANCEL_REQUESTED", {"order_id": order_id})
            cancellation = _mapping(self._invoke(lambda: self._client.cancel_orders([order_id]), deadline))
            canceled, not_canceled = cancellation.get("canceled"), cancellation.get("not_canceled")
            ack = canceled == [order_id] and not_canceled == {}
            absent = (canceled == [] and isinstance(not_canceled, Mapping) and set(not_canceled) == {order_id}
                      and str(not_canceled[order_id]).lower() in {"not found", "already canceled", "already cancelled"})
            if not ack and not absent:
                raise BrokerContractError("exact cancellation proof missing")
            self._event(sid, "CANCEL_ACK", {"order_id": order_id, "exact_canceled": ack, "exact_absent": absent})
            row = self._row(sid)
            detail = self._invoke(lambda: self._client.get_order(order_id), deadline)
            zero_detail = (isinstance(detail, Mapping) and _status(detail.get("status")) in {"CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED"}
                           and _decimal(detail.get("size_matched"), zero=True) == 0)
            if detail and not zero_detail:
                self._reconcile_one(row, deadline)
                return self._snapshot(sid)
            if not zero_detail and row["response_status"] != "DELAYED":
                return self._snapshot(sid)
            trades = self._trades(row["envelope"]["token_id"], deadline)
            # New Guava v2 envelopes only; never query/migrate legacy order epochs.
            catalog = self._invoke(lambda: self._client.get_open_orders(), deadline)
            if not isinstance(catalog, list) or any(not isinstance(item, Mapping) for item in catalog):
                raise BrokerContractError("order catalog completeness unavailable")
            if any(item.get("id") == order_id for item in catalog):
                raise BrokerContractError("canceled order still present in catalog")
            if any(raw.get("taker_order_id") == order_id or any(_field(m, "order_id") == order_id for m in raw.get("maker_orders", [])) for raw in trades):
                raise BrokerContractError("exact trade evidence forbids zero-fill proof")
            self._event(sid, "CANCEL_CATALOG_PROOF", {"order_id": order_id,
                        "token_id": row["envelope"]["token_id"], "complete_token_trades": True,
                        "complete_current_v2_orders": True, "exact_order_absent": True})
            if zero_detail:
                env = row["envelope"]
                if (detail.get("id") != order_id or detail.get("asset_id") != env["token_id"]
                        or detail.get("market") != env["condition_id"] or detail.get("side") != env["side"]
                        or abs(_decimal(detail.get("original_size")) - _decimal(env["signed_shares"])) > SHARE_QUANTUM
                        or detail.get("associate_trades", detail.get("associated_trades", []))
                        or json.loads(row["associated_trade_ids_json"])):
                    raise BrokerContractError("zero-detail identity/association conflict")
                con = self._connect()
                try:
                    any_fill = con.execute("SELECT 1 FROM order_fills WHERE submission_id=? LIMIT 1", (sid,)).fetchone()
                finally:
                    con.close()
                if any_fill:
                    raise BrokerContractError("persisted fill forbids zero-fill proof")
                public = {"id": order_id, "status": "CANCELED", "original_size": env["signed_shares"],
                          "size_matched": "0", "price": env["limit_price"], "associate_trades": []}
                self._invoke(lambda: self.ledger.record_order_status(sid, public), deadline)
                if not self._invoke(lambda: self.ledger.finish_reconciliation(sid), deadline):
                    raise BrokerContractError("zero-fill ledger proof incomplete")
                proof = "EXACT_CANCEL_DETAIL_AND_COMPLETE_CATALOGS_ZERO_FILL"
            else:
                from polybot_observability import ClobReconciliationPhaseError, ClobResponseUnavailableError
                absence = ClobReconciliationPhaseError("match_authoritative_order_catalogs",
                            ClobResponseUnavailableError("exact current-v2 order absent"), "complete_empty_exact_match")
                self._invoke(lambda: self.ledger.record_reconciliation_error(sid, absence), deadline)
            # The shared helper independently rejects associated/partial fills.
                proof = self._invoke(lambda: self.ledger.record_delayed_fok_zero_fill(
                    order_id=order_id, token_id=row["envelope"]["token_id"], cancellation=cancellation,
                    authenticated_trades=trades, minimum_age_minutes=self.settings.delayed_zero_fill_minutes), deadline)
            self._event(sid, "RECONCILED", {"zero_fill_proven": True, "partial_terminal": False,
                        "remaining_order_shares": row["envelope"]["signed_shares"], "proof": proof})
            return self._snapshot(sid)

    def close(self):
        """No network, liquidation, cancellation or forced process termination."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._worker is not None and self._worker.is_alive():
                # Closing a socket while an ambiguous POST is active is not proof
                # of cancellation. Caller must preserve the DB for reconciliation.
                return
            if self._real_sdk and self._client is not None:
                self._client.close()

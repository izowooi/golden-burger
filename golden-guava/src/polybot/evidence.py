"""Accountless Guava evidence, using only the Python standard library.

The caller owns workspace/mount validation and the single-writer lock. STARTED
and public request receipts are committed independently; publication is atomic.
``run_audits`` contains immutable starts, ``run_events`` the append-only lifecycle.
Only ``latest_event_state`` is mutable and it is never the historical authority.

Hashes cover canonical UTF-8 JSON (not unavailable HTTP wire bytes). Unknown
public fields are retained, including clocks, fees and full books. No missing
price, fee, fill or P&L is inferred. ``read_only=True`` never creates a database.
An optional budget implements ``require_commit()`` and bounds publication SQL
cooperatively. Failure audit writes intentionally remain available after expiry.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote


DATA_CONTRACT = "guava-research-v1"
SCHEMA_VERSION = 1
_IDENTITY_KEYS = ("strategy_name", "job_name", "mode", "data_contract")
_COHORT_KEYS = (*_IDENTITY_KEYS, "config_hash", "strategy_source_digest")
_SECRET_PARTS = (
    "secret", "privatekey", "apikey", "accesskey", "accesstoken", "refreshtoken",
    "idtoken", "clientauth", "authorization", "password", "passwd", "passphrase",
    "credential", "signaturetype", "funderaddress", "walletaddress",
)
_SECRET_EXACT = {"auth", "authentication", "token", "jwt", "cookie", "setcookie",
                 "mnemonic", "seedphrase", "signingkey", "funder"}
_SECRET_TEXT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:bearer|basic)\s+\S+|"
    r"\b(?:secret[_-]?key|private[_-]?key|api[_-]?key|access[_-]?token|"
    r"client[_-]?auth|password|authorization)\s*[=:]\s*\S+|"
    r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE,
)


def _safe_json(value):
    """Reject credential-shaped keys/values, not public market token identifiers.

    Unknown objects and byte strings are rejected, never stringified. Exceptions
    intentionally contain neither offending values nor caller-supplied keys.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("evidence JSON keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in _SECRET_EXACT or any(part in normalized for part in _SECRET_PARTS):
                raise ValueError("credential-shaped evidence is forbidden")
            _safe_json(key)
            _safe_json(child)
    elif isinstance(value, list):
        for child in value:
            _safe_json(child)
    elif isinstance(value, str):
        decoded = unquote(value)
        if _SECRET_TEXT.search(decoded):
            raise ValueError("credential-shaped evidence is forbidden")
        # Some public endpoints wrap arrays/objects in JSON strings.
        if decoded.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(decoded)
            except (ValueError, RecursionError):
                pass
            else:
                _safe_json(nested)
    elif value is None or isinstance(value, (bool, int)):
        pass
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite evidence number")
    else:
        raise ValueError("evidence must contain only JSON types")


def _json(value):
    _safe_json(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _freeze(value):
    return json.loads(_json(value))


def _raw(value):
    if value is None:
        return None, None
    encoded = _json(value).encode("utf-8")
    return gzip.compress(encoded, mtime=0), hashlib.sha256(encoded).hexdigest()


def _text(value):
    _safe_json(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a nonempty text field is required")
    return value


def _time(value):
    _text(value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("an ISO-8601 timestamp is required") from None
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _mapping(value, required=()):
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise ValueError("evidence object is missing required fields")


_SCHEMA = (
    """CREATE TABLE collection_contracts (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        schema_version INTEGER NOT NULL CHECK(schema_version=1),
        contract_name TEXT NOT NULL CHECK(contract_name='guava-research-v1'),
        data_contract TEXT NOT NULL CHECK(data_contract='guava-research-v1'),
        database_utc_date TEXT CHECK(database_utc_date IS NULL),
        strategy_name TEXT NOT NULL CHECK(strategy_name='golden-guava'),
        job_name TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='sim'),
        identity_json TEXT NOT NULL
    )""",
    """CREATE TABLE strategy_configs (
        config_hash TEXT NOT NULL, strategy_source_digest TEXT NOT NULL,
        config_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, first_observed_at TEXT NOT NULL,
        PRIMARY KEY(config_hash,strategy_source_digest)
    )""",
    """CREATE TABLE run_audits (
        run_id TEXT PRIMARY KEY NOT NULL, started_at TEXT NOT NULL,
        strategy_name TEXT NOT NULL, job_name TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='sim'),
        data_contract TEXT NOT NULL CHECK(data_contract='guava-research-v1'),
        config_hash TEXT NOT NULL,
        strategy_source_digest TEXT NOT NULL, cohort_key TEXT NOT NULL,
        contract_json TEXT NOT NULL, UNIQUE(run_id,cohort_key),
        FOREIGN KEY(config_hash,strategy_source_digest)
            REFERENCES strategy_configs(config_hash,strategy_source_digest)
    )""",
    """CREATE TABLE run_events (
        run_id TEXT NOT NULL REFERENCES run_audits(run_id),
        status TEXT NOT NULL CHECK(status IN ('STARTED','FAILED','SUCCEEDED')),
        occurred_at TEXT NOT NULL, error_type TEXT, phase TEXT,
        PRIMARY KEY(run_id,status)
    )""",
    """CREATE TABLE source_requests (
        run_id TEXT NOT NULL REFERENCES run_audits(run_id), request_id TEXT NOT NULL,
        source TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
        params_json TEXT NOT NULL, started_at TEXT NOT NULL, received_at TEXT,
        status, error_type TEXT, receipt_json TEXT NOT NULL,
        payload_gzip BLOB, payload_sha256 TEXT,
        CHECK((payload_gzip IS NULL) = (payload_sha256 IS NULL)),
        PRIMARY KEY(run_id,request_id)
    )""",
    """CREATE TABLE cycles (
        run_id TEXT PRIMARY KEY NOT NULL, cohort_key TEXT NOT NULL,
        observed_at TEXT NOT NULL, summary_json TEXT NOT NULL,
        UNIQUE(run_id,cohort_key),
        FOREIGN KEY(run_id,cohort_key) REFERENCES run_audits(run_id,cohort_key)
    )""",
    """CREATE TABLE events (
        run_id TEXT NOT NULL, event_id TEXT NOT NULL, cohort_key TEXT NOT NULL,
        sport_family TEXT, league_code TEXT, observed_at TEXT NOT NULL,
        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), exclusion_reason TEXT,
        expected_token_ids_json TEXT NOT NULL, clock_json TEXT NOT NULL,
        event_json TEXT NOT NULL, raw_gzip BLOB NOT NULL, raw_sha256 TEXT NOT NULL,
        PRIMARY KEY(run_id,event_id), UNIQUE(run_id,event_id,cohort_key),
        FOREIGN KEY(run_id,cohort_key) REFERENCES cycles(run_id,cohort_key)
    )""",
    """CREATE TABLE book_attempts (
        run_id TEXT NOT NULL, event_id TEXT NOT NULL, token_id TEXT NOT NULL,
        condition_id TEXT, result_kind TEXT, outcome_side TEXT,
        observed_at TEXT NOT NULL, request_id TEXT, status TEXT NOT NULL,
        book_json TEXT NOT NULL, raw_gzip BLOB, raw_sha256 TEXT,
        fee_evidence_json TEXT NOT NULL, depth_metrics_json TEXT NOT NULL,
        CHECK((raw_gzip IS NULL) = (raw_sha256 IS NULL)),
        PRIMARY KEY(run_id,event_id,token_id),
        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id),
        FOREIGN KEY(run_id,request_id) REFERENCES source_requests(run_id,request_id)
    )""",
    """CREATE TABLE features (
        run_id TEXT NOT NULL, event_id TEXT NOT NULL, hypothesis_id TEXT NOT NULL,
        observed_at TEXT NOT NULL, applicable INTEGER NOT NULL CHECK(applicable IN (0,1)),
        reason TEXT, metrics_json TEXT NOT NULL, feature_json TEXT NOT NULL,
        PRIMARY KEY(run_id,event_id,hypothesis_id),
        FOREIGN KEY(run_id,event_id) REFERENCES events(run_id,event_id)
    )""",
    """CREATE TABLE latest_event_state (
        cohort_key TEXT NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL,
        observed_at TEXT NOT NULL, PRIMARY KEY(cohort_key,event_id),
        FOREIGN KEY(run_id,event_id,cohort_key) REFERENCES events(run_id,event_id,cohort_key)
    )""",
    "CREATE INDEX run_audits_latest ON run_audits(started_at DESC,run_id DESC)",
    "CREATE INDEX run_audits_cohort_latest ON run_audits(cohort_key,started_at DESC,run_id DESC)",
    "CREATE INDEX cycles_cohort_latest ON cycles(cohort_key,observed_at DESC)",
    "CREATE INDEX cycles_latest ON cycles(observed_at DESC)",
    "CREATE UNIQUE INDEX run_events_terminal ON run_events(run_id) WHERE status!='STARTED'",
    """CREATE TRIGGER run_events_lifecycle BEFORE INSERT ON run_events BEGIN
        SELECT CASE WHEN NEW.status='STARTED' AND EXISTS(
            SELECT 1 FROM run_events WHERE run_id=NEW.run_id
        ) THEN RAISE(ABORT,'run already started') END;
        SELECT CASE WHEN NEW.status!='STARTED' AND (
            NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status='STARTED')
            OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status!='STARTED')
        ) THEN RAISE(ABORT,'run is not open') END;
        SELECT CASE WHEN NEW.status='SUCCEEDED' AND NOT EXISTS(
            SELECT 1 FROM cycles WHERE run_id=NEW.run_id
        ) THEN RAISE(ABORT,'success requires a published cycle') END;
        SELECT CASE WHEN NEW.status='FAILED' AND EXISTS(
            SELECT 1 FROM cycles WHERE run_id=NEW.run_id
        ) THEN RAISE(ABORT,'published cycle cannot fail') END;
    END""",
)
_IMMUTABLE_KEYS = {
    "collection_contracts": ("singleton",),
    "strategy_configs": ("config_hash", "strategy_source_digest"),
    "run_audits": ("run_id",), "run_events": ("run_id", "status"),
    "source_requests": ("run_id", "request_id"), "cycles": ("run_id",),
    "events": ("run_id", "event_id"), "book_attempts": ("run_id", "event_id", "token_id"),
    "features": ("run_id", "event_id", "hypothesis_id"),
}


def _triggers():
    statements = []
    for table, keys in _IMMUTABLE_KEYS.items():
        for action in ("UPDATE", "DELETE"):
            statements.append(f"""CREATE TRIGGER {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN
                SELECT RAISE(ABORT,'append-only evidence'); END""")
        # REPLACE otherwise bypasses delete triggers on connections which have
        # recursive_triggers disabled (SQLite's default).
        match = " AND ".join(f"{key}=NEW.{key}" for key in keys)
        statements.append(f"""CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM {table} WHERE {match}) BEGIN
            SELECT RAISE(ABORT,'duplicate append-only evidence'); END""")
    for table in ("source_requests", "cycles", "events", "book_attempts", "features"):
        statements.append(f"""CREATE TRIGGER {table}_open_run BEFORE INSERT ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status='STARTED')
                OR EXISTS(SELECT 1 FROM run_events WHERE run_id=NEW.run_id AND status!='STARTED')
            BEGIN SELECT RAISE(ABORT,'run is not open'); END""")
    return tuple(statements)


class Repository:
    """A fixed strategy/job database with separate config/source cohorts.

    ``cohort_key`` on caller event dictionaries is preserved as source metadata;
    cache isolation always uses our hash of the six explicit contract fields.
    Config/source changes are allowed, but reusing a config/source pair with a
    different snapshot and opening a different strategy/job/mode/data contract
    are not. Snapshots may include their own source provenance.
    """

    def __init__(self, path, contract: dict, *, read_only=False, budget=None):
        contract = _freeze(contract)
        _mapping(contract, _COHORT_KEYS)
        for key in _COHORT_KEYS:
            _text(contract[key])
        if (contract["strategy_name"] != "golden-guava" or contract["mode"] != "sim"
                or contract["data_contract"] != DATA_CONTRACT
                or contract.get("schema_profile", DATA_CONTRACT) != DATA_CONTRACT):
            raise ValueError("incompatible accountless Guava contract")
        if budget is not None and not callable(getattr(budget, "require_commit", None)):
            raise ValueError("publication budget must implement require_commit")
        self._budget = budget
        self.path = Path(path).resolve()
        self._contract_json = _json(contract)
        self._identity = {key: contract[key] for key in _IDENTITY_KEYS}
        self._config_hash = contract["config_hash"]
        self._source_digest = contract["strategy_source_digest"]
        self._cohort_key = hashlib.sha256(
            _json({key: contract[key] for key in _COHORT_KEYS}).encode("utf-8")
        ).hexdigest()
        self._read_only = bool(read_only)
        self.connection = None
        connection = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rwc"),
                                     uri=True, timeout=2, isolation_level=None)
        self.connection = connection
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA recursive_triggers=ON")
            objects = connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if objects:
                self._validate_schema(objects)
            elif read_only:
                raise ValueError("missing Guava evidence schema")
            if read_only:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                if not objects:
                    with self._transaction():
                        for statement in (*_SCHEMA, *_triggers()):
                            connection.execute(statement)
                        self._insert("collection_contracts", {
                            "singleton": 1, "schema_version": SCHEMA_VERSION,
                            "contract_name": DATA_CONTRACT, "data_contract": DATA_CONTRACT,
                            "database_utc_date": None,
                            "strategy_name": self._identity["strategy_name"],
                            "job_name": self._identity["job_name"], "mode": "sim",
                            "identity_json": _json(self._identity),
                        })
        except BaseException:
            self.close()
            raise

    @property
    def cohort_key(self):
        return self._cohort_key

    @property
    def contract(self):
        return json.loads(self._contract_json)

    def _validate_schema(self, objects):
        actual = {(row["type"], row["name"]) for row in objects}
        expected = {("table", name) for name in (*_IMMUTABLE_KEYS, "latest_event_state")}
        for statement in (*_SCHEMA, *_triggers()):
            words = statement.split()
            if words[1] == "UNIQUE":
                expected.add(("index", words[3]))
            elif words[1] in ("INDEX", "TRIGGER"):
                expected.add((words[1].lower(), words[2]))
        if not expected.issubset(actual):
            raise ValueError("missing or incompatible Guava evidence schema")
        row = self.connection.execute("SELECT * FROM collection_contracts WHERE singleton=1").fetchone()
        if (row is None or row["schema_version"] != SCHEMA_VERSION
                or row["contract_name"] != DATA_CONTRACT or row["database_utc_date"] is not None
                or "data_contract" not in row.keys() or row["data_contract"] != DATA_CONTRACT
                or row["identity_json"] != _json(self._identity)
                or any(row[key] != self._identity[key] for key in ("strategy_name", "job_name", "mode"))):
            raise ValueError("database identity does not match Guava contract")

    @contextmanager
    def _transaction(self, *, budget=None):
        if self._read_only:
            raise ValueError("read-only evidence repository")
        if self.connection is None:
            raise ValueError("evidence repository is closed")
        progress_error = None

        def check_progress():
            nonlocal progress_error
            try:
                budget.require_commit()
            except BaseException as error:
                # sqlite3 otherwise suppresses Python callback exceptions and
                # exposes only OperationalError('interrupted').
                progress_error = error
                return 1
            return 0

        if budget is not None:
            budget.require_commit()
            self.connection.set_progress_handler(check_progress, 1000)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            if budget is not None:
                budget.require_commit()
                # Do not interrupt an already-started COMMIT/fsync and risk
                # reporting FAILED after publication has become durable.
                self.connection.set_progress_handler(None, 0)
            self.connection.execute("COMMIT")
        except BaseException as error:
            if budget is not None:
                self.connection.set_progress_handler(None, 0)
            # Interrupted DML can make SQLite roll back on its own. Interrupted
            # SELECT leaves our earlier writes pending and needs this rollback.
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            if progress_error is not None:
                raise progress_error from error
            raise
        finally:
            if budget is not None:
                self.connection.set_progress_handler(None, 0)

    def _insert(self, table, values):
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        self.connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                                tuple(values.values()))

    def _run_state(self, run_id):
        _text(run_id)
        row = self.connection.execute("SELECT cohort_key FROM run_audits WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["cohort_key"] != self._cohort_key:
            raise ValueError("unknown run or incompatible cohort")
        row = self.connection.execute(
            "SELECT status FROM run_events WHERE run_id=? AND status!='STARTED'", (run_id,)
        ).fetchone()
        if row:
            return row["status"]
        if self.connection.execute(
            "SELECT 1 FROM run_events WHERE run_id=? AND status='STARTED'", (run_id,)
        ).fetchone() is None:
            raise ValueError("run has no durable STARTED evidence")
        return "STARTED"

    def _require_open(self, run_id):
        if self._run_state(run_id) != "STARTED":
            raise ValueError("run is already terminal")

    def start_run(self, run_id, started_at, config_snapshot):
        run_id, started_at = _text(run_id), _time(started_at)
        _mapping(config_snapshot)
        snapshot_json = _json(config_snapshot)
        contract = self.contract
        if any(key in config_snapshot and config_snapshot[key] != contract[key] for key in _COHORT_KEYS):
            raise ValueError("config snapshot identity does not match the run contract")
        if "simulation_mode" in config_snapshot and config_snapshot["simulation_mode"] is not True:
            raise ValueError("research snapshot must be simulation-only")
        with self._transaction():
            existing = self.connection.execute(
                "SELECT config_json FROM strategy_configs WHERE config_hash=? AND strategy_source_digest=?",
                (self._config_hash, self._source_digest),
            ).fetchone()
            if existing and existing["config_json"] != snapshot_json:
                raise ValueError("config/source pair was reused with a different snapshot")
            if not existing:
                self._insert("strategy_configs", {
                    "config_hash": self._config_hash, "strategy_source_digest": self._source_digest,
                    "config_json": snapshot_json,
                    "snapshot_sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                    "first_observed_at": started_at,
                })
            self._insert("run_audits", {
                "run_id": run_id, "started_at": started_at,
                **self._identity,
                "config_hash": self._config_hash, "strategy_source_digest": self._source_digest,
                "cohort_key": self._cohort_key, "contract_json": self._contract_json,
            })
            self._insert("run_events", {"run_id": run_id, "status": "STARTED", "occurred_at": started_at})

    def record_request(self, run_id, receipt: dict, payload):
        receipt, payload = _freeze(receipt), _freeze(payload)
        _mapping(receipt, ("request_id", "source", "method", "path", "params", "started_at",
                           "received_at", "status", "error_type"))
        if payload is not None and not isinstance(payload, (dict, list)):
            raise ValueError("source payload must be an object, list or absent")
        for key in ("request_id", "source", "method", "path"):
            _text(receipt[key])
        _mapping(receipt["params"])
        if receipt["status"] is not None and type(receipt["status"]) not in (str, int):
            raise ValueError("invalid request status")
        if receipt["error_type"] is not None:
            _text(receipt["error_type"])
        payload_gzip, payload_sha256 = _raw(payload)
        with self._transaction():
            self._require_open(run_id)
            self._insert("source_requests", {
                "run_id": run_id,
                **{key: receipt[key] for key in ("request_id", "source", "method", "path", "status", "error_type")},
                "params_json": _json(receipt["params"]), "started_at": _time(receipt["started_at"]),
                "received_at": _time(receipt["received_at"]) if receipt["received_at"] is not None else None,
                "receipt_json": _json(receipt), "payload_gzip": payload_gzip, "payload_sha256": payload_sha256,
            })

    def previous_events(self, event_ids=None):
        """Read only requested current-cohort IDs; None is the full diagnostic scan.

        Empty input performs no SQL. Bounded IN queries use the cache's composite
        primary key, so a new cycle need not reload months of unrelated events.
        """
        query = """SELECT e.event_id,e.event_json FROM latest_event_state AS c
            JOIN events AS e ON e.run_id=c.run_id AND e.event_id=c.event_id AND e.cohort_key=c.cohort_key
            WHERE c.cohort_key=?"""
        if event_ids is None:
            rows = self.connection.execute(query + " ORDER BY c.event_id", (self._cohort_key,))
            return {row["event_id"]: json.loads(row["event_json"]) for row in rows}
        if isinstance(event_ids, (str, bytes)):
            raise ValueError("event IDs must be an iterable of nonempty strings")
        try:
            event_ids = sorted({_text(event_id) for event_id in event_ids})
        except TypeError:
            raise ValueError("event IDs must be an iterable of nonempty strings") from None
        if not event_ids:
            return {}
        batch_size = min(400, self.connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) - 1)
        if batch_size < 1:
            raise ValueError("SQLite variable limit is too small for a scoped event lookup")
        result = {}
        for offset in range(0, len(event_ids), batch_size):
            batch = event_ids[offset:offset + batch_size]
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                query + f" AND c.event_id IN ({placeholders}) ORDER BY c.event_id",
                (self._cohort_key, *batch),
            )
            result.update((row["event_id"], json.loads(row["event_json"])) for row in rows)
        return result

    def publish_cycle(self, run_id, observed_at, events: list, books: list, features: list, summary: dict):
        if self._budget is not None:
            self._budget.require_commit()
        # Freeze/check the *entire* batch before any write: secrets may occur in
        # an unused extra field, the last feature, or the summary.
        batch = _freeze({"events": events, "books": books, "features": features, "summary": summary})
        events, books, features, summary = (batch[key] for key in ("events", "books", "features", "summary"))
        if any(not isinstance(rows, list) for rows in (events, books, features)):
            raise ValueError("cycle evidence must use lists")
        _mapping(summary)
        observed_at = _time(observed_at)
        by_event = {}
        for event in events:
            _mapping(event, ("event_id", "sport_family", "league_code", "observed_at", "raw", "clock",
                             "eligible", "exclusion_reason", "expected_token_ids", "cohort_key"))
            for key in ("event_id", "cohort_key"):
                _text(event[key])
            _time(event["observed_at"])
            _mapping(event["raw"])
            _mapping(event["clock"])
            if type(event["eligible"]) is not bool or not isinstance(event["expected_token_ids"], list):
                raise ValueError("invalid event eligibility or expected token list")
            for key in ("sport_family", "league_code"):
                if event["eligible"] or event[key] is not None:
                    _text(event[key])
            tokens = event["expected_token_ids"]
            for token in tokens:
                _text(token)
            if len(tokens) != len(set(tokens)) or (event["eligible"] and not tokens):
                raise ValueError("eligible events require unique nonempty expected tokens")
            if event["exclusion_reason"] is not None:
                _text(event["exclusion_reason"])
            if not event["eligible"] and not event["exclusion_reason"]:
                raise ValueError("excluded events require an explicit reason")
            if event["event_id"] in by_event:
                raise ValueError("duplicate event in cycle")
            by_event[event["event_id"]] = event
        attempts = set()
        for book in books:
            _mapping(book, ("event_id", "token_id", "condition_id", "result_kind", "outcome_side",
                            "observed_at", "request_id", "status", "raw", "fee_evidence", "depth_metrics"))
            for key in ("event_id", "token_id", "status"):
                _text(book[key])
            _time(book["observed_at"])
            pair = (book["event_id"], book["token_id"])
            if (book["event_id"] not in by_event
                    or book["token_id"] not in by_event[book["event_id"]]["expected_token_ids"]
                    or pair in attempts):
                raise ValueError("book is duplicate or outside the event's expected tokens")
            attempts.add(pair)
            for key in ("condition_id", "result_kind", "outcome_side", "request_id"):
                if book[key] is not None:
                    _text(book[key])
            if book["raw"] is not None:
                _mapping(book["raw"])
                if not book["request_id"]:
                    raise ValueError("raw book requires a source request receipt")
            elif book["status"].upper() in ("OK", "SUCCESS", "SUCCEEDED", "200"):
                raise ValueError("successful book cannot have absent raw evidence")
            _mapping(book["fee_evidence"])
            if not isinstance(book["depth_metrics"], list):
                raise ValueError("book depth metrics must use a list")
        for event in events:
            for token in event["expected_token_ids"]:
                if (event["event_id"], token) not in attempts:
                    books.append({
                        "event_id": event["event_id"], "token_id": token, "condition_id": None,
                        "result_kind": None, "outcome_side": None, "observed_at": event["observed_at"],
                        "request_id": None, "status": "NOT_ATTEMPTED", "reason": "missing_book_attempt",
                        "raw": None, "fee_evidence": {}, "depth_metrics": [],
                    })
        for feature in features:
            _mapping(feature, ("event_id", "hypothesis_id", "observed_at", "applicable", "reason", "metrics"))
            _text(feature["event_id"])
            _text(feature["hypothesis_id"])
            _time(feature["observed_at"])
            if feature["event_id"] not in by_event or type(feature["applicable"]) is not bool:
                raise ValueError("feature requires a cycle event and explicit applicability")
            if feature["reason"] is not None:
                _text(feature["reason"])
            if not feature["applicable"] and not feature["reason"]:
                raise ValueError("inapplicable feature requires a reason")
            _mapping(feature["metrics"])
        with self._transaction(budget=self._budget):
            self._require_open(run_id)
            self._insert("cycles", {"run_id": run_id, "cohort_key": self._cohort_key,
                                    "observed_at": observed_at, "summary_json": _json(summary)})
            for event in events:
                raw_gzip, raw_sha256 = _raw(event["raw"])
                self._insert("events", {
                    "run_id": run_id, "cohort_key": self._cohort_key,
                    **{key: event[key] for key in ("event_id", "sport_family", "league_code", "exclusion_reason")},
                    "observed_at": _time(event["observed_at"]), "eligible": int(event["eligible"]),
                    "expected_token_ids_json": _json(event["expected_token_ids"]),
                    "clock_json": _json(event["clock"]), "event_json": _json(event),
                    "raw_gzip": raw_gzip, "raw_sha256": raw_sha256,
                })
            for book in books:
                raw_gzip, raw_sha256 = _raw(book["raw"])
                self._insert("book_attempts", {
                    "run_id": run_id,
                    **{key: book[key] for key in ("event_id", "token_id", "condition_id", "result_kind",
                                                 "outcome_side", "request_id", "status")},
                    "observed_at": _time(book["observed_at"]), "book_json": _json(book),
                    "raw_gzip": raw_gzip, "raw_sha256": raw_sha256,
                    "fee_evidence_json": _json(book["fee_evidence"]),
                    "depth_metrics_json": _json(book["depth_metrics"]),
                })
            for feature in features:
                self._insert("features", {
                    "run_id": run_id,
                    **{key: feature[key] for key in ("event_id", "hypothesis_id", "reason")},
                    "observed_at": _time(feature["observed_at"]), "applicable": int(feature["applicable"]),
                    "metrics_json": _json(feature["metrics"]), "feature_json": _json(feature),
                })
            for event in events:
                self.connection.execute("""INSERT INTO latest_event_state(cohort_key,event_id,run_id,observed_at)
                    VALUES(?,?,?,?) ON CONFLICT(cohort_key,event_id) DO UPDATE
                    SET run_id=excluded.run_id,observed_at=excluded.observed_at
                    WHERE excluded.observed_at >= latest_event_state.observed_at""",
                    (self._cohort_key, event["event_id"], run_id, _time(event["observed_at"])))
            self._insert("run_events", {"run_id": run_id, "status": "SUCCEEDED", "occurred_at": observed_at})

    def fail_run(self, run_id, ended_at, error_type, phase):
        ended_at, error_type, phase = _time(ended_at), _text(error_type), _text(phase)
        with self._transaction():
            if self._run_state(run_id) != "STARTED":
                return  # Repeated cleanup cannot rewrite either terminal outcome.
            self._insert("run_events", {"run_id": run_id, "status": "FAILED", "occurred_at": ended_at,
                                        "error_type": error_type, "phase": phase})

    def status(self):
        """Bounded metadata and indexed latest start/terminal lookup; no integrity scan."""
        metadata = dict(self.connection.execute(
            "SELECT schema_version,contract_name,data_contract,database_utc_date,strategy_name,job_name,mode "
            "FROM collection_contracts WHERE singleton=1"
        ).fetchone())
        row = self.connection.execute("""SELECT run_id,started_at,strategy_name,job_name,mode,data_contract,
            config_hash,strategy_source_digest,cohort_key FROM run_audits
            ORDER BY started_at DESC,run_id DESC LIMIT 1""").fetchone()
        latest = dict(row) if row else None
        if latest is not None:
            terminal = self.connection.execute("""SELECT status,occurred_at,error_type,phase
                FROM run_events WHERE run_id=? AND status!='STARTED'""", (latest["run_id"],)).fetchone()
            latest.update(status=terminal["status"] if terminal else "STARTED",
                          ended_at=terminal["occurred_at"] if terminal else None,
                          error_type=terminal["error_type"] if terminal else None,
                          phase=terminal["phase"] if terminal else None)
        sizes = {}
        for key, suffix in (("database_bytes", ""), ("wal_bytes", "-wal"), ("shm_bytes", "-shm")):
            try:
                sizes[key] = Path(str(self.path) + suffix).stat().st_size
            except FileNotFoundError:
                sizes[key] = 0
        return {"metadata": metadata, "latest_run": latest, "cohort_key": self._cohort_key,
                "file_sizes": sizes}

    def latest_summary(self):
        """Small current-cohort context, never a full raw-history scan."""
        row = self.connection.execute(
            "SELECT summary_json FROM cycles WHERE cohort_key=? ORDER BY observed_at DESC LIMIT 1",
            (self._cohort_key,),
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def tracking_obligations(self):
        """Carry existing identities through instrumentation changes, not price features."""
        row = self.connection.execute(
            "SELECT summary_json FROM cycles ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        return json.loads(row[0]).get('tracking', {}) if row else {}

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None


SQLiteRepository = Repository

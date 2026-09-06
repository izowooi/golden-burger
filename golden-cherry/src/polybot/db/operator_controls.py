"""Explicit operator ownership of historical unknown BUYs, not venue proof."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3

TABLE = "cherry_operator_handled_intents"
DDL = (
    f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        submission_id TEXT PRIMARY KEY,
        token_id TEXT NOT NULL,
        original_submission_sha256 TEXT NOT NULL,
        evidence_kind TEXT NOT NULL CHECK(evidence_kind='OPERATOR_HANDLED_ASSUMPTION'),
        approval_id TEXT NOT NULL CHECK(length(trim(approval_id))>0),
        reason TEXT NOT NULL CHECK(length(trim(reason))>0),
        acknowledged_at TEXT NOT NULL
    )""",
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
    "BEGIN SELECT RAISE(ABORT, 'immutable operator acknowledgement'); END",
    f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
    "BEGIN SELECT RAISE(ABORT, 'immutable operator acknowledgement'); END",
)
IDENTITY_KEYS = (
    "submission_id", "strategy_name", "token_id", "side", "requested_price",
    "requested_size", "submitted_at", "simulation", "order_id", "response_status",
)


def submission_fingerprint(row) -> str:
    return hashlib.sha256(json.dumps({key:row[key] for key in IDENTITY_KEYS},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_unknown(row) -> None:
    if (row["strategy_name"] != "golden-cherry" or row["simulation"] != 0
        or row["side"] != "BUY" or row["order_id"] is not None
        or row["response_status"] != "SUBMIT_OUTCOME_UNKNOWN"
        or row["outcome_resolution"] is not None or row["needs_reconciliation"] != 0
        or not row["token_id"]):
        raise ValueError("operator acknowledgement requires an exact historical unknown BUY")
    price, size = float(row["requested_price"]), float(row["requested_size"])
    if not math.isfinite(price) or not 0 < price < 1 or not math.isfinite(size) or size <= 0:
        raise ValueError("invalid acknowledged submission amount")


def register_operator_handled(connection: sqlite3.Connection, submission_ids: list[str],
                              *, reason: str, approval_id: str) -> dict:
    """Caller owns the backup, single-writer lock and surrounding transaction."""
    if not reason.strip() or not approval_id.strip() or not submission_ids:
        raise ValueError("explicit approval, reason and exact submission IDs are required")
    if len(set(submission_ids)) != len(submission_ids):
        raise ValueError("duplicate submission IDs")
    connection.row_factory = sqlite3.Row
    prepared = []
    for key in submission_ids:
        row = connection.execute("SELECT * FROM order_submissions WHERE submission_id=?", (key,)).fetchone()
        if row is None:
            raise ValueError("unknown submission ID")
        validate_unknown(row)
        if connection.execute("SELECT 1 FROM trades WHERE token_id=? LIMIT 1", (row["token_id"],)).fetchone():
            raise ValueError("managed trade exists for operator-owned token; separate review required")
        if connection.execute("SELECT 1 FROM order_fills WHERE submission_id=? LIMIT 1", (key,)).fetchone():
            raise ValueError("venue fill exists; use exact reconciliation, not operator assumption")
        prepared.append(row)
    for statement in DDL:
        connection.execute(statement)
    created = 0
    for row in prepared:
        old = connection.execute(f"SELECT * FROM {TABLE} WHERE submission_id=?", (row["submission_id"],)).fetchone()
        fingerprint = submission_fingerprint(row)
        if old:
            if (old["original_submission_sha256"] != fingerprint
                or old["token_id"] != row["token_id"] or old["approval_id"] != approval_id
                or old["reason"] != reason):
                raise ValueError("operator acknowledgement conflicts with existing record")
            continue
        connection.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?)", (
            row["submission_id"], row["token_id"], fingerprint,
            "OPERATOR_HANDLED_ASSUMPTION", approval_id, reason,
            datetime.now(timezone.utc).isoformat(),
        ))
        created += 1
    return {"acknowledged":len(prepared), "created":created,
            "venue_fill_or_zero_fill_proven":False, "tokens_protected_from_bot":True}

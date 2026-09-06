"""Operator-only, backed-up historical intent acknowledgement; no venue calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from polybot.db.operator_controls import register_operator_handled, submission_fingerprint, validate_unknown
from polybot.utils.process_lock import DatabaseRunLock


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--submission-id", action="append", required=True)
    p.add_argument("--approval-id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--expected-set-sha256")
    p.add_argument("--backup-dir", type=Path)
    p.add_argument("--apply", action="store_true")
    a=p.parse_args()
    db=a.db.resolve(strict=True)
    if not db.is_file() or a.db.is_symlink():
        raise ValueError("existing non-symlink database required")
    ids=sorted(a.submission_id)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate submission IDs")
    with DatabaseRunLock(db) as lock:
        if not lock.acquired:
            raise RuntimeError("bot writer is active; pause Jenkins and wait")
        with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as source:
            source.row_factory=sqlite3.Row
            rows=[]
            for key in ids:
                row=source.execute("SELECT * FROM order_submissions WHERE submission_id=?",(key,)).fetchone()
                if row is None:raise ValueError("submission not found")
                validate_unknown(row);rows.append(row)
            original=[submission_fingerprint(row) for row in rows]
            set_sha=hashlib.sha256(json.dumps(original,separators=(",",":")).encode()).hexdigest()
            result={"count":len(ids),"submission_set_sha256":set_sha,"applied":False,
                    "resolution_basis":"OPERATOR_HANDLED_ASSUMPTION","venue_outcome_proven":False}
            if not a.apply:
                print(json.dumps(result));return
            if a.expected_set_sha256 != set_sha or a.backup_dir is None:
                raise ValueError("apply requires exact dry-run set hash and backup directory")
            backup_dir=a.backup_dir.resolve()
            if backup_dir.is_relative_to(db.parent):
                raise ValueError("backup must be outside the bot data directory")
            backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
            backup=backup_dir/f"cherry-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.db"
            with sqlite3.connect(backup) as target:source.backup(target)
            backup.chmod(0o600)
        with sqlite3.connect(db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current=[]
            connection.row_factory=sqlite3.Row
            for key in ids:
                current.append(submission_fingerprint(connection.execute(
                    "SELECT * FROM order_submissions WHERE submission_id=?",(key,)).fetchone()))
            if current != original:raise RuntimeError("submission changed since approval check")
            result.update(register_operator_handled(connection,ids,reason=a.reason,approval_id=a.approval_id))
            assert [submission_fingerprint(connection.execute(
                "SELECT * FROM order_submissions WHERE submission_id=?",(key,)).fetchone()) for key in ids] == original
        result.update(applied=True,backup=str(backup),backup_sha256=sha256(backup))
        manifest=backup.with_suffix(".manifest.json")
        manifest.write_text(json.dumps(result,indent=2)+"\n");manifest.chmod(0o600)
        print(json.dumps(result))


if __name__ == "__main__":main()

"""Independent White raw sidecar; never migrates or writes the parent schema."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

RAW_SIDECAR_FILENAME = "shadow.db"
RAW_CONTRACT = "watermelon-independent-raw-lifecycle-v1"
RAW_APPLICATION_ID = 0x57525231
SCHEMA = """
CREATE TABLE raw_metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL,schema_sha256 TEXT NOT NULL,parent_filename TEXT NOT NULL);
CREATE TABLE raw_tracked_events(event_id TEXT PRIMARY KEY,family TEXT NOT NULL,state TEXT NOT NULL,slots_json TEXT NOT NULL,first_run_id TEXT NOT NULL,last_run_id TEXT NOT NULL,next_attempt_at TEXT NOT NULL,missing_count INTEGER NOT NULL,terminal_reason TEXT);
CREATE INDEX raw_pending_idx ON raw_tracked_events(state,next_attempt_at,event_id);
CREATE TABLE raw_cycles(run_id TEXT PRIMARY KEY,component_run_id TEXT NOT NULL UNIQUE,contract TEXT NOT NULL,parent_filename TEXT NOT NULL,config_hash TEXT NOT NULL,source_digest TEXT NOT NULL,job_name TEXT NOT NULL,reference_at TEXT NOT NULL,published_at TEXT NOT NULL,status TEXT NOT NULL,expected_events INTEGER NOT NULL,expected_tokens INTEGER NOT NULL,stats_json TEXT NOT NULL);
CREATE TABLE raw_events(run_id TEXT NOT NULL,event_id TEXT NOT NULL,family TEXT NOT NULL,metadata_status TEXT NOT NULL,metadata_received_at TEXT,metadata_request_id TEXT,source_kind TEXT NOT NULL,source_ref_json TEXT NOT NULL,event_json TEXT,slots_json TEXT NOT NULL,identity_valid INTEGER NOT NULL,lifecycle_state TEXT NOT NULL,terminal_json TEXT,missing_count INTEGER NOT NULL,PRIMARY KEY(run_id,event_id));
CREATE TABLE raw_books(run_id TEXT NOT NULL,event_id TEXT NOT NULL,slot TEXT NOT NULL,condition_id TEXT,token_id TEXT,outcome TEXT,status TEXT NOT NULL,request_id TEXT,requested_at TEXT,received_at TEXT,book_sha256 TEXT,source_kind TEXT NOT NULL,source_ref_json TEXT NOT NULL,point_in_time_identity_valid INTEGER NOT NULL,error_type TEXT,PRIMARY KEY(run_id,event_id,slot));
CREATE INDEX raw_books_token_time_idx ON raw_books(token_id,received_at);
CREATE TABLE raw_payloads(payload_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,request_id TEXT,received_at TEXT,sha256 TEXT NOT NULL,payload_gzip BLOB NOT NULL);
"""
PKS = {"raw_metadata": ("singleton",), "raw_cycles": ("run_id",),
       "raw_events": ("run_id", "event_id"), "raw_books": ("run_id", "event_id", "slot"),
       "raw_payloads": ("payload_id",)}


def schema_sql():
    sql = SCHEMA
    for table, keys in PKS.items():
        for operation in ("UPDATE", "DELETE"):
            sql += f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END;\n"
        where = " AND ".join(f"{key}=NEW.{key}" for key in keys)
        if table == "raw_cycles":
            where = f"({where}) OR component_run_id=NEW.component_run_id"
        sql += f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {where}) BEGIN SELECT RAISE(ABORT,'append-only duplicate'); END;\n"
    return sql


def fingerprint(c):
    rows = c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps([tuple(r) for r in rows], separators=(",", ":")).encode()).hexdigest()


class RawRepository:
    def __init__(self, parent_path: Path, *, busy_timeout_ms=5000):
        self.path = parent_path.with_name(RAW_SIDECAR_FILENAME)
        self.parent_filename = parent_path.name
        if self.path == parent_path or self.path.is_symlink():
            raise RuntimeError("raw sidecar path is unsafe")
        memory = sqlite3.connect(":memory:")
        try:
            memory.executescript(schema_sql())
            expected = fingerprint(memory)
        finally:
            memory.close()
        new = not self.path.exists()
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create refuses races with any other shadow.db producer.
            self.path.open("xb").close()
        elif not self.path.is_file():
            raise RuntimeError("raw sidecar must be a regular file")
        self.connection = sqlite3.connect(self.path, timeout=busy_timeout_ms / 1000)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self.connection.execute("PRAGMA synchronous=FULL")
        try:
            if new:
                self.connection.executescript(schema_sql())
                self.connection.execute(f"PRAGMA application_id={RAW_APPLICATION_ID}")
                self.connection.execute("PRAGMA user_version=1")
                self.connection.execute("INSERT INTO raw_metadata VALUES(1,?,?,?)", (RAW_CONTRACT, expected, self.parent_filename))
                self.connection.commit()
            row = self.connection.execute("SELECT * FROM raw_metadata").fetchall()
            if (self.connection.execute("PRAGMA application_id").fetchone()[0] != RAW_APPLICATION_ID
                    or self.connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or len(row) != 1 or tuple(row[0]) != (1, RAW_CONTRACT, expected, self.parent_filename)
                    or fingerprint(self.connection) != expected):
                raise RuntimeError("raw sidecar schema/parent/contract mismatch")
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def published(self, run_id):
        row = self.connection.execute("SELECT stats_json FROM raw_cycles WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def pending(self, reference_at):
        # Indexed mutable working set, never a historical observation/episode scan.
        return [dict(r) for r in self.connection.execute(
            "SELECT * FROM raw_tracked_events INDEXED BY raw_pending_idx WHERE state IN ('TRACKING','WAIT_RESOLUTION') AND next_attempt_at<=? ORDER BY next_attempt_at,event_id", (reference_at,))]

    @staticmethod
    def insert(c, table, row):
        keys = list(row)
        c.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(row[k] for k in keys))

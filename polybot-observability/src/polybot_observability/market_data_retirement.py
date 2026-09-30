"""Immutable catalog schema shared by retirement writers and pin readers."""
import hashlib
import json

from .market_data_sql_schema import canonical_sql_key


RETIREMENT_TABLE = "source_retirement_validations"
RETIREMENT_CONTRACT = "strawberry-v1-local-retirement-validation-v1"
RETIREMENT_COLUMNS = (
    "source_key", "generation_sha256", "anchor_sha256", "pin_path", "record_sha256", "record_json",
)
RETIREMENT_LOGICAL_ANCHOR_FIELDS = (
    "source_schema_version", "source_data_contract", "source_job_name", "source_entry_start",
    "source_entry_end", "source_followup_end", "source_sweep_id", "source_cycle_number",
    "source_sweep_completed_at", "source_successful_at", "source_config_hash", "source_strategy_digest",
    "source_counts_json", "episode_seed_sha256", "condition_seed_sha256", "threshold_seed_sha256",
    "executable_episode_count", "condition_count", "terminal_condition_count", "threshold_event_count",
)
RETIREMENT_TABLE_SQL = """CREATE TABLE source_retirement_validations (
    source_key TEXT NOT NULL,
    generation_sha256 TEXT NOT NULL,
    anchor_sha256 TEXT NOT NULL,
    pin_path TEXT NOT NULL,
    record_sha256 TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    PRIMARY KEY(source_key,generation_sha256,anchor_sha256,pin_path)
)"""
RETIREMENT_GUARD_SQL = {
    "source_retirement_validations_no_update": """CREATE TRIGGER source_retirement_validations_no_update
BEFORE UPDATE ON source_retirement_validations BEGIN
SELECT RAISE(ABORT,'immutable source retirement validation'); END""",
    "source_retirement_validations_no_delete": """CREATE TRIGGER source_retirement_validations_no_delete
BEFORE DELETE ON source_retirement_validations BEGIN
SELECT RAISE(ABORT,'immutable source retirement validation'); END""",
    "source_retirement_validations_no_replace": """CREATE TRIGGER source_retirement_validations_no_replace
BEFORE INSERT ON source_retirement_validations WHEN EXISTS(
SELECT 1 FROM source_retirement_validations WHERE
(source_key=NEW.source_key AND generation_sha256=NEW.generation_sha256
AND anchor_sha256=NEW.anchor_sha256 AND pin_path=NEW.pin_path)
OR record_sha256=NEW.record_sha256) BEGIN
SELECT RAISE(ABORT,'immutable source retirement validation'); END""",
}


def canonical_retirement_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def retirement_record_digest(value):
    return hashlib.sha256(canonical_retirement_json({
        key: item for key, item in value.items() if key != "record_sha256"
    })).hexdigest()


def validate_retirement_schema(connection):
    expected = {RETIREMENT_TABLE: RETIREMENT_TABLE_SQL, **RETIREMENT_GUARD_SQL}
    actual = {row[0]: row[1] for row in connection.execute(
        "SELECT name,sql FROM main.sqlite_master WHERE tbl_name=? AND sql IS NOT NULL",
        (RETIREMENT_TABLE,),
    )}
    if (set(actual) != set(expected)
            or any(canonical_sql_key(actual[name]) != canonical_sql_key(sql)
                   for name, sql in expected.items())):
        raise RuntimeError("retirement catalog schema or immutable guards differ")

"""Cherry's immutable experiment evidence survives the reviewed RAW transition."""
import ast
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot_observability.market_data_bundle import reference_closure, verify_closure
from polybot_observability.market_data_levels import (
    BINDING_TABLE, insert_shared_levels, iter_level_logical_rows,
)
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_projection_closure import verify_projection_closure
from polybot_observability.market_data_raw_links import (
    initialize_raw_links, insert_raw_rows, iter_raw_logical_rows,
    logical_raw_schema_rows, raw_layout_metadata, verify_raw_dependencies,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import raw_profile
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreError


STRATEGY = "golden-cherry"
PROFILE_ID = "cherry-shadow-resolution-v2"
NAMESPACE = scalar_namespace("fixture", "polybot-cherry-shadow", STRATEGY, PROFILE_ID)
STAMP = "2026-09-10T12:00:00Z"
PREREGISTRATION_SHA = "72d87684fa9ec7145b64fb8614afee60c9a7391a518bd0a867df7d19c9f95ee7"
RAW_TABLES = (
    "shadow_market_observations", "shadow_book_snapshots",
    "shadow_resolution_observations", "shadow_episodes",
    "shadow_cell_decisions", "shadow_path_observations",
)
PUBLIC_COLUMNS = {
    "shadow_market_observations": {
        "event_id", "event_slug", "event_title", "category", "condition_id", "market_id",
        "market_slug", "question", "end_date", "game_start_time", "sports_market_type",
        "liquidity", "volume_total", "active", "closed", "accepting_orders", "enable_order_book",
        "primary_outcome_label", "primary_token_id", "primary_gamma_probability",
    },
    "shadow_book_snapshots": {
        "token_id", "source_timestamp", "market_hash", "best_bid", "best_ask",
        "bid_level_count", "ask_level_count",
    },
    "shadow_resolution_observations": {"condition_id", "token_id", "winner_index", "token_payout"},
    "shadow_episodes": {
        "event_id", "condition_id", "question", "category", "token_id", "outcome_label",
        "end_date", "game_start_time", "liquidity", "volume_total", "entry_best_ask",
    },
    "shadow_cell_decisions": {"condition_id", "token_id", "entry_best_ask"},
    "shadow_path_observations": {"best_bid"},
}


def original_schema():
    source = Path(__file__).resolve().parents[2] / "golden-cherry/src/polybot/shadow/db.py"
    assignments = {
        target.id: ast.literal_eval(node.value)
        for node in ast.parse(source.read_text()).body if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"SCHEMA", "_IMMUTABLE_TABLES"}
    }
    return assignments["SCHEMA"], assignments["_IMMUTABLE_TABLES"]


def create_schema(connection):
    schema, tables = original_schema()
    connection.executescript(schema)
    for table in tables:
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_deny_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"
            )
    return tables


def source_row(connection, table, **values):
    row = {
        name: (0 if affinity == "INTEGER" else .375 if affinity == "REAL" else
               b"PUBLIC_BODY" if affinity == "BLOB" else
               '{"context":"PRIVATE_JSON"}' if name.endswith("_json") else "PRIVATE_" + name)
        for _, name, affinity, *_ in connection.execute(f'PRAGMA main.table_info("{table}")')
    }
    return {**row, **values}


def fixture_rows(connection):
    def row(table, **values):
        return source_row(connection, table, **values)

    payload = gzip.compress(b' {"id":"public-event","markets":[]}\n', mtime=0)
    rows = {
        "shadow_schema_metadata": [row("shadow_schema_metadata", data_contract=PROFILE_ID, created_at=STAMP)],
        "shadow_config_versions": [row(
            "shadow_config_versions", config_hash="PRIVATE_CONFIG_" + str(epoch),
            data_contract=PROFILE_ID, runtime_job=PROFILE_ID, mode="shadow",
            strategy_source_digest="PRIVATE_SOURCE_" + str(epoch),
            preregistration_id=PROFILE_ID + "-prereg-2026-09-05",
            preregistration_sha256=PREREGISTRATION_SHA, first_seen_at=STAMP,
        ) for epoch in (1, 2)],
        "shadow_run_events": [row(
            "shadow_run_events", event_id="PRIVATE_RUN_EVENT_" + status,
            run_id="PRIVATE_RUN_" + status, event_type=status, observed_at=STAMP,
        ) for status in ("STARTED", "SUCCEEDED", "FAILED")],
        "shadow_api_attempts": [row(
            "shadow_api_attempts", method="GET", status="SUCCESS",
            url="https://gamma-api.polymarket.com/markets", params_json=' {"closed":false} ',
        )],
        "shadow_raw_payloads": [row(
            "shadow_raw_payloads", payload_gzip=payload,
            sha256=hashlib.sha256(gzip.decompress(payload)).hexdigest(),
            raw_bytes=len(gzip.decompress(payload)), gzip_bytes=len(payload),
        )],
        "shadow_market_sweeps": [row("shadow_market_sweeps", sweep_id="PRIVATE_SWEEP", cursor_complete=1)],
        "shadow_sweep_memberships": [row(
            "shadow_sweep_memberships", sweep_id="PRIVATE_SWEEP", condition_id="condition",
        )],
        "shadow_market_observations": [row(
            "shadow_market_observations", observation_id="PRIVATE_MARKET", sweep_id="PRIVATE_SWEEP",
            event_cluster_id="PRIVATE_CLUSTER", event_id="event", event_slug="event-slug",
            event_title="Public event", category="sports", condition_id="condition", market_id="market",
            market_slug="market-slug", question="Public question?", end_date=STAMP, game_start_time=None,
            sports_market_type="moneyline", liquidity=1234.5, volume_total=4567.25, active=1, closed=0,
            accepting_orders=1, enable_order_book=1, primary_outcome_label="Yes", primary_token_id="token",
            primary_gamma_probability=.72, event_tags_json=' ["sport"]\n', market_tags_json='[]',
            outcomes_json=' ["Yes", "No"] ', token_ids_json='["token","other-token"]',
            gamma_probabilities_json='[0.72, 0.28]', identity_aligned=1,
        )],
        "shadow_book_attempts": [row("shadow_book_attempts", token_id="token", status="SUCCESS")],
        "shadow_book_snapshots": [row(
            "shadow_book_snapshots", snapshot_id="PRIVATE_BOOK", token_id="token", source_timestamp=None,
            market_hash="clob-market-field-is-not-a-sha", best_bid=.69, best_ask=.73,
            bid_level_count=1, ask_level_count=2,
        )],
        "shadow_book_levels": [row(
            "shadow_book_levels", level_id=identifier, snapshot_id="PRIVATE_BOOK",
            side=side, level_index=index, price=price, size=size,
        ) for identifier, side, index, price, size in (
            ("PRIVATE_LEVEL_BID", "BID", 0, .69, 8.),
            (None, "ASK", 0, .73, 3.), ("PRIVATE_LEVEL_ASK", "ASK", 1, .73, 7.),
        )],
        "shadow_cell_decisions": [row(
            "shadow_cell_decisions", decision_id="PRIVATE_DECISION", market_observation_id="PRIVATE_MARKET",
            snapshot_id="PRIVATE_BOOK", condition_id="condition", token_id="token", entry_best_ask=.73,
            entry_vwap=.743, entry_shares=4.5, entry_cost=3.3435, episode_id="PRIVATE_EPISODE",
        )],
        "shadow_episodes": [row(
            "shadow_episodes", episode_id="PRIVATE_EPISODE", config_hash="PRIVATE_CONFIG_1",
            event_id="event", condition_id="condition", token_id="token", question="Public question?",
            category="sports", outcome_label="Yes", end_date=STAMP, game_start_time=None,
            liquidity=1234.5, volume_total=4567.25, entry_best_ask=.73,
            entry_vwap=.743, entry_shares=4.5, entry_cost=3.3435,
        )],
        "shadow_episode_policies": [row("shadow_episode_policies", episode_id="PRIVATE_EPISODE")],
        "shadow_path_observations": [row(
            "shadow_path_observations", episode_id="PRIVATE_EPISODE", snapshot_id="PRIVATE_BOOK",
            best_bid=.69, executable_bid_vwap=.682, executable_proceeds=3.069,
            filled_shares=4.5, remaining_shares=0., depth_complete=1, peak_executable_bid_vwap=.8,
        )],
        "shadow_resolution_observations": [row(
            "shadow_resolution_observations", resolution_id="PRIVATE_RESOLUTION_" + str(index),
            run_id="PRIVATE_RESOLUTION_RUN_" + str(index), condition_id="condition", token_id="token",
            resolution_status=status, outcomes_json=' ["Yes", "No"] ',
            token_ids_json='["token","other-token"]', final_prices_json=prices,
            winner_index=winner, token_payout=payout,
        ) for index, (status, prices, winner, payout) in enumerate((
            ("PROVEN", "[1, 0]", 0, 1.), ("NOT_PROVEN", "[]", None, None),
            ("NOT_FINAL", "[0.25, 0.75]", None, None),
        ))],
        "shadow_policy_exits": [row(
            "shadow_policy_exits", episode_id="PRIVATE_EPISODE", resolution_id="PRIVATE_RESOLUTION_0",
            exit_kind="RESOLUTION", exit_proceeds=4.5, pnl_usdc=1.1565, roi=.3459,
        )],
        "shadow_data_quality_issues": [row("shadow_data_quality_issues", condition_id=None, token_id=None)],
    }
    for table_rows in rows.values():
        for index, item in enumerate(table_rows):
            item["__rowid__"] = (-7, 0, 91)[index]
    return rows


def insert_plain(connection, table, row):
    values = dict(row)
    rowid = values.pop("__rowid__")
    connection.execute(
        f'INSERT INTO main."{table}"(rowid,' + ",".join(values) + ") VALUES(" +
        ",".join("?" for _ in range(len(values) + 1)) + ")", (rowid, *values.values()),
    )


def seed(path, *, references=None, raw=False):
    with closing(connect(path, references=references) if references else sqlite3.connect(path)) as connection:
        tables = create_schema(connection)
        rows = fixture_rows(connection)
        connection.execute("BEGIN IMMEDIATE")
        for table in ("shadow_schema_metadata", "shadow_config_versions"):
            for row in rows[table]:
                insert_plain(connection, table, row)
        if raw:
            initialize_raw_links(connection, NAMESPACE, references.writer.scalar_authority_identity(),
                                 references=references, profile_id=PROFILE_ID)
        for table in tables[2:]:
            table_rows = rows[table]
            if raw and table in RAW_TABLES:
                assert insert_raw_rows(connection, STRATEGY, table, table_rows,
                                       references=references, namespace=NAMESPACE)
            elif references and table == "shadow_book_levels":
                assert insert_shared_levels(connection, STRATEGY, table, table_rows,
                                            references=references, preserve_rowid=True)
            else:
                for row in table_rows:
                    encoded = externalize_row(STRATEGY, table, row, references=references) if references else row
                    insert_plain(connection, table, encoded)
        connection.commit()
    return rows


def assert_reconstructed(path, references, expected):
    with closing(connect(path.as_uri() + "?mode=ro", uri=True, references=references)) as connection:
        connection.execute("PRAGMA query_only=ON")
        for table, rows in expected.items():
            if table in RAW_TABLES:
                actual = list(iter_raw_logical_rows(connection, table, references=references, profile_id=PROFILE_ID))
            elif table == "shadow_book_levels":
                actual = list(iter_level_logical_rows(connection, STRATEGY, table,
                                                      references=references, require_rowid=True))
            else:
                columns = [item[1] for item in connection.execute(f'PRAGMA main.table_info("{table}")')]
                actual = [dict(zip(("__rowid__", *columns), values, strict=True)) for values in
                          connection.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]
            assert actual == rows, table
            assert [[type(value) for value in item.values()] for item in actual] == [
                [type(item[key]) for key in actual[index]] for index, item in enumerate(rows)
            ], table
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        verify_raw_dependencies(connection, references=references)


@pytest.fixture
def raw_case(tmp_path):
    private = tmp_path / "native.db"
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store, cache_bytes=0)
        rows = seed(private, references=refs, raw=True)
        yield private, store, refs, rows


@pytest.mark.parametrize("shared_source", [False, True], ids=["inline", "body-and-levels"])
def test_offline_migration_preserves_all_rows_schema_level_ids_and_types(tmp_path, shared_source):
    source, target = tmp_path / "source.db", tmp_path / "derivative.db"
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store, cache_bytes=0)
        expected = seed(source, references=refs if shared_source else None)
        source_sha = file_sha256(source)
        result = migrate_raw_database(source, target, source_sha256=source_sha,
                                      namespace=NAMESPACE, references=refs, profile_id=PROFILE_ID)
        assert result["status"] == "VERIFIED"
        assert file_sha256(source) == source_sha
        assert result["tables"]["shadow_book_levels"]["implicit_rowid_preserved"] is True
        with PayloadReader(store.path) as cold:
            assert_reconstructed(target, PayloadReferences(cold, cache_bytes=0), expected)
            assert verify_projection_closure(cold, target, STRATEGY)["receipt_count"] == 8
            assert verify_closure(cold, reference_closure(target, STRATEGY))["payload_count"] > 0
        with closing(sqlite3.connect(source)) as before, closing(sqlite3.connect(target)) as after:
            assert logical_raw_schema_rows(after, profile_id=PROFILE_ID) == logical_raw_schema_rows(before, profile_id=PROFILE_ID)


def test_native_schema_and_private_derived_values_do_not_enter_public_groups(raw_case):
    path, store, refs, rows = raw_case
    profile = raw_profile(PROFILE_ID)
    assert set(profile.tables) == set(RAW_TABLES)
    assert profile.level_tables == ("shadow_book_levels",)
    with closing(sqlite3.connect(":memory:")) as original:
        create_schema(original)
        objects = list(original.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ))
    assert tuple(objects) == profile.source_objects
    assert {kind: sum(item[0] == kind for item in objects) for kind in ("table", "index", "trigger")} == {
        "table": 18, "index": 4, "trigger": 36,
    }
    assert hashlib.sha256(json.dumps(objects, separators=(",", ":")).encode()).hexdigest() == profile.logical_schema_sha256
    for table, public in PUBLIC_COLUMNS.items():
        assert set(profile.tables[table].public_columns) == public
    with closing(sqlite3.connect(path)) as physical:
        for table in RAW_TABLES:
            columns = {item[1] for item in physical.execute(f'PRAGMA table_info("{table}")')}
            assert columns == set(profile.tables[table].private_columns) | {"_public_record_id"}
        assert physical.execute("SELECT COUNT(*) FROM shadow_book_levels").fetchone()[0] == 0
    with PayloadReader(store.path) as cold:
        assert_reconstructed(path, PayloadReferences(cold, cache_bytes=0), rows)
        closure = verify_projection_closure(cold, path, STRATEGY)
        assert closure["receipt_count"] == 8
        assert closure["namespace"] == NAMESPACE
    with closing(connect(path, references=refs)) as connection:
        assert raw_layout_metadata(connection)["profile_id"] == PROFILE_ID
        assert connection.execute("SELECT event_cluster_id,entry_vwap,entry_cost FROM shadow_episodes").fetchone() == (
            "PRIVATE_event_cluster_id", .743, 3.3435,
        )
        assert connection.execute("SELECT resolution_status,token_payout FROM shadow_resolution_observations ORDER BY resolution_id").fetchall() == [
            ("PROVEN", 1.), ("NOT_PROVEN", None), ("NOT_FINAL", None),
        ]


@pytest.mark.parametrize("violation", ["primary_key", "unique", "foreign_key", "check"])
def test_native_insert_preserves_original_constraints(raw_case, violation):
    path, _, refs, rows = raw_case
    table = "shadow_cell_decisions" if violation == "foreign_key" else "shadow_market_observations"
    row = dict(rows[table][0])
    row["__rowid__"] = 199
    if violation != "primary_key":
        row["decision_id" if violation == "foreign_key" else "observation_id"] = "another-id"
    if violation == "foreign_key":
        row.update(market_observation_id="missing-parent", band_id="another-band")
    if violation == "check":
        row.update(condition_id="another-condition", identity_aligned=2)
    with closing(connect(path, references=refs)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        before = connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0]
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            insert_raw_rows(connection, STRATEGY, table, [row], references=refs, namespace=NAMESPACE)
        connection.rollback()
        assert connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0] == before


def test_original_append_only_guards_remain_on_skeletons_and_logical_views(raw_case):
    path, _, refs, _ = raw_case
    with closing(connect(path, references=refs)) as connection:
        for table in RAW_TABLES:
            primary_key = raw_profile(PROFILE_ID).tables[table].primary_key
            for qualified in (f'main."{table}"', f'"{table}"'):
                for statement in (f'UPDATE {qualified} SET "{primary_key}"="{primary_key}"',
                                  f"DELETE FROM {qualified}"):
                    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                        connection.execute(statement)
                    connection.rollback()


@pytest.mark.parametrize("damage", ["receipt", "projection_missing", "projection_corrupt", "body_missing", "body_corrupt"])
def test_cold_reader_rejects_missing_or_corrupt_public_evidence(raw_case, damage):
    path, store, _, _ = raw_case
    table = "payloads" if damage.startswith("body_") else "projection_receipts" if damage == "receipt" else "projection_hot"
    with closing(sqlite3.connect(store.path)) as physical:
        guards = physical.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
        for name, _ in guards:
            physical.execute(f'DROP TRIGGER "{name}"')
        if damage.endswith("corrupt"):
            physical.execute(f"UPDATE {table} SET {'body' if table == 'payloads' else 'cells'}=X'010203'")
        else:
            physical.execute(f"DELETE FROM {table}")
        for _, ddl in guards:
            physical.execute(ddl)
        physical.commit()
    with PayloadReader(store.path) as cold:
        refs = PayloadReferences(cold, cache_bytes=0)
        with pytest.raises((ValueError, sqlite3.Error, StoreError)):
            if damage.startswith("body_"):
                verify_closure(cold, reference_closure(path, STRATEGY))
            else:
                verify_projection_closure(cold, path, STRATEGY)
        with pytest.raises((ValueError, sqlite3.Error, StoreError)):
            with closing(connect(path, references=refs)) as connection:
                query = "SELECT payload_gzip FROM shadow_raw_payloads" if damage.startswith("body_") else "SELECT best_ask FROM shadow_book_snapshots"
                connection.execute(query).fetchall()


@pytest.mark.parametrize("field,value", [
    ("runtime_job", "cherry-live"), ("mode", "live"), ("data_contract", "foreign-contract"),
    ("preregistration_id", "unreviewed"), ("preregistration_sha256", "0" * 64),
])
def test_profile_rejects_any_foreign_config_epoch_before_creating_derivative(tmp_path, field, value):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    seed(source)
    with closing(sqlite3.connect(source)) as connection:
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='shadow_config_versions_deny_update'").fetchone()[0]
        connection.execute("DROP TRIGGER shadow_config_versions_deny_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE shadow_config_versions SET {field}=? WHERE config_hash='PRIVATE_CONFIG_2'", (value,))
        connection.execute(ddl)
        connection.commit()
    with PayloadStore(tmp_path / "public.db") as store:
        with pytest.raises(ValueError, match="authority"):
            migrate_raw_database(source, target, source_sha256=file_sha256(source), namespace=NAMESPACE,
                                 references=PayloadReferences(store, store), profile_id=PROFILE_ID)
    assert not target.exists()
    assert not target.with_suffix(".db.raw-migration.json").exists()


@pytest.mark.parametrize("job,runtime", [("polybot-cherry", PROFILE_ID), ("polybot-cherry-shadow", "cherry-live")])
def test_profile_rejects_wrong_runtime_namespace(tmp_path, job, runtime):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    seed(source)
    with PayloadStore(tmp_path / "public.db") as store:
        with pytest.raises(ValueError, match="namespace"):
            migrate_raw_database(source, target, source_sha256=file_sha256(source),
                                 namespace=scalar_namespace("fixture", job, STRATEGY, runtime),
                                 references=PayloadReferences(store, store), profile_id=PROFILE_ID)
    assert not target.exists()


def test_legacy_level_links_without_original_rowids_are_not_invented(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        seed(source, references=refs)
        with closing(sqlite3.connect(source)) as connection:
            connection.execute(f"UPDATE {BINDING_TABLE} SET retained_json=json_remove(retained_json,'$.__rowid__')")
            connection.commit()
        with pytest.raises(ValueError, match="rowid"):
            migrate_raw_database(source, target, source_sha256=file_sha256(source),
                                 namespace=NAMESPACE, references=refs, profile_id=PROFILE_ID)


def test_native_bad_public_ack_rolls_back_local_rows(raw_case, monkeypatch):
    path, store, refs, rows = raw_case
    row = dict(rows["shadow_book_snapshots"][0], __rowid__=199,
               snapshot_id="new-book", run_id="new-run", best_ask=.82)
    monkeypatch.setattr(store, "put_public_projections", lambda rows: [])
    with closing(connect(path, references=refs)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="ACK"):
            insert_raw_rows(connection, STRATEGY, "shadow_book_snapshots", [row],
                            references=refs, namespace=NAMESPACE)
        connection.rollback()
        assert connection.execute("SELECT snapshot_id,best_ask FROM shadow_book_snapshots").fetchall() == [("PRIVATE_BOOK", .73)]

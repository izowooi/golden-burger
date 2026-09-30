import sqlite3

import pytest
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE,
    delete_snapshot_rows,
    insert_shared_projection_snapshot,
    iter_projection_link_rows,
    iter_projection_private_rows,
    iter_projection_record_ids,
    projection_layout_metadata,
    update_private_projection_snapshot,
)
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def schema(strategy, *, legacy_order=False, unique=True):
    profile = snapshot_profile(strategy)
    columns = list(profile.columns)
    if legacy_order:
        columns = [
            next(item for item in columns if item.name == name)
            for name in (
                "id",
                "condition_id",
                "probability",
                "liquidity",
                "volume_24h",
                "timestamp",
            )
        ]
        columns += [
            item for item in profile.columns if item.name not in {row.name for row in columns}
        ]
    clauses = []
    for column in columns:
        clause = column.name + " " + column.affinity
        clause += (
            " PRIMARY KEY" if column.name == "id" else " NOT NULL" if not column.nullable else ""
        )
        clauses.append(clause)
    if unique:
        clauses += ["UNIQUE(" + ",".join(fields) + ")" for fields in profile.unique_constraints]
    return "CREATE TABLE market_snapshots(" + ",".join(clauses) + ")"


@pytest.fixture
def projection_db(tmp_path, monkeypatch, request):
    strategy, legacy_order = getattr(request, "param", ("golden-blueberry", False))
    monkeypatch.delenv("PUBLIC_MARKET_DATA_SOURCE", raising=False)
    monkeypatch.delenv("JOB_NAME", raising=False)
    public = tmp_path / "public.db"
    with PayloadStore(public) as writer, PayloadReader(public) as reader:
        references = PayloadReferences(reader=reader, writer=writer)
        connection = connect(tmp_path / "private.db", references=references)
        connection.execute(schema(strategy, legacy_order=legacy_order))
        connection.commit()
        namespace = scalar_namespace("source", "jenkins-job", strategy, "runtime")
        yield connection, references, namespace, strategy
        connection.close()


def put(fixture, **changes):
    connection, references, namespace, strategy = fixture
    row = {column: None for column in snapshot_profile(strategy).column_names}
    row.update(
        condition_id="condition-a",
        probability=0.85,
        liquidity=100.0,
        volume_24h=200.0,
        best_bid=0.84,
        best_ask=0.86,
        spread=0.02,
        source_updated_at="2026-09-01T00:00:00Z",
        run_id="private-run",
        timestamp="2026-09-01 00:00:00.000000",
    )
    row.update(changes)
    return insert_shared_projection_snapshot(
        connection, strategy, row, namespace=namespace, references=references
    )


@pytest.mark.parametrize(
    "projection_db", [("golden-blueberry", False), ("golden-blueberry", True)], indirect=True
)
def test_projection_restores_full_actual_column_order_and_keeps_private_fields_local(projection_db):
    connection, refs, namespace, strategy = projection_db
    first = put(projection_db)
    connection.commit()
    second = put(projection_db, run_id="other-private-run", timestamp="2026-09-02 00:00:00.000000")
    connection.commit()
    names = [row[1] for row in connection.execute("PRAGMA main.table_info(market_snapshots)")]
    assert connection.execute("SELECT * FROM market_snapshots ORDER BY id").fetchall() == [
        tuple(first[name] for name in names),
        tuple(second[name] for name in names),
    ]
    assert connection.execute("SELECT COUNT(*) FROM main.market_snapshots").fetchone() == (0,)
    records = list(iter_projection_record_ids(connection))
    assert (
        len(records) == 1
    )  # Private run/observation clock do not duplicate immutable public groups.
    group = refs.reader.get_projection_records(records, refs.reader.scalar_authority_identity())[0]
    assert "private-run" not in repr(group.projection.row())
    assert "2026-09-02 00:00:00.000000" not in repr(group.projection.row())
    assert list(iter_projection_link_rows(connection)) == [
        (1, "gamma-quote-v1", records[0]),
        (2, "gamma-quote-v1", records[0]),
    ]
    assert projection_layout_metadata(connection)["namespace"] == namespace
    assert connection.execute(
        "SELECT id FROM market_snapshots WHERE condition_id=? AND timestamp>=? ORDER BY timestamp",
        ("condition-a", second["timestamp"]),
    ).fetchall() == [(2,)]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM public_projection.projection_records")


def test_context_read_your_writes_and_rollback_keep_existing_ledger_transaction(projection_db):
    connection, refs, _, _ = projection_db
    connection.execute("CREATE TABLE private_ledger(note TEXT)")
    connection.execute("INSERT INTO private_ledger VALUES('pending')")
    first = put(projection_db)
    assert connection.execute("SELECT id FROM market_snapshots").fetchall() == [(first["id"],)]
    put(projection_db, probability=0.90)
    assert connection.execute(
        "SELECT id,probability FROM market_snapshots ORDER BY id"
    ).fetchall() == [(1, 0.85), (2, 0.9)]
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM private_ledger").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (0,)


def test_explicit_offline_namespace_never_uses_the_migration_process_environment(
    projection_db, monkeypatch
):
    connection, _, namespace, _ = projection_db
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "unrelated-process-host")
    monkeypatch.setenv("JOB_NAME", "unrelated-process-job")
    put(projection_db)
    put(projection_db, probability=0.9)
    connection.commit()
    assert projection_layout_metadata(connection)["namespace"] == namespace
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (2,)


def test_private_iterator_expands_dictionaries_without_decoding_reference_markers(projection_db):
    connection, refs, _, strategy = projection_db
    first = put(projection_db)
    connection.commit()
    columns = ("id", "condition_id", "run_id", "timestamp")
    assert list(iter_projection_private_rows(connection, strategy, columns)) == [
        (1, "condition-a", "private-run", first["timestamp"]),
    ]
    marker = refs.encode_many(["private-run"])[0]
    connection.execute("UPDATE _public_projection_runs SET run_id=?", (marker,))
    connection.commit()
    assert connection.execute("SELECT run_id FROM market_snapshots").fetchone() == ("private-run",)
    assert list(iter_projection_private_rows(connection, strategy, columns))[0][2] == marker
    with pytest.raises(ValueError, match="unowned columns"):
        list(iter_projection_private_rows(connection, strategy, ("probability",)))


def test_private_iterator_rejects_missing_dictionary_rows(projection_db):
    connection, _, _, strategy = projection_db
    put(projection_db)
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DELETE FROM _public_projection_runs")
    connection.commit()
    with pytest.raises(ValueError, match="dictionary dependency"):
        list(iter_projection_private_rows(connection, strategy, ("id", "run_id")))


@pytest.mark.parametrize(
    "failure", ["put_public_projections", "append_projection_receipts", "get_projection_records"]
)
def test_failed_group_ack_or_readback_never_publishes_context(projection_db, monkeypatch, failure):
    connection, refs, _, _ = projection_db
    connection.execute("CREATE TABLE private_ledger(note TEXT)")
    connection.execute("INSERT INTO private_ledger VALUES('pending')")
    target = refs.reader if failure == "get_projection_records" else refs.writer
    monkeypatch.setattr(
        target,
        failure,
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic publication failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic publication"):
        put(projection_db)
    assert connection.in_transaction
    assert connection.execute("SELECT note FROM private_ledger").fetchone() == ("pending",)
    assert not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name=?", (CONTEXT_TABLE,)
    ).fetchone()


def test_only_explicit_private_timestamp_update_is_allowed_and_rollbackable(projection_db):
    connection, refs, _, strategy = projection_db
    first = put(projection_db)
    connection.commit()
    links = list(iter_projection_link_rows(connection))
    assert (
        update_private_projection_snapshot(
            connection,
            strategy,
            first["id"],
            {"timestamp": "2026-08-01 00:00:00.000000"},
            references=refs,
        )
        == 1
    )
    assert connection.execute("SELECT timestamp FROM market_snapshots").fetchone() == (
        "2026-08-01 00:00:00.000000",
    )
    connection.rollback()
    assert connection.execute("SELECT timestamp FROM market_snapshots").fetchone() == (
        first["timestamp"],
    )
    assert list(iter_projection_link_rows(connection)) == links
    for values in ({"probability": 0.9}, {"run_id": "changed"}, {"condition_id": "changed"}):
        with pytest.raises(ValueError, match="immutable or public"):
            update_private_projection_snapshot(
                connection, strategy, first["id"], values, references=refs
            )
    with pytest.raises(sqlite3.OperationalError, match="view"):
        connection.execute("UPDATE market_snapshots SET probability=.99")


@pytest.mark.parametrize("projection_db", [("golden-kiwi", False)], indirect=True)
def test_kiwi_unique_constraint_spans_legacy_and_context_but_keeps_sqlite_null_semantics(
    projection_db,
):
    connection, refs, _, _ = projection_db
    put(projection_db)
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        put(projection_db, probability=0.86)
    put(projection_db, run_id=None)
    put(projection_db, run_id=None)
    connection.commit()
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (3,)
    assert delete_snapshot_rows(connection, "id=1") == 1
    connection.commit()
    put(projection_db)  # Retiring the private association permits the original unique key again.
    connection.commit()
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (3,)


@pytest.mark.parametrize("projection_db", [("golden-kiwi", False)], indirect=True)
def test_kiwi_catalog_existing_public_body_refs_resolve_into_separate_public_group(projection_db):
    connection, refs, _, _ = projection_db
    arrays = ('["Yes","No"]', '["0.85","0.15"]', '["token-a","token-b"]', '["public-tag"]')
    markers = refs.encode_many(arrays)
    names = (
        "catalog_outcomes_json",
        "catalog_outcome_prices_json",
        "catalog_token_ids_json",
        "catalog_tags_json",
    )
    put(projection_db, **dict(zip(names, markers, strict=True)))
    connection.commit()
    assert (
        connection.execute("SELECT " + ",".join(names) + " FROM market_snapshots").fetchone()
        == arrays
    )
    groups = refs.reader.get_projection_records(
        list(iter_projection_record_ids(connection)), refs.reader.scalar_authority_identity()
    )
    catalog = next(row for row in groups if row.projection.kind == "kiwi-catalog-v1")
    assert not any(
        isinstance(value, str) and value.startswith("\x1ePMDATA")
        for value in catalog.projection.row()
    )


def test_cold_projection_block_has_one_statement_bounded_proof_and_indexed_private_window(
    projection_db, monkeypatch
):
    from polybot_observability.market_data_projection_links import (
        initialize_projection_links,
        install_projection_views,
    )
    from polybot_observability.market_data_projections import PublicProjection

    connection, refs, namespace, strategy = projection_db
    projections = [
        PublicProjection(
            "gamma-quote-v1",
            (
                "condition-a",
                index / 1024,
                None,
                None,
                None,
                None,
                None,
                f"2026-09-01T00:{index // 60:02d}:{index % 60:02d}Z",
            ),
        )
        for index in range(1024)
    ]
    references = refs.writer.put_public_projections(projections)
    assert refs.writer.projection_stats()["block_count"] == 1
    connection.execute("BEGIN")
    initialize_projection_links(
        connection, strategy, namespace, refs.reader.scalar_authority_identity()
    )
    connection.execute("INSERT INTO _public_projection_conditions VALUES(1,'condition-a')")
    connection.execute("INSERT INTO _public_projection_runs VALUES(1,'private-run')")
    connection.executemany(
        f"INSERT INTO {CONTEXT_TABLE}(id,condition_key,run_key,timestamp,projection_0_id) "
        "VALUES(?,?,?,?,?)",
        [
            (index + 1, 1, 1, "2026-09-01 00:00:00.000000", reference.record_id)
            for index, reference in enumerate(references)
        ],
    )
    connection.commit()
    install_projection_views(connection, references=refs)
    actual = refs.reader.get_projection_records
    batches = []

    def observed(ids, authority):
        batches.append(len(ids))
        return actual(ids, authority)

    monkeypatch.setattr(refs.reader, "get_projection_records", observed)
    rows = connection.execute(
        "SELECT id,probability,best_bid FROM market_snapshots ORDER BY id"
    ).fetchall()
    assert len(rows) == 1024 and rows[100] == (101, 100 / 1024, None)
    assert batches == [1024]
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (1024,)
    assert batches == [1024, 1024]
    plan = [
        row[3]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM market_snapshots "
            "WHERE condition_id=? AND timestamp>? ORDER BY timestamp LIMIT 5",
            ("condition-a", "2026-08-01"),
        )
    ]
    assert any("_public_projection_condition_time" in detail for detail in plan), plan


def test_revisited_old_group_does_not_evict_current_cold_block(projection_db, monkeypatch):
    from polybot_observability.market_data_projection_links import (
        initialize_projection_links,
        install_projection_views,
    )
    from polybot_observability.market_data_projections import PublicProjection

    connection, refs, namespace, strategy = projection_db
    references = []
    for start in (0, 1024):
        values = [
            PublicProjection(
                "gamma-quote-v1",
                (
                    "condition-a",
                    index / 4096,
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"2026-09-01T00:{index // 60:02d}:{index % 60:02d}Z",
                ),
            )
            for index in range(start, start + 1024)
        ]
        references.extend(refs.writer.put_public_projections(values))
    connection.execute("BEGIN")
    initialize_projection_links(
        connection, strategy, namespace, refs.reader.scalar_authority_identity()
    )
    connection.execute("INSERT INTO _public_projection_conditions VALUES(1,'condition-a')")
    connection.execute("INSERT INTO _public_projection_runs VALUES(1,'private-run')")
    rows = []
    for index in range(1024):
        for ref in (references[index + 1024], references[0]):
            rows.append((len(rows) + 1, 1, 1, "2026-09-01 00:00:00.000000", ref.record_id))
    connection.executemany(
        f"INSERT INTO {CONTEXT_TABLE}(id,condition_key,run_key,timestamp,projection_0_id) "
        "VALUES(?,?,?,?,?)",
        rows,
    )
    connection.commit()
    install_projection_views(connection, references=refs)
    actual = refs.reader.get_projection_records
    batches = []

    def observed(ids, authority):
        batches.append(len(ids))
        return actual(ids, authority)

    monkeypatch.setattr(refs.reader, "get_projection_records", observed)
    assert (
        len(
            connection.execute("SELECT id,probability FROM market_snapshots ORDER BY id").fetchall()
        )
        == 2048
    )
    assert batches == [1024, 1024]

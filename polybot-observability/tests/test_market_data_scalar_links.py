import sqlite3

import pytest
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import (
    COLUMNS,
    delete_snapshot_rows,
    insert_shared_snapshot,
    scalar_namespace,
)
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore

SCHEMA = """
CREATE TABLE market_snapshots (
 id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, probability REAL NOT NULL,
 liquidity REAL, volume_24h REAL, timestamp DATETIME
);
CREATE INDEX snapshots_condition_time ON market_snapshots(condition_id,timestamp);
"""
NAMESPACE = scalar_namespace("fixture-source", "polybot-orange", "golden-date", "date-live")


@pytest.fixture
def linked(tmp_path):
    public = tmp_path / "public.db"
    with PayloadStore(public) as store, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=store)
        connection = connect(tmp_path / "private.db", references=refs)
        connection.executescript(SCHEMA)
        yield connection, refs, public
        connection.close()


def put(connection, refs, **values):
    row = {
        "condition_id": "condition-a",
        "probability": 0.5,
        "liquidity": None,
        "volume_24h": 123,
        "timestamp": "2026-09-01 12:00:00.000000",
        **values,
    }
    return insert_shared_snapshot(
        connection, "golden-date", row, namespace=NAMESPACE, references=refs
    )


def test_shared_snapshot_view_preserves_sqlite_values_and_legacy_rows(linked):
    connection, refs, public = linked
    connection.execute("INSERT INTO market_snapshots VALUES (8,'legacy',.25,NULL,1,'2026-08-01')")
    connection.commit()
    saved = put(connection, refs, probability="0.875", volume_24h="42")
    assert saved == dict(
        zip(
            COLUMNS,
            (9, "condition-a", 0.875, None, 42.0, "2026-09-01 12:00:00.000000"),
            strict=True,
        )
    )
    connection.commit()
    assert connection.execute("SELECT * FROM market_snapshots ORDER BY id").fetchall() == [
        (8, "legacy", 0.25, None, 1.0, "2026-08-01"),
        tuple(saved.values()),
    ]
    assert connection.execute("SELECT COUNT(*) FROM main.market_snapshots").fetchone()[0] == 1
    assert connection.execute("SELECT typeof(record_id) FROM _public_scalar_links").fetchone() == (
        "integer",
    )
    assert (
        refs.reader.get_scalar_receipts(
            [
                (
                    NAMESPACE,
                    9,
                    connection.execute("SELECT record_id FROM _public_scalar_links").fetchone()[0],
                )
            ],
            authority_uuid=refs.reader.scalar_authority_identity(),
        )[0].original_id
        == 9
    )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM public_scalar.scalar_snapshots")
    with connect(
        connection.execute("PRAGMA database_list").fetchone()[2], references=refs
    ) as reopened:
        assert reopened.execute(
            "SELECT probability FROM market_snapshots WHERE id=9"
        ).fetchone() == (0.875,)


@pytest.mark.parametrize("autoincrement", [False, True])
def test_local_retention_reuses_original_default_ids_and_preserves_public_history(
    tmp_path, autoincrement
):
    public = tmp_path / "public.db"
    with PayloadStore(public) as store, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=store)
        connection = connect(tmp_path / "private.db", references=refs)
        schema = (
            SCHEMA.replace("INTEGER PRIMARY KEY,", "INTEGER PRIMARY KEY AUTOINCREMENT,")
            if autoincrement
            else SCHEMA
        )
        connection.executescript(schema)
        first = put(connection, refs)
        connection.commit()
        old_hash = connection.execute("SELECT record_id FROM _public_scalar_links").fetchone()[0]
        assert delete_snapshot_rows(connection, "id=?", (first["id"],)) == 1
        connection.commit()
        second = put(connection, refs, probability=0.6)
        connection.commit()
        assert second["id"] == (2 if autoincrement else 1)
        assert (
            reader.get_scalar_records([old_hash], reader.scalar_authority_identity())[
                0
            ].snapshot.probability
            == 0.5
        )
        assert (
            reader.get_scalar_receipts(
                [(NAMESPACE, first["id"], old_hash)],
                authority_uuid=reader.scalar_authority_identity(),
            )[0]
            is not None
        )
        connection.close()


def test_rollback_never_publishes_local_membership_or_commits_private_rows(linked):
    connection, refs, _ = linked
    connection.execute("CREATE TABLE private_ledger(id INTEGER PRIMARY KEY, note TEXT)")
    connection.execute("INSERT INTO private_ledger VALUES(1,'pending')")
    put(connection, refs)
    assert connection.in_transaction
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM private_ledger").fetchone()[0] == 0
    assert not connection.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='_public_scalar_links'"
    ).fetchone()


@pytest.mark.parametrize("failure", ["payload_ack", "receipt_ack", "readback"])
def test_failed_publication_preserves_caller_transaction(linked, monkeypatch, failure):
    connection, refs, _ = linked
    connection.execute("CREATE TABLE private_ledger(note TEXT)")
    connection.execute("INSERT INTO private_ledger VALUES('pending')")
    name = {
        "payload_ack": "put_scalar_snapshots",
        "receipt_ack": "append_scalar_receipts",
        "readback": "get_scalar_records",
    }[failure]
    target = refs.reader if failure == "readback" else refs.writer
    monkeypatch.setattr(
        target, name, lambda *_, **__: (_ for _ in ()).throw(RuntimeError("synthetic failure"))
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        put(connection, refs)
    assert connection.in_transaction
    assert connection.execute("SELECT note FROM private_ledger").fetchone() == ("pending",)
    assert not connection.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='_public_scalar_links'"
    ).fetchone()


def test_original_constraints_fail_before_any_public_write(linked, monkeypatch):
    connection, refs, _ = linked
    calls = []
    monkeypatch.setattr(refs.writer, "put_scalar_snapshots", lambda *_: calls.append(True))
    with pytest.raises(sqlite3.IntegrityError):
        put(connection, refs, condition_id=None)
    assert not calls


def test_new_scalar_writer_requires_an_explicit_source_namespace(linked):
    connection, refs, _ = linked
    with pytest.raises(ValueError, match="explicit runtime namespace"):
        insert_shared_snapshot(
            connection,
            "golden-date",
            {"condition_id": "condition-a", "probability": 0.5},
            references=refs,
        )
    assert not connection.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='_public_scalar_links'"
    ).fetchone()


def test_canonical_default_runtime_requires_explicit_source_job_and_preserves_binding(
    tmp_path, monkeypatch
):
    database = tmp_path / "golden-date/data/default/trades.db"
    database.parent.mkdir(parents=True)
    public = tmp_path / "public.db"
    with PayloadStore(public) as store, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=store)
        connection = connect(database, references=refs)
        connection.executescript(SCHEMA)
        values = {"condition_id": "condition-a", "probability": 0.5, "timestamp": "2026-09-01"}
        monkeypatch.delenv("PUBLIC_MARKET_DATA_SOURCE", raising=False)
        monkeypatch.delenv("JOB_NAME", raising=False)
        with pytest.raises(ValueError, match="explicit bounded source"):
            insert_shared_snapshot(connection, "golden-date", values, references=refs)
        monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture-source")
        monkeypatch.setenv("JOB_NAME", "polybot-red")
        saved = insert_shared_snapshot(connection, "golden-date", values, references=refs)
        connection.commit()
        assert saved["id"] == 1
        namespace = connection.execute("SELECT namespace FROM _public_scalar_layout").fetchone()[0]
        assert namespace == scalar_namespace(
            "fixture-source", "polybot-red", "golden-date", "default"
        )
        monkeypatch.setenv("JOB_NAME", "polybot-other")
        with pytest.raises(ValueError, match="differs from the bound namespace"):
            insert_shared_snapshot(connection, "golden-date", values, references=refs)
        assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (1,)
        connection.close()
        bare = tmp_path / "golden-date/data/trades.db"
        with connect(bare, references=refs) as unsupported:
            unsupported.executescript(SCHEMA)
            with pytest.raises(ValueError, match="explicit runtime namespace"):
                insert_shared_snapshot(unsupported, "golden-date", values, references=refs)


def test_missing_selected_shared_record_fails_closed(linked):
    connection, refs, _ = linked
    saved = put(connection, refs)
    connection.commit()
    connection.execute("UPDATE _public_scalar_links SET record_id=?", (999999999,))
    connection.commit()
    with pytest.raises(sqlite3.OperationalError, match="user-defined function"):
        connection.execute("SELECT * FROM market_snapshots WHERE id=?", (saved["id"],)).fetchall()


def test_condition_query_does_not_inspect_unselected_corrupt_values(linked):
    connection, refs, _ = linked
    wanted = put(connection, refs)
    connection.commit()
    unwanted = put(connection, refs, condition_id="condition-unrelated")
    connection.commit()
    db = refs.writer._connection
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='scalar_hot'"
    ).fetchall():
        db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    db.execute(
        "UPDATE scalar_hot SET probability=.99 WHERE record_id IN "
        "(SELECT s.id FROM scalar_snapshots s JOIN scalar_conditions c ON c.id=s.condition_key "
        "WHERE c.condition_id='condition-unrelated')"
    )
    db.commit()
    assert connection.execute(
        "SELECT id FROM market_snapshots WHERE condition_id='condition-a' AND timestamp>=?",
        ("2026-09-01",),
    ).fetchall() == [(wanted["id"],)]
    with pytest.raises(sqlite3.OperationalError, match="user-defined function"):
        connection.execute(
            "SELECT * FROM market_snapshots WHERE id=?", (unwanted["id"],)
        ).fetchall()


def test_selected_corrupt_shared_values_fail_checksum_validation(linked):
    connection, refs, _ = linked
    put(connection, refs)
    connection.commit()
    db = refs.writer._connection
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='scalar_hot'"
    ).fetchall():
        db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    db.execute("UPDATE scalar_hot SET probability=.99")
    db.commit()
    with pytest.raises(sqlite3.OperationalError, match="user-defined function"):
        connection.execute(
            "SELECT probability FROM market_snapshots WHERE condition_id='condition-a'"
        ).fetchall()


def test_condition_time_query_uses_public_condition_and_snapshot_indexes(linked):
    connection, refs, _ = linked
    put(connection, refs)
    connection.commit()
    details = [
        row[3]
        for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM market_snapshots "
            "WHERE condition_id=? AND timestamp>=? "
            "ORDER BY timestamp LIMIT 5",
            ("condition-a", "2026-09-01"),
        )
    ]
    assert any("condition_id=?" in detail for detail in details), details
    assert any(
        "condition_key=?" in detail and "timestamp_numeric>?" in detail for detail in details
    ), details
    assert not any("SCAN l" in detail for detail in details), details


def test_view_preserves_original_datetime_affinity_for_numeric_text_and_null_clocks(linked):
    connection, refs, _ = linked
    original = sqlite3.connect(":memory:")
    original.executescript(SCHEMA)
    try:
        clocks = (None, 1, 1.0, "1", "1.0", "01", 1.5, "1.5", "2026-09-01 12:00:00", "+Inf")
        for identifier, clock in enumerate(clocks, 1):
            row = (identifier, "condition-a", 0.5, None, None, clock)
            original.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?)", row)
            if identifier % 2:
                connection.execute("INSERT INTO main.market_snapshots VALUES(?,?,?,?,?,?)", row)
            else:
                put(connection, refs, id=identifier, timestamp=clock, volume_24h=None)
            connection.commit()
        queries = (
            ("SELECT id,timestamp,typeof(timestamp) FROM market_snapshots ORDER BY id", ()),
            ("SELECT id FROM market_snapshots WHERE timestamp=? ORDER BY id", ("1",)),
            ("SELECT id FROM market_snapshots WHERE timestamp>=? ORDER BY id", ("1.0",)),
            ("SELECT id FROM market_snapshots WHERE timestamp<? ORDER BY id", ("2",)),
            ("SELECT id FROM market_snapshots WHERE timestamp IS NULL ORDER BY id", ()),
            ("SELECT id FROM market_snapshots WHERE probability=? ORDER BY id", ("0.5",)),
            ("SELECT id FROM market_snapshots WHERE probability<? ORDER BY id", ("0.2",)),
            ("SELECT id FROM market_snapshots WHERE liquidity IS NULL ORDER BY id", ()),
        )
        for query, parameters in queries:
            assert connection.execute(query, parameters).fetchall() == (
                original.execute(query, parameters).fetchall()
            )
    finally:
        original.close()


def test_pending_overlay_reads_new_acknowledged_record_inside_existing_private_transaction(linked):
    connection, refs, _ = linked
    first = put(connection, refs)
    connection.commit()
    connection.execute("BEGIN")
    assert connection.execute("SELECT id FROM market_snapshots").fetchall() == [(first["id"],)]
    second = put(connection, refs, probability=0.75, timestamp="2026-09-01 12:01:00.000000")
    assert connection.in_transaction
    assert connection.execute(
        "SELECT id,probability FROM market_snapshots ORDER BY id"
    ).fetchall() == [
        (first["id"], 0.5),
        (second["id"], 0.75),
    ]
    connection.rollback()
    assert connection.execute("SELECT id FROM market_snapshots").fetchall() == [(first["id"],)]
    assert (
        len(
            list(
                refs.reader.query_scalar_snapshots(
                    "condition-a",
                    authority_uuid=refs.reader.scalar_authority_identity(),
                )
            )
        )
        == 2
    )


def test_pending_overlay_limit_preserves_prior_work_without_new_public_write(linked, monkeypatch):
    from polybot_observability import market_data_scalar_links

    connection, refs, _ = linked
    monkeypatch.setattr(market_data_scalar_links, "MAX_PENDING", 1)
    first = put(connection, refs)
    calls = []
    monkeypatch.setattr(refs.writer, "put_scalar_snapshots", lambda *_: calls.append(True))
    with pytest.raises(ValueError, match="pending overlay limit"):
        put(connection, refs, probability=0.75)
    assert not calls and connection.in_transaction
    assert connection.execute("SELECT id FROM market_snapshots").fetchall() == [(first["id"],)]
    connection.rollback()


@pytest.mark.parametrize("explicit_cursor", [False, True])
def test_scalar_record_cache_is_bounded_to_each_statement(linked, monkeypatch, explicit_cursor):
    connection, refs, _ = linked
    put(connection, refs)
    connection.commit()
    original = refs.reader.get_scalar_records
    reads = []

    def observed(ids, authority_uuid):
        reads.append(tuple(ids))
        return original(ids, authority_uuid)

    monkeypatch.setattr(refs.reader, "get_scalar_records", observed)
    execute = connection.cursor().execute if explicit_cursor else connection.execute
    assert len(execute("SELECT * FROM market_snapshots").fetchall()) == 1
    assert len(reads) == 1  # Three numeric projections and ownership check share one proof.
    assert len(execute("SELECT * FROM market_snapshots").fetchall()) == 1
    assert len(reads) == 2


def test_nested_cursors_preserve_row_factory_capture_and_scalar_values(linked):
    connection, refs, _ = linked
    for probability in (0.5, 0.6, 0.7):
        put(connection, refs, probability=probability)
        connection.commit()
    connection.row_factory = sqlite3.Row
    outer = connection.cursor()
    outer.execute("SELECT id,probability AS price FROM market_snapshots ORDER BY id")
    assert outer.fetchone()["price"] == 0.5
    connection.row_factory = None
    assert connection.execute("SELECT probability FROM market_snapshots WHERE id=3").fetchone() == (
        0.7,
    )
    remaining = outer.fetchall()
    assert [row["price"] for row in remaining] == [0.6, 0.7]


def test_cold_block_scan_reuses_one_bounded_verified_block(linked, monkeypatch):
    from polybot_observability.market_data_scalar_links import (
        initialize_scalar_links,
        install_scalar_views,
    )
    from polybot_observability.market_data_scalars import ScalarSnapshot

    connection, refs, _ = linked
    values = [
        ScalarSnapshot(
            "condition-a",
            index / 1024,
            None,
            None,
            f"2026-09-01 12:{index // 60:02d}:{index % 60:02d}.000000",
        )
        for index in range(1024)
    ]
    references = refs.writer.put_scalar_snapshots(values)
    assert refs.writer._connection.execute("SELECT COUNT(*) FROM scalar_blocks").fetchone() == (1,)
    connection.execute("BEGIN")
    initialize_scalar_links(
        connection, "golden-date", NAMESPACE, refs.reader.scalar_authority_identity()
    )
    connection.executemany(
        "INSERT INTO _public_scalar_links VALUES(?,?)",
        [(index + 1, reference.record_id) for index, reference in enumerate(references)],
    )
    connection.commit()
    install_scalar_views(connection, references=refs)
    original = refs.reader.get_scalar_records
    batches = []

    def observed(ids, authority_uuid):
        batches.append(len(ids))
        return original(ids, authority_uuid)

    monkeypatch.setattr(refs.reader, "get_scalar_records", observed)
    rows = connection.execute("SELECT id,probability FROM market_snapshots ORDER BY id").fetchall()
    assert len(rows) == 1024 and rows[100] == (101, 100 / 1024)
    assert batches == [1024]
    assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (1024,)
    assert batches == [1024, 1024]


def test_foreign_authority_is_rejected_even_for_an_empty_local_layout(linked, tmp_path):
    connection, refs, _ = linked
    put(connection, refs)
    connection.commit()
    assert delete_snapshot_rows(connection, "1=1") == 1
    connection.commit()
    private = connection.execute("PRAGMA database_list").fetchone()[2]
    other = tmp_path / "other-public.db"
    with PayloadStore(other), PayloadReader(other) as foreign:
        with pytest.raises(ValueError, match="authority"):
            connect(private, references=PayloadReferences(reader=foreign))


def test_maintenance_reads_shared_rows_and_preserves_trade_lineage_and_public_history(linked):
    from polybot_observability.sqlite_maintenance import (
        SQLiteMaintenancePolicy,
        SQLiteMaintenanceRequirements,
        _compact_connection,
    )

    connection, refs, _ = linked
    for timestamp in (
        "2026-08-01 00:00:00.000000",
        "2026-08-01 00:01:00.000000",
        "2026-08-02 00:00:00.000000",
        "2026-09-10 00:00:00.000000",
        "2026-09-10 00:10:00.000000",
        "2026-09-15 00:00:00.000000",
    ):
        put(connection, refs, timestamp=timestamp)
        connection.commit()
    connection.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY,entry_snapshot_id INTEGER)")
    connection.execute("INSERT INTO trades VALUES(1,2)")
    connection.commit()
    path = connection.execute("PRAGMA database_list").fetchone()[2]
    policy = SQLiteMaintenancePolicy("golden-date", 24, 1, 7, "latest", 1, 24)
    # A maintenance-style fresh handle must install the view before counting
    # snapshots and computing its protected entry/prior IDs.
    with connect(path, references=refs) as maintained:
        counts = _compact_connection(
            maintained,
            policy,
            SQLiteMaintenanceRequirements(),
            activate=True,
        )
        assert counts["snapshots"] == 6
        assert counts["snapshots_after"] == 4
        assert [
            row[0] for row in maintained.execute("SELECT id FROM market_snapshots ORDER BY id")
        ] == [1, 2, 5, 6]
        assert maintained.execute("SELECT entry_snapshot_id FROM trades").fetchone() == (2,)
        assert (
            len(
                list(
                    refs.reader.query_scalar_snapshots(
                        "condition-a",
                        limit=20,
                        authority_uuid=refs.reader.scalar_authority_identity(),
                    )
                )
            )
            == 6
        )
        backup = sqlite3.connect(":memory:", uri=True)
        try:
            maintained.backup(backup)
            backup.execute("VACUUM")
            from polybot_observability.market_data_scalar_links import install_scalar_views

            install_scalar_views(backup, references=refs)
            assert backup.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (4,)
            assert backup.execute("SELECT COUNT(*) FROM main.market_snapshots").fetchone() == (0,)
        finally:
            backup.close()

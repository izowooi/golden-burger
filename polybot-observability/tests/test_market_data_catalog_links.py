import json
import sqlite3
from types import SimpleNamespace

import pytest

from polybot_observability.market_data_catalog_links import (
    CONTEXT_TABLE,
    MissingCatalogRowError,
    catalog_layout_metadata,
    catalog_original_rowid,
    install_catalog_views,
    iter_catalog_link_rows,
    iter_catalog_private_rows,
    iter_catalog_record_ids,
    iter_catalog_rows,
    update_shared_catalog,
    upsert_shared_catalog,
    validate_catalog_layout,
)
from polybot_observability.market_data_catalog_profiles import (
    CATALOG_PROFILES,
    catalog_profile,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_market_data_catalog_profiles import model_columns


def namespace(strategy):
    return json.dumps(
        dict(source="fixture", jenkins_job="job", strategy=strategy, runtime="default"),
        sort_keys=True,
        separators=(",", ":"),
    )


def make_source(connection, strategy, *, nullable_legacy=False):
    fields = model_columns(strategy)
    clauses = []
    for name, kind, nullable, default, indexed in fields:
        affinity = {
            "String": "TEXT",
            "Integer": "INTEGER",
            "Float": "REAL",
            "DateTime": "DATETIME",
        }[kind]
        clause = name + " " + affinity
        if name == "condition_id":
            clause += " PRIMARY KEY"
        if not nullable and not nullable_legacy:
            clause += " NOT NULL"
        clauses.append(clause)
    connection.execute("CREATE TABLE market_catalog(" + ",".join(clauses) + ")")
    for name, _, _, _, indexed in fields:
        if indexed:
            connection.execute(f"CREATE INDEX catalog_{name} ON market_catalog({name})")
    connection.execute(
        "CREATE TABLE private_ledger(id INTEGER PRIMARY KEY, fee REAL, pnl REAL)"
    )
    connection.execute("INSERT INTO private_ledger VALUES(7,1.25,-43.12)")
    connection.commit()


def row(strategy, condition="condition"):
    profile = catalog_profile(strategy)
    values = dict.fromkeys(profile.column_names)
    values.update(
        condition_id=condition,
        market_id="market",
        market_slug="slug",
        question="Exact? 질문",
        event_id="event",
        event_slug="event-slug",
        end_date="2026-10-01T00:00:00+08:00",
        outcomes_json=' [ "YES", "NO" ] ',
        token_ids_json='["yes-token","no-token"]',
        tags_json="[]",
        fees_enabled=1,
        fee_rate=0.0123,
        first_seen_at="2026-01-01 00:00:00.000001",
        last_seen_at="2026-09-30 01:02:03.123456",
    )
    for name, value in dict(
        outcome_prices_json="[0.2, 0.8]",
        event_title="source event",
        event_market_count=3,
        neg_risk=1,
        active=1,
        closed=0,
        accepting_orders=1,
        enable_order_book=1,
        fee_exponent=2,
        fee_taker_only=1,
        followup_attempt_count=0,
        resolution_status="PRIVATE_PROOF",
        resolution_evidence_json="PRIVATE_EVIDENCE",
        source_updated_at="source-clock-lexical",
        sport_family="private-sport",
        league_code="private-league",
    ).items():
        if name in values:
            values[name] = value
    return values


@pytest.fixture
def stores(tmp_path):
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        yield SimpleNamespace(
            writer=writer,
            reader=reader,
            refs=PayloadReferences(reader=reader, writer=writer),
        )


@pytest.mark.parametrize("strategy", CATALOG_PROFILES)
def test_all_catalog_families_preserve_schema_rows_rowid_and_private_ledger(
    tmp_path, stores, strategy
):
    path = tmp_path / "source.db"
    with connect(path, references=stores.refs) as db:
        make_source(db, strategy)
        values = row(strategy)
        names = tuple(values)
        db.execute(
            "INSERT INTO market_catalog(rowid,"
            + ",".join(names)
            + ") VALUES(?,"
            + ",".join("?" for _ in names)
            + ")",
            (-7, *values.values()),
        )
        db.commit()
        schema = db.execute(
            "SELECT sql FROM main.sqlite_master WHERE name='market_catalog'"
        ).fetchone()[0]
        original = list(iter_catalog_rows(db, strategy, include_rowid=True))
        returned = upsert_shared_catalog(
            db,
            strategy,
            values,
            namespace=namespace(strategy),
            original_rowid=-7,
            references=stores.refs,
        )
        assert returned == values
        assert list(iter_catalog_rows(db, strategy, include_rowid=True)) == original
        assert db.execute("SELECT * FROM market_catalog").description and len(
            db.execute("SELECT * FROM market_catalog").description
        ) == len(names)
        assert db.execute("SELECT * FROM main.market_catalog").fetchall() == []
        assert (
            db.execute(
                "SELECT sql FROM main.sqlite_master WHERE name='market_catalog'"
            ).fetchone()[0]
            == schema
        )
        assert catalog_original_rowid(db, "condition") == -7
        assert db.execute(
            "SELECT catalog_rowid(condition_id) FROM market_catalog"
        ).fetchall() == [(-7,)]
        assert all(link[:2] == (-7, "condition") for link in iter_catalog_link_rows(db))
        assert len(list(iter_catalog_record_ids(db))) == 2
        assert list(
            iter_catalog_private_rows(
                db, strategy, ("__rowid__", "condition_id", "first_seen_at")
            )
        ) == [(-7, "condition", values["first_seen_at"])]
        assert db.execute("SELECT * FROM private_ledger").fetchall() == [
            (7, 1.25, -43.12)
        ]
        assert validate_catalog_layout(db)
        db.commit()
    with connect(path, references=stores.refs) as reopened:
        # Installer also works explicitly while the shared reader registration is pending.
        install_catalog_views(reopened, references=stores.refs)
        assert (
            list(iter_catalog_rows(reopened, strategy, include_rowid=True)) == original
        )


def test_state_versions_share_identity_and_dirty_update_merges_current_private_context(
    tmp_path, stores
):
    strategy = "golden-plum"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        initial = row(strategy)
        upsert_shared_catalog(
            db, strategy, initial, namespace=namespace(strategy), references=stores.refs
        )
        db.commit()
        before = list(iter_catalog_link_rows(db))
        # A separate bulk private update happened after an ORM object was loaded.
        db.execute(
            f"UPDATE {CONTEXT_TABLE} SET last_event_set_reason='new-health' WHERE condition_id='condition'"
        )
        result = update_shared_catalog(
            db,
            strategy,
            "condition",
            {"outcome_prices_json": "[0.4,0.6]", "fee_rate": 0.0234},
            references=stores.refs,
        )
        after = list(iter_catalog_link_rows(db))
        assert before[0] == after[0] and before[1][3] != after[1][3]
        assert (
            result["last_event_set_reason"] == "new-health"
            and result["fee_rate"] == 0.0234
        )
        assert result["active"] == 1
        assert stores.writer.projection_stats()["record_count"] == 3
        db.rollback()
        assert db.execute(
            "SELECT outcome_prices_json,fee_rate,last_event_set_reason FROM market_catalog"
        ).fetchone() == (initial["outcome_prices_json"], 0.0123, None)
        with pytest.raises(MissingCatalogRowError):
            update_shared_catalog(
                db, strategy, "missing", {"fee_rate": 0.5}, references=stores.refs
            )
        with pytest.raises(ValueError, match="condition key"):
            update_shared_catalog(
                db,
                strategy,
                "condition",
                {"condition_id": "changed"},
                references=stores.refs,
            )
        with pytest.raises(sqlite3.IntegrityError):
            upsert_shared_catalog(
                db,
                strategy,
                initial,
                namespace=namespace(strategy),
                insert_only=True,
                references=stores.refs,
            )


def test_missing_receipt_ack_rolls_back_local_pointer_and_retry_is_idempotent(
    tmp_path, stores, monkeypatch
):
    strategy = "golden-blueberry"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        original = stores.writer.append_projection_receipts

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("ACK lost")

        monkeypatch.setattr(stores.writer, "append_projection_receipts", lost)
        with pytest.raises(RuntimeError, match="ACK lost"):
            upsert_shared_catalog(
                db,
                strategy,
                row(strategy),
                namespace=namespace(strategy),
                references=stores.refs,
            )
        assert catalog_layout_metadata(db) is None
        assert db.execute("SELECT * FROM market_catalog").fetchall() == []
        public = stores.writer.projection_stats().copy()
        monkeypatch.setattr(stores.writer, "append_projection_receipts", original)
        upsert_shared_catalog(
            db,
            strategy,
            row(strategy),
            namespace=namespace(strategy),
            references=stores.refs,
        )
        assert stores.writer.projection_stats() == public
        assert catalog_original_rowid(db, "condition") == 1


def test_legacy_nullable_cells_and_sqlite_affinity_are_preserved(tmp_path, stores):
    strategy = "golden-honeydew"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy, nullable_legacy=True)
        values = row(strategy)
        values.update(
            tags_json=None,
            outcomes_json=None,
            first_seen_at=None,
            fees_enabled="1",
            fee_rate=1,
        )
        normalized = upsert_shared_catalog(
            db,
            strategy,
            values,
            namespace=namespace(strategy),
            references=stores.refs,
            original_rowid=0,
        )
        assert (
            normalized["fees_enabled"] == 1 and type(normalized["fees_enabled"]) is int
        )
        assert normalized["fee_rate"] == 1.0 and type(normalized["fee_rate"]) is float
        assert db.execute(
            "SELECT tags_json,outcomes_json,first_seen_at,typeof(fee_rate) FROM market_catalog"
        ).fetchone() == (None, None, None, "real")
        assert catalog_original_rowid(db, "condition") == 0


def test_catalog_indexed_identity_tampering_fails_on_read(tmp_path, stores):
    strategy = "golden-blueberry"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        upsert_shared_catalog(
            db,
            strategy,
            row(strategy),
            namespace=namespace(strategy),
            references=stores.refs,
        )
        db.commit()
        assert "source_" in str(
            db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM market_catalog WHERE event_id='event'"
            ).fetchall()
        )
        db.execute(f"UPDATE {CONTEXT_TABLE} SET event_id='tampered'")
        with pytest.raises(sqlite3.OperationalError, match="user-defined"):
            db.execute("SELECT * FROM market_catalog").fetchall()


def test_legacy_public_body_refs_resolve_before_typed_group_and_unknown_schema_fails(
    tmp_path, stores
):
    strategy = "golden-blueberry"
    with connect(tmp_path / "legacy.db", references=stores.refs) as db:
        make_source(db, strategy)
        values = row(strategy)
        original = values["outcomes_json"]
        values["outcomes_json"] = stores.refs.encode_many([original])[0]
        assert values["outcomes_json"] != original
        result = upsert_shared_catalog(
            db, strategy, values, references=stores.refs, namespace=namespace(strategy)
        )
        assert result["outcomes_json"] == original
        assert db.execute("SELECT outcomes_json FROM market_catalog").fetchone() == (
            original,
        )
        assert stores.writer.projection_stats()["record_count"] == 2
        db.commit()
        db.execute("ALTER TABLE main.market_catalog ADD COLUMN unreviewed_private TEXT")
        with pytest.raises(ValueError, match="schema differs"):
            validate_catalog_layout(db)


def test_caller_outer_transaction_rolls_back_catalog_with_other_private_changes(
    tmp_path, stores
):
    strategy = "golden-blueberry"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        db.execute("BEGIN")
        db.execute("INSERT INTO private_ledger VALUES(8,9.8,-12.5)")
        upsert_shared_catalog(
            db,
            strategy,
            row(strategy),
            references=stores.refs,
            namespace=namespace(strategy),
        )
        assert db.in_transaction
        db.rollback()
        assert catalog_layout_metadata(db) is None
        assert db.execute("SELECT count(*) FROM market_catalog").fetchone() == (0,)
        assert db.execute("SELECT * FROM private_ledger").fetchall() == [
            (7, 1.25, -43.12)
        ]
        assert stores.writer.projection_stats()["record_count"] == 2


def test_public_receipt_readback_mismatch_does_not_publish_local_context(
    tmp_path, stores, monkeypatch
):
    strategy = "golden-blueberry"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        monkeypatch.setattr(
            stores.reader, "get_projection_receipts", lambda *a, **k: []
        )
        with pytest.raises(ValueError, match="independent readback"):
            upsert_shared_catalog(
                db,
                strategy,
                row(strategy),
                references=stores.refs,
                namespace=namespace(strategy),
            )
        assert catalog_layout_metadata(db) is None
        assert not db.in_transaction


def test_original_rowid_conflict_does_not_replace_other_condition(tmp_path, stores):
    strategy = "golden-blueberry"
    with connect(tmp_path / "local.db", references=stores.refs) as db:
        make_source(db, strategy)
        upsert_shared_catalog(
            db,
            strategy,
            row(strategy),
            references=stores.refs,
            namespace=namespace(strategy),
            original_rowid=99,
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="rowid already exists"):
            upsert_shared_catalog(
                db,
                strategy,
                row(strategy, "other"),
                references=stores.refs,
                namespace=namespace(strategy),
                original_rowid=99,
            )
        with pytest.raises(ValueError, match="rowid changed"):
            upsert_shared_catalog(
                db,
                strategy,
                row(strategy),
                references=stores.refs,
                namespace=namespace(strategy),
                original_rowid=100,
            )
        assert catalog_original_rowid(db, "condition") == 99
        assert catalog_original_rowid(db, "other") is None

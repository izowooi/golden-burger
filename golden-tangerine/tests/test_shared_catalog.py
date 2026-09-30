"""Actual catalog ORM, raw readers and private transactions over public groups."""

import inspect as signature_inspect
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import polybot.db.repository as repository_module
import pytest
from polybot.db.models import MarketCatalog, Trade, init_database
from polybot.db.repository import TradeRepository
from polybot_observability.market_data_catalog_links import (
    CONTEXT_TABLE,
    iter_catalog_link_rows,
    iter_catalog_private_rows,
    update_shared_catalog,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

STRATEGY = "golden-tangerine"
NOW = datetime(2026, 9, 1, 12, 34, 56, 123456, tzinfo=timezone.utc).replace(tzinfo=None)


@pytest.fixture
def shared_catalog(tmp_path, monkeypatch):
    path = tmp_path / STRATEGY / "data/runtime-a/trades_sim.db"
    path.parent.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture-source")
    monkeypatch.setenv("JOB_NAME", "polybot-fixture")
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader=reader, writer=writer)
        for module in (
            "market_data_refs",
            "market_data_sqlalchemy",
            "market_data_sqlite",
            "market_data_projection_links",
            "market_data_scalar_links",
        ):
            monkeypatch.setattr(
                "polybot_observability." + module + ".configured_references",
                lambda: refs,
            )
        options = (
            {"activate_compact_on_create": False}
            if "activate_compact_on_create"
            in signature_inspect.signature(init_database).parameters
            else {}
        )
        factory = init_database(str(path), **options)
        session = factory()
        repo = TradeRepository(session)
        monkeypatch.setattr(repository_module, "current_run_id", lambda: "private-run")
        if STRATEGY == "golden-plum":
            monkeypatch.setattr(repo, "_current_config_hash", lambda: "private-config")
        value = SimpleNamespace(
            path=path,
            factory=factory,
            session=session,
            repo=repo,
            writer=writer,
            reader=reader,
            refs=refs,
            options=options,
        )
        try:
            yield value
        finally:
            session.close()
            factory.kw["bind"].dispose()


def market(condition="condition"):
    return {
        "conditionId": condition,
        "id": condition,
        "slug": "source-slug",
        "question": "source-question",
        "events": [
            {"id": "source-event", "slug": "source-event", "title": "Public event"}
        ],
        "outcomes": ["Yes", "No"],
        "outcomePrices": [0.4, 0.6],
        "clobTokenIds": [condition + "-yes", condition + "-no"],
        "tags": [],
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "negRisk": False,
        "feesEnabled": True,
        "feeSchedule": {"rate": 0.05, "exponent": 1, "takerOnly": True},
        "updatedAt": "source-provider-clock",
    }


def upsert(m, condition="condition", payload=None):
    m.repo._upsert_market_catalog(
        condition, market(condition) if payload is None else payload
    )
    m.session.flush()
    return m.session.get(MarketCatalog, condition)


def test_original_pending_object_defaults_are_evaluated_at_flush(
    shared_catalog, monkeypatch
):
    m = shared_catalog
    row = MarketCatalog(condition_id="condition")
    m.session.add(row)
    assert (
        inspect(row).pending and row.first_seen_at is None and row.last_seen_at is None
    )
    for name in ("first_seen_at", "last_seen_at"):
        monkeypatch.setattr(
            MarketCatalog.__table__.c[name].default, "arg", lambda context: NOW
        )
    m.session.flush()
    assert inspect(row).persistent and m.session.get(MarketCatalog, "condition") is row
    assert row.first_seen_at == NOW and row.last_seen_at == NOW
    with sqlite3.connect(m.path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_catalog").fetchone() == (0,)
        assert raw.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    m.session.rollback()
    assert inspect(row).transient and row.first_seen_at == NOW
    assert m.session.get(MarketCatalog, "condition") is None


def test_repository_null_semantics_reopen_and_original_raw_values(shared_catalog):
    m = shared_catalog
    row = upsert(m)
    first_seen = row.first_seen_at
    original_tokens = row.token_ids_json
    m.session.commit()
    upsert(m, payload={"conditionId": "condition"})
    assert m.session.get(MarketCatalog, "condition") is row
    assert row.question == ("source-question" if STRATEGY == "golden-plum" else None)
    assert row.first_seen_at == first_seen
    m.session.rollback()
    assert row.question == "source-question"
    with connect(m.path, references=m.refs) as connection:
        assert connection.execute(
            "SELECT condition_id, token_ids_json, fees_enabled, fee_rate FROM market_catalog"
        ).fetchone() == ("condition", original_tokens, 1, 0.05)
        links = list(iter_catalog_link_rows(connection))
        records = m.reader.get_projection_records(
            [link[-1] for link in links], m.reader.scalar_authority_identity()
        )
        assert "private-config" not in repr(records) and "private-run" not in repr(
            records
        )
        assert next(
            iter_catalog_private_rows(connection, STRATEGY, ("first_seen_at",))
        )[0] == first_seen.strftime("%Y-%m-%d %H:%M:%S.%f")
    with sqlite3.connect(m.path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_catalog").fetchone() == (0,)
        assert raw.execute(f"SELECT COUNT(*) FROM {CONTEXT_TABLE}").fetchone() == (1,)
    reopened = init_database(str(m.path), **m.options)
    with reopened() as session:
        assert session.get(MarketCatalog, "condition").question == "source-question"
    reopened.kw["bind"].dispose()


def test_dirty_nested_rollback_and_outer_rollback_recover_same_object(shared_catalog):
    m = shared_catalog
    row = upsert(m)
    m.session.commit()
    row.question = "outer-change"
    m.session.flush()
    nested = m.session.begin_nested()
    row.question = "nested-change"
    m.session.flush()
    nested.rollback()
    assert row.question == "outer-change" and inspect(row).persistent
    with m.session.begin_nested():
        row.question = "merged-change"
        m.session.flush()
    assert row.question == "merged-change"
    m.session.rollback()
    assert (
        row.question == "source-question"
        and m.session.get(MarketCatalog, "condition") is row
    )


def test_only_changed_fields_publish_and_missing_row_does_not_resurrect(shared_catalog):
    m = shared_catalog
    row = upsert(m)
    m.session.commit()
    assert row.fee_rate == 0.05
    connection = m.session.connection().connection.driver_connection
    update_shared_catalog(
        connection, STRATEGY, "condition", {"fee_rate": 0.2}, references=m.refs
    )
    row.question = "new-source-question"
    m.session.flush()
    assert connection.execute(
        "SELECT fee_rate,question FROM market_catalog"
    ).fetchone() == (0.2, "new-source-question")
    m.session.commit()
    assert row.fee_rate == 0.2
    m.session.execute(
        text(f"DELETE FROM main.{CONTEXT_TABLE} WHERE condition_id='condition'")
    )
    row.question = "must-not-resurrect"
    with pytest.raises(StaleDataError):
        m.session.flush()
    m.session.rollback()
    assert row.question == "new-source-question"
    row.condition_id = "other-condition"
    with pytest.raises(ValueError, match="primary key is immutable"):
        m.session.flush()
    m.session.rollback()
    assert row.condition_id == "condition"


def test_later_ack_failure_restores_dirty_histories_and_new_pending_objects(
    shared_catalog, monkeypatch
):
    m = shared_catalog
    first = upsert(m, "first")
    second = upsert(m, "second")
    m.session.commit()
    first.question = "first-update"
    second.question = "second-update"
    original = m.writer.put_public_projections
    count = 0

    def fail_second(values):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("second catalog ACK failed")
        return original(values)

    monkeypatch.setattr(m.writer, "put_public_projections", fail_second)
    with pytest.raises(RuntimeError, match="second catalog ACK"):
        m.session.flush()
    assert first.question == "first-update" and second.question == "second-update"
    assert inspect(first).attrs.question.history.has_changes()
    assert inspect(second).attrs.question.history.has_changes()
    with m.session.no_autoflush:
        assert m.session.execute(
            text("SELECT DISTINCT question FROM market_catalog")
        ).scalars().all() == ["source-question"]
    m.session.rollback()
    assert first.question == "source-question" and second.question == "source-question"
    monkeypatch.setattr(m.writer, "put_public_projections", original)
    first.question = "retry-update"
    m.session.commit()
    assert first.question == "retry-update"


def test_private_orm_failure_reverts_catalog_insert_and_update(shared_catalog):
    m = shared_catalog
    old = upsert(m)
    m.session.commit()
    old.question = "failed-change"
    new = MarketCatalog(condition_id="new")
    m.session.add_all([new, Trade(condition_id="private-invalid", token_id=None)])
    with pytest.raises(IntegrityError):
        m.session.flush()
    m.session.rollback()
    assert old.question == "source-question"
    assert inspect(new).transient
    assert m.session.get(MarketCatalog, "new") is None
    assert m.session.query(Trade).count() == 0

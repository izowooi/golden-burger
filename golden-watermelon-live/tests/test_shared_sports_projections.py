"""Actual sports repository/query paths using public projections and private evidence."""

import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import polybot.db.repository as repository_module
from polybot.config import TradingConfig
from polybot.db.models import MarketSnapshot, MarketSweep, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE,
    iter_projection_link_rows,
    iter_projection_private_rows,
    update_projection_private,
)
from polybot_observability.market_data_projection_profiles import projection_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore

STRATEGY = "golden-watermelon-live"
NOW = datetime(2026, 9, 1, 12, 34, 56, 123456)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    path = tmp_path / STRATEGY / "data" / "runtime-a" / "trades_sim.db"
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
        Session = init_database(str(path), activate_compact_on_create=False)
        session = Session()
        repo = TradeRepository(session)
        fixture = SimpleNamespace(
            path=path,
            session=session,
            Session=Session,
            repo=repo,
            writer=writer,
            reader=reader,
            refs=refs,
            run_id="private-run",
            config_hash="private-config",
        )
        monkeypatch.setattr(repository_module, "current_run_id", lambda: fixture.run_id)
        if STRATEGY == "golden-plum":
            monkeypatch.setattr(
                repo, "_current_config_hash", lambda: fixture.config_hash
            )
        try:
            yield fixture
        finally:
            session.close()
            Session.kw["bind"].dispose()


def save(
    m,
    *,
    condition="condition",
    token="yes-token",
    probability=0.6312345,
    timestamp=NOW,
    commit=True,
    **extra,
):
    supplied = dict(
        condition_id=condition,
        token_id=token,
        outcome="Yes",
        probability=probability,
        liquidity=1234.0,
        volume_24h=2345.0,
        best_bid=0.70,
        best_ask=0.72,
        spread=0.02,
        source_updated_at="provider-source-clock",
        timestamp=timestamp,
        commit=commit,
    )
    optional = dict(
        event_id="event",
        outcome_side="YES",
        result_kind="HOME",
        midpoint=0.71,
        source_elapsed_minutes=12.5,
        source_clock_reason="private-clock-evidence",
        book_json='{"asset_id":"yes-token","bids":[{"price":".70","size":"40"}],"asks":[]}',
        execution_capacity_json='{"private_capacity":12345}',
        sport_family="soccer",
        league_code="private-fallback-league",
        league_name="private-fallback-name",
        market_tags_json='["public-tag"]',
        sport_profile_version="private-profile",
        book_shape="private-book-shape",
        event_cycle_id="private-event-cycle",
        event_set_complete=False,
        event_set_reason="private-pending",
    )
    if STRATEGY == "golden-plum":
        optional["evidence_context"] = dict(
            sport_family="soccer",
            sport_profile_version="private-profile",
            protocol_sha256="private-protocol",
            classifier_version="private-classifier",
            league_mapping_sha256="private-mapping",
            strategy_source_digest="private-source",
            book_shape="private-book-shape",
        )
    allowed = inspect.signature(m.repo.save_snapshot).parameters
    supplied.update({key: value for key, value in optional.items() if key in allowed})
    supplied.update(extra)
    return m.repo.save_snapshot(**supplied)


def test_vwap_clock_run_and_sizing_stay_private_with_token_aligned_reads(shared):
    m = shared
    first = save(m, commit=False)
    assert first in m.session and first.id == 1 and first.timestamp == NOW
    assert first.probability == 0.6312345 and first.run_id == "private-run"
    m.run_id = "next-private-run"
    second = save(
        m, probability=0.7123456, timestamp=NOW + timedelta(minutes=1), commit=False
    )
    opposite = save(
        m,
        token="no-token",
        probability=0.2876544,
        timestamp=NOW + timedelta(minutes=2),
        commit=False,
    )
    ids = (first.id, second.id, opposite.id)
    with sqlite3.connect(m.path) as outside:
        assert outside.execute("SELECT count(*) FROM market_snapshots").fetchone() == (
            0,
        )
        assert not outside.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (CONTEXT_TABLE,)
        ).fetchone()
    m.repo.commit()
    assert [row.id for row in m.repo.get_snapshots_since("condition", NOW)] == list(ids)
    assert m.repo.get_latest_snapshot("condition").id == opposite.id
    assert (
        m.session.query(MarketSnapshot)
        .filter(MarketSnapshot.token_id == "yes-token")
        .count()
        == 2
    )
    with connect(m.path, references=m.refs) as db:
        links = list(iter_projection_link_rows(db))
        assert links[0][2] == links[1][2]
        original_private = list(
            iter_projection_private_rows(
                db, STRATEGY, ("id", "probability", "run_id", "timestamp")
            )
        )
        assert original_private[0] == (
            1,
            0.6312345,
            "private-run",
            "2026-09-01 12:34:56.123456",
        )
        assert db.execute("SELECT count(*) FROM main.market_snapshots").fetchone() == (
            0,
        )
        record = m.reader.get_projection_records(
            [links[0][2]], m.reader.scalar_authority_identity()
        )[0]
        public = dict(
            zip(
                projection_profile(record.projection.kind).columns,
                record.projection.values,
            )
        )
        assert (
            "probability" not in public
            and "timestamp" not in public
            and "run_id" not in public
        )
        assert public["best_bid"] == 0.70 and public["best_ask"] == 0.72
        assert public["source_updated_at"] == "provider-source-clock"
        assert "private_capacity" not in json.dumps(record.to_wire())
        if hasattr(first, "execution_capacity_json"):
            assert db.execute(
                "SELECT execution_capacity_json FROM market_snapshots WHERE id=1"
            ).fetchone() == ('{"private_capacity":12345}',)
            assert db.execute(
                "SELECT midpoint FROM market_snapshots WHERE id=1"
            ).fetchone() == (0.71,)
    reopened = init_database(str(m.path), activate_compact_on_create=False)
    with reopened() as session:
        assert (
            TradeRepository(session).get_latest_snapshot("condition").id == opposite.id
        )
    reopened.kw["bind"].dispose()


def test_private_clock_update_and_transaction_rollback_do_not_mutate_public_groups(
    shared,
):
    m = shared
    row = save(m)
    before = m.writer.projection_stats().copy()
    shifted = NOW - timedelta(minutes=8)
    assert (
        update_projection_private(
            m.session,
            MarketSnapshot,
            STRATEGY,
            row.id,
            {"timestamp": shifted},
            commit=False,
        )
        == 1
    )
    assert m.repo.get_latest_snapshot("condition").timestamp == shifted
    m.repo.rollback()
    assert m.repo.get_latest_snapshot("condition").timestamp == NOW
    pending = save(m, condition="rollback", commit=False)
    assert pending.id == 2
    m.repo.rollback()
    assert m.session.query(MarketSnapshot).count() == 1
    assert m.writer.projection_stats()["record_count"] >= before["record_count"]
    assert m.repo.get_latest_snapshot("condition").probability == 0.6312345


def test_protected_retention_keeps_trade_snapshot_pair_and_never_deletes_shared_receipts(
    shared,
):
    m = shared
    old = datetime.utcnow() - timedelta(days=61)
    prior = save(m, probability=0.62, timestamp=old)
    prior_id = prior.id
    entry = save(m, probability=0.73, timestamp=old + timedelta(minutes=1))
    entry_id = entry.id
    expired = save(m, condition="expired", timestamp=old)
    expired_id = expired.id
    m.repo.create_trade(
        condition_id="condition",
        token_id="yes-token",
        outcome="Yes",
        prior_snapshot_id_at_entry=prior_id,
        entry_snapshot_id=entry_id,
    )
    ledger = m.session.execute(text("SELECT * FROM trades")).fetchall()
    public = m.writer.projection_stats().copy()
    assert m.repo.cleanup_old_snapshots(days=60) == 1
    assert {row.id for row in m.session.query(MarketSnapshot)} == {prior_id, entry_id}
    assert expired_id not in {row.id for row in m.session.query(MarketSnapshot)}
    assert m.session.execute(text("SELECT * FROM trades")).fetchall() == ledger
    assert m.writer.projection_stats() == public


def test_scanner_supplies_its_existing_observation_clock_before_publication(shared):
    from . import test_scanner as fixture

    m = shared
    if STRATEGY == "golden-tangerine":
        markets = [fixture._market()]
        gamma = fixture._Gamma()
        clob = fixture._Clob(0.945, 0.055)
    elif STRATEGY == "golden-watermelon-live":
        markets = [fixture._market()]
        gamma = fixture._Gamma(tuple(item["conditionId"] for item in markets))
        clob = fixture._Clob(
            {"yes-condition-1": fixture._walk("yes-condition-1", 0.985)}
        )
    else:
        markets = fixture._triad()
        gamma = fixture._Gamma(markets)
        clob = fixture._Clob(fixture._walks())
    scanner = MarketScanner(gamma, TradingConfig(), m.repo, clob_client=clob)
    original = m.repo.save_snapshot
    captured = []

    def checked(*args, **kwargs):
        assert "timestamp" in kwargs
        captured.append(kwargs["timestamp"])
        return original(*args, **kwargs)

    m.repo.save_snapshot = checked
    count = scanner.save_market_snapshots(
        markets, now=fixture.NOW.astimezone(timezone(timedelta(hours=9)))
    )
    assert count > 0 and len(captured) == count
    assert captured == [fixture.NOW.replace(tzinfo=None)] * count
    assert {row.timestamp for row in m.session.query(MarketSnapshot)} == {
        fixture.NOW.replace(tzinfo=None)
    }
    assert m.session.query(MarketSweep).count() == 1
    with sqlite3.connect(m.path) as raw:
        assert raw.execute("SELECT count(*) FROM market_snapshots").fetchone() == (0,)
        assert raw.execute("SELECT count(*) FROM " + CONTEXT_TABLE).fetchone() == (
            count,
        )


def test_repository_default_is_private_observation_time_not_source_time(shared):
    before = datetime.utcnow()
    row = save(shared, timestamp=None, source_updated_at=None)
    assert before <= row.timestamp <= datetime.utcnow()
    assert row.source_updated_at is None
    with connect(shared.path, references=shared.refs) as database:
        link = next(iter_projection_link_rows(database))
    record = shared.reader.get_projection_records(
        [link[2]], shared.reader.scalar_authority_identity()
    )[0]
    public = dict(
        zip(
            projection_profile(record.projection.kind).columns, record.projection.values
        )
    )
    assert public["source_updated_at"] is None
    assert "timestamp" not in public

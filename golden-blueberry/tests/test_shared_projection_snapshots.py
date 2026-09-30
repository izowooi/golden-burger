"""Blueberry's actual storage/query/sweep paths over shared public groups."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import polybot.db.repository as repository_module
import pytest
from polybot.api.gamma_client import GammaClient
from polybot.config import TradingConfig
from polybot.db.models import MarketSnapshot, MarketSweep, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE,
    iter_projection_link_rows,
    iter_projection_private_rows,
    update_private_projection_snapshot,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from polybot_observability.sqlite_maintenance import (
    SQLiteMaintenancePolicy,
    SQLiteMaintenanceRequirements,
    _compact_connection,
)
from sqlalchemy import text

from .test_market_catalog import NOW, _KeysetSession, market


@pytest.fixture
def shared(tmp_path, monkeypatch):
    path = tmp_path / "golden-blueberry/data/runtime-a/trades_sim.db"
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
                "polybot_observability." + module + ".configured_references", lambda: refs
            )
        Session = init_database(str(path), activate_compact_on_create=False)
        session = Session()
        value = SimpleNamespace(
            path=path,
            Session=Session,
            session=session,
            repo=TradeRepository(session),
            writer=writer,
            reader=reader,
            refs=refs,
            run_id="private-run",
        )
        monkeypatch.setattr(repository_module, "current_run_id", lambda: value.run_id)
        try:
            yield value
        finally:
            session.close()
            Session.kw["bind"].dispose()


def save(m, condition="condition", probability=0.86, *, timestamp=None, commit=True):
    return m.repo.save_snapshot(
        condition,
        probability,
        liquidity=200000.0,
        volume_24h=200000.0,
        best_bid=0.84,
        best_ask=0.86,
        spread=0.02,
        source_updated_at="2026-07-14T00:00:00Z",
        timestamp=timestamp or NOW.replace(tzinfo=None),
        commit=commit,
    )


def test_full_queries_private_run_clock_and_reopen_preserve_original_semantics(shared):
    m = shared
    first_time = NOW.replace(tzinfo=None)
    first = save(m, timestamp=first_time, commit=False)
    assert first in m.session and first.id == 1
    assert first.timestamp == first_time and first.run_id == "private-run"
    assert m.repo.get_latest_snapshot("condition").id == first.id
    m.run_id = "next-private-run"
    second = save(m, timestamp=first_time + timedelta(minutes=5), commit=False)
    assert m.repo.get_latest_snapshot("condition").id == second.id
    assert (
        m.repo.get_latest_snapshot_before_run("condition", run_id="next-private-run").id == first.id
    )
    assert [row.id for row in m.repo.get_snapshots_since("condition", first_time)] == [1, 2]
    with sqlite3.connect(m.path) as other:
        assert other.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (0,)
        assert not other.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (CONTEXT_TABLE,)
        ).fetchone()
    m.repo.commit()
    with connect(m.path, references=m.refs) as reader:
        rows = reader.execute(
            "SELECT id,condition_id,probability,liquidity,volume_24h,best_bid,best_ask,spread,"
            "source_updated_at,run_id,timestamp FROM market_snapshots ORDER BY id"
        ).fetchall()
        assert rows[0] == (
            1,
            "condition",
            0.86,
            200000.0,
            200000.0,
            0.84,
            0.86,
            0.02,
            "2026-07-14T00:00:00Z",
            "private-run",
            "2026-07-14 00:00:00.000000",
        )
        assert rows[1][-2:] == ("next-private-run", "2026-07-14 00:05:00.000000")
        links = list(iter_projection_link_rows(reader))
        assert links[0][2] == links[1][2]
        plan = [
            row[3]
            for row in reader.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM market_snapshots "
                "WHERE condition_id=? AND timestamp>? ORDER BY timestamp",
                ("condition", "2026-01-01"),
            )
        ]
        assert any("_public_projection_condition_time" in detail for detail in plan), plan
    # Startup ALTER/INDEX maintenance must target original main tables, not the TEMP view.
    reopened = init_database(str(m.path), activate_compact_on_create=False)
    with reopened() as session:
        assert TradeRepository(session).get_latest_snapshot("condition").id == 2
    reopened.kw["bind"].dispose()


def test_scanner_passes_final_observation_clock_before_publication(shared):
    m = shared
    gamma = GammaClient()
    gamma.session = _KeysetSession([market("condition")])
    scanner = MarketScanner(gamma, TradingConfig(), repo=m.repo)
    qualified = scanner.fetch_markets()
    observed = NOW.astimezone(timezone(timedelta(hours=9)))
    assert scanner.save_market_snapshots(qualified, now=observed) == 1
    stored = m.repo.get_latest_snapshot("condition")
    assert stored.timestamp == NOW.replace(tzinfo=None)
    assert stored.source_updated_at == "2026-07-14T00:00:00Z"
    assert m.session.query(MarketSweep).count() == 1
    with sqlite3.connect(m.path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_snapshots").fetchone() == (0,)
        assert list(iter_projection_private_rows(raw, "golden-blueberry", ("run_id", "timestamp")))[
            0
        ] == (
            "private-run",
            "2026-07-14 00:00:00.000000",
        )


def test_failed_sweep_rolls_back_catalog_snapshot_context_and_sweep(shared, monkeypatch):
    m = shared
    gamma = GammaClient()
    gamma.session = _KeysetSession([market("first"), market("second")])
    scanner = MarketScanner(gamma, TradingConfig(), repo=m.repo)
    real = m.writer.put_public_projections
    calls = []

    def fail_second(values):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("fixture group publication failure")
        return real(values)

    monkeypatch.setattr(m.writer, "put_public_projections", fail_second)
    with pytest.raises(RuntimeError, match="fixture group publication"):
        scanner.save_market_snapshots(scanner.fetch_markets(), now=NOW)
    assert m.session.query(MarketSnapshot).count() == 0
    assert m.session.query(MarketSweep).count() == 0
    assert m.session.execute(text("SELECT COUNT(*) FROM market_catalog")).scalar() == 0


def test_private_timestamp_update_rollback_does_not_rewrite_public_group(shared):
    m = shared
    snapshot = save(m)
    with connect(m.path, references=m.refs) as connection:
        links = list(iter_projection_link_rows(connection))
    shifted = NOW.replace(tzinfo=None) - timedelta(minutes=5)
    assert m.repo.update_snapshot_private(snapshot.id, timestamp=shifted) == 1
    assert m.repo.get_latest_snapshot("condition").timestamp == shifted
    m.repo.rollback()
    assert m.repo.get_latest_snapshot("condition").timestamp == NOW.replace(tzinfo=None)
    with connect(m.path, references=m.refs) as connection:
        assert list(iter_projection_link_rows(connection)) == links
        with pytest.raises(ValueError, match="immutable or public"):
            update_private_projection_snapshot(
                connection,
                "golden-blueberry",
                snapshot.id,
                {"probability": 0.99},
                references=m.refs,
            )


@pytest.mark.parametrize("maintenance", [False, True])
def test_retention_and_rollup_preserve_exact_trade_pair_and_private_ledger(shared, maintenance):
    m = shared
    old = datetime.utcnow() - timedelta(days=61)
    prior = save(m, probability=0.83, timestamp=old)
    prior_id = prior.id
    entry = save(m, probability=0.86, timestamp=old + timedelta(minutes=5))
    entry_id = entry.id
    save(m, condition="unrelated", timestamp=old)
    m.repo.create_trade(
        condition_id="condition",
        token_id="private-token",
        outcome="Yes",
        prior_snapshot_id_at_entry=prior_id,
        entry_snapshot_id=entry_id,
    )
    before = m.session.execute(text("SELECT * FROM trades")).fetchall()
    if maintenance:
        save(m, condition="recent", timestamp=datetime.utcnow())
        m.session.rollback()
        with connect(m.path, references=m.refs) as connection:
            report = _compact_connection(
                connection,
                SQLiteMaintenancePolicy("golden-blueberry", 1, 12, 60, "extrema", 1, 24),
                SQLiteMaintenanceRequirements(),
                activate=True,
            )
            assert report["snapshots"] == 4 and report["snapshots_after"] == 3
        m.session.expire_all()
    else:
        assert m.repo.cleanup_old_snapshots(days=60) == 1
    remaining = {row.id for row in m.session.query(MarketSnapshot)}
    assert prior_id in remaining and entry_id in remaining and 3 not in remaining
    assert m.session.execute(text("SELECT * FROM trades")).fetchall() == before

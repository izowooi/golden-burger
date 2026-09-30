import sqlite3
from datetime import datetime, timedelta

from polybot.db.models import MarketSnapshot, init_database
from polybot.db.repository import TradeRepository
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def test_shared_repository_save_query_retention_and_private_state(tmp_path, monkeypatch):
    public = tmp_path / "public.db"
    database = tmp_path / "golden-date/data/runtime-a/trades_sim.db"
    database.parent.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture-source")
    monkeypatch.setenv("JOB_NAME", "polybot-fixture")
    with PayloadStore(public) as writer, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=writer)
        monkeypatch.setattr(
            "polybot_observability.market_data_scalar_links.configured_references", lambda: refs
        )
        Session = init_database(str(database))
        session = Session()
        repo = TradeRepository(session)
        first = repo.save_snapshot("condition", 0.8, liquidity=100, volume_24h=None)
        assert isinstance(first, MarketSnapshot) and first.id == 1
        assert isinstance(first.timestamp, datetime)
        assert first.probability == 0.8 and first.liquidity == 100.0 and first.volume_24h is None
        second = repo.save_snapshot("condition", 0.81, liquidity=None, volume_24h=200)
        assert second.id == 2
        assert repo.get_latest_snapshot("condition").id == 2
        assert [
            row.id
            for row in repo.get_snapshots_since("condition", datetime.utcnow() - timedelta(days=1))
        ] == [1, 2]
        with sqlite3.connect(database) as raw:
            assert raw.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
            assert raw.execute("SELECT COUNT(*) FROM _public_scalar_links").fetchone()[0] == 2
            assert raw.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        # A negative retention interval deliberately selects both fresh fixture rows.
        assert repo.cleanup_old_snapshots(days=-1) == 2
        assert repo.get_latest_snapshot("condition") is None
        third = repo.save_snapshot("condition", 0.82)
        assert third.id == 1  # The original SQLAlchemy schema does not use AUTOINCREMENT.
        assert (
            len(
                list(
                    reader.query_scalar_snapshots(
                        "condition", limit=20, authority_uuid=reader.scalar_authority_identity()
                    )
                )
            )
            == 3
        )
        session.close()
        Session.kw["bind"].dispose()


def test_legacy_repository_stays_inline_without_public_configuration(tmp_path, monkeypatch):
    for name in (
        "PUBLIC_MARKET_DATA_DB",
        "PUBLIC_MARKET_DATA_SOCKET",
        "PUBLIC_MARKET_DATA_REQUIRED",
        "PUBLIC_MARKET_DATA_SOURCE",
        "JOB_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    Session = init_database(str(tmp_path / "legacy.db"))
    session = Session()
    row = TradeRepository(session).save_snapshot("legacy", 0.2)
    assert row.id == 1 and row.probability == 0.2
    with sqlite3.connect(tmp_path / "legacy.db") as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert not raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name='_public_scalar_links'"
        ).fetchone()
    session.close()
    Session.kw["bind"].dispose()

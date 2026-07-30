"""Atomic Gamma catalog/sweep and buffered archive evidence."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from polybot.config import TradingConfig
from polybot.db.models import MarketCatalog, MarketSnapshot, MarketSweep, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def make_market(condition="c1", probability=0.40):
    return {
        "id": condition,
        "conditionId": condition,
        "slug": condition,
        "question": condition,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [probability, 1 - probability],
        "clobTokenIds": [f"{condition}-yes", f"{condition}-no"],
        "negRisk": False,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "liquidity": 30_000,
        "volume24hr": 12_000,
        "bestBid": probability - 0.005,
        "bestAsk": probability + 0.005,
        "spread": 0.01,
        "endDate": (NOW + timedelta(hours=8)).isoformat(),
        "events": [{"id": "event-1", "slug": "event-1"}],
        "tags": [],
        "updatedAt": NOW.isoformat(),
    }


def attestation(markets):
    memberships = [
        {
            "condition_id": market["conditionId"],
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        }
        for market in markets
    ]
    memberships.sort(key=lambda row: row["condition_id"])
    digest = hashlib.sha256(
        json.dumps(
            memberships,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "sweep_id": "sweep-1",
        "started_at": (NOW - timedelta(seconds=1)).isoformat(),
        "completed_at": NOW.isoformat(),
        "cursor_complete": True,
        "pages": 1,
        "raw_market_count": len(markets),
        "unique_condition_count": len(markets),
        "qualified_market_count": len(markets),
        "excluded_condition_count": 0,
        "exclusion_counts": {},
        "missing_condition_id_count": 0,
        "duplicate_raw_count": 0,
        "min_liquidity": 1000.0,
        "min_volume": 0.0,
        "membership_digest_sha256": digest,
        "membership_digest_scope": "qualified_only",
        "memberships": memberships,
    }


class Gamma:
    def __init__(self, markets):
        self.last_sweep_attestation = attestation(markets)


def repo_for(tmp_path):
    session = init_database(str(tmp_path / "kiwi.db"))()
    return session, TradeRepository(session)


def test_catalog_snapshot_and_sweep_commit_as_one_unit(tmp_path):
    markets = [make_market()]
    session, repo = repo_for(tmp_path)
    scanner = MarketScanner(Gamma(markets), TradingConfig(), repo)
    assert scanner.save_market_snapshots(markets, now=NOW) == 1
    assert session.query(MarketCatalog).count() == 1
    assert session.query(MarketSnapshot).count() == 1
    snapshot = session.query(MarketSnapshot).one()
    assert snapshot.catalog_event_id == "event-1"
    assert json.loads(snapshot.catalog_outcomes_json) == ["Yes", "No"]
    assert json.loads(snapshot.catalog_outcome_prices_json) == [0.4, 0.6]
    assert json.loads(snapshot.catalog_token_ids_json) == ["c1-yes", "c1-no"]
    assert json.loads(snapshot.catalog_tags_json) == []
    assert snapshot.catalog_neg_risk == 0
    assert snapshot.catalog_end_date == markets[0]["endDate"]
    sweep = session.query(MarketSweep).one()
    assert sweep.cursor_complete == 1
    assert sweep.snapshot_eligible_count == 1
    assert sweep.snapshotted_market_count == 1
    session.close()


def test_page_receipt_clock_is_used_without_a_forced_test_clock(tmp_path):
    markets = [make_market()]
    observed = NOW - timedelta(minutes=2)
    markets[0]["_gammaObservedAt"] = observed.isoformat()
    session, repo = repo_for(tmp_path)
    scanner = MarketScanner(Gamma(markets), TradingConfig(), repo)

    assert scanner.save_market_snapshots(markets) == 1
    snapshot = session.query(MarketSnapshot).one()
    assert snapshot.timestamp == observed.replace(tzinfo=None)
    session.close()


def test_missing_page_receipt_clock_fails_closed_in_runtime_mode(tmp_path):
    markets = [make_market()]
    session, repo = repo_for(tmp_path)
    scanner = MarketScanner(Gamma(markets), TradingConfig(), repo)

    with pytest.raises(ValueError, match="page observation clock"):
        scanner.save_market_snapshots(markets)
    assert session.query(MarketSnapshot).count() == 0
    session.close()


@pytest.mark.parametrize("probability", [0.16, 0.84])
def test_buffered_archive_probability_boundaries_are_inclusive(
    tmp_path, probability
):
    markets = [make_market(probability=probability)]
    session, repo = repo_for(tmp_path)
    scanner = MarketScanner(Gamma(markets), TradingConfig(), repo)
    assert scanner.save_market_snapshots(markets, now=NOW) == 1
    session.close()


def test_no_inferred_history_backfill_client_is_used(tmp_path):
    markets = [make_market()]
    session, repo = repo_for(tmp_path)

    class ForbiddenHistory:
        def __getattr__(self, _name):
            raise AssertionError("Micro-Cascade must never backfill lineage")

    scanner = MarketScanner(
        Gamma(markets), TradingConfig(), repo, history_client=ForbiddenHistory()
    )
    assert scanner.save_market_snapshots(markets, now=NOW) == 1
    session.close()


def test_tampered_digest_rolls_back_catalog_snapshot_and_sweep(tmp_path):
    markets = [make_market()]
    gamma = Gamma(markets)
    gamma.last_sweep_attestation["membership_digest_sha256"] = "0" * 64
    session, repo = repo_for(tmp_path)
    scanner = MarketScanner(gamma, TradingConfig(), repo)
    with pytest.raises(ValueError, match="digest mismatch"):
        scanner.save_market_snapshots(markets, now=NOW)
    assert session.query(MarketCatalog).count() == 0
    assert session.query(MarketSnapshot).count() == 0
    assert session.query(MarketSweep).count() == 0
    session.close()


def test_missing_completed_attestation_fails_before_any_write(tmp_path):
    markets = [make_market()]
    session, repo = repo_for(tmp_path)

    class MissingGamma:
        last_sweep_attestation = None

    scanner = MarketScanner(MissingGamma(), TradingConfig(), repo)
    with pytest.raises(RuntimeError, match="attestation"):
        scanner.save_market_snapshots(markets, now=NOW)
    assert session.query(MarketCatalog).count() == 0
    assert session.query(MarketSnapshot).count() == 0
    session.close()

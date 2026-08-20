from datetime import datetime, timedelta, timezone

from polybot.api.clob_client import BuyBookWalk
from polybot.config import TangerineEntryConfig, TradingConfig
from polybot.db.models import EntryEpisode, MarketSnapshot, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)


def _market():
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "slug": "sports-market",
        "question": "Will the home team win?",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": "20000",
        "liquidityNum": 20_000,
        "volume": "8000",
        "volumeNum": 8_000,
        "volume24hr": "1200",
        "endDate": (NOW + timedelta(hours=3)).isoformat(),
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.94", "0.06"],
        "clobTokenIds": ["yes-token", "no-token"],
        "negRisk": False,
        "events": [{"id": "event-1", "slug": "event-1", "title": "Game"}],
        "tags": [{"slug": "sports", "label": "Sports"}],
    }


def _walk(token, vwap, bid, ask):
    return BuyBookWalk(token, bid, ask, ask - bid, vwap, 5 / vwap, 5, ask, 1)


class _Gamma:
    def __init__(self, sweep="sweep-1"):
        self.last_sweep_attestation = self.proof(sweep)

    @staticmethod
    def proof(sweep):
        import hashlib
        import json

        membership = {
            "condition_id": "condition-1",
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        }
        digest = hashlib.sha256(
            json.dumps([membership], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "schema_version": 1,
            "sweep_id": sweep,
            "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "cursor_complete": True,
            "pages": 1,
            "raw_market_count": 1,
            "unique_condition_count": 1,
            "qualified_market_count": 1,
            "excluded_condition_count": 0,
            "exclusion_counts": {},
            "missing_condition_id_count": 0,
            "duplicate_raw_count": 0,
            "min_liquidity": 10_000,
            "min_volume": 5_000,
            "membership_digest_sha256": digest,
            "membership_digest_scope": "qualified_only",
            "memberships": [membership],
        }


class _Clob:
    def __init__(self, yes_vwap, no_vwap):
        self.walks = {
            "yes-token": _walk("yes-token", yes_vwap, 0.93, 0.94),
            "no-token": _walk("no-token", no_vwap, 0.05, 0.06),
        }

    def get_buy_book_walks(self, token_ids, *, notional_usdc):
        assert notional_usdc == 5
        return {token: self.walks[token] for token in token_ids}


def _scanner(tmp_path, config, clob):
    Session = init_database(str(tmp_path / "scanner.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = _Gamma()
    return session, repo, gamma, MarketScanner(gamma, config, repo, clob_client=clob)


def test_arm_a_claims_only_first_exact_book_observation(tmp_path) -> None:
    config = TradingConfig()
    session, _repo, gamma, scanner = _scanner(tmp_path, config, _Clob(0.945, 0.055))
    markets = [_market()]

    assert scanner.save_market_snapshots(markets, now=NOW) == 2
    first = scanner.scan_buy_candidates(markets, now=NOW)
    assert len(first) == 1
    assert first[0]["outcome"] == "Yes"
    assert first[0]["probability"] == 0.945
    assert first[0]["entry_episode_id"] > 0
    assert {(row.token_id, row.outcome) for row in session.query(MarketSnapshot)} == {
        ("yes-token", "Yes"),
        ("no-token", "No"),
    }
    assert session.query(EntryEpisode).count() == 1

    gamma.last_sweep_attestation = gamma.proof("sweep-2")
    scanner.save_market_snapshots(markets, now=NOW + timedelta(minutes=5))
    assert scanner.scan_buy_candidates(markets, now=NOW + timedelta(minutes=5)) == []
    assert session.query(EntryEpisode).count() == 1
    session.close()


def test_arm_b_can_select_no_without_yes_only_bias(tmp_path) -> None:
    config = TradingConfig(entry=TangerineEntryConfig(0.92, 0.93, 0, 0, 6))
    session, _repo, _gamma, scanner = _scanner(tmp_path, config, _Clob(0.075, 0.925))
    markets = [_market()]
    scanner.save_market_snapshots(markets, now=NOW)
    candidates = scanner.scan_buy_candidates(markets, now=NOW)
    assert [(item["outcome"], item["token_id"]) for item in candidates] == [
        ("No", "no-token")
    ]
    session.close()

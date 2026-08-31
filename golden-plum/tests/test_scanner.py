from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from polybot.api.clob_client import BuyBookWalk
from polybot.config import TradingConfig
from polybot.db.models import MarketSnapshot, MarketSweepMembership, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner, get_source_regulation_minute


NOW = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)


def _event(*, elapsed="20", period="1H"):
    return {
        "id": "event-1",
        "slug": "home-away",
        "title": "Home FC vs. Away FC",
        "parentEventId": None,
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "startTime": (NOW - timedelta(minutes=20)).isoformat(),
        "elapsed": elapsed,
        "period": period,
        "teams": [
            {"name": "Home FC", "league": "epl"},
            {"name": "Away FC", "league": "epl"},
        ],
    }


def _market(kind, descriptor, yes_probability, *, event=None):
    condition = f"condition-{kind.lower()}"
    event = event or _event()
    return {
        "id": f"market-{kind.lower()}",
        "conditionId": condition,
        "slug": f"market-{kind.lower()}",
        "question": f"Will {descriptor} win?",
        "groupItemTitle": descriptor,
        "sportsMarketType": "moneyline",
        "description": (
            "This market refers only to the outcome within the first 90 minutes "
            "of regular play plus stoppage time."
        ),
        "gameStartTime": event["startTime"],
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidityNum": 10_000,
        "volumeNum": 20_000,
        "volume24hr": 3_000,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [str(yes_probability), str(1 - yes_probability)],
        "clobTokenIds": [f"yes-{kind}", f"no-{kind}"],
        "negRisk": True,
        "leagueCode": "epl",
        "leagueName": "Premier League",
        "events": [event],
        "tags": [{"id": "100350", "slug": "soccer"}],
    }


def _triad(*, event=None):
    return [
        _market("HOME", "Home FC", 0.45, event=event),
        _market("DRAW", "Draw", 0.30, event=event),
        _market("AWAY", "Away FC", 0.42, event=event),
    ]


def _walk(token, vwap):
    return BuyBookWalk(
        token,
        vwap - 0.01,
        vwap,
        0.01,
        vwap,
        5 / vwap,
        5,
        vwap,
        1,
    )


def _walks(leader_price=0.72):
    prices = {
        "yes-HOME": 0.45,
        "no-HOME": 0.55,
        "yes-DRAW": 0.30,
        "no-DRAW": 0.50,
        "yes-AWAY": 0.42,
        "no-AWAY": leader_price,
    }
    return {token: _walk(token, price) for token, price in prices.items()}


class _Gamma:
    def __init__(self, markets):
        self.markets = markets
        self.set_sweep(1, NOW)

    def set_sweep(self, index, observed_at):
        memberships = [
            {
                "condition_id": market["conditionId"],
                "raw_seen_count": 1,
                "qualified": True,
                "qualification_reason": "qualified",
            }
            for market in self.markets
        ]
        digest = hashlib.sha256(
            json.dumps(
                sorted(memberships, key=lambda item: item["condition_id"]),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self.last_sweep_attestation = {
            "schema_version": 2,
            "sweep_id": f"sweep-{index}",
            "started_at": observed_at.isoformat(),
            "completed_at": (observed_at + timedelta(seconds=1)).isoformat(),
            "cursor_complete": True,
            "pages": 1,
            "raw_market_count": len(self.markets),
            "unique_condition_count": len(self.markets),
            "qualified_market_count": len(self.markets),
            "excluded_condition_count": 0,
            "exclusion_counts": {},
            "missing_condition_id_count": 0,
            "duplicate_raw_count": 0,
            "min_liquidity": 5000,
            "min_volume": 5000,
            "membership_digest_sha256": digest,
            "membership_digest_scope": "qualified_only",
            "memberships": memberships,
        }


class _Clob:
    def __init__(self, walks):
        self.walks = walks

    def get_buy_book_walks(self, token_ids, *, notional_usdc):
        assert notional_usdc == 5
        return {token: self.walks[token] for token in token_ids if token in self.walks}

    @staticmethod
    def get_cached_book_evidence(token_id):
        return json.dumps({"schema_version": 1, "token_id": token_id})


def _scanner(tmp_path, markets, walks=None):
    Session = init_database(str(tmp_path / "scanner.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = _Gamma(markets)
    clob = _Clob(walks or _walks())
    scanner = MarketScanner(gamma, TradingConfig(), repo, clob_client=clob)
    return session, repo, scanner, gamma, clob


def _save_cycle(scanner, gamma, clob, markets, *, index, price):
    observed_at = NOW + timedelta(minutes=index - 1)
    gamma.set_sweep(index, observed_at)
    clob.walks = _walks(price)
    assert scanner.save_market_snapshots(markets, now=observed_at) == 6
    return scanner.scan_buy_candidates(markets, now=observed_at)


def test_three_fresh_snapshots_confirm_direct_no_first_cross(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets)
    assert _save_cycle(scanner, gamma, clob, markets, index=1, price=0.72) == []
    assert _save_cycle(scanner, gamma, clob, markets, index=2, price=0.74) == []
    candidates = _save_cycle(scanner, gamma, clob, markets, index=3, price=0.75)
    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "NO_AWAY"
    assert candidates[0]["token_id"] == "no-AWAY"
    assert candidates[0]["trend_prices"] == [0.72, 0.74, 0.75]
    assert candidates[0]["trend_cumulative_move"] == pytest.approx(0.03)
    snapshots = session.query(MarketSnapshot).all()
    assert len(snapshots) == 18
    assert {row.outcome_side for row in snapshots} == {"YES", "NO"}
    assert all(row.book_json for row in snapshots)
    session.close()


def test_entry_requires_source_clock_between_five_and_seventy_five(tmp_path) -> None:
    markets = _triad(event=_event(elapsed="76", period="2H"))
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets)
    assert _save_cycle(scanner, gamma, clob, markets, index=1, price=0.72) == []
    session.close()


def test_missing_one_direct_book_fails_closed(tmp_path) -> None:
    markets = _triad()
    walks = _walks(0.75)
    walks.pop("no-AWAY")
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets, walks)
    gamma.set_sweep(1, NOW)
    assert scanner.save_market_snapshots(markets, now=NOW) == 5
    assert scanner.scan_buy_candidates(markets, now=NOW) == []
    session.close()


def test_tied_current_leader_fails_closed_after_history(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets)
    _save_cycle(scanner, gamma, clob, markets, index=1, price=0.72)
    _save_cycle(scanner, gamma, clob, markets, index=2, price=0.74)
    observed_at = NOW + timedelta(minutes=2)
    gamma.set_sweep(3, observed_at)
    clob.walks = _walks(0.75)
    clob.walks["no-DRAW"] = _walk("no-DRAW", 0.748)
    scanner.save_market_snapshots(markets, now=observed_at)
    assert scanner.scan_buy_candidates(markets, now=observed_at) == []
    session.close()


def test_source_clock_normalization_handles_second_half_and_stoppage() -> None:
    assert get_source_regulation_minute({"period": "2H", "elapsed": "6"}) == (
        51.0,
        "SOURCE_SECOND_HALF_PERIOD_OFFSET",
    )
    assert get_source_regulation_minute({"period": "2H", "elapsed": "90+5"}) == (
        95.0,
        "SOURCE_TOTAL_ELAPSED",
    )


def test_sweep_membership_records_all_three_conditions(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets)
    _save_cycle(scanner, gamma, clob, markets, index=1, price=0.72)
    rows = session.query(MarketSweepMembership).all()
    assert len(rows) == 3
    assert all(row.snapshotted == 1 for row in rows)
    session.close()

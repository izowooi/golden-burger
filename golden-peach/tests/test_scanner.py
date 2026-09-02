from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

from polybot.api.clob_client import BuyBookWalk
from polybot.config import SPORT_PARAMETER_PROFILES, TradingConfig
from polybot.db.models import MarketSnapshot, MarketSweepMembership, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner, get_source_regulation_minute


NOW = datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc)


def _event(*, elapsed="5", period="1H"):
    return {
        "id": "event-1",
        "slug": "home-away",
        "title": "Home FC vs. Away FC",
        "parentEventId": None,
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "startTime": (NOW - timedelta(minutes=5)).isoformat(),
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
        _market("HOME", "Home FC", 0.50, event=event),
        _market("DRAW", "Draw", 0.30, event=event),
        _market("AWAY", "Away FC", 0.20, event=event),
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


def _walks():
    prices = {
        "yes-HOME": 0.50,
        "no-HOME": 0.50,
        "yes-DRAW": 0.30,
        "no-DRAW": 0.70,
        "yes-AWAY": 0.20,
        "no-AWAY": 0.80,
    }
    return {token: _walk(token, price) for token, price in prices.items()}


class _Gamma:
    def __init__(self, markets):
        memberships = [
            {
                "condition_id": market["conditionId"],
                "raw_seen_count": 1,
                "qualified": True,
                "qualification_reason": "qualified",
            }
            for market in markets
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
            "sweep_id": "sweep-1",
            "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "cursor_complete": True,
            "pages": 1,
            "raw_market_count": len(markets),
            "unique_condition_count": len(markets),
            "qualified_market_count": len(markets),
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

    def get_cached_book_evidence(self, token_id):
        walk = self.walks[token_id]
        return json.dumps(
            {
                "schema_version": 1,
                "token_id": token_id,
                "bids": [{"price": walk.best_bid, "size": 10_000}],
                "asks": [{"price": walk.best_ask, "size": 10_000}],
            }
        )


def _scanner(tmp_path, markets, walks=None, config=None):
    Session = init_database(str(tmp_path / "scanner.db"))
    session = Session()
    repo = TradeRepository(session)
    scanner = MarketScanner(
        _Gamma(markets), config or TradingConfig(), repo,
        clob_client=_Clob(walks or _walks())
    )
    return session, repo, scanner


def test_complete_six_token_event_selects_direct_no_leader(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner = _scanner(tmp_path, markets)
    assert scanner.save_market_snapshots(markets, now=NOW) == 6
    candidates = scanner.scan_buy_candidates(markets, now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "NO_AWAY"
    assert candidates[0]["token_id"] == "no-AWAY"
    assert candidates[0]["source_elapsed_minutes"] == 5
    snapshots = session.query(MarketSnapshot).all()
    assert len(snapshots) == 6
    assert {row.outcome_side for row in snapshots} == {"YES", "NO"}
    assert all(row.book_json for row in snapshots)
    session.close()


def test_entry_requires_actual_source_clock_within_first_ten_minutes(tmp_path) -> None:
    markets = _triad(event=_event(elapsed="11", period="1H"))
    session, _repo, scanner = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    assert scanner.scan_buy_candidates(markets, now=NOW) == []
    session.close()


def test_missing_one_direct_book_fails_closed(tmp_path) -> None:
    markets = _triad()
    walks = _walks()
    walks.pop("no-AWAY")
    session, _repo, scanner = _scanner(tmp_path, markets, walks)
    assert scanner.save_market_snapshots(markets, now=NOW) == 5
    assert scanner.scan_buy_candidates(markets, now=NOW) == []
    session.close()


def test_tied_leader_margin_fails_closed(tmp_path) -> None:
    markets = _triad()
    walks = _walks()
    walks["no-DRAW"] = _walk("no-DRAW", 0.798)
    session, _repo, scanner = _scanner(tmp_path, markets, walks)
    scanner.save_market_snapshots(markets, now=NOW)
    assert scanner.scan_buy_candidates(markets, now=NOW) == []
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
    session, _repo, scanner = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    rows = session.query(MarketSweepMembership).all()
    assert len(rows) == 3
    assert all(row.snapshotted == 1 for row in rows)
    session.close()


def test_direct_sport_shadow_uses_two_team_books_and_records_sizing(tmp_path) -> None:
    event = _event()
    event["teams"] = [
        {"name": "Home Nine", "league": "mlb"},
        {"name": "Away Nine", "league": "mlb"},
    ]
    market = {
        **_market("HOME", "Home Nine", 0.70, event=event),
        "conditionId": "condition-mlb",
        "question": "Home Nine vs Away Nine",
        "groupItemTitle": "Home Nine vs Away Nine",
        "outcomes": ["Home Nine", "Away Nine"],
        "outcomePrices": ["0.70", "0.30"],
        "clobTokenIds": ["mlb-home", "mlb-away"],
        "negRisk": False,
        "sportFamily": "mlb",
        "leagueCode": "mlb",
        "leagueName": "MLB",
    }
    walks = {
        "mlb-home": _walk("mlb-home", 0.70),
        "mlb-away": _walk("mlb-away", 0.30),
    }
    profile = SPORT_PARAMETER_PROFILES["mlb"]
    base = TradingConfig()
    config = replace(
        base,
        sport_family="mlb",
        sport_profile_version=profile.profile_version,
        book_shape=profile.book_shape,
        expected_result_kinds=profile.expected_result_kinds,
        expected_market_count=profile.expected_market_count,
        expected_token_count=profile.expected_token_count,
        source_clock_required=profile.source_clock_required,
        scaling_notionals_usdc=(5.0, 10.0),
        entry=replace(base.entry, hours_max=profile.max_in_play_hours),
        archive=replace(base.archive, hours_max=profile.max_in_play_hours),
    )
    session, _repo, scanner = _scanner(
        tmp_path, [market], walks=walks, config=config
    )

    assert scanner.save_market_snapshots([market], now=NOW) == 2
    candidates = scanner.scan_buy_candidates([market], now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "DIRECT_HOME"
    snapshots = session.query(MarketSnapshot).all()
    assert {row.sport_family for row in snapshots} == {"mlb"}
    assert {row.league_code for row in snapshots} == {"mlb"}
    assert all(row.execution_capacity_json for row in snapshots)
    assert all(row.book_shape == "direct-two-team-moneyline" for row in snapshots)
    session.close()

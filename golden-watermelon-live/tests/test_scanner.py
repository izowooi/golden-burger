from datetime import datetime, timedelta, timezone

from polybot.api.clob_client import BuyBookWalk
from polybot.config import TradingConfig, WatermelonLiveEntryConfig
from sqlalchemy import text

from polybot.db.models import (
    EntryEpisode,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _event(event_id="event-1"):
    return {
        "id": event_id,
        "slug": event_id,
        "title": "Home FC vs. Away FC",
        "parentEventId": None,
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "startTime": (NOW - timedelta(hours=1)).isoformat(),
        "teams": [
            {"name": "Home FC", "league": "epl"},
            {"name": "Away FC", "league": "epl"},
        ],
    }


def _market(
    condition_id="condition-1",
    *,
    result="Home FC",
    event_id="event-1",
):
    return {
        "id": f"market-{condition_id}",
        "conditionId": condition_id,
        "slug": f"market-{condition_id}",
        "question": f"Will {result} win?",
        "groupItemTitle": result,
        "sportsMarketType": "moneyline",
        "gameStartTime": (NOW - timedelta(hours=1)).isoformat(),
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": "200",
        "liquidityNum": 200,
        "volume": "100",
        "volumeNum": 100,
        "volume24hr": "50",
        "endDate": (NOW - timedelta(hours=1)).isoformat(),
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.985", "0.015"],
        "clobTokenIds": [f"yes-{condition_id}", f"no-{condition_id}"],
        "negRisk": True,
        "leagueCode": "epl",
        "leagueName": "Premier League",
        "events": [_event(event_id)],
        "tags": [{"id": "100350", "slug": "soccer"}],
    }


def _walk(token, vwap):
    return BuyBookWalk(token, vwap - 0.01, vwap, 0.01, vwap, 5 / vwap, 5, vwap, 1)


class _Gamma:
    def __init__(self, condition_ids=("condition-1",), sweep="sweep-1"):
        self.last_sweep_attestation = self.proof(condition_ids, sweep)

    @staticmethod
    def proof(condition_ids, sweep):
        import hashlib
        import json

        memberships = sorted(
            [
            {
                "condition_id": condition_id,
                "raw_seen_count": 1,
                "qualified": True,
                "qualification_reason": "qualified",
            }
            for condition_id in condition_ids
            ],
            key=lambda item: item["condition_id"],
        )
        digest = hashlib.sha256(
            json.dumps(memberships, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "schema_version": 2,
            "sweep_id": sweep,
            "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "cursor_complete": True,
            "pages": 1,
            "raw_market_count": len(memberships),
            "unique_condition_count": len(memberships),
            "qualified_market_count": len(memberships),
            "excluded_condition_count": 0,
            "exclusion_counts": {},
            "missing_condition_id_count": 0,
            "duplicate_raw_count": 0,
            "min_liquidity": 0,
            "min_volume": 0,
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


def _scanner(tmp_path, config, markets, walks):
    Session = init_database(str(tmp_path / "scanner.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = _Gamma(tuple(market["conditionId"] for market in markets))
    scanner = MarketScanner(gamma, config, repo, clob_client=_Clob(walks))
    return session, repo, gamma, scanner


def test_cat_claims_only_first_exact_yes_book_observation(tmp_path) -> None:
    config = TradingConfig()
    market = _market()
    token = "yes-condition-1"
    session, _repo, gamma, scanner = _scanner(
        tmp_path, config, [market], {token: _walk(token, 0.985)}
    )

    assert scanner.save_market_snapshots([market], now=NOW) == 1
    first = scanner.scan_buy_candidates([market], now=NOW)
    assert len(first) == 1
    assert first[0]["outcome"] == "Yes"
    assert first[0]["result_kind"] == "HOME"
    assert first[0]["probability"] == 0.985
    assert session.query(MarketSnapshot).one().token_id == token
    assert session.query(EntryEpisode).count() == 1

    gamma.last_sweep_attestation = gamma.proof(("condition-1",), "sweep-2")
    scanner.save_market_snapshots([market], now=NOW + timedelta(minutes=5))
    assert scanner.scan_buy_candidates([market], now=NOW + timedelta(minutes=5)) == []
    assert session.query(EntryEpisode).count() == 1
    session.close()


def test_dog_99_arm_accepts_draw_yes_and_never_no_token(tmp_path) -> None:
    config = TradingConfig(
        entry=WatermelonLiveEntryConfig(0.99, 0.999, 0.70, 0, 4)
    )
    market = _market(result="Draw (Home FC vs. Away FC)")
    token = "yes-condition-1"
    session, _repo, _gamma, scanner = _scanner(
        tmp_path, config, [market], {token: _walk(token, 0.995)}
    )
    scanner.save_market_snapshots([market], now=NOW)
    candidates = scanner.scan_buy_candidates([market], now=NOW)
    assert [(item["result_kind"], item["token_id"]) for item in candidates] == [
        ("DRAW", token)
    ]
    session.close()


def test_multiple_results_above_threshold_for_one_event_fail_closed(tmp_path) -> None:
    config = TradingConfig()
    home = _market("home", result="Home FC")
    away = _market("away", result="Away FC")
    walks = {
        "yes-home": _walk("yes-home", 0.985),
        "yes-away": _walk("yes-away", 0.986),
    }
    session, _repo, _gamma, scanner = _scanner(
        tmp_path, config, [home, away], walks
    )
    scanner.save_market_snapshots([home, away], now=NOW)
    assert scanner.scan_buy_candidates([home, away], now=NOW) == []
    session.close()


def test_detail_checkpoint_keeps_excluded_identity_and_repairs_legacy_gap(
    tmp_path,
) -> None:
    import hashlib
    import json

    Session = init_database(str(tmp_path / "membership.db"))
    session = Session()
    repo = TradeRepository(session)
    qualified = {
        "condition_id": "qualified",
        "raw_seen_count": 1,
        "qualified": True,
        "qualification_reason": "qualified",
    }
    excluded = {
        "condition_id": "excluded",
        "raw_seen_count": 1,
        "qualified": False,
        "qualification_reason": "league_not_allowed",
    }

    def attestation(sweep_id, minute):
        digest = hashlib.sha256(
            json.dumps(
                [qualified], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        return {
            "schema_version": 2,
            "sweep_id": sweep_id,
            "started_at": (NOW + timedelta(minutes=minute)).isoformat(),
            "completed_at": (
                NOW + timedelta(minutes=minute, seconds=1)
            ).isoformat(),
            "cursor_complete": True,
            "pages": 1,
            "raw_market_count": 2,
            "unique_condition_count": 2,
            "qualified_market_count": 1,
            "excluded_condition_count": 1,
            "exclusion_counts": {"league_not_allowed": 1},
            "missing_condition_id_count": 0,
            "duplicate_raw_count": 0,
            "min_liquidity": 0,
            "min_volume": 0,
            "membership_digest_sha256": digest,
            "membership_digest_scope": "qualified_only",
            "memberships": [qualified, excluded],
        }

    snapshots = {
        "qualified": {
            "snapshot_eligible": True,
            "snapshotted": False,
            "snapshot_reason": "no_full_exact_5_usdc_yes_book",
        }
    }
    repo.record_market_sweep(attestation("sweep-1", 0), snapshots, commit=True)
    first = session.get(MarketSweep, "sweep-1")
    assert first.membership_detail_stored == 1
    rows = (
        session.query(MarketSweepMembership)
        .filter(MarketSweepMembership.sweep_id == "sweep-1")
        .order_by(MarketSweepMembership.condition_id)
        .all()
    )
    assert [(row.condition_id, row.qualified, row.snapshot_reason) for row in rows] == [
        ("excluded", 0, "not_qualified:league_not_allowed"),
        ("qualified", 1, "no_full_exact_5_usdc_yes_book"),
    ]

    # Recreate the old production defect: a sweep claimed detail coverage but
    # persisted no membership rows. The next run must repair immediately rather
    # than waiting for the normal 24-hour checkpoint.
    session.execute(
        text("DELETE FROM market_sweep_memberships WHERE sweep_id='sweep-1'")
    )
    session.commit()
    repo.record_market_sweep(attestation("sweep-2", 1), snapshots, commit=True)
    second = session.get(MarketSweep, "sweep-2")
    assert second.membership_detail_stored == 1
    assert (
        session.query(MarketSweepMembership)
        .filter(MarketSweepMembership.sweep_id == "sweep-2")
        .count()
        == 2
    )
    session.close()

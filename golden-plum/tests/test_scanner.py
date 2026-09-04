from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from polybot.api.clob_client import BuyBookWalk, _normalize_clob_resolution
from polybot.config import TradingConfig
from polybot.db.models import (
    EventCycleEvidence,
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    TrackedResolutionObservation,
    Trade,
    init_database,
)
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


def _mlb_market():
    event = {
        "id": "mlb-event-1",
        "slug": "mlb-home-away",
        "title": "Home Club vs. Away Club",
        "parentEventId": None,
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "startTime": (NOW - timedelta(hours=1)).isoformat(),
        # Deliberately no elapsed/period: baseball innings are not soccer minutes.
        "teams": [
            {"name": "Home Club", "league": "mlb"},
            {"name": "Away Club", "league": "mlb"},
        ],
    }
    return {
        "id": "mlb-market-1",
        "conditionId": "mlb-condition-1",
        "slug": "mlb-home-away",
        "question": "Home Club vs Away Club",
        "groupItemTitle": "",
        "sportsMarketType": "moneyline",
        "gameStartTime": event["startTime"],
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidityNum": 10_000,
        "volumeNum": 20_000,
        "volume24hr": 3_000,
        "outcomes": ["Home Club", "Away Club"],
        "outcomePrices": ["0.72", "0.28"],
        "clobTokenIds": ["mlb-home", "mlb-away"],
        "negRisk": False,
        "sportFamily": "mlb",
        "leagueCode": "mlb",
        "leagueName": "MLB",
        "events": [event],
        "tags": [{"id": "100381", "slug": "mlb"}],
    }


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
        self.followups = {}
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
            "sport_family": str(
                self.markets[0].get("sportFamily") or "soccer"
            ).lower(),
        }

    def get_market_by_condition_id(self, condition_id):
        value = self.followups.get(condition_id)
        return deepcopy(value) if value is not None else None


class _Clob:
    def __init__(self, walks):
        self.walks = walks
        self.resolutions = {}

    def get_buy_book_walks(self, token_ids, *, notional_usdc):
        assert notional_usdc == 5
        return {token: self.walks[token] for token in token_ids if token in self.walks}

    def get_cached_book_evidence(self, token_id):
        walk = self.walks[token_id]
        return json.dumps(
            {
                "schema_version": 1,
                "token_id": token_id,
                "bids": [{"price": walk.best_bid, "size": 1_000}],
                "asks": [{"price": walk.best_ask, "size": 1_000}],
            }
        )

    def get_market_resolution(self, condition_id):
        value = self.resolutions.get(condition_id, {"closed": False})
        return _normalize_clob_resolution(condition_id, deepcopy(value))


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
    assert all(row.execution_capacity_json is None for row in snapshots)
    session.close()


def test_entry_accepts_explicit_live_source_clock_after_minute_seventy_five(
    tmp_path,
) -> None:
    markets = _triad(event=_event(elapsed="76", period="2H"))
    session, _repo, scanner, gamma, clob = _scanner(tmp_path, markets)
    assert _save_cycle(scanner, gamma, clob, markets, index=1, price=0.72) == []
    assert _save_cycle(scanner, gamma, clob, markets, index=2, price=0.74) == []
    candidates = _save_cycle(scanner, gamma, clob, markets, index=3, price=0.75)
    assert len(candidates) == 1
    assert candidates[0]["source_elapsed_minutes"] == 76
    session.close()


def test_simulation_scaling_ladder_is_persisted_without_extra_book_reads(
    tmp_path,
) -> None:
    markets = _triad()
    Session = init_database(str(tmp_path / "scaling.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = _Gamma(markets)
    clob = _Clob(_walks())
    config = TradingConfig(scaling_notionals_usdc=(5.0, 10.0, 25.0))
    scanner = MarketScanner(gamma, config, repo, clob_client=clob)

    assert scanner.save_market_snapshots(markets, now=NOW) == 6
    rows = session.query(MarketSnapshot).all()
    assert len(rows) == 6
    for row in rows:
        payload = json.loads(row.execution_capacity_json)
        assert payload["semantics"].endswith("not_actual_fill")
        assert [item["notional_usdc"] for item in payload["notionals"]] == [
            5.0,
            10.0,
            25.0,
        ]
        assert all(item["buy_full_fill"] for item in payload["notionals"])
        assert all(item["sell_full_fill"] for item in payload["notionals"])
    session.close()


def test_mlb_direct_two_team_collection_and_trend_need_no_fake_minute(
    tmp_path,
) -> None:
    market = _mlb_market()
    markets = [market]
    Session = init_database(str(tmp_path / "mlb.db"))
    session = Session()
    repo = TradeRepository(session)
    gamma = _Gamma(markets)
    clob = _Clob(
        {
            "mlb-home": _walk("mlb-home", 0.72),
            "mlb-away": _walk("mlb-away", 0.28),
        }
    )
    config = TradingConfig(
        sport_family="mlb",
        sport_profile_version="mlb-collection-uncalibrated-v1",
        book_shape="direct-two-team-moneyline",
        expected_result_kinds=("HOME", "AWAY"),
        expected_market_count=1,
        expected_token_count=2,
        source_clock_required=False,
        scaling_notionals_usdc=(5.0, 10.0),
    )
    scanner = MarketScanner(gamma, config, repo, clob_client=clob)

    for index, price in enumerate((0.72, 0.74, 0.75), start=1):
        observed_at = NOW + timedelta(minutes=index - 1)
        gamma.set_sweep(index, observed_at)
        clob.walks = {
            "mlb-home": _walk("mlb-home", price),
            "mlb-away": _walk("mlb-away", 1 - price),
        }
        assert scanner.save_market_snapshots(markets, now=observed_at) == 2
        candidates = scanner.scan_buy_candidates(markets, now=observed_at)

    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "DIRECT_HOME"
    assert candidates[0]["event_token_ids"] == ["mlb-home", "mlb-away"]
    assert candidates[0]["source_elapsed_minutes"] is None
    rows = session.query(MarketSnapshot).all()
    assert len(rows) == 6
    assert {row.outcome_side for row in rows} == {"DIRECT"}
    assert all(row.source_elapsed_minutes is None for row in rows)
    assert all(
        row.source_clock_reason == "SOURCE_CLOCK_NOT_COMPARABLE_MLB"
        for row in rows
    )
    assert all(row.execution_capacity_json for row in rows)
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
    event = session.query(EventCycleEvidence).one()
    sweep = session.query(MarketSweep).one()
    assert event.complete == 1
    assert event.observed_market_count == 3
    assert event.observed_token_count == 6
    assert event.reason == "complete"
    assert sweep.complete_event_count == 1
    assert all(row.event_cycle_id == event.event_cycle_id for row in rows)
    snapshots = session.query(MarketSnapshot).all()
    assert all(row.sport_family == "soccer" for row in snapshots)
    assert all(row.sport_profile_version == "soccer-full-match-v2" for row in snapshots)
    assert all(row.event_set_complete == 1 for row in snapshots)
    session.close()


def test_missing_soccer_result_set_is_recorded_and_rejected(tmp_path) -> None:
    markets = _triad()[:2]
    walks = {key: value for key, value in _walks().items() if "AWAY" not in key}
    session, _repo, scanner, gamma, _clob = _scanner(tmp_path, markets, walks)

    assert scanner.save_market_snapshots(markets, now=NOW) == 4
    assert scanner.scan_buy_candidates(markets, now=NOW) == []

    event = session.query(EventCycleEvidence).one()
    assert event.complete == 0
    assert event.observed_market_count == 2
    assert json.loads(event.missing_result_kinds_json) == ["AWAY"]
    assert "market_count:2/3" in event.reason
    assert all(row.event_set_complete == 0 for row in session.query(MarketSnapshot))
    session.close()


def test_duplicate_result_identity_is_recorded_and_rejected(tmp_path) -> None:
    markets = _triad()
    duplicate = deepcopy(markets[0])
    duplicate["id"] = "market-home-duplicate"
    duplicate["conditionId"] = "condition-home-duplicate"
    duplicate["clobTokenIds"] = ["yes-HOME-duplicate", "no-HOME-duplicate"]
    markets.append(duplicate)
    walks = _walks()
    walks["yes-HOME-duplicate"] = _walk("yes-HOME-duplicate", 0.45)
    walks["no-HOME-duplicate"] = _walk("no-HOME-duplicate", 0.55)
    session, _repo, scanner, _gamma, _clob = _scanner(tmp_path, markets, walks)

    assert scanner.save_market_snapshots(markets, now=NOW) == 8
    assert scanner.scan_buy_candidates(markets, now=NOW) == []

    event = session.query(EventCycleEvidence).one()
    assert event.complete == 0
    assert event.duplicate_identity_count == 2
    assert "duplicate_direct_identities:2" in event.reason
    session.close()


def test_disappeared_condition_followup_persists_order_independent_terminal_one_hot(
    tmp_path,
) -> None:
    markets = _triad()
    session, _repo, scanner, gamma, _clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    terminal = deepcopy(markets[0])
    terminal["closed"] = True
    terminal["active"] = False
    terminal["acceptingOrders"] = False
    terminal["outcomePrices"] = ["1", "0"]
    terminal["updatedAt"] = (NOW + timedelta(minutes=2)).isoformat()
    gamma.followups[terminal["conditionId"]] = terminal

    stats = scanner.follow_tracked_conditions(
        markets[1:],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats == {
        "due": 1,
        "attempted": 1,
        "terminal": 1,
        "pending": 0,
        "source_missing": 0,
    }
    catalog = session.get(MarketCatalog, terminal["conditionId"])
    assert catalog.followup_status == "TERMINAL"
    assert catalog.resolution_evidence_sha256
    resolution = session.query(TrackedResolutionObservation).one()
    assert json.loads(resolution.payouts_json) == {
        "no-HOME": 0.0,
        "yes-HOME": 1.0,
    }
    assert session.query(Trade).count() == 0
    session.close()


def test_gamma_followup_persists_authoritative_void_with_aligned_payouts(
    tmp_path,
) -> None:
    markets = _triad()
    session, _repo, scanner, gamma, _clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    terminal = deepcopy(markets[0])
    terminal["closed"] = True
    terminal["active"] = False
    terminal["acceptingOrders"] = False
    terminal["outcomePrices"] = ["0.5", "0.5"]
    terminal["umaResolutionStatus"] = "resolved"
    terminal["updatedAt"] = (NOW + timedelta(minutes=2)).isoformat()
    gamma.followups[terminal["conditionId"]] = terminal

    stats = scanner.follow_tracked_conditions(
        markets[1:],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats["terminal"] == 1
    catalog = session.get(MarketCatalog, terminal["conditionId"])
    assert catalog.followup_status == "TERMINAL"
    assert catalog.resolution_status == "gamma_closed_resolved_void_0_5_0_5"
    assert catalog.resolved_outcome == "VOID"
    resolution = session.query(TrackedResolutionObservation).one()
    assert resolution.winner_index == -1
    assert resolution.winner_token_id == "__VOID__"
    assert resolution.winner_outcome == "VOID"
    assert json.loads(resolution.payouts_json) == {
        "no-HOME": 0.5,
        "yes-HOME": 0.5,
    }
    assert session.query(Trade).count() == 0
    session.close()


def test_followup_source_gap_remains_pending_with_bounded_retry(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, _gamma, _clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)

    stats = scanner.follow_tracked_conditions(
        [],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats["source_missing"] == 1
    catalog = (
        session.query(MarketCatalog)
        .filter(MarketCatalog.followup_status == "SOURCE_MISSING")
        .one()
    )
    assert catalog.resolution_evidence_sha256 is None
    assert catalog.followup_next_attempt_at > NOW.replace(tzinfo=None)
    session.close()


def test_gamma_missing_followup_uses_exact_clob_one_hot_resolution(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, _gamma, clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    condition_id = markets[0]["conditionId"]
    clob.resolutions[condition_id] = {
        "condition_id": condition_id,
        "closed": True,
        "tokens": [
            {
                "outcome": "Yes",
                "token_id": "yes-HOME",
                "price": 1,
                "winner": True,
            },
            {
                "outcome": "No",
                "token_id": "no-HOME",
                "price": 0,
                "winner": False,
            },
        ],
    }

    stats = scanner.follow_tracked_conditions(
        markets[1:],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats == {
        "due": 1,
        "attempted": 1,
        "terminal": 1,
        "pending": 0,
        "source_missing": 0,
    }
    catalog = session.get(MarketCatalog, condition_id)
    assert catalog.followup_status == "TERMINAL"
    assert catalog.resolution_status == "clob_closed_unique_winner"
    resolution = session.query(TrackedResolutionObservation).one()
    assert resolution.source == "CLOB_CONDITION_FOLLOWUP"
    assert resolution.winner_token_id == "yes-HOME"
    assert json.loads(resolution.payouts_json) == {
        "no-HOME": 0.0,
        "yes-HOME": 1.0,
    }
    assert session.query(Trade).count() == 0
    session.close()


def test_gamma_missing_followup_uses_exact_clob_void_resolution(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, _gamma, clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    condition_id = markets[0]["conditionId"]
    clob.resolutions[condition_id] = {
        "condition_id": condition_id,
        "closed": True,
        "tokens": [
            {
                "outcome": "Yes",
                "token_id": "yes-HOME",
                "price": 0.5,
                "winner": False,
            },
            {
                "outcome": "No",
                "token_id": "no-HOME",
                "price": 0.5,
                "winner": False,
            },
        ],
    }

    stats = scanner.follow_tracked_conditions(
        markets[1:],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats["terminal"] == 1
    assert stats["source_missing"] == 0
    catalog = session.get(MarketCatalog, condition_id)
    assert catalog.followup_status == "TERMINAL"
    assert catalog.resolution_status == "clob_closed_void_0_5_0_5"
    assert catalog.resolved_outcome == "VOID"
    resolution = session.query(TrackedResolutionObservation).one()
    assert resolution.source == "CLOB_CONDITION_FOLLOWUP"
    assert resolution.winner_index == -1
    assert resolution.winner_token_id == "__VOID__"
    assert resolution.winner_outcome == "VOID"
    assert json.loads(resolution.payouts_json) == {
        "no-HOME": 0.5,
        "yes-HOME": 0.5,
    }
    assert session.query(Trade).count() == 0
    session.close()


def test_clob_followup_token_mismatch_stays_explicitly_unresolved(tmp_path) -> None:
    markets = _triad()
    session, _repo, scanner, _gamma, clob = _scanner(tmp_path, markets)
    scanner.save_market_snapshots(markets, now=NOW)
    condition_id = markets[0]["conditionId"]
    clob.resolutions[condition_id] = {
        "condition_id": condition_id,
        "closed": True,
        "tokens": [
            {
                "outcome": "Yes",
                "token_id": "wrong-yes-token",
                "price": 1,
                "winner": True,
            },
            {
                "outcome": "No",
                "token_id": "wrong-no-token",
                "price": 0,
                "winner": False,
            },
        ],
    }

    stats = scanner.follow_tracked_conditions(
        markets[1:],
        now=NOW + timedelta(minutes=2),
        limit=1,
    )

    assert stats["terminal"] == 0
    assert stats["source_missing"] == 1
    catalog = session.get(MarketCatalog, condition_id)
    assert catalog.followup_status == "SOURCE_MISSING"
    assert catalog.followup_last_error == "clob_resolution_identity_mismatch"
    assert catalog.resolution_evidence_sha256 is None
    assert session.query(TrackedResolutionObservation).count() == 0
    session.close()

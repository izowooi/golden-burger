"""Offline fixtures only; load this module without main/config/sibling polybot.

Run with the project interpreter: python -B tests/test_official_news.py.
Every Session.request is intercepted, including unexpected calls.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import requests


SPEC = importlib.util.spec_from_file_location(
    "guava_official_news_under_test", Path(__file__).resolve().parents[1] / "src/polybot/official_news.py"
)
news_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(news_module)
NOW = "2026-09-06T01:00:00Z"
SCHEDULED = "2026-09-06T00:00:00Z"


class Budget:
    def __init__(self, remaining=30.0):
        self.remaining = remaining

    def require(self):
        if self.remaining <= 0:
            raise TimeoutError("fixture budget exhausted")
        return self.remaining


class Response:
    def __init__(self, payload, status=200, headers=None, on_read=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = json.dumps(payload).encode("utf-8")
        self.closed = False
        self.raw = self
        self.offset = 0
        self.on_read = on_read

    def read1(self, size, decode_content=False):
        assert not decode_content
        if self.on_read:
            self.on_read()
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.proxies = {}
        self.auth = None
        self.trust_env = True
        self.adapters = {}
        self.closed = False

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert method == "GET"
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert self.trust_env is False and self.auth is None and not self.proxies
        assert not self.cookies
        assert not {k.lower() for k in self.headers} & {"authorization", "cookie", "proxy-authorization"}
        assert all(0 < seconds <= 5 for seconds in kwargs["timeout"])
        if not self.responses:
            raise AssertionError("unexpected request; no fixture response remains")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def venue_event(family="soccer", league="epl", event_id="venue-1", home="Alpha FC", away="Beta FC"):
    return {"event_id": event_id, "sport_family": family, "league_code": league,
            "raw": {"id": event_id, "startTime": SCHEDULED,
                    "teams": [{"name": away, "league": league}, {"name": home, "league": league}],
                    "markets": []}}


def espn_game(game_id="provider-1", home="Alpha FC", away="Beta FC", date=SCHEDULED,
              period=2, final=True, score=(2, 1), status_name="STATUS_FULL_TIME"):
    return {"id": game_id, "date": date, "competitions": [{
        "id": game_id, "date": date,
        "status": {"period": period, "displayClock": "90:00",
                   "type": {"name": status_name, "state": "post" if final else "in",
                            "completed": final, "description": "Full Time" if final else "In Progress"}},
        "competitors": [
            {"id": "away-id", "homeAway": "away", "score": str(score[1]),
             "team": {"id": "away-id", "displayName": away}},
            {"id": "home-id", "homeAway": "home", "score": str(score[0]),
             "team": {"id": "home-id", "displayName": home}},
        ]}]}


def board(games=None, league="eng.1"):
    return {"leagues": [{"abbreviation": league}], "events": [espn_game()] if games is None else games}


def mlb_game(game_id=1234, score=(6, 4), inning=10):
    return {"gamePk": game_id, "gameDate": SCHEDULED, "officialDate": "2026-09-06",
            "doubleHeader": "N", "gameNumber": 1, "scheduledInnings": 9,
            "status": {"abstractGameState": "Final", "codedGameState": "F",
                       "detailedState": "Final", "statusCode": "F"},
            "teams": {"home": {"score": score[0], "team": {"id": 147, "name": "New York Yankees"}},
                      "away": {"score": score[1], "team": {"id": 111, "name": "Boston Red Sox"}}},
            "linescore": {"currentInning": inning, "inningState": "End"}}


def mlb_board(games=None):
    games = [mlb_game()] if games is None else games
    return {"totalGames": len(games), "dates": [{"date": "2026-09-06", "games": games}]}


def payout_event(family="soccer"):
    event = venue_event(family, "epl" if family == "soccer" else family)
    if family == "soccer":
        event["raw"]["markets"] = [
            {"conditionId": "condition-" + role, "sportsMarketType": "moneyline",
             "groupItemTitle": label, "outcomes": '["Yes","No"]',
             "clobTokenIds": [role + "-yes", role + "-no"]}
            for role, label in (("home", "Alpha FC"), ("draw", "Draw (Alpha FC vs Beta FC)"), ("away", "Beta FC"))
        ]
    else:
        event["raw"]["markets"] = [{"conditionId": "direct-condition", "sportsMarketType": "moneyline",
                                     "outcomes": '["Beta FC","Alpha FC"]',
                                     "clobTokenIds": '["away-token","home-token"]'}]
    event["expected_token_ids"] = sorted(token for market in event["raw"]["markets"]
                                          for token in news_module._array(market["clobTokenIds"]))
    return event


class OfficialNewsTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(requests.sessions.Session, "request", side_effect=AssertionError("real network forbidden")).start()
        patch.object(news_module, "_utcnow", return_value=NOW).start()

    def make(self, responses, budget=None, sink=None):
        session = Session(responses)
        patch.object(news_module.requests, "Session", return_value=session).start()
        receipts = []
        client = news_module.OfficialNews(budget or Budget(), sink or (lambda r, p: receipts.append((r, p))))
        self.addCleanup(client.close)
        return client, session, receipts

    def result(self, game=None, event=None, league="eng.1"):
        client, session, receipts = self.make([Response(board([game or espn_game()], league))])
        return client.fetch_for_events([event or venue_event()], NOW), session, receipts

    def test_exact_team_league_day_match_and_explicit_home_away(self):
        result, session, receipts = self.result()
        item = result["by_event"]["venue-1"]
        self.assertEqual(item["home"], {"team_id": "home-id", "name": "Alpha FC", "score": 2})
        self.assertEqual(item["away"], {"team_id": "away-id", "name": "Beta FC", "score": 1})
        self.assertTrue(item["final"])
        self.assertTrue(item["regulation_result_known"])
        self.assertEqual(item["regulation_result"], "HOME")
        self.assertEqual(item["score_time_basis"], "provider_snapshot_not_exact_goal_time")
        self.assertEqual(item["matching_evidence"]["schedule_difference_seconds"], 0)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][2]["params"]["dates"], "20260905-20260906")
        self.assertEqual(receipts[0][0]["source"], "espn")

    def test_only_active_leagues_and_one_range_call_each(self):
        families = [("soccer", "epl", "eng.1"), ("soccer", "ucl", "uefa.champions"),
                    ("nba", "nba", "nba"), ("nfl", "nfl", "nfl"), ("nhl", "nhl", "nhl")]
        responses = [Response(board([], provider_league)) for _, _, provider_league in families]
        client, session, _ = self.make(responses)
        events = [venue_event(f, league, str(i)) for i, (f, league, _) in enumerate(families)]
        # Exact response order is independent of repeated input events.
        result = client.fetch_for_events(events, NOW)
        self.assertEqual(len(session.calls), 5)
        self.assertEqual(len({url for _, url, _ in session.calls}), 5)
        self.assertEqual(set(result["by_event"]), {"0", "1", "2", "3", "4"})

    def test_all_eight_soccer_endpoint_routes_are_explicit(self):
        expected = {"epl": "eng.1", "bun": "ger.1", "fl1": "fra.1", "lal": "esp.1",
                    "mls": "usa.1", "sea": "ita.1", "ucl": "uefa.champions", "uel": "uefa.europa"}
        for league, provider_league in expected.items():
            with self.subTest(league=league):
                client, session, _ = self.make([Response(board([], provider_league))])
                client.fetch_for_events([venue_event(league=league)], NOW)
                self.assertEqual(session.calls[0][1],
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{provider_league}/scoreboard")

    def test_no_events_or_unsupported_league_make_no_requests(self):
        client, session, _ = self.make([])
        self.assertEqual(client.fetch_for_events([], NOW), {"by_event": {}, "context": [], "errors": []})
        bad = venue_event(league="unlisted-cup")
        result = client.fetch_for_events([bad], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertTrue(result["errors"])
        self.assertEqual(session.calls, [])

    def test_exact_normalization_and_frozen_alias_not_fuzzy(self):
        event = venue_event(home="Man Utd", away="Béta FC")
        result, _, _ = self.result(espn_game(home="Manchester United", away="Beta FC"), event)
        self.assertIsNotNone(result["by_event"]["venue-1"])
        event["raw"]["teams"][0]["name"] = "Beta AFC"
        result, _, _ = self.result(espn_game(home="Manchester United", away="Beta FC"), event)
        self.assertIsNone(result["by_event"]["venue-1"])

    def test_no_numeric_game_id_fallback_for_wrong_teams(self):
        event = venue_event(event_id="1234")
        result, _, _ = self.result(espn_game(game_id="1234", home="Unrelated United"), event)
        self.assertIsNone(result["by_event"]["1234"])
        self.assertTrue(any(e["reason"] == "teams_not_matched" for e in result["errors"]))

    def test_wrong_league_provider_response_is_unconfirmed(self):
        result, _, receipts = self.result(league="ger.1")
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertTrue(result["errors"])
        self.assertNotEqual(receipts[0][0]["result_status"], "OK")

    def test_same_utc_day_and_maximum_ninety_minutes(self):
        for schedule, matched in (("2026-09-06T01:30:00Z", True),
                                  ("2026-09-06T01:30:01Z", False),
                                  ("2026-09-05T23:59:00Z", False)):
            with self.subTest(schedule=schedule):
                result, _, _ = self.result(espn_game(date=schedule))
                self.assertEqual(result["by_event"]["venue-1"] is not None, matched)

    def test_duplicate_games_and_multiple_competitions_do_not_choose_one(self):
        for games in ([espn_game(), espn_game("other", date="2026-09-06T04:00:00Z")],
                      [espn_game(), espn_game()]):
            client, _, _ = self.make([Response(board(games))])
            result = client.fetch_for_events([venue_event()], NOW)
            self.assertIsNone(result["by_event"]["venue-1"])
            self.assertEqual(len(result["context"]), 2)
        game = espn_game()
        game["competitions"].append(deepcopy(game["competitions"][0]))
        result, _, _ = self.result(game)
        self.assertIsNone(result["by_event"]["venue-1"])

    def test_provider_roles_are_required_not_array_order(self):
        game = espn_game()
        for competitor in game["competitions"][0]["competitors"]:
            competitor.pop("homeAway")
        result, _, _ = self.result(game)
        self.assertIsNone(result["by_event"]["venue-1"])

    def test_conflicting_explicit_venue_home_away_rejected(self):
        event = venue_event()
        event["raw"]["teams"][0]["homeAway"] = "home"  # Beta is provider away.
        event["raw"]["teams"][1]["homeAway"] = "away"
        result, _, _ = self.result(event=event)
        self.assertIsNone(result["by_event"]["venue-1"])

    def test_market_group_titles_and_exact_direct_outcomes(self):
        event = venue_event()
        del event["raw"]["teams"]
        event["raw"]["markets"] = [{"sportsMarketType": "moneyline", "groupItemTitle": name,
                                     "outcomes": '["Yes","No"]'}
                                    for name in ("Alpha FC", "Draw (Alpha FC vs Beta FC)", "Beta FC")]
        result, _, _ = self.result(event=event)
        self.assertIsNotNone(result["by_event"]["venue-1"])
        for market in event["raw"]["markets"]:
            market.pop("outcomes")
        result, _, _ = self.result(event=event)
        self.assertIsNotNone(result["by_event"]["venue-1"])
        event = venue_event("nba", "nba", home="Alpha FC", away="Beta FC")
        del event["raw"]["teams"]
        event["raw"]["markets"] = [{"sportsMarketType": "moneyline", "outcomes": '["Beta FC","Alpha FC"]'}]
        result, _, _ = self.result(espn_game(period=4, status_name="STATUS_FINAL"), event, "nba")
        self.assertIsNotNone(result["by_event"]["venue-1"])

    def test_missing_clock_does_not_invent_zero_or_end_wallclock(self):
        game = espn_game()
        del game["competitions"][0]["status"]["displayClock"]
        result, _, _ = self.result(game)
        item = result["by_event"]["venue-1"]
        self.assertIsNone(item["clock"])
        self.assertIsNone(item["end_wallclock"])
        self.assertIn("clock", item["missing_fields"])

    def test_explicit_end_wallclock_preserved_not_receipt_imputed(self):
        game = espn_game()
        game["competitions"][0]["endDate"] = "2026-09-06T00:59:20Z"
        result, _, _ = self.result(game)
        self.assertEqual(result["by_event"]["venue-1"]["end_wallclock"], "2026-09-06T00:59:20Z")

    def test_completed_is_strict_bool_not_text_or_status_name_alone(self):
        for completed in ("true", 1, None, False):
            game = espn_game()
            game["competitions"][0]["status"]["type"]["completed"] = completed
            result, _, _ = self.result(game)
            self.assertFalse(result["by_event"]["venue-1"]["final"])

    def test_cancelled_postponed_suspended_are_never_final(self):
        for state in ("STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_SUSPENDED"):
            game = espn_game(status_name=state)
            result, _, _ = self.result(game)
            item = result["by_event"]["venue-1"]
            self.assertFalse(item["final"])
            self.assertFalse(item["regulation_result_known"])

    def test_soccer_normal_draw_known_but_et_or_penalty_total_not_regulation(self):
        result, _, _ = self.result(espn_game(score=(1, 1)))
        self.assertEqual(result["by_event"]["venue-1"]["regulation_result"], "DRAW")
        for period, name in ((4, "STATUS_FINAL"), (5, "STATUS_END_OF_PENALTIES")):
            result, _, _ = self.result(espn_game(period=period, status_name=name))
            item = result["by_event"]["venue-1"]
            self.assertTrue(item["final"])
            self.assertFalse(item["regulation_result_known"])
            self.assertIsNone(item["regulation_result"])

    def test_explicit_labeled_half_scores_recover_only_regulation_result(self):
        game = espn_game(period=4, status_name="STATUS_FINAL", score=(3, 2))
        competition = game["competitions"][0]
        for competitor in competition["competitors"]:
            competitor["linescores"] = [{"period": 1, "value": 1}, {"period": 2, "value": 0},
                                        {"period": 3, "value": 1}]
        result, _, _ = self.result(game)
        item = result["by_event"]["venue-1"]
        self.assertTrue(item["regulation_result_known"])
        self.assertEqual(item["regulation_result"], "DRAW")
        self.assertEqual(item["regulation_score"], {"home": 1, "away": 1})
        self.assertEqual(item["regulation_basis"], "explicit_labeled_first_two_halves")
        for competitor in competition["competitors"]:
            for row in competitor["linescores"]:
                row.pop("period")
        result, _, _ = self.result(game)
        self.assertFalse(result["by_event"]["venue-1"]["regulation_result_known"])

    def test_non_soccer_overtime_allowed_but_tie_has_no_winner(self):
        for family, period in (("nba", 5), ("nfl", 5), ("nhl", 4)):
            for scores in ((105, 100), (100, 100)):
                with self.subTest(family=family, scores=scores):
                    event = venue_event(family, family)
                    result, _, _ = self.result(espn_game(period=period, status_name="STATUS_FINAL", score=scores),
                                                event, family)
                    item = result["by_event"]["venue-1"]
                    self.assertTrue(item["final"])
                    self.assertEqual(item["whole_game_result_known"], scores[0] != scores[1])
                    self.assertEqual(item["winner_role"], "HOME" if scores[0] != scores[1] else None)

    def test_mlb_range_query_final_extra_innings_and_doubleheader(self):
        event = venue_event("mlb", "mlb", home="NY Yankees", away="Boston Red Sox")
        client, session, receipts = self.make([Response(mlb_board())])
        result = client.fetch_for_events([event], NOW)
        item = result["by_event"]["venue-1"]
        self.assertTrue(item["final"] and item["whole_game_result_known"])
        self.assertEqual(item["provider"], "mlb_statsapi")
        self.assertEqual(session.calls[0][1], "https://statsapi.mlb.com/api/v1/schedule")
        params = session.calls[0][2]["params"]
        self.assertEqual((params["startDate"], params["endDate"], params["sportId"]), ("2026-09-05", "2026-09-06", 1))
        self.assertEqual(params["hydrate"], "linescore")
        self.assertEqual(receipts[0][0]["source"], "mlb_statsapi")
        for flag in ("Y", "S"):
            game = mlb_game()
            game["doubleHeader"] = flag
            client, _, _ = self.make([Response(mlb_board([game]))])
            result = client.fetch_for_events([event], NOW)
            self.assertIsNone(result["by_event"]["venue-1"])
            self.assertTrue(any(e["reason"] == "doubleheader_unconfirmed" for e in result["errors"]))

    def test_projection_excludes_prices_roster_news_pii_secrets_and_headers(self):
        game = espn_game()
        game.update(news={"clientauth": "MUST-NOT-PERSIST", "email": "private@example.test"},
                    odds=[{"price": 0.95}], roster=[{"name": "Private Person"}])
        game["competitions"][0]["competitors"][0]["team"]["secretkey"] = "MUST-NOT-PERSIST"
        payload = board([game])
        payload.update(summary={"password": "MUST-NOT-PERSIST"}, movies=["Private Person"])
        client, _, receipts = self.make([Response(payload, headers={"Set-Cookie": "MUST-NOT-PERSIST"})])
        result = client.fetch_for_events([venue_event()], NOW)
        serialized = json.dumps([result, receipts]).lower()
        for unwanted in ("must-not-persist", "private@example", "private person", '"odds"',
                         '"roster"', '"password"', '"secretkey"', '"clientauth"', '"price"', '"set-cookie"'):
            self.assertNotIn(unwanted, serialized)
        receipt, projection = receipts[0]
        encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(receipt["payload_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(receipt["sha256_scope"], "canonical_utf8_json_economic_projection_not_http_body")

    def test_all_context_games_preserved_not_only_matching_event(self):
        client, _, receipts = self.make([Response(board([espn_game(), espn_game("unrelated", home="Elsewhere FC")]))])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertEqual(len(result["context"]), 2)
        self.assertEqual(len(receipts[0][1]["games"]), 2)
        self.assertIsNotNone(result["by_event"]["venue-1"])

    def test_score_change_has_receipt_interval_gap_not_exact_goal_or_causal_claim(self):
        client, _, _ = self.make([Response(board([espn_game(score=(1, 0), final=False)])),
                                  Response(board([espn_game(score=(2, 0), final=False)]))])
        first = client.fetch_for_events([venue_event()], NOW)["by_event"]["venue-1"]
        self.assertTrue(first["score_updates_gap"])
        with patch.object(news_module, "_utcnow", return_value="2026-09-06T01:02:00Z"):
            second = client.fetch_for_events([venue_event()], "2026-09-06T01:02:00Z")["by_event"]["venue-1"]
        self.assertEqual(second["snapshot_gap_seconds"], 120)
        self.assertTrue(second["score_changed"])
        self.assertEqual(second["score_time_basis"], "provider_snapshot_not_exact_goal_time")
        self.assertEqual(second["causal_independence"], "UNPROVEN")

    def test_no_auth_proxy_netrc_redirect_or_retry(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://invalid.example", "NETRC": "/must-not-read",
                                       "POLYMARKET_PRIVATE_KEY": "NOT-READ-BY-NEWS"}):
            client, session, _ = self.make([Response(board())])
            client.fetch_for_events([venue_event()], NOW)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.adapters["https://"].max_retries.total, 0)
        self.assertEqual(len(session.calls), 1)
        client.close()
        self.assertTrue(session.closed)

    def test_denial_latches_and_redirect_does_not_follow(self):
        for status in (403, 451, 302):
            response = Response({"secretkey": "MUST-NOT-PERSIST"}, status,
                                {"Location": "https://unlisted.invalid/alternate"})
            client, session, receipts = self.make([response])
            result = client.fetch_for_events([venue_event()], NOW)
            self.assertIsNone(result["by_event"]["venue-1"])
            self.assertEqual(len(session.calls), 1)
            self.assertIsNone(receipts[0][1])
            self.assertTrue(response.closed)
            if status in (403, 451):
                client.fetch_for_events([venue_event()], NOW)
                self.assertEqual(len(session.calls), 1)

    def test_exhausted_budget_and_stream_timeout_are_explicit_and_bounded(self):
        client, session, receipts = self.make([], Budget(0))
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertEqual(session.calls, [])
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertEqual(receipts[0][0]["result_status"], "TIMEOUT")
        budget = Budget()
        response = Response(board(), on_read=lambda: setattr(budget, "remaining", 0))
        client, session, receipts = self.make([response], budget)
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertTrue(response.closed)
        self.assertIsNone(receipts[0][1])

    def test_bad_json_oversized_body_compression_and_http_errors_no_raw_fallback(self):
        invalid = Response({})
        invalid.body = b'{"clientauth":"MUST-NOT-PERSIST"'
        cases = [invalid, Response(board(), headers={"Content-Encoding": "gzip"}),
                 Response({"password": "MUST-NOT-PERSIST"}, 500),
                 requests.Timeout("secretkey=MUST-NOT-PERSIST")]
        for response in cases:
            client, _, receipts = self.make([response])
            result = client.fetch_for_events([venue_event()], NOW)
            self.assertIsNone(result["by_event"]["venue-1"])
            self.assertIsNone(receipts[0][1])
            self.assertNotIn("MUST-NOT-PERSIST", json.dumps([result, receipts]))
        with patch.object(news_module, "MAX_BODY_BYTES", 10):
            client, _, receipts = self.make([Response(board())])
            client.fetch_for_events([venue_event()], NOW)
            self.assertIsNone(receipts[0][1])

    def test_sink_failure_propagates_and_never_claims_success(self):
        def failing_sink(receipt, payload):
            raise RuntimeError("fixture durable sink unavailable")
        response = Response(board())
        client, _, _ = self.make([response], sink=failing_sink)
        with self.assertRaisesRegex(RuntimeError, "durable sink"):
            client.fetch_for_events([venue_event()], NOW)
        self.assertTrue(response.closed)

    def test_distinct_events_same_league_share_one_call_without_price_gates(self):
        first = venue_event()
        second = venue_event(event_id="venue-2", home="Gamma FC", away="Delta FC")
        first["raw"].update(price=0.0001, liquidity=0, volume=0)
        second["raw"].update(price=0.9999, liquidity=100000000)
        games = [espn_game(), espn_game("provider-2", home="Gamma FC", away="Delta FC")]
        client, session, receipts = self.make([Response(board(games))])
        result = client.fetch_for_events([first, second], NOW)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(all(result["by_event"].values()))
        self.assertNotIn('"price"', json.dumps(receipts))

    def test_previous_utc_day_supported_but_missing_ambiguous_and_old_schedule_unconfirmed(self):
        yesterday = "2026-09-05T23:00:00Z"
        event = venue_event()
        event["raw"]["startTime"] = yesterday
        result, _, _ = self.result(espn_game(date=yesterday), event)
        self.assertIsNotNone(result["by_event"]["venue-1"])
        for changes in ({"startTime": None}, {"startTime": "2026-09-06T00:00:00"},
                        {"startTime": "2026-09-04T23:00:00Z"},
                        {"gameStartTime": "2026-09-06T01:00:00Z"}):
            event = venue_event()
            event["raw"].update(changes)
            client, session, _ = self.make([])
            result = client.fetch_for_events([event], NOW)
            self.assertIsNone(result["by_event"]["venue-1"])
            self.assertEqual(session.calls, [])

    def test_duplicate_venue_ids_and_multiple_venue_events_for_one_game(self):
        client, session, _ = self.make([])
        result = client.fetch_for_events([venue_event(), venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertFalse(session.calls)
        client, _, _ = self.make([Response(board())])
        result = client.fetch_for_events([venue_event(), venue_event(event_id="another")], NOW)
        self.assertTrue(all(row is None for row in result["by_event"].values()))
        self.assertTrue(any(e["reason"] == "multiple_venue_events_for_provider_game" for e in result["errors"]))

    def test_duplicate_provider_snapshots_never_become_zero_second_score_update(self):
        client, _, _ = self.make([Response(board([espn_game(), espn_game()]))])
        result = client.fetch_for_events([venue_event()], NOW)
        for row in result["context"]:
            self.assertIsNone(row["snapshot_gap_seconds"])
            self.assertIsNone(row["score_changed"])
            self.assertFalse(row["valid_for_matching"])

    def test_period_score_projection_is_not_silently_truncated(self):
        game = espn_game()
        game["competitions"][0]["competitors"][0]["linescores"] = [{"value": 1}] * 101
        client, _, receipts = self.make([Response(board([game]))])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertIsNone(receipts[0][1])

    def test_incomplete_count_and_wrong_game_league_do_not_match(self):
        payload = board()
        payload["totalEvents"] = 2
        client, _, receipts = self.make([Response(payload)])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertTrue(receipts[0][1]["coverage"]["possible_gap"])
        self.assertFalse(result["context"][0]["source_coverage_complete"])
        game = espn_game()
        game["competitions"][0]["league"] = {"abbreviation": "ger.1"}
        result, _, _ = self.result(game)
        self.assertIsNone(result["by_event"]["venue-1"])

    def test_secret_inside_selected_field_is_not_persisted_or_echoed(self):
        game = espn_game()
        game["competitions"][0]["competitors"][0]["team"]["displayName"] = "authorization=MUST-NOT-PERSIST"
        game["competitions"][0]["status"]["type"]["detail"] = {"clientauth": "MUST-NOT-PERSIST"}
        result, _, receipts = self.result(game)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertNotIn("MUST-NOT-PERSIST", json.dumps([result, receipts]))

    def test_penalty_abbreviation_and_missing_regulation_period_never_assume_full_time(self):
        for change in ({"period": 2, "name": "STATUS_FINAL_PEN"},
                       {"period": None, "name": "STATUS_FULL_TIME"}):
            game = espn_game(period=change["period"], status_name=change["name"])
            result, _, _ = self.result(game)
            self.assertFalse(result["by_event"]["venue-1"]["regulation_result_known"])

    def test_extra_time_in_progress_requires_completed_labeled_halves_for_regulation(self):
        for period, known in ((2, False), (3, True)):
            game = espn_game(period=period, final=False, status_name="STATUS_IN_PROGRESS")
            for competitor in game["competitions"][0]["competitors"]:
                competitor["linescores"] = [{"period": 1, "value": 1}, {"period": 2, "value": 0}]
            result, _, _ = self.result(game)
            item = result["by_event"]["venue-1"]
            self.assertFalse(item["final"])
            self.assertEqual(item["regulation_result_known"], known)

    def test_mlb_tie_cancelled_missing_doubleheader_and_minor_sport(self):
        event = venue_event("mlb", "mlb", home="New York Yankees", away="Boston Red Sox")
        game = mlb_game(score=(4, 4))
        client, _, _ = self.make([Response(mlb_board([game]))])
        item = client.fetch_for_events([event], NOW)["by_event"]["venue-1"]
        self.assertTrue(item["final"])
        self.assertFalse(item["whole_game_result_known"])
        self.assertIsNone(item["winner_role"])
        game["status"]["detailedState"] = "Postponed"
        client, _, _ = self.make([Response(mlb_board([game]))])
        self.assertFalse(client.fetch_for_events([event], NOW)["by_event"]["venue-1"]["final"])
        game = mlb_game()
        game.pop("doubleHeader")
        client, _, _ = self.make([Response(mlb_board([game]))])
        self.assertIsNone(client.fetch_for_events([event], NOW)["by_event"]["venue-1"])
        game = mlb_game()
        game["sport"] = {"id": 11, "name": "Triple-A"}
        client, _, _ = self.make([Response(mlb_board([game]))])
        self.assertIsNone(client.fetch_for_events([event], NOW)["by_event"]["venue-1"])

    def test_auth_mutation_is_refused_and_response_cookie_is_not_reused(self):
        client, session, receipts = self.make([])
        session.auth = ("fixture-user", "MUST-NOT-PERSIST")
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertFalse(session.calls)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertNotIn("MUST-NOT-PERSIST", json.dumps(receipts))
        client, session, _ = self.make([Response(board()), Response(board())])
        client.fetch_for_events([venue_event()], NOW)
        session.cookies.set("fixture-cookie", "MUST-NOT-PERSIST")
        client.fetch_for_events([venue_event()], NOW)
        self.assertFalse(session.cookies)
        self.assertEqual(len(session.calls), 2)

    def test_unallowlisted_route_and_closed_client_never_request(self):
        client, session, _ = self.make([])
        now = news_module._dt(NOW).date()
        with self.assertRaises(ValueError):
            client._request(("soccer", "https://unlisted.invalid"), now, now)
        self.assertFalse(session.calls)
        client.close()
        with self.assertRaises(RuntimeError):
            client.fetch_for_events([venue_event()], NOW)

    def test_total_attempt_deadline_expires_even_when_cycle_budget_is_positive(self):
        ticks = iter(range(0, 200, 6))
        response = Response(board())
        with patch.object(news_module.time, "monotonic", side_effect=lambda: next(ticks)):
            client, session, receipts = self.make([response])
            result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertEqual(receipts[0][0]["result_status"], "TIMEOUT")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)

    def test_older_transport_streams_one_byte_with_budget_checks(self):
        class OlderResponse(Response):
            def __init__(self, payload):
                super().__init__(payload)
                self.raw = None

            def iter_content(self, chunk_size):
                self.requested_chunk_size = chunk_size
                for offset in range(len(self.body)):
                    yield self.body[offset:offset + 1]

        response = OlderResponse(board())
        client, _, _ = self.make([response])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNotNone(result["by_event"]["venue-1"])
        self.assertEqual(response.requested_chunk_size, 1)

    def test_rate_limit_is_not_retried_and_malformed_game_is_explicit_context_gap(self):
        client, session, receipts = self.make([Response({}, 429, {"Retry-After": "600"})])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertIsNone(result["by_event"]["venue-1"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(receipts[0][0]["status"], 429)
        client, _, _ = self.make([Response(board([None]))])
        result = client.fetch_for_events([venue_event()], NOW)
        self.assertFalse(result["context"][0]["valid_for_matching"])
        self.assertTrue(result["context"][0]["identity_errors"])

    def test_received_time_is_body_receipt_not_projection_end(self):
        times = ["2026-09-06T01:00:00Z", "2026-09-06T01:00:01Z",
                 "2026-09-06T01:00:02Z", "2026-09-06T01:00:04Z"]
        client, _, receipts = self.make([Response(board())])
        with patch.object(news_module, "_utcnow", side_effect=times):
            result = client.fetch_for_events([venue_event()], NOW)
        receipt = receipts[0][0]
        self.assertEqual(receipt["headers_received_at"], times[1])
        self.assertEqual(receipt["received_at"], times[2])
        self.assertEqual(receipt["projection_completed_at"], times[3])
        self.assertTrue(receipt["body_complete"])
        self.assertGreater(receipt["body_bytes"], 0)
        self.assertEqual(result["by_event"]["venue-1"]["received_at"], times[2])

    def test_missing_score_stays_unknown_not_zero_and_update_time_is_scoped(self):
        game = espn_game()
        game["competitions"][0]["competitors"][0].pop("score")
        game["updatedAt"] = "2026-09-06T00:58:00Z"
        result, _, _ = self.result(game)
        item = result["by_event"]["venue-1"]
        self.assertIsNone(item["away"]["score"])
        self.assertFalse(item["regulation_result_known"])
        self.assertEqual(item["provider_updated_at"], "2026-09-06T00:58:00Z")
        self.assertEqual(item["provider_updated_at_basis"], "provider_update_not_exact_score_time")

    def expectation(self, score=(2, 1), family="soccer", game=None, event=None):
        event = event or payout_event(family)
        game = game or espn_game(score=score, period=2 if family == "soccer" else 5,
                                status_name="STATUS_FULL_TIME" if family == "soccer" else "STATUS_FINAL")
        result, _, _ = self.result(game, event, "eng.1" if family == "soccer" else family)
        news = result["by_event"][event["event_id"]]
        self.assertIsNotNone(news)
        return event, news, news_module.derive_expected_payouts(event, news)

    def assert_expected_payouts(self, result, payouts, scope="REGULATION"):
        self.assertEqual(result["status"], "OK", result)
        self.assertEqual(result["token_payouts"], payouts)
        self.assertIs(result["rule_authority_verified"], False)
        self.assertEqual(result["basis"], "PROVIDER_RESULT_EXPECTATION_NOT_VENUE_SETTLEMENT")
        self.assertEqual(result["result_scope"], scope)
        self.assertTrue(all(type(value) is int and value in (0, 1) for value in result["token_payouts"].values()))

    def test_expected_soccer_home_win_six_tokens(self):
        _, _, result = self.expectation(score=(2, 1))
        self.assert_expected_payouts(result, {"home-yes": 1, "home-no": 0, "draw-yes": 0,
                                              "draw-no": 1, "away-yes": 0, "away-no": 1})

    def test_expected_soccer_draw_six_tokens(self):
        _, _, result = self.expectation(score=(1, 1))
        self.assert_expected_payouts(result, {"home-yes": 0, "home-no": 1, "draw-yes": 1,
                                              "draw-no": 0, "away-yes": 0, "away-no": 1})

    def test_expected_soccer_away_win_six_tokens(self):
        _, _, result = self.expectation(score=(0, 3))
        self.assert_expected_payouts(result, {"home-yes": 0, "home-no": 1, "draw-yes": 0,
                                              "draw-no": 1, "away-yes": 1, "away-no": 0})

    def test_expected_direct_two_tokens_follow_exact_labels_not_array_order(self):
        event, news, result = self.expectation(family="nba", score=(100, 105))
        self.assert_expected_payouts(result, {"home-token": 0, "away-token": 1}, "WHOLE_GAME")
        market = event["raw"]["markets"][0]
        market["outcomes"] = ["Alpha FC", "Beta FC"]
        market["clobTokenIds"] = ["home-token", "away-token"]
        event["raw"]["teams"].reverse()
        repeated = news_module.derive_expected_payouts(event, news)
        self.assert_expected_payouts(repeated, result["token_payouts"], "WHOLE_GAME")

    def test_expected_soccer_yes_no_arrays_and_market_order_may_be_reversed(self):
        event, news, result = self.expectation()
        for market in event["raw"]["markets"]:
            market["outcomes"] = ["No", "Yes"]
            market["clobTokenIds"].reverse()
        event["raw"]["markets"].reverse()
        event["raw"]["teams"].reverse()
        self.assert_expected_payouts(news_module.derive_expected_payouts(event, news), result["token_payouts"])

    def test_expected_aliases_use_exact_matched_team_forms(self):
        event = payout_event()
        event["raw"]["teams"][1]["name"] = "Man Utd"
        event["raw"]["markets"][0]["groupItemTitle"] = "Manchester United"
        event["raw"]["markets"][1]["groupItemTitle"] = "Draw (Man Utd vs Beta FC)"
        _, _, result = self.expectation(event=event, game=espn_game(home="Manchester United"))
        self.assertEqual(result["status"], "OK", result)
        self.assertEqual(result["token_payouts"]["home-yes"], 1)

    def test_expected_direct_tie_is_unsupported_not_zero_payouts(self):
        _, _, result = self.expectation(family="nfl", score=(20, 20))
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(result["token_payouts"], {})
        self.assertFalse(result["rule_authority_verified"])

    def test_expected_missing_news_wrong_date_team_league_or_scope_is_unconfirmed(self):
        event, news, _ = self.expectation()
        cases = [None, {}, {**news, "league_code": "bun"}, {**news, "provider": "unlisted"},
                 {**news, "scheduled_at": "2026-09-05T00:00:00Z"},
                 {**news, "result_scope": "UNKNOWN"},
                 {**news, "status": {"type": {"name": "STATUS_CANCELED"}}},
                 {**news, "home": {**news["home"], "name": "Other FC"}},
                 {**news, "matching_evidence": None}, {**news, "source_coverage_complete": False}]
        for invalid in cases:
            with self.subTest(invalid=invalid is None):
                result = news_module.derive_expected_payouts(event, invalid)
                self.assertNotEqual(result["status"], "OK")
                self.assertEqual(result["token_payouts"], {})
                self.assertTrue(result["reason"])

    def test_expected_wrong_labels_missing_token_duplicate_token_and_unknown_market_scope(self):
        original, news, _ = self.expectation()
        for change in (lambda e: e["raw"]["markets"][0].update(groupItemTitle="Alpha FC or Draw"),
                       lambda e: e["raw"]["markets"][1].update(groupItemTitle="Draw no bet"),
                       lambda e: e["raw"]["markets"][0].update(outcomes=["Yes", "Yes"]),
                       lambda e: e["raw"]["markets"][0].update(clobTokenIds=["home-yes"]),
                       lambda e: e["raw"]["markets"][0].update(clobTokenIds=["draw-yes", "home-no"]),
                       lambda e: e["raw"]["markets"][0].update(sportsMarketType="unknown"),
                       lambda e: e["raw"]["markets"][0].update(result_scope="FIRST_HALF"),
                       lambda e: e.update(expected_token_ids=[])):
            event = deepcopy(original)
            change(event)
            result = news_module.derive_expected_payouts(event, news)
            self.assertNotEqual(result["status"], "OK", result)
            self.assertEqual(result["token_payouts"], {})

    def test_expected_et_aggregate_never_substitutes_for_regulation(self):
        game = espn_game(period=4, status_name="STATUS_FINAL", score=(3, 2))
        event, news, result = self.expectation(game=game)
        self.assertEqual(result["token_payouts"], {})
        spoofed = {**news, "regulation_result_known": True, "regulation_result": "HOME",
                   "regulation_score": {"home": 3, "away": 2}}
        self.assertEqual(news_module.derive_expected_payouts(event, spoofed)["token_payouts"], {})
        for competitor in game["competitions"][0]["competitors"]:
            competitor["linescores"] = [{"period": 1, "value": 1}, {"period": 2, "value": 0}]
        _, _, confirmed = self.expectation(game=game)
        self.assertEqual(confirmed["status"], "OK", confirmed)
        self.assertEqual(confirmed["token_payouts"]["draw-yes"], 1)
        self.assertEqual(confirmed["token_payouts"]["home-yes"], 0)

    def test_expected_helper_is_pure_and_never_verifies_venue_rules(self):
        event, news, expected = self.expectation()
        event["raw"]["markets"][0]["description"] = "Cancellation and void policies need a separate authority review."
        before = deepcopy((event, news))
        with patch.object(news_module, "_utcnow", side_effect=AssertionError("pure helper must not read time")), \
             patch.object(news_module.requests, "Session", side_effect=AssertionError("pure helper must not open client")):
            result = news_module.derive_expected_payouts(event, news)
        self.assertEqual((event, news), before)
        self.assert_expected_payouts(result, expected["token_payouts"])
        self.assertIs(result["rule_authority_verified"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)

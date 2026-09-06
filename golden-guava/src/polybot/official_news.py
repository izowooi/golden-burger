"""Independent acquisition of *provider snapshots*, not independent causal news.

Only frozen ESPN scoreboard and MLB schedule GET endpoints are used. Two UTC
dates are requested together, once per needed league; soccer requires one path
per competition. ESPN is a publisher, not proof of an independent upstream feed
or the market's settlement authority. Endpoint response contracts are fixture-
tested here; deployment must verify them without substituting another host.

The sink receives a whitelisted economic projection, NEVER the entire HTTP JSON,
error body, roster, news, odds or arbitrary headers. SHA-256 covers canonical
UTF-8 JSON of that projection, not discarded wire bytes. Secrets in unselected
fields are discarded; credential-shaped selected text is not retained either.

Network/body deadlines are cooperative, including between read1 calls. Requests
connect/read timeouts cannot supply a hard DNS/header/fsync wall-clock SLA.
No auth, environment/netrc/proxy lookup, redirects, retries or access-denial
fallback. Sink failures propagate and cannot be converted to successful news.

For soccer, use regulation_result_known/regulation_result; for other sports use
whole_game_result_known/winner_role, which allow completed overtime. Receipt
intervals cannot locate an exact goal or completion time. No price, execution,
account, signing or profit functionality is present.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time
import unicodedata
from uuid import uuid4

import requests


PROJECTION_SCOPE = "guava-official-news-economic-projection-v1"
HASH_SCOPE = "canonical_utf8_json_economic_projection_not_http_body"
EXPECTED_PAYOUT_BASIS = "PROVIDER_RESULT_EXPECTATION_NOT_VENUE_SETTLEMENT"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_GAMES = 4096
ATTEMPT_SECONDS = 15.0
MAX_SCHEDULE_DIFFERENCE_SECONDS = 90 * 60

# No host or path is supplied by venue data, provider JSON, redirects or callers.
HOSTS = {"espn": "https://site.api.espn.com", "mlb_statsapi": "https://statsapi.mlb.com"}
ROUTES = {
    ("soccer", "epl"): ("espn", "/apis/site/v2/sports/soccer/eng.1/scoreboard"),
    ("soccer", "bun"): ("espn", "/apis/site/v2/sports/soccer/ger.1/scoreboard"),
    ("soccer", "fl1"): ("espn", "/apis/site/v2/sports/soccer/fra.1/scoreboard"),
    ("soccer", "lal"): ("espn", "/apis/site/v2/sports/soccer/esp.1/scoreboard"),
    ("soccer", "mls"): ("espn", "/apis/site/v2/sports/soccer/usa.1/scoreboard"),
    ("soccer", "sea"): ("espn", "/apis/site/v2/sports/soccer/ita.1/scoreboard"),
    ("soccer", "ucl"): ("espn", "/apis/site/v2/sports/soccer/uefa.champions/scoreboard"),
    ("soccer", "uel"): ("espn", "/apis/site/v2/sports/soccer/uefa.europa/scoreboard"),
    ("nba", "nba"): ("espn", "/apis/site/v2/sports/basketball/nba/scoreboard"),
    ("nfl", "nfl"): ("espn", "/apis/site/v2/sports/football/nfl/scoreboard"),
    ("nhl", "nhl"): ("espn", "/apis/site/v2/sports/hockey/nhl/scoreboard"),
    ("mlb", "mlb"): ("mlb_statsapi", "/api/v1/schedule"),
}
LEAGUE_NAMES = {
    "epl": ("epl", "eng.1", "Premier League", "English Premier League", "english-premier-league"),
    "bun": ("bun", "ger.1", "Bundesliga", "German Bundesliga", "german-bundesliga"),
    "fl1": ("fl1", "fra.1", "Ligue 1", "French Ligue 1", "french-ligue-1"),
    "lal": ("lal", "esp.1", "LaLiga", "La Liga", "Spanish LALIGA", "Spanish Primera Division"),
    "mls": ("mls", "usa.1", "Major League Soccer", "MLS", "United States Major League Soccer"),
    "sea": ("sea", "ita.1", "Serie A", "Italian Serie A", "italian-serie-a"),
    "ucl": ("ucl", "uefa.champions", "UEFA Champions League", "uefa-champions-league"),
    "uel": ("uel", "uefa.europa", "UEFA Europa League", "uefa-europa-league"),
    "nba": ("nba", "National Basketball Association"),
    "nfl": ("nfl", "National Football League"),
    "nhl": ("nhl", "National Hockey League"),
    "mlb": ("mlb", "Major League Baseball"),
}
# Explicit small aliases, not edit-distance, substring or generic suffix removal.
# New aliases require reviewed evidence and a fixture; unsupported names fail closed.
TEAM_ALIASES = {
    "soccer": (
        ("Manchester United", "Man United", "Man Utd"),
        ("Manchester City", "Man City"),
        ("Tottenham Hotspur", "Tottenham", "Spurs"),
        ("Newcastle United", "Newcastle"),
        ("Brighton & Hove Albion", "Brighton and Hove Albion", "Brighton"),
        ("Paris Saint-Germain", "Paris Saint Germain", "PSG"),
        ("Bayern Munich", "Bayern München", "FC Bayern München"),
        ("Internazionale", "Inter Milan", "Inter"),
        ("AC Milan", "A.C. Milan", "Milan"),
        ("Atletico Madrid", "Atlético de Madrid", "Atletico de Madrid"),
    ),
    "nba": (("Los Angeles Lakers", "LA Lakers"), ("Los Angeles Clippers", "LA Clippers")),
    "nfl": (("Los Angeles Rams", "LA Rams"), ("Los Angeles Chargers", "LA Chargers")),
    "nhl": (("Los Angeles Kings", "LA Kings"), ("New York Rangers", "NY Rangers", "N.Y. Rangers")),
    "mlb": (("New York Yankees", "NY Yankees", "N.Y. Yankees"),
            ("New York Mets", "NY Mets", "N.Y. Mets"), ("Los Angeles Dodgers", "LA Dodgers")),
}
_PRIVATE_TEXT = re.compile(
    r"\b(?:bearer|basic)\s+\S+|private.?key|secret.?key|client.?auth|api.?key|"
    r"access.?token|authorization|password|passphrase|-----BEGIN|[\w.+-]+@[\w.-]+\.[a-z]{2,}|://", re.I,
)
_STOPPED = re.compile(r"cancel|postpon|suspend|abandon|forfeit|delay|interrupt|no.?contest", re.I)
_EXTRA = re.compile(r"extra.?time|overtime|penalt|shoot.?out|(?:^|[_\W])(?:aet|pens?|pso|ot)(?:$|[_\W])", re.I)
_TEAM_FIELDS = ("id", "name", "displayName", "shortDisplayName", "abbreviation", "location", "alias")
_SOURCE_TIMES = ("endDate", "endTime", "completedAt", "gameEndDateTime", "regulationEndTime",
                 "lastUpdated", "lastModified", "updatedAt")


def _utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or _PRIVATE_TEXT.search(value):
        return None
    return value.strip()


def _norm(value):
    value = _text(value)
    if value is None:
        return ""
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value
                            if not unicodedata.combining(ch)).split())


def _league(value):
    normalized = _norm(value)
    return next((code for code, names in LEAGUE_NAMES.items()
                 if normalized and normalized in {_norm(name) for name in names}), None)


def _dt(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _iso(value):
    parsed = _dt(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _identifier(value):
    if type(value) is int and value >= 0:
        return str(value)
    return _text(value) if isinstance(value, str) else None


def _integer(value):
    if type(value) is int:
        return value if 0 <= value <= 100000 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}(?:\.0+)?", value):
        result = int(value.split(".")[0])
        return result if result <= 100000 else None
    return None


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _reject_json_constant(_):
    raise ValueError("nonfinite_json")


def _pick(source, fields):
    """Only safe scalars in named economic fields; never copy an arbitrary dict."""
    if not isinstance(source, dict):
        return {}
    selected = {}
    for field in fields:
        if field not in source:
            continue
        value = source[field]
        if value is None or type(value) is bool or (type(value) is int and abs(value) <= 10**15):
            selected[field] = value
        elif isinstance(value, str):
            safe = _text(value)
            if safe is not None:
                selected[field] = safe
        elif type(value) is float and math.isfinite(value):
            selected[field] = value
    return selected


def _status_projection(source):
    result = _pick(source, ("period", "displayClock", "clock"))
    if isinstance(source, dict) and isinstance(source.get("type"), dict):
        result["type"] = _pick(source["type"], ("id", "name", "state", "completed", "description", "detail", "shortDetail"))
    return result


def _line_projection(rows):
    if not isinstance(rows, list):
        return []
    if len(rows) > 100:
        raise ValueError("too_many_period_scores")
    result = []
    for row in rows:
        selected = _pick(row, ("period", "value", "displayValue", "periodType", "name", "label"))
        if isinstance(row, dict) and isinstance(row.get("period"), dict):
            selected["period"] = _pick(row["period"], ("number", "type", "name"))
        result.append(selected)
    return result


def _espn_projection(game):
    if not isinstance(game, dict):
        return [{"competition_count": 0}]
    competitions = game.get("competitions")
    competitions = competitions if isinstance(competitions, list) else []
    if len(competitions) > MAX_GAMES:
        raise ValueError("too_many_competitions")
    result = []
    for competition in competitions or [{}]:
        row = {"event": _pick(game, ("id", "date", *_SOURCE_TIMES)),
               "competition_count": len(competitions)}
        if isinstance(game.get("league"), dict):
            row["event"]["league"] = _pick(game["league"], ("id", "name", "slug", "abbreviation"))
        row.update(_pick(competition, ("id", "date", "doubleHeader", "gameNumber", *_SOURCE_TIMES)))
        if isinstance(competition, dict) and isinstance(competition.get("league"), dict):
            row["league"] = _pick(competition["league"], ("id", "name", "slug", "abbreviation"))
        source_status = competition.get("status", game.get("status")) if isinstance(competition, dict) else None
        row["status"] = _status_projection(source_status)
        competitors = competition.get("competitors") if isinstance(competition, dict) else None
        row["competitors"] = []
        for competitor in competitors if isinstance(competitors, list) else []:
            selected = _pick(competitor, ("id", "homeAway", "score", "shootoutScore"))
            if isinstance(competitor, dict):
                selected["team"] = _pick(competitor.get("team"), _TEAM_FIELDS)
                if "linescores" in competitor:
                    selected["linescores"] = _line_projection(competitor["linescores"])
            row["competitors"].append(selected)
        result.append(row)
    return result


def _mlb_projection(game):
    result = _pick(game, ("gamePk", "gameDate", "officialDate", "doubleHeader", "gameNumber",
                          "scheduledInnings", "gameType", *_SOURCE_TIMES))
    game = game if isinstance(game, dict) else {}
    if "sport" in game:
        result["sport"] = _pick(game["sport"], ("id", "name", "abbreviation"))
    result["status"] = _pick(game.get("status"),
                            ("abstractGameState", "abstractGameCode", "codedGameState", "detailedState", "statusCode"))
    result["teams"] = {}
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    for role in ("home", "away"):
        source = teams.get(role) if isinstance(teams.get(role), dict) else {}
        result["teams"][role] = {**_pick(source, ("score",)), "team": _pick(source.get("team"), _TEAM_FIELDS)}
    lines = game.get("linescore") if isinstance(game.get("linescore"), dict) else {}
    result["linescore"] = _pick(lines, ("currentInning", "currentInningOrdinal", "inningState", "scheduledInnings"))
    if isinstance(lines.get("innings"), list):
        if len(lines["innings"]) > 100:
            raise ValueError("too_many_inning_scores")
        result["linescore"]["innings"] = [
            {**_pick(row, ("num", "ordinalNum")),
             **{role: _pick(row.get(role), ("runs",)) for role in ("home", "away")}}
            for row in lines["innings"] if isinstance(row, dict)
        ]
    return result


def _response_league_ok(descriptors, code):
    if descriptors is None:
        return True  # The frozen endpoint is league-scoped; absence is disclosed.
    if not isinstance(descriptors, list) or len(descriptors) != 1 or not isinstance(descriptors[0], dict):
        return False
    recognized = {_league(descriptors[0].get(key)) for key in ("abbreviation", "slug", "name")} - {None}
    return recognized == {code}


def _project(payload, route, first_day, last_day):
    if not isinstance(payload, dict):
        raise ValueError("scoreboard_object_required")
    family, code = route
    provider = ROUTES[route][0]
    reasons = []
    descriptors = None
    games = []
    if provider == "espn":
        raw_games = payload.get("events")
        if not isinstance(raw_games, list):
            raise ValueError("scoreboard_events_missing")
        descriptors = payload.get("leagues")
        if not _response_league_ok(descriptors, code):
            reasons.append("provider_league_mismatch")
        if len(raw_games) > MAX_GAMES:
            raise ValueError("too_many_games")
        for game in raw_games:
            games.extend(_espn_projection(game))
        declared_total = _integer(payload.get("totalEvents"))
        if len(raw_games) >= 1000:
            reasons.append("scoreboard_limit_reached")
    else:
        dates = payload.get("dates")
        if not isinstance(dates, list):
            raise ValueError("schedule_dates_missing")
        raw_games = []
        for date_row in dates:
            if not isinstance(date_row, dict) or not isinstance(date_row.get("games"), list):
                raise ValueError("schedule_games_missing")
            raw_games.extend(date_row["games"])
        if len(raw_games) > MAX_GAMES:
            raise ValueError("too_many_games")
        games = [_mlb_projection(game) for game in raw_games]
        declared_total = _integer(payload.get("totalGames"))
    if declared_total is not None and declared_total != len(raw_games):
        reasons.append("provider_game_count_mismatch")
    if len(games) > MAX_GAMES:
        raise ValueError("too_many_game_snapshots")
    projection = {"projection_scope": PROJECTION_SCOPE, "provider": provider,
                  "sport_family": family, "league_code": code, "games": games,
                  "coverage": {"scope": "requested_active_leagues_two_utc_days_only",
                               "start_date": first_day.isoformat(), "end_date": last_day.isoformat(),
                               "raw_event_count": len(raw_games), "snapshot_count": len(games),
                               "declared_game_count": declared_total, "possible_gap": bool(reasons),
                               "reasons": reasons, "response_league_explicit": descriptors is not None}}
    if isinstance(descriptors, list):
        projection["leagues"] = [_pick(row, ("id", "name", "slug", "abbreviation")) for row in descriptors[:16]]
    return projection


def _forms(team, family):
    if not isinstance(team, dict):
        return set()
    forms = {_norm(team.get(key)) for key in ("name", "displayName", "shortDisplayName", "abbreviation", "alias")} - {""}
    if _text(team.get("location")) and _text(team.get("name")):
        forms.add(_norm(team["location"] + " " + team["name"]))
    for aliases in TEAM_ALIASES.get(family, ()):
        group = {_norm(alias) for alias in aliases}
        if forms & group:
            forms |= group
    return forms


def _team(source):
    source = source if isinstance(source, dict) else {}
    team = source.get("team") if isinstance(source.get("team"), dict) else {}
    return {"team_id": _identifier(team.get("id")),
            "name": next((_text(team.get(key)) for key in ("displayName", "name", "shortDisplayName")
                          if _text(team.get(key))), None), "score": _integer(source.get("score"))}


def _period(row):
    value = row.get("period")
    if isinstance(value, dict):
        if _norm(value.get("type")) in {"overtime", "extra time", "penalties"}:
            return None
        value = value.get("number")
    if _norm(row.get("periodType")) in {"overtime", "extra time", "penalties"}:
        return None
    number = _integer(value)
    if number is not None:
        return number
    labels = {_norm(row.get(key)) for key in ("label", "name")}
    if labels & {"first half", "1st half"}:
        return 1
    if labels & {"second half", "2nd half"}:
        return 2
    return None


def _halves(competitor):
    found = {}
    for row in competitor.get("linescores", []):
        period = _period(row)
        if period not in (1, 2):
            continue
        score = _integer(row.get("value", row.get("displayValue")))
        if period in found or score is None:
            return None
        found[period] = score
    return found[1] + found[2] if set(found) == {1, 2} else None


def _outcome(home, away):
    if home is None or away is None:
        return None
    return "HOME" if home > away else "AWAY" if away > home else "DRAW"


def _news_snapshot(raw, route, request):
    family, code = route
    provider = ROUTES[route][0]
    invalid = []
    if provider == "espn":
        provider_id = _identifier(raw.get("event", {}).get("id"))
        date = raw.get("date", raw.get("event", {}).get("date"))
        if raw.get("competition_count") != 1:
            invalid.append("ambiguous_provider_competitions")
        for source in (raw, raw.get("event", {})):
            if "league" in source and not _response_league_ok([source["league"]], code):
                invalid.append("provider_game_league_mismatch")
        event_date = _iso(raw.get("event", {}).get("date"))
        if event_date and _iso(date) and event_date != _iso(date):
            invalid.append("conflicting_provider_schedule")
        competitors = raw.get("competitors", [])
        roles = {role: [item for item in competitors if item.get("homeAway") == role] for role in ("home", "away")}
        if len(competitors) != 2 or any(len(roles[role]) != 1 for role in roles):
            invalid.append("provider_home_away_roles_unproven")
        home_source = roles["home"][0] if len(roles["home"]) == 1 else {}
        away_source = roles["away"][0] if len(roles["away"]) == 1 else {}
        status = deepcopy(raw.get("status", {}))
        status_type = status.get("type", {})
        status_text = " ".join(str(status_type.get(key, "")) for key in ("name", "description", "detail", "shortDetail"))
        stopped = bool(_STOPPED.search(status_text))
        final = status_type.get("completed") is True and status_type.get("state") == "post" and not stopped
        period = _integer(status.get("period"))
        clock = _text(status.get("displayClock"))
        extra = (period is not None and period > 2) or bool(_EXTRA.search(status_text)) or any(
            "shootoutScore" in competitor for competitor in competitors
        )
        full_time = status_type.get("name") == "STATUS_FULL_TIME" or any(
            _norm(status_type.get(key)) in {"ft", "full time"} for key in ("description", "detail", "shortDetail")
        )
        normal_regulation = final and period == 2 and full_time and not extra
    else:
        provider_id, date = _identifier(raw.get("gamePk")), raw.get("gameDate")
        if "sport" in raw and _integer(raw["sport"].get("id")) != 1:
            invalid.append("provider_major_sport_mismatch")
        home_source, away_source = raw.get("teams", {}).get("home", {}), raw.get("teams", {}).get("away", {})
        status = deepcopy(raw.get("status", {}))
        stopped = bool(_STOPPED.search(" ".join(str(value) for value in status.values())))
        final = (status.get("abstractGameState") == "Final" and status.get("codedGameState") == "F"
                 and status.get("detailedState") in {"Final", "Final: Tied"} and not stopped)
        period, clock = _integer(raw.get("linescore", {}).get("currentInning")), None
        normal_regulation = False
    home, away = _team(home_source), _team(away_source)
    home_forms, away_forms = _forms(home_source.get("team"), family), _forms(away_source.get("team"), family)
    if (not home["team_id"] or not away["team_id"] or home["team_id"] == away["team_id"]
            or not home["name"] or not away["name"] or not home_forms or not away_forms or home_forms & away_forms):
        invalid.append("provider_team_identity_unproven")
    scheduled = _iso(date)
    if not provider_id:
        invalid.append("provider_game_id_missing")
    if not scheduled:
        invalid.append("provider_schedule_missing")
    doubleheader = (raw.get("doubleHeader") not in (None, "N", False, "")
                    or (_integer(raw.get("gameNumber")) or 0) > 1)
    doubleheader_status = "flagged" if doubleheader else "explicit_no" if raw.get("doubleHeader") == "N" else "unknown"
    result = _outcome(home["score"], away["score"])
    regulation_score, regulation_result, regulation_basis = None, None, "unproven"
    if family == "soccer" and not stopped:
        half_home, half_away = _halves(home_source), _halves(away_source)
        halves_complete = final or (period is not None and period > 2)
        if half_home is not None and half_away is not None and halves_complete:
            if normal_regulation and (half_home != home["score"] or half_away != away["score"]):
                regulation_basis = "conflicting_normal_regulation_scores"
            else:
                regulation_score = {"home": half_home, "away": half_away}
                regulation_result = _outcome(half_home, half_away)
                regulation_basis = "explicit_labeled_first_two_halves"
        elif normal_regulation and result is not None:
            regulation_score = {"home": home["score"], "away": away["score"]}
            regulation_result, regulation_basis = result, "explicit_normal_regulation_final"
    whole_known = family != "soccer" and final and result in {"HOME", "AWAY"}
    source_times = {**raw.get("event", {}), **raw}
    end_field = next((key for key in ("endDate", "endTime", "completedAt", "gameEndDateTime")
                      if _iso(source_times.get(key))), None)
    update_field = next((key for key in ("lastUpdated", "lastModified", "updatedAt")
                         if _iso(source_times.get(key))), None)
    missing = [key for key, value in (("clock", clock), ("period", period), ("scheduled_at", scheduled),
                                      ("home.score", home["score"]), ("away.score", away["score"])) if value is None]
    return {
        "provider": provider, "provider_game_id": provider_id, "sport_family": family, "league_code": code,
        "request_id": request["request_id"], "received_at": request["received_at"], "scheduled_at": scheduled,
        "status": status, "home": home, "away": away, "period": period, "clock": clock,
        "final": bool(final), "regulation_result_known": regulation_result is not None,
        "regulation_result": regulation_result, "regulation_score": regulation_score,
        "regulation_basis": regulation_basis, "whole_game_result_known": bool(whole_known),
        "winner_role": result if whole_known else None,
        "winner_team_id": (home if result == "HOME" else away)["team_id"] if whole_known else None,
        "result_scope": "REGULATION" if family == "soccer" else "WHOLE_GAME",
        "end_wallclock": _iso(source_times[end_field]) if end_field else None,
        "end_wallclock_source_field": end_field,
        "end_wallclock_basis": "provider_field_not_verified_result_publication_time",
        "provider_updated_at": _iso(source_times[update_field]) if update_field else None,
        "provider_updated_at_source_field": update_field,
        "provider_updated_at_basis": "provider_update_not_exact_score_time",
        "matching_evidence": None,
        "doubleheader": bool(doubleheader), "doubleheader_status": doubleheader_status,
        "valid_for_matching": not invalid,
        "identity_errors": invalid, "missing_fields": missing,
        "score_time_basis": "provider_snapshot_not_exact_goal_time", "score_updates_gap": True,
        "snapshot_gap_seconds": None, "previous_received_at": None, "score_changed": None,
        "score_revision_or_multiple_updates_possible": True,
        "causal_independence": "UNPROVEN", "settlement_authority_verified": False,
        "context_scope": "requested_active_leagues_two_utc_days_only",
        "raw_snapshot": deepcopy(raw), "projection_scope": PROJECTION_SCOPE,
        "snapshot_sha256": _digest(raw), "sha256_scope": HASH_SCOPE,
    }


def _array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else None
        except ValueError:
            return None
    return None


def _venue_teams(raw, family, code):
    source = raw.get("teams")
    if source is not None:
        if not isinstance(source, list) or len(source) != 2 or any(not isinstance(team, dict) for team in source):
            return None, "venue_team_count_ambiguous"
        entries = []
        for index, team in enumerate(source):
            if team.get("league") is not None and _league(team["league"]) != code:
                return None, "venue_team_league_mismatch"
            roles = {_norm(team.get(key)) for key in ("homeAway", "role", "side") if team.get(key) is not None}
            if roles and (not roles <= {"home", "away"} or len(roles) != 1):
                return None, "venue_role_ambiguous"
            entries.append({"forms": _forms(team, family), "role": next(iter(roles), None),
                            "source": f"raw.teams[{index}]"})
    else:
        markets = raw.get("markets")
        if not isinstance(markets, list):
            return None, "venue_team_names_missing"
        group_names, pairs = set(), set()
        for market in markets:
            if not isinstance(market, dict) or market.get("sportsMarketType") not in (None, "moneyline"):
                continue
            labels = _array(market.get("outcomes"))
            # Group titles can identify the two teams even when an independent
            # news caller did not include token/outcome data. This is identity
            # matching only, not attestation of the venue market's payout rules.
            if family == "soccer" and (market.get("outcomes") is None
                                        or (labels and {_norm(label) for label in labels} == {"yes", "no"})):
                label = _text(market.get("groupItemTitle"))
                if label and not re.match(r"^(?:draw|tie)(?:\s|$)", _norm(label)):
                    group_names.add(label)
                continue
            if not labels or len(labels) != 2 or any(_text(label) is None for label in labels):
                continue
            normalized = {_norm(label) for label in labels}
            if not normalized & {"yes", "no", "draw", "tie"} and len(normalized) == 2:
                pairs.add(tuple(sorted(labels)))
        if family == "soccer" and len(group_names) == 2 and not pairs:
            names = sorted(group_names)
        elif len(pairs) == 1 and not group_names:
            names = list(next(iter(pairs)))
        else:
            return None, "venue_market_team_names_ambiguous"
        entries = [{"forms": _forms({"name": name}, family), "role": None,
                    "source": "raw.markets.exact_names"} for name in names]
    if any(not entry["forms"] for entry in entries) or entries[0]["forms"] & entries[1]["forms"]:
        return None, "venue_team_names_ambiguous"
    return entries, None


def _target(event, now):
    family = event.get("sport_family")
    family = family.casefold() if isinstance(family, str) else None
    code = _league(event.get("league_code"))
    if (family, code) not in ROUTES:
        return None, "unsupported_sport_or_league"
    raw = event.get("raw") if isinstance(event.get("raw"), dict) else event
    sport = raw.get("sport")
    if isinstance(sport, dict) and sport.get("sport") is not None and _league(sport["sport"]) != code:
        return None, "venue_sport_league_mismatch"
    teams, error = _venue_teams(raw, family, code)
    if error:
        return None, error
    schedule_values = [event["scheduled_at"]] if event.get("scheduled_at") else [
        raw[key] for key in ("gameStartTime", "startTime", "eventStartTime") if raw.get(key)
    ]
    if not schedule_values:
        markets = raw.get("markets") if isinstance(raw.get("markets"), list) else []
        schedule_values = [market["gameStartTime"] for market in markets
                           if isinstance(market, dict) and market.get("gameStartTime")]
    if not schedule_values:
        schedule_values = [raw[key] for key in ("startDate", "eventDate") if raw.get(key)]
    parsed = [_dt(value) for value in schedule_values]
    if not parsed or any(value is None for value in parsed):
        return None, "venue_schedule_missing_or_unzoned"
    if len(set(parsed)) != 1:
        return None, "venue_schedule_ambiguous"
    scheduled = parsed[0]
    if scheduled.date() not in {now.date(), (now - timedelta(days=1)).date()}:
        return None, "venue_schedule_outside_requested_utc_days"
    return {"route": (family, code), "teams": teams, "scheduled": scheduled}, None


def _team_match(target, snapshot):
    raw = snapshot["raw_snapshot"]
    if snapshot["provider"] == "espn":
        sources = {role: next((row.get("team") for row in raw.get("competitors", [])
                               if row.get("homeAway") == role), {}) for role in ("home", "away")}
    else:
        sources = {role: raw.get("teams", {}).get(role, {}).get("team") for role in ("home", "away")}
    provider_forms = {role: _forms(team, target["route"][0]) for role, team in sources.items()}
    matches = [[role for role, forms in provider_forms.items() if entry["forms"] & forms]
               for entry in target["teams"]]
    if any(len(roles) != 1 for roles in matches) or {roles[0] for roles in matches} != {"home", "away"}:
        return None
    return [{"venue_team_source": entry["source"], "provider_role": roles[0],
             "exact_normalized_aliases": sorted(entry["forms"] & provider_forms[roles[0]]),
             "venue_declared_role": entry["role"]} for entry, roles in zip(target["teams"], matches)]


def _matching_evidence(event_id, target, snapshot, matched):
    return {
        "method": "exact_normalized_explicit_aliases_league_utc_day_and_schedule",
        "venue_event_id": event_id, "sport_family": target["route"][0], "league_code": target["route"][1],
        "venue_scheduled_at": _iso(target["scheduled"]), "provider_scheduled_at": snapshot["scheduled_at"],
        "schedule_difference_seconds": abs((_dt(snapshot["scheduled_at"]) - target["scheduled"]).total_seconds()),
        "team_matches": matched, "numeric_game_id_used_as_match_key": False,
        "home_away_basis": "explicit_provider_roles_validated_against_venue_roles_if_present",
    }


def _role_proof(rows):
    if not isinstance(rows, list) or len(rows) != 2 or any(not isinstance(row, dict) for row in rows):
        return None
    if {row.get("provider_role") for row in rows} != {"home", "away"}:
        return None
    proof = {}
    for row in rows:
        aliases = row.get("exact_normalized_aliases")
        if not isinstance(aliases, list) or not aliases or any(_text(alias) is None for alias in aliases):
            return None
        proof[row["provider_role"]] = {"aliases": sorted(aliases), "declared_role": row.get("venue_declared_role")}
    return proof


def _validated_result_news(event, news):
    """Revalidate the same matching contract; a copied boolean is not evidence."""
    received = _dt(news.get("received_at"))
    if received is None:
        return None, None, "news_receipt_time_missing"
    target, reason = _target(event, received)
    if reason:
        return None, None, reason
    family, league = target["route"]
    if (news.get("provider") != ROUTES[target["route"]][0]
            or news.get("sport_family") != family or news.get("league_code") != league
            or news.get("valid_for_matching") is not True or news.get("source_coverage_complete") is not True
            or news.get("projection_scope") != PROJECTION_SCOPE or news.get("sha256_scope") != HASH_SCOPE
            or not isinstance(news.get("raw_snapshot"), dict)
            or not _identifier(news.get("request_id"))):
        return None, None, "matched_provider_projection_required"
    recomputed = _news_snapshot(news["raw_snapshot"], target["route"],
                                {"request_id": news["request_id"], "received_at": news["received_at"]})
    facts = ("provider_game_id", "scheduled_at", "home", "away", "status", "period", "clock", "final", "result_scope",
             "regulation_result_known", "regulation_result", "regulation_score", "regulation_basis",
             "whole_game_result_known", "winner_role", "winner_team_id", "doubleheader", "doubleheader_status", "snapshot_sha256")
    if (_canonical({key: recomputed[key] for key in facts}) != _canonical({key: news.get(key) for key in facts})
            or recomputed["valid_for_matching"] is not True or recomputed["doubleheader"]
            or (news["provider"] == "mlb_statsapi" and recomputed["doubleheader_status"] != "explicit_no")):
        return None, None, "provider_result_projection_inconsistent_or_ambiguous"
    scheduled = _dt(news["scheduled_at"])
    if (scheduled is None or scheduled.date() != target["scheduled"].date()
            or abs((scheduled - target["scheduled"]).total_seconds()) > MAX_SCHEDULE_DIFFERENCE_SECONDS):
        return None, None, "provider_venue_date_or_schedule_mismatch"
    matched = _team_match(target, recomputed)
    if matched is None or any(row["venue_declared_role"] not in (None, row["provider_role"]) for row in matched):
        return None, None, "provider_venue_team_or_role_mismatch"
    attested = news.get("matching_evidence")
    if not isinstance(attested, dict):
        return None, None, "exact_matching_evidence_missing"
    expected = _matching_evidence(_identifier(event.get("event_id")), target, recomputed, matched)
    # Team array order is not a role: compare role/name proofs, not the incidental
    # source-array index recorded on the earlier observation.
    keys = tuple(key for key in expected if key != "team_matches")
    if (_canonical({key: expected[key] for key in keys}) != _canonical({key: attested.get(key) for key in keys})
            or _role_proof(attested.get("team_matches")) != _role_proof(matched)):
        return None, None, "exact_matching_evidence_mismatch"
    return target, matched, None


def derive_expected_payouts(event, news):
    """Pure 6/2-token expectation; NEVER an assertion of venue settlement.

    Requires this module's matched, complete projected news plus exact venue
    moneyline labels and token arrays. ``OK`` carries integer 0/1 expectations;
    ``UNCONFIRMED`` and ``UNSUPPORTED`` always carry an empty mapping. Recompute
    provider result facts from the stored projection to prevent ET/penalty totals
    or a changed boolean from masquerading as a regulation result.

    No network, wall-clock reads, I/O, mutable cache or input mutation. A hash
    verifies internal consistency, not origin authenticity. Receipt lineage,
    ``flag_venue_rules``/rule authority, cancellation/void, payout equivalence and
    actual venue settlement MUST be checked by the caller. They remain unverified
    even when this helper returns OK. No live authorization is implied.
    """
    output = {"status": "UNCONFIRMED", "token_payouts": {}, "rule_authority_verified": False,
              "basis": EXPECTED_PAYOUT_BASIS, "result_scope": None, "reason": "matched_news_required"}

    def fail(reason, status="UNCONFIRMED"):
        return {**output, "status": status, "reason": reason}

    if not isinstance(event, dict) or not isinstance(news, dict) or not news:
        return output
    if _identifier(event.get("event_id")) is None:
        return fail("venue_event_id_missing")
    try:
        target, matched, reason = _validated_result_news(event, news)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return fail("invalid_news_projection_or_matching_evidence")
    if reason:
        return fail(reason)
    family = target["route"][0]
    scope = "REGULATION" if family == "soccer" else "WHOLE_GAME"
    output["result_scope"] = scope
    if news.get("result_scope") != scope:
        return fail("result_scope_missing_or_mismatched")
    if family == "soccer":
        if news.get("regulation_result_known") is not True or news.get("regulation_result") not in {"HOME", "DRAW", "AWAY"}:
            return fail("regulation_result_unconfirmed")
        winner = news["regulation_result"]
    else:
        if news.get("whole_game_result_known") is not True or news.get("winner_role") not in {"HOME", "AWAY"}:
            if news.get("final") is True and news["home"]["score"] is not None and news["home"]["score"] == news["away"]["score"]:
                return fail("direct_tie_has_no_supported_winner", "UNSUPPORTED")
            return fail("whole_game_winner_unconfirmed")
        winner = news["winner_role"]
    role_forms = {row["provider_role"].upper(): set(row["exact_normalized_aliases"]) for row in matched}

    def label_roles(label):
        forms = _forms({"name": label}, family)
        return {role for role, aliases in role_forms.items() if forms & aliases}

    draw_forms = {"draw", "tie"}
    for left, right in (("HOME", "AWAY"), ("AWAY", "HOME")):
        draw_forms.update(f"{prefix} {a} {separator} {b}" for prefix in ("draw", "tie")
                          for a in role_forms[left] for b in role_forms[right] for separator in ("vs", "v"))
    raw = event.get("raw") if isinstance(event.get("raw"), dict) else event
    markets = raw.get("markets")
    if not isinstance(markets, list) or any(not isinstance(market, dict) for market in markets):
        return fail("venue_moneyline_markets_missing", "UNSUPPORTED")
    selected = [market for market in markets if market.get("sportsMarketType") == "moneyline"]
    if len(selected) != (3 if family == "soccer" else 1):
        return fail("exact_three_or_one_moneyline_topology_required", "UNSUPPORTED")
    payouts, token_evidence, conditions, partitions = {}, {}, set(), set()
    for market in selected:
        if (any(market.get(key) not in (None, "") for key in ("parentMarketId", "parentEventId", "period"))
                or (market.get("result_scope") is not None and market["result_scope"] != scope)):
            return fail("venue_market_result_scope_unproven", "UNSUPPORTED")
        if "events" in market:
            relations = market["events"]
            if (not isinstance(relations, list) or len(relations) != 1 or not isinstance(relations[0], dict)
                    or _identifier(relations[0].get("id")) != _identifier(event.get("event_id"))):
                return fail("venue_market_event_relation_mismatch", "UNSUPPORTED")
        condition = _text(market.get("conditionId"))
        labels, ids = _array(market.get("outcomes")), _array(market.get("clobTokenIds"))
        if (condition is None or condition in conditions or not isinstance(labels, list) or not isinstance(ids, list)
                or len(labels) != 2 or len(ids) != 2 or any(_text(label) is None for label in labels)
                or any(_identifier(token) is None for token in ids)):
            return fail("exact_condition_labels_and_token_arrays_required", "UNSUPPORTED")
        conditions.add(condition)
        ids = [_identifier(token) for token in ids]
        if len(set(ids)) != 2 or any(token in payouts for token in ids):
            return fail("duplicate_outcome_token_identity", "UNSUPPORTED")
        if family == "soccer":
            if {label.casefold() for label in labels} != {"yes", "no"}:
                return fail("soccer_requires_exact_yes_no_outcomes", "UNSUPPORTED")
            proposition = _text(market.get("groupItemTitle"))
            roles = label_roles(proposition)
            if _norm(proposition) in draw_forms:
                roles.add("DRAW")
            if len(roles) != 1:
                return fail("soccer_proposition_label_or_role_ambiguous", "UNSUPPORTED")
            role = next(iter(roles))
            if role in partitions:
                return fail("duplicate_soccer_result_proposition", "UNSUPPORTED")
            partitions.add(role)
            truth = role == winner
            for label, token in zip(labels, ids):
                payouts[token] = int(truth if label.casefold() == "yes" else not truth)
                token_evidence[token] = (condition, label, label.upper())
        else:
            roles = [label_roles(label) for label in labels]
            if any(len(role) != 1 for role in roles) or set.union(*roles) != {"HOME", "AWAY"}:
                return fail("direct_outcome_labels_not_exact_distinct_teams", "UNSUPPORTED")
            for label, token, role in zip(labels, ids, roles):
                payouts[token] = int(next(iter(role)) == winner)
                token_evidence[token] = (condition, label, "DIRECT")
    if family == "soccer" and partitions != {"HOME", "DRAW", "AWAY"}:
        return fail("complete_soccer_three_proposition_partition_required", "UNSUPPORTED")
    if "expected_token_ids" in event:
        expected = event["expected_token_ids"]
        if (not isinstance(expected, list) or any(_identifier(token) is None for token in expected)
                or len(expected) != len(payouts) or {_identifier(token) for token in expected} != set(payouts)):
            return fail("venue_expected_token_identity_mismatch", "UNSUPPORTED")
    if "side_definitions" in event:
        sides = event["side_definitions"]
        if (not isinstance(sides, list) or len(sides) != len(payouts) or any(not isinstance(side, dict) for side in sides)
                or {_identifier(side.get("token_id")) for side in sides} != set(payouts)):
            return fail("venue_side_definition_identity_mismatch", "UNSUPPORTED")
        for side in sides:
            condition, label, outcome_side = token_evidence[_identifier(side["token_id"])]
            if (side.get("condition_id") != condition or side.get("outcome_side") != outcome_side
                    or ("outcome_label" in side and _norm(side["outcome_label"]) != _norm(label))):
                return fail("venue_side_definition_label_mismatch", "UNSUPPORTED")
    return {**output, "status": "OK", "token_payouts": dict(sorted(payouts.items())),
            "reason": "provider_result_mapped_under_unverified_venue_rule_assumptions"}


class OfficialNews:
    def __init__(self, budget, sink):
        if not callable(getattr(budget, "require", None)) or not callable(sink):
            raise TypeError("budget.require and sink must be callable")
        self.budget, self.sink = budget, sink
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = None
        self.session.proxies.clear()
        self.session.cookies.clear()
        self.session.headers.clear()
        self.session.headers.update({"Accept": "application/json", "Accept-Encoding": "identity",
                                     "User-Agent": "GoldenGuava-OfficialSnapshotResearch/1.0"})
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self._denied = False
        self._closed = False
        self._previous = OrderedDict()

    def _remaining(self, deadline):
        try:
            remaining = self.budget.require()
        except Exception as exc:
            raise TimeoutError("work budget exhausted") from exc
        if type(remaining) not in (int, float) or not math.isfinite(remaining):
            raise TimeoutError("invalid remaining budget")
        remaining = min(float(remaining), deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("request deadline exhausted")
        return remaining

    def _chunks(self, response, deadline):
        read1 = getattr(getattr(response, "raw", None), "read1", None)
        if callable(read1):
            while True:
                self._remaining(deadline)
                chunk = read1(32 * 1024, decode_content=False)
                self._remaining(deadline)
                if not chunk:
                    return
                yield chunk
        else:
            for chunk in response.iter_content(chunk_size=1):
                self._remaining(deadline)
                yield chunk

    def _request(self, route, first_day, last_day):
        if route not in ROUTES:
            raise ValueError("official endpoint not allowlisted")
        if self._closed:
            raise RuntimeError("official news client is closed")
        provider, path = ROUTES[route]
        params = ({"dates": first_day.strftime("%Y%m%d") + "-" + last_day.strftime("%Y%m%d"), "limit": 1000}
                  if provider == "espn" else
                  {"sportId": 1, "startDate": first_day.isoformat(), "endDate": last_day.isoformat(), "hydrate": "linescore"})
        receipt = {"request_id": str(uuid4()), "source": provider, "method": "GET", "path": path,
                   "params": params, "started_at": _utcnow(), "received_at": None,
                   "status": None, "error_type": None, "http_attempted": False,
                   "headers_received_at": None, "body_complete": False, "body_bytes": 0,
                   "projection_scope": PROJECTION_SCOPE, "sha256_scope": HASH_SCOPE, "payload_sha256": None}
        response, projection = None, None
        result_status = "ERROR"
        deadline = time.monotonic() + ATTEMPT_SECONDS
        try:
            if self._denied:
                raise PermissionError("access_denial_latched")
            if (self.session.trust_env is not False or self.session.auth is not None or self.session.proxies
                    or {key.lower() for key in self.session.headers} & {"authorization", "proxy-authorization", "cookie"}):
                raise PermissionError("unexpected_client_auth_or_environment_state")
            self.session.cookies.clear()
            remaining = self._remaining(deadline)
            receipt["http_attempted"] = True
            response = self.session.request("GET", HOSTS[provider] + path, params=params, stream=True,
                                            allow_redirects=False,
                                            timeout=(min(2.0, remaining / 4), min(5.0, remaining / 4)))
            receipt["status"] = response.status_code
            receipt["headers_received_at"] = _utcnow()
            if response.status_code != 200:
                self._denied = response.status_code in (403, 451) or self._denied
                result_status = "DENIED" if self._denied else "REDIRECT_REFUSED" if 300 <= response.status_code < 400 else "HTTP_ERROR"
                receipt["error_type"] = f"HTTP_{response.status_code}"
            else:
                self._remaining(deadline)
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise ValueError("unexpected_content_encoding")
                chunks = bytearray()
                for chunk in self._chunks(response, deadline):
                    self._remaining(deadline)
                    chunks.extend(chunk)
                    receipt["body_bytes"] = len(chunks)
                    if len(chunks) > MAX_BODY_BYTES:
                        raise ValueError("response_body_limit_exceeded")
                # Use body receipt, not later JSON/projection work, as observation
                # time. Both are recorded so processing delay remains observable.
                receipt["received_at"] = _utcnow()
                receipt["body_complete"] = True
                self._remaining(deadline)
                payload = json.loads(chunks, parse_float=str, parse_constant=_reject_json_constant)
                self._remaining(deadline)
                projection = _project(payload, route, first_day, last_day)
                self._remaining(deadline)
                receipt["payload_sha256"] = _digest(projection)
                result_status = "INCOMPLETE" if projection["coverage"]["possible_gap"] else "OK"
                if result_status != "OK":
                    receipt["error_type"] = "provider_coverage_or_league_unproven"
        except (TimeoutError, requests.Timeout):
            result_status, receipt["error_type"], projection = "TIMEOUT", "TimeoutError", None
        except PermissionError:
            result_status, receipt["error_type"], projection = "DENIED", "AccessDenied", None
        except (requests.RequestException, OSError, ValueError, TypeError):
            # Never serialize exception text or body fragments, even on failures.
            result_status, receipt["error_type"], projection = "ERROR", "PublicSourceError", None
        finally:
            if response is not None:
                try:
                    response.close()
                except (OSError, requests.RequestException):
                    result_status, receipt["error_type"], projection = "ERROR", "ResponseCloseError", None
            self.session.cookies.clear()
            receipt["projection_completed_at"] = _utcnow()
            if receipt["received_at"] is None:
                receipt["received_at"] = receipt["projection_completed_at"]
            receipt["received_at_basis"] = ("complete_body_received" if receipt["body_complete"]
                                              else "attempt_end_without_complete_body")
            receipt["result_status"] = result_status
            if projection is None:
                receipt["payload_sha256"] = None
            self.sink(receipt, projection)
        return {"receipt": receipt, "projection": projection, "status": result_status}

    def _gap(self, snapshot):
        key = (snapshot["provider"], snapshot["league_code"], snapshot["provider_game_id"], snapshot["scheduled_at"],
               snapshot["home"]["team_id"], snapshot["away"]["team_id"])
        previous = self._previous.get(key)
        current_at = _dt(snapshot["received_at"])
        scores = (snapshot["home"]["score"], snapshot["away"]["score"])
        if previous is not None and current_at and _dt(previous["received_at"]):
            gap = (current_at - _dt(previous["received_at"])).total_seconds()
            snapshot["previous_received_at"] = previous["received_at"]
            snapshot["snapshot_gap_seconds"] = gap if gap >= 0 else None
            if gap >= 0 and None not in scores and None not in previous["scores"]:
                snapshot["score_changed"] = scores != previous["scores"]
        snapshot["previous_snapshot_scope"] = "process_local_received_snapshot_only"
        if snapshot["valid_for_matching"]:
            self._previous[key] = {"received_at": snapshot["received_at"], "scores": scores}
            self._previous.move_to_end(key)
            while len(self._previous) > MAX_GAMES:
                self._previous.popitem(last=False)

    def fetch_for_events(self, events, now_utc):
        if self._closed:
            raise RuntimeError("official news client is closed")
        now = _dt(now_utc)
        if now is None or not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError("event list and timezone-aware UTC reference required")
        by_event, context, errors, targets = {}, [], [], {}
        counts = Counter(_identifier(event.get("event_id")) for event in events)
        for event in events:
            event_id = _identifier(event.get("event_id"))
            if event_id is None:
                errors.append({"event_id": None, "reason": "venue_event_id_missing"})
                continue
            by_event[event_id] = None
            if counts[event_id] != 1:
                errors.append({"event_id": event_id, "reason": "duplicate_venue_event_id"})
                continue
            target, reason = _target(event, now)
            if reason:
                errors.append({"event_id": event_id, "reason": reason})
            else:
                targets[event_id] = target
        results = {}
        first_day, last_day = (now - timedelta(days=1)).date(), now.date()
        for route in dict.fromkeys(target["route"] for target in targets.values()):
            result = self._request(route, first_day, last_day)
            results[route] = result
            if result["status"] != "OK":
                errors.append({"provider": ROUTES[route][0], "league_code": route[1],
                               "request_id": result["receipt"]["request_id"],
                               "reason": result["receipt"]["error_type"] or "source_unavailable"})
            projection = result["projection"]
            if projection is not None:
                for raw in projection["games"]:
                    snapshot = _news_snapshot(raw, route, result["receipt"])
                    snapshot["source_coverage_complete"] = result["status"] == "OK"
                    context.append(snapshot)
        provider_counts = Counter((row["provider"], row["league_code"], row["provider_game_id"]) for row in context)
        for snapshot in context:
            key = (snapshot["provider"], snapshot["league_code"], snapshot["provider_game_id"])
            if provider_counts[key] != 1:
                snapshot["valid_for_matching"] = False
                snapshot["identity_errors"].append("duplicate_provider_game_id")
            elif snapshot["source_coverage_complete"]:
                self._gap(snapshot)
        for event_id, target in targets.items():
            route = target["route"]
            if results[route]["status"] != "OK":
                errors.append({"event_id": event_id, "reason": "provider_snapshot_unavailable"})
                continue
            games = [row for row in context if (row["sport_family"], row["league_code"]) == route]
            named = [(row, _team_match(target, row)) for row in games]
            named = [(row, match) for row, match in named if match is not None]
            same_day = [(row, match) for row, match in named if _dt(row["scheduled_at"])
                        and _dt(row["scheduled_at"]).date() == target["scheduled"].date()]
            reason = None
            if not named:
                reason = "teams_not_matched"
            elif not same_day:
                reason = "provider_utc_day_mismatch"
            elif len(same_day) != 1:
                reason = "duplicate_or_doubleheader_candidates"
            else:
                selected, matched = same_day[0]
                difference = abs((_dt(selected["scheduled_at"]) - target["scheduled"]).total_seconds())
                key = (selected["provider"], selected["league_code"], selected["provider_game_id"])
                if provider_counts[key] != 1 or not selected["valid_for_matching"]:
                    reason = "provider_game_identity_ambiguous"
                elif selected["doubleheader"] or (selected["provider"] == "mlb_statsapi"
                                                   and selected["doubleheader_status"] != "explicit_no"):
                    reason = "doubleheader_unconfirmed"
                elif difference > MAX_SCHEDULE_DIFFERENCE_SECONDS:
                    reason = "schedule_difference_exceeds_90_minutes"
                elif any(row["venue_declared_role"] is not None and row["venue_declared_role"] != row["provider_role"] for row in matched):
                    reason = "venue_provider_home_away_conflict"
                else:
                    selected = deepcopy(selected)
                    selected["matching_evidence"] = _matching_evidence(event_id, target, selected, matched)
                    by_event[event_id] = selected
            if reason:
                errors.append({"event_id": event_id, "reason": reason})
        assignments = {}
        for event_id, matched in by_event.items():
            if matched is not None:
                key = (matched["provider"], matched["league_code"], matched["provider_game_id"])
                assignments.setdefault(key, []).append(event_id)
        for event_ids in assignments.values():
            if len(event_ids) > 1:
                for event_id in event_ids:
                    by_event[event_id] = None
                    errors.append({"event_id": event_id, "reason": "multiple_venue_events_for_provider_game"})
        return {"by_event": by_event, "context": context, "errors": errors}

    def close(self):
        if not self._closed:
            self.session.close()
            self._closed = True

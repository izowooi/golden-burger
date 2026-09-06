"""Pure, frozen major-sport identity and direct-token partitions for Guava.

Metadata is ported from Plum config/classifier/filters, not imported from a
sibling polybot. Domestic soccer series are deliberately frozen to the same
IDs/slugs: drift is an exclusion, not permission to guess a new season.

``eligible`` means research collection, NOT proven payout equivalence or live
eligibility. Without independently proven home/away roles we emit role_unknown;
array order is never authority. Soccer H1 uses condition-ID partitions and is
conditional on normal regulation completion. Cancellation/void equivalence is
always UNPROVEN. Missing rules/fees/source clock fields are not imputed.
``cohort_key`` is an identity label only; runtime must add config/source/mode/job
provenance before treating anything as an analytical cohort.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit


IDENTITY_VERSION = "guava-major-direct-partitions-v1"
# code: sport id, exact name, primary tag, root series, series slug, extra tags
SOCCER_IDENTITIES = {
    "epl": (2, "Premier League", 306, "10188", "premier-league-2025", (82, 306)),
    "bun": (7, "Bundesliga", 1494, "10194", "bundesliga-2025", (1494,)),
    "fl1": (11, "Ligue 1", 102070, "10195", "ligue-1-2025", (102070,)),
    "lal": (3, "LaLiga", 780, "10193", "la-liga-2025", (780,)),
    "mls": (33, "MLS", 100100, "10189", "mls-2025", (100100,)),
    "sea": (12, "Serie A", 100618, "10203", "serie-a-2025", (101962,)),
}
CUP_IDENTITIES = {"ucl": (100977, "10204", "ucl-2025"), "uel": (101787, "10209", "uel-2025")}
DIRECT_IDENTITIES = {"mlb": (8, "MLB", 100381, "3"), "nba": (34, "NBA", 745, "10345"),
                     "nfl": (10, "NFL", 450, "10187"), "nhl": (35, "NHL", 899, "10346")}
REGULATION_CLAUSE = "this market refers only to the outcome within the first 90 minutes of regular play plus stoppage time"
MINOR = re.compile(r"\b(?:esports?|e sports?|milb|minor league|g league|summer league|ahl|echl|ncaa|college|u ?2[013]|reserve|academy)\b")
PARTIAL = re.compile(
    r"\b(?:(?:first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)(?:\s+\w+){0,2}\s+"
    r"(?:half|quarters?|periods?|innings?)|(?:half|quarter|period|inning)\s*[1-9]|"
    r"spread|handicap|total|over under|draw no bet|dnb|advance(?:ment)?|qualify|"
    r"penalt(?:y|ies)|corners?|shots?|goalscorer|touchdowns?|runs?|puck line|run line|"
    r"futures?|season long|series winner|championship winner|conference winner|division winner|"
    r"half time|halftime|first basket|first goal|next goal|both teams to score|"
    r"top of (?:the )?[1-9]|bottom of (?:the )?[1-9])\b"
)
FUTURE = re.compile(
    r"\b(?:win|wins|winner|champion)(?: of)? (?:the )?(?:[0-9]{4} )?"
    r"(?:(?:mlb|nba|nfl|nhl) )?"
    r"(?:world series|stanley cup|nba finals|super bowl|championship|league|division|conference)\b"
    r"(?!\s+(?:game|match)\s*[1-9]\b)|"
    r"\b(?:world series|stanley cup|nba finals|super bowl)(?: [0-9]{4})? (?:winner|champion)\b"
)
CLOCK_FIELDS = ("score", "period", "elapsed", "clock", "minute", "live", "ended", "closed",
                "startTime", "startDate", "eventDate", "eventStartTime", "gameStartTime",
                "endDate", "updatedAt", "updated_at", "timestamp", "lastUpdate",
                "finishedTimestamp", "status", "gameStatus")
RULE_FIELDS = ("description", "rules", "resolutionSource", "resolutionRules", "resolutionCriteria",
               "cancellationRules", "voidRules", "negRisk", "negRiskMarketID", "negRiskRequestID")
TRADABLE_FLAG_FIELDS = ("active", "closed", "acceptingOrders", "enableOrderBook", "archived")


def _name(value):
    return " ".join("".join(c if c.isalnum() else " " for c in str(value or "")).casefold().split())


def _id(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r"[0-9]+", text) else None


def _objects(value):
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        if isinstance(parsed, list):
            return parsed
    return None


def _forms(team):
    return {_name(team.get(key)) for key in ("name", "alias", "abbreviation")} - {""}


def _league(event, family):
    tags = _objects(event.get("tags"))
    tag_ids = {_id(tag.get("id")) for tag in tags}
    if "64" in tag_ids or any(_name(tag.get("slug")) in {"esports", "e sports"} for tag in tags):
        return None, "esports_excluded"
    text = " ".join(_name(event.get(k)) for k in ("title", "slug"))
    if MINOR.search(text):
        return None, "minor_or_non_major_competition"
    teams = _objects(event.get("teams"))
    if len(teams) != 2 or not isinstance(event.get("teams"), list) or len(event["teams"]) != 2:
        return None, "exactly_two_teams_required"
    series = _objects(event.get("series"))
    if len(series) != 1 or len(event["series"]) != 1 or _id(series[0].get("id")) is None:
        return None, "exactly_one_series_required"
    relation = series[0]
    slug = event.get("seriesSlug")
    if slug != relation.get("slug"):
        return None, "primary_series_slug_mismatch"
    sport = event.get("sport") if isinstance(event.get("sport"), Mapping) else {}
    if family == "soccer":
        cups = [code for code, (tag, _, _) in CUP_IDENTITIES.items() if str(tag) in tag_ids]
        if len(cups) > 1:
            return None, "ambiguous_cup_identity"
        if cups:
            code = cups[0]
            tag, root, expected_slug = CUP_IDENTITIES[code]
            try:
                host = urlsplit(str(event.get("resolutionSource") or "")).hostname
            except ValueError:
                host = None
            if (not {"1", "100639", "100350", str(tag)} <= tag_ids
                    or _id(relation.get("id")) != root or slug != expected_slug
                    or not str(event.get("slug") or "").startswith(code + "-")
                    or host != "www.uefa.com"):
                return code, "cup_identity_mismatch"
            return code, None
        code = sport.get("sport")
        if not isinstance(code, str) or code not in SOCCER_IDENTITIES:
            return None, "league_not_allowed_or_missing_sport"
        sport_id, name, tag, root, expected_slug, extra_tags = SOCCER_IDENTITIES[code]
        required = {"1", "100639", "100350", *map(str, extra_tags)}
        if _id(relation.get("id")) != root or slug != expected_slug:
            return code, "frozen_soccer_series_mismatch"
    else:
        code = family
        sport_id, name, tag, root = DIRECT_IDENTITIES[family]
        required = {"1", "100639", str(tag)}
        if relation.get("seriesType") != "single" or relation.get("recurrence") != "daily":
            return code, "non_game_series_metadata"
        if slug == family:
            if (_id(relation.get("id")) != root or relation.get("ticker") != code
                    or relation.get("title") != name):
                return code, "major_root_series_mismatch"
        else:
            match = re.fullmatch(re.escape(family) + r"-([0-9]{4})", str(slug or ""))
            if not match:
                return code, "major_season_series_mismatch"
            year = int(match.group(1))
            scheduled = next((event.get(k) for k in ("startTime", "eventDate", "startDate", "eventStartTime", "gameStartTime") if event.get(k)), None)
            try:
                schedule_year = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")).year
            except ValueError:
                return code, "season_schedule_missing"
            if (relation.get("ticker") != slug or relation.get("title") != f"{name} {year}"
                    or schedule_year - year not in {0, 1}):
                return code, "major_season_metadata_mismatch"
    sport_tags = {_id(t.strip()) for t in str(sport.get("tags") or "").split(",")}
    if (sport.get("sport") != code or _id(sport.get("id")) != str(sport_id)
            or sport.get("name") != name or _id(sport.get("primaryTagId")) != str(tag)
            or _id(sport.get("series")) != root):
        return code, "major_sport_identity_mismatch"
    if not required <= tag_ids or not required <= sport_tags:
        return code, "required_tags_missing"
    if any(team.get("league") != code for team in teams):
        return code, "team_league_mismatch"
    return code, None


def _non_game_reason(value, parent_key):
    if value.get(parent_key) not in (None, ""):
        return "child_market_or_event"
    if any(value.get(k) is True for k in ("isFuture", "future", "isProp", "prop", "isAdvancement", "advancement")):
        return "prop_future_or_advancement"
    text = " ".join(_name(value.get(k)) for k in ("title", "groupItemTitle", "question", "slug"))
    if FUTURE.search(text):
        return "series_winner_not_individual_game"
    if PARTIAL.search(text):
        return "partial_game_or_prop"
    return None


def _regulation_scope(market):
    description = " ".join(str(market.get("description") or "").casefold().split())
    if REGULATION_CLAUSE not in description:
        return "regulation_scope_missing_or_unproven"
    for clause in re.split(r"[.;]", description):
        if not re.search(r"extra time|penalt", clause):
            continue
        if re.search(r"\b(?:not|never)\s+(?:explicitly\s+)?(?:exclude|excluded|excluding|outside)\b", clause):
            return "regulation_scope_contradictory"
        masked = clause
        for phrase in ("does not include", "do not include", "not included", "does not count",
                       "do not count", "not count", "does not apply", "do not apply"):
            masked = masked.replace(phrase, " excluded ")
        if re.search(r"\b(?:include|includes|included|including|count|counts|counted|apply|applies)\b", masked):
            return "regulation_scope_contradictory"
        if not re.search(r"\b(?:exclude|excludes|excluded|excluding|outside|without)\b", masked):
            return "regulation_scope_unproven"
    return "normal_regulation_only"


def _market_sides(market, event, family, forms):
    reason = _non_game_reason(market, "parentMarketId")
    if reason:
        return [], None, reason
    if market.get("sportsMarketType") != "moneyline":
        return [], None, "not_whole_game_moneyline"
    condition = market.get("conditionId") or market.get("condition_id")
    if not isinstance(condition, str) or not condition.strip():
        return [], None, "condition_id_missing"
    relations = market.get("events")
    if relations is not None and (not isinstance(relations, list) or len(relations) != 1
            or not isinstance(relations[0], Mapping) or str(relations[0].get("id")) != str(event.get("id"))):
        return [], None, "market_event_relation_mismatch"
    labels, tokens = _array(market.get("outcomes")), _array(market.get("clobTokenIds"))
    if (labels is None or tokens is None or len(labels) != 2 or len(tokens) != 2
            or any(not isinstance(s, str) or not s.strip() for s in [*labels, *tokens])
            or len(set(tokens)) != 2):
        return [], None, "exact_two_direct_labels_tokens_required"
    if family == "soccer":
        if set(labels) != {"Yes", "No"} or market.get("negRisk") is not True:
            return [], None, "exact_negrisk_yes_no_required"
        descriptor = _name(market.get("groupItemTitle"))
        draws = {"draw", "tie"} | {
            f"{prefix} {a} {sep} {b}" for prefix in ("draw", "tie") for sep in ("vs", "v")
            for left, right in ((forms[0], forms[1]), (forms[1], forms[0])) for a in left for b in right
        }
        matched = [i for i, names in enumerate(forms) if descriptor in names]
        if descriptor in draws:
            partition, role = "DRAW", "DRAW"
        elif len(matched) == 1:
            # Team index is only a local set-membership check, never HOME/AWAY.
            partition, role = matched[0], "role_unknown"
        else:
            return [], None, "result_partition_not_identified"
        return [{"condition_id": condition, "token_id": token, "partition_key": condition,
                 "result_kind": role, "outcome_side": label.upper(), "outcome_label": label}
                for label, token in zip(labels, tokens)], partition, None
    if market.get("negRisk") is not False or set(labels) == {"Yes", "No"}:
        return [], None, "direct_team_moneyline_required"
    matches = [[i for i, names in enumerate(forms) if _name(label) in names] for label in labels]
    if any(len(m) != 1 for m in matches) or {m[0] for m in matches} != {0, 1}:
        return [], None, "direct_outcomes_not_exact_teams"
    return [{"condition_id": condition, "token_id": token, "partition_key": condition,
             "result_kind": "role_unknown", "outcome_side": "DIRECT", "outcome_label": label}
            for label, token in zip(labels, tokens)], "DIRECT_TWO_TEAM", None


def extract_event(event, family, observed_at, allow_postgame=False):
    """Return IMPLEMENTATION event fields plus side_definitions and limitations.

    An incomplete/ambiguous topology is excluded before books. Unrelated props
    may coexist in an otherwise valid event; every rejected market has a reason.
    Raw prices are not a gate: token alignment is enough to fetch fresh books.
    allow_postgame=True is for already tracked identities, never new discovery.
    POSTGAME_TRACKED labels that route, not independently verified finality.
    Research eligibility never authorizes trading; each side's raw tradable_flags
    and strict flag-only tradable gate must be rechecked by a fresh live selector
    (NoClosedOrder plus its other rules). Missing/string booleans fail that gate.
    """
    if not isinstance(event, Mapping):
        raise TypeError("event must be a Mapping")
    if type(allow_postgame) is not bool:
        raise ValueError("allow_postgame must be a boolean")
    raw = deepcopy(dict(event))
    observed = observed_at.isoformat() if isinstance(observed_at, datetime) else observed_at
    clock = {key: deepcopy(raw[key]) for key in CLOCK_FIELDS if key in raw}
    row = {"event_id": str(raw.get("id") or ""), "sport_family": family, "league_code": None,
           "observed_at": observed, "raw": raw, "clock": clock, "eligible": False,
           "exclusion_reason": None, "expected_token_ids": [], "cohort_key": None,
           "side_definitions": [], "identity_version": IDENTITY_VERSION,
           "phase": "POSTGAME_TRACKED" if allow_postgame else "IN_PLAY",
           "eligibility_scope": "RESEARCH_COLLECTION_ONLY",
           "market_exclusions": [], "missing_fields": [], "h1_partition_comparable": False,
           "payout_comparison_scope": "NORMAL_REGULATION_COMPLETION_ONLY",
           "live_rule_equivalence": "UNPROVEN", "home_away_role_evidence": "UNPROVEN",
           "rules_evidence": {"event": {k: deepcopy(raw[k]) for k in RULE_FIELDS if k in raw}, "markets": {}},
           "limitations": ["cancellation_void_equivalence_unproven", "home_away_roles_unproven",
                           "cohort_requires_runtime_config_source_mode_job", "REST_clock_not_independent_goal_news"]}

    def reject(reason):
        row["exclusion_reason"] = reason
        # Excluded rows are durable census evidence too. Repository requires a
        # nonempty identity label even before any valid partition is available.
        row["cohort_key"] = hashlib.sha256(json.dumps(
            [IDENTITY_VERSION, family, row["league_code"], row["event_id"], "excluded"],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        return row

    if not isinstance(family, str) or family not in {"soccer", *DIRECT_IDENTITIES}:
        return reject("unsupported_sport_family")
    if (not row["event_id"] or isinstance(raw.get("id"), bool)
            or not isinstance(raw.get("id"), (str, int))):
        return reject("event_id_missing")
    code, reason = _league(raw, family)
    row["league_code"] = code
    if reason:
        return reject(reason)
    reason = _non_game_reason(raw, "parentEventId")
    if reason:
        return reject(reason)
    cancelled_or_void = (
        any(raw.get(k) is True for k in ("cancelled", "canceled", "void", "isVoid"))
        or any(_name(raw.get(k)) in {"cancelled", "canceled", "void"} for k in ("status", "gameStatus"))
    )
    outside_in_play = (
        raw.get("live") is not True or raw.get("closed") is not False or raw.get("ended") is True
        or any(_name(raw.get(k)) in {"final", "finished", "ended"} for k in ("status", "gameStatus"))
    )
    if cancelled_or_void or (not allow_postgame and outside_in_play):
        return reject("not_explicit_in_play_nonclosed")
    forms = [_forms(t) for t in raw["teams"]]
    if not all(forms) or forms[0] & forms[1]:
        return reject("team_identity_ambiguous")
    markets = raw.get("markets")
    if not isinstance(markets, list) or any(not isinstance(m, Mapping) for m in markets):
        return reject("markets_missing_or_malformed")
    selected, partitions, conditions, sides, scopes = [], [], [], [], []
    for market in markets:
        market_sides, partition, reason = _market_sides(market, raw, family, forms)
        condition = market.get("conditionId") or market.get("condition_id")
        if reason:
            row["market_exclusions"].append({"condition_id": condition, "reason": reason})
            continue
        conditions.append(condition)
        selected.append(market)
        partitions.append(partition)
        sides.extend(market_sides)
        row["rules_evidence"]["markets"][condition] = {k: deepcopy(market[k]) for k in RULE_FIELDS if k in market}
        if not market.get("description"):
            row["missing_fields"].append(f"markets[{condition}].description")
        if not any(k in market for k in ("feeSchedule", "feesEnabled", "feeRateBps", "fee_rate_bps", "makerBaseFee", "takerBaseFee")):
            row["missing_fields"].append(f"markets[{condition}].fee_evidence")
        scopes.append(_regulation_scope(market) if family == "soccer" else "not_soccer")
    if family == "soccer":
        if len(selected) != 3 or set(partitions) != {0, 1, "DRAW"} or len(set(conditions)) != 3:
            return reject("complete_distinct_three_partition_required")
    elif len(selected) != 1:
        return reject("exactly_one_direct_two_team_market_required")
    tokens = [s["token_id"] for s in sides]
    if len(set(tokens)) != len(tokens):
        return reject("cross_partition_token_collision")
    for key in ("score", "period", "elapsed", "updatedAt"):
        if key not in raw:
            row["missing_fields"].append("clock." + key)
    row["side_definitions"] = sorted(sides, key=lambda s: (s["partition_key"], s["token_id"]))
    row["expected_token_ids"] = sorted(tokens)
    row["h1_partition_comparable"] = family == "soccer" and all(s == "normal_regulation_only" for s in scopes)
    row["rule_scope_statuses"] = dict(zip(conditions, scopes))
    row["cohort_key"] = hashlib.sha256(json.dumps(
        [IDENTITY_VERSION, family, code, row["event_id"], row["side_definitions"]],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    # Add mutable source flags AFTER hashing the immutable partition identity.
    # A closure must not split one tracked event into a new identity cohort.
    markets_by_condition = {m.get("conditionId") or m.get("condition_id"): m for m in selected}
    for side in row["side_definitions"]:
        market = markets_by_condition[side["condition_id"]]
        flags = {k: deepcopy(market[k]) for k in TRADABLE_FLAG_FIELDS if k in market}
        side["tradable_flags"] = flags
        side["tradable"] = (
            flags.get("active") is True and flags.get("closed") is False
            and flags.get("acceptingOrders") is True and flags.get("enableOrderBook") is True
            and ("archived" not in flags or flags["archived"] is False)
        )
    row["eligible"] = True
    return row

"""Synthetic Gamma metadata only; no config, sibling package, or network."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "guava_identity", Path(__file__).resolve().parents[1] / "src/polybot/identity.py"
)
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)

NOW = "2026-09-06T01:00:00Z"
RULES = (
    "This market refers only to the outcome within the first 90 minutes of regular "
    "play plus stoppage time. Extra time and penalty shoot-outs are excluded. "
    "If cancelled, this market resolves according to the published cancellation rules."
)


def soccer_event(code="epl"):
    leagues = {
        "epl": (2, "Premier League", 306, "10188", "premier-league-2025", [82, 306]),
        "bun": (7, "Bundesliga", 1494, "10194", "bundesliga-2025", [1494]),
        "fl1": (11, "Ligue 1", 102070, "10195", "ligue-1-2025", [102070]),
        "lal": (3, "LaLiga", 780, "10193", "la-liga-2025", [780]),
        "mls": (33, "MLS", 100100, "10189", "mls-2025", [100100]),
        "sea": (12, "Serie A", 100618, "10203", "serie-a-2025", [101962]),
    }
    event = {
        "id": "fixture-event", "slug": f"{code}-alpha-beta-2026-09-06",
        "title": "Alpha FC vs Beta FC", "live": True, "closed": False,
        "ended": False, "active": True, "score": "1-0", "period": "2H",
        "elapsed": "60:02", "updatedAt": "2026-09-06T00:59:58Z",
        "startTime": "2026-09-06T00:00:00Z",
        "teams": [{"id": 1, "name": "Alpha FC", "league": code},
                  {"id": 2, "name": "Beta FC", "league": code}],
        "markets": [],
    }
    if code in {"ucl", "uel"}:
        tag, series = {"ucl": (100977, "10204"), "uel": (101787, "10209")}[code]
        tags = [1, 100639, 100350, tag]
        slug = code + "-2025"
        event.update(sport={}, resolutionSource="https://www.uefa.com/match/")
    else:
        sport, name, tag, series, slug, extra = leagues[code]
        tags = [1, 100639, 100350, *extra]
        event["sport"] = {"id": sport, "name": name, "sport": code,
                          "primaryTagId": tag, "series": series,
                          "tags": ",".join(map(str, tags))}
    event.update(tags=[{"id": str(tag)} for tag in tags], seriesSlug=slug,
                 series=[{"id": series, "slug": slug}])
    for i, label in enumerate(["Alpha FC", "Draw (Alpha FC vs. Beta FC)", "Beta FC"]):
        event["markets"].append({
            "conditionId": f"c{i}", "sportsMarketType": "moneyline",
            "groupItemTitle": label, "question": label, "negRisk": True,
            "outcomes": '["Yes","No"]', "clobTokenIds": [f"y{i}", f"n{i}"],
            "outcomePrices": '["0.123400", "0.876600"]', "description": RULES,
            "feeSchedule": {"rate": "0.03", "exponent": 2},
        })
    return event


def direct_event(family="mlb", season=False):
    sport, tag, root = {"mlb": (8, 100381, 3), "nba": (34, 745, 10345),
                        "nfl": (10, 450, 10187), "nhl": (35, 899, 10346)}[family]
    event = soccer_event()
    slug = family + ("-2026" if season else "")
    event.update(sport={"id": sport, "name": family.upper(), "sport": family,
                        "primaryTagId": tag, "series": root, "tags": f"1,100639,{tag}"},
                 seriesSlug=slug, tags=[{"id": str(t)} for t in [1, 100639, tag]],
                 series=[{"id": str(root + 20000 if season else root), "slug": slug,
                          "ticker": slug, "title": family.upper() + (" 2026" if season else ""),
                          "seriesType": "single", "recurrence": "daily"}])
    for team in event["teams"]:
        team["league"] = family
    event["markets"] = [{"conditionId": "direct-c", "sportsMarketType": "moneyline",
                         "question": "Alpha FC vs Beta FC", "negRisk": False,
                         "outcomes": ["Beta FC", "Alpha FC"],
                         "clobTokenIds": ["beta", "alpha"], "description": "Full game rules."}]
    return event


@pytest.mark.parametrize("code", ["epl", "bun", "fl1", "lal", "mls", "sea", "ucl", "uel"])
def test_exact_eight_competitions_six_direct_tokens(code):
    event = soccer_event(code)
    before = deepcopy(event)
    row = identity.extract_event(event, "soccer", NOW)
    assert row["eligible"], row["exclusion_reason"]
    assert row["league_code"] == code
    assert set(row["expected_token_ids"]) == {f"{side}{i}" for side in "yn" for i in range(3)}
    assert len(row["side_definitions"]) == 6
    assert {side["outcome_side"] for side in row["side_definitions"]} == {"YES", "NO"}
    assert row["raw"] == before == event
    assert row["clock"]["score"] == "1-0"
    assert row["live_rule_equivalence"] == "UNPROVEN"
    assert row["h1_partition_comparable"] is True


def test_roles_not_invented_and_partition_is_permutation_invariant():
    event = soccer_event()
    a = identity.extract_event(event, "soccer", NOW)
    event["teams"].reverse()
    event["markets"].reverse()
    b = identity.extract_event(event, "soccer", NOW)
    assert a["side_definitions"] == b["side_definitions"]
    assert a["cohort_key"] == b["cohort_key"]
    assert {s["result_kind"] for s in a["side_definitions"]} == {"DRAW", "role_unknown"}
    assert {s["partition_key"] for s in a["side_definitions"]} == {"c0", "c1", "c2"}


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
@pytest.mark.parametrize("season", [False, True])
def test_exact_major_direct_two(family, season):
    row = identity.extract_event(direct_event(family, season), family, NOW)
    assert row["eligible"], row["exclusion_reason"]
    assert set(row["expected_token_ids"]) == {"alpha", "beta"}
    assert all(s["outcome_side"] == "DIRECT" for s in row["side_definitions"])
    assert all(s["result_kind"] == "role_unknown" for s in row["side_definitions"])
    assert row["h1_partition_comparable"] is False


@pytest.mark.parametrize("text", ["Series winner", "Will Alpha win the World Series?",
    "Stanley Cup winner", "NBA championship winner", "Will Alpha win the 2026 MLB championship?", "First five innings",
    "First half winner", "Alpha to qualify", "Player total shots", "Quarter 2 winner"])
def test_futures_props_and_partial_games_excluded(text):
    event = direct_event()
    event["markets"][0]["question"] = text
    assert not identity.extract_event(event, "mlb", NOW)["eligible"]


def test_world_series_individual_game_is_allowed():
    event = direct_event(season=True)
    event["title"] = "World Series Game 3: Alpha FC vs Beta FC"
    event["markets"][0]["question"] = "Who wins World Series Game 3?"
    assert identity.extract_event(event, "mlb", NOW)["eligible"]


@pytest.mark.parametrize("mutation", [
    lambda e: e["tags"].append({"id": "64", "slug": "esports"}),
    lambda e: e["sport"].update(id=999),
    lambda e: e["teams"][0].update(league="minor"),
    lambda e: e.update(parentEventId="parent"),
    lambda e: e.update(live=False),
    lambda e: e.update(ended=True),
    lambda e: e["markets"].pop(),
    lambda e: e["markets"][0].update(clobTokenIds=["y0", "y0"]),
    lambda e: e["markets"][0].update(groupItemTitle="Beta FC"),
    lambda e: e["markets"][0].update(conditionId="c2"),
])
def test_invalid_population_never_eligible(mutation):
    event = soccer_event()
    mutation(event)
    row = identity.extract_event(event, "soccer", NOW)
    assert not row["eligible"]
    assert row["exclusion_reason"]


def test_rules_unknown_are_collected_but_never_comparable():
    event = soccer_event()
    event["markets"][0].pop("description")
    row = identity.extract_event(event, "soccer", NOW)
    assert row["eligible"]
    assert not row["h1_partition_comparable"]
    assert "markets[c0].description" in row["missing_fields"]
    assert row["live_rule_equivalence"] == "UNPROVEN"


@pytest.mark.parametrize("family", ["wnba", "tennis", "esports", "", None])
def test_unsupported_family_is_explicit_exclusion(family):
    row = identity.extract_event(soccer_event(), family, NOW)
    assert not row["eligible"]
    assert row["exclusion_reason"] == "unsupported_sport_family"


def test_exclusion_still_has_repository_compatible_identity_label():
    event = soccer_event()
    event["sport"]["id"] = 999
    row = identity.extract_event(event, "soccer", NOW)
    assert row["cohort_key"] and isinstance(row["cohort_key"], str)
    assert row["raw"] == event


@pytest.mark.parametrize("rules", [
    "Extra time is included.", "Extra time is not excluded.",
    "Penalty shoot-outs count.", "Penalty shoot-outs are included.",
])
def test_contradictory_rules_never_comparable(rules):
    event = soccer_event()
    event["markets"][1]["description"] = RULES + " " + rules
    row = identity.extract_event(event, "soccer", NOW)
    assert row["eligible"]  # research still needs the contradictory raw rules
    assert not row["h1_partition_comparable"]
    assert row["live_rule_equivalence"] == "UNPROVEN"


def test_reversed_yes_no_arrays_are_aligned_by_real_labels():
    event = soccer_event()
    event["markets"][0].update(outcomes=["No", "Yes"], clobTokenIds=["actual-no", "actual-yes"])
    row = identity.extract_event(event, "soccer", NOW)
    sides = {s["token_id"]: s for s in row["side_definitions"]}
    assert row["eligible"]
    assert sides["actual-no"]["outcome_side"] == "NO"
    assert sides["actual-yes"]["outcome_side"] == "YES"


def test_unrelated_props_preserved_without_blocking_full_match_set():
    event = soccer_event()
    prop = deepcopy(event["markets"][0])
    prop.update(conditionId="prop-c", sportsMarketType="total", groupItemTitle="Total goals")
    event["markets"].append(prop)
    row = identity.extract_event(event, "soccer", NOW)
    assert row["eligible"]
    assert len(row["expected_token_ids"]) == 6
    assert row["market_exclusions"][0]["condition_id"] == "prop-c"
    assert row["raw"]["markets"][-1] == prop


@pytest.mark.parametrize("mutation", [
    lambda e: e.update(cancelled=True), lambda e: e.update(status="FINAL"),
    lambda e: e["markets"][0].update(clobTokenIds=["y1", "n0"]),
    lambda e: e["markets"][0].update(events=[{"id": "other-event"}]),
    lambda e: e["markets"].append(deepcopy(e["markets"][0])),
])
def test_terminal_conflicting_and_duplicate_topology_is_excluded(mutation):
    event = soccer_event()
    mutation(event)
    assert not identity.extract_event(event, "soccer", NOW)["eligible"]


def test_source_metadata_missing_is_not_backfilled_from_current_time_or_zero_fee():
    event = soccer_event()
    for key in ["elapsed", "updatedAt", "score", "period"]:
        del event[key]
    for market in event["markets"]:
        market.pop("feeSchedule")
    row = identity.extract_event(event, "soccer", NOW)
    assert row["eligible"]
    assert "updatedAt" not in row["clock"]
    assert "clock.updatedAt" in row["missing_fields"]
    assert "markets[c0].fee_evidence" in row["missing_fields"]
    assert "fee_rate_bps" not in row


def test_no_sibling_import_or_network_in_pure_classifier():
    import ast
    tree = ast.parse(Path(identity.__file__).read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(word in name for word in ("polybot", "requests", "signer", "auth", "order")) for name in imports)


def test_postgame_tracked_six_tokens_preserve_closed_subset_and_defaults():
    event = soccer_event()
    event.update(live=False, ended=True, closed=True, status="FINAL")
    for i, market in enumerate(event["markets"]):
        market.update(active=True, closed=i == 1, acceptingOrders=i != 1, enableOrderBook=True)
    before = deepcopy(event)
    assert not identity.extract_event(event, "soccer", NOW)["eligible"]
    row = identity.extract_event(event, "soccer", NOW, allow_postgame=True)
    assert row["eligible"] and row["eligibility_scope"] == "RESEARCH_COLLECTION_ONLY"
    assert row["phase"] == "POSTGAME_TRACKED"
    assert row["raw"] == before == event
    assert len(row["side_definitions"]) == 6
    for side in row["side_definitions"]:
        closed = side["condition_id"] == "c1"
        assert side["tradable_flags"] == {"active": True, "closed": closed,
                                          "acceptingOrders": not closed, "enableOrderBook": True}
        assert side["tradable"] is (not closed)
    assert row["live_rule_equivalence"] == "UNPROVEN"


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
def test_postgame_closed_direct_two_token_identity_is_still_research(family):
    event = direct_event(family)
    event.update(live=False, ended=True, closed=True)
    event["markets"][0].update(active=False, closed=True, acceptingOrders=False)
    row = identity.extract_event(event, family, NOW, allow_postgame=True)
    assert row["eligible"] and len(row["side_definitions"]) == 2
    assert all(not s["tradable"] for s in row["side_definitions"])
    assert row["live_rule_equivalence"] == "UNPROVEN"


@pytest.mark.parametrize("flags", [
    {}, {"active": True, "acceptingOrders": True},
    {"active": True, "closed": "false", "acceptingOrders": True, "enableOrderBook": True},
    {"active": True, "closed": True, "acceptingOrders": True, "enableOrderBook": True},
    {"active": True, "closed": False, "acceptingOrders": False, "enableOrderBook": True},
    {"active": True, "closed": False, "acceptingOrders": True, "enableOrderBook": False},
])
def test_side_flags_missing_or_conflicting_are_never_tradable(flags):
    event = direct_event()
    event["markets"][0].update(flags)
    row = identity.extract_event(event, "mlb", NOW, allow_postgame=True)
    assert row["eligible"]
    assert all(not s["tradable"] and s["tradable_flags"] == flags for s in row["side_definitions"])


def test_postgame_flags_do_not_change_partition_identity_cohort():
    event = soccer_event()
    in_play = identity.extract_event(event, "soccer", NOW)
    event.update(live=False, ended=True, closed=True)
    for market in event["markets"]:
        market.update(active=False, closed=True, acceptingOrders=False)
    postgame = identity.extract_event(event, "soccer", NOW, allow_postgame=True)
    assert in_play["cohort_key"] == postgame["cohort_key"]
    assert in_play["expected_token_ids"] == postgame["expected_token_ids"]


@pytest.mark.parametrize("mutate", [
    lambda e: e["sport"].update(id=999), lambda e: e["markets"].pop(),
    lambda e: e["markets"][0].update(groupItemTitle="Series winner"),
    lambda e: e["tags"].append({"id": "64"}),
])
def test_postgame_option_does_not_relax_major_or_full_partition_scope(mutate):
    event = soccer_event()
    event.update(live=False, ended=True, closed=True)
    mutate(event)
    assert not identity.extract_event(event, "soccer", NOW, allow_postgame=True)["eligible"]


@pytest.mark.parametrize("allow", ["true", 1, None])
def test_postgame_opt_in_requires_real_boolean(allow):
    with pytest.raises(ValueError):
        identity.extract_event(soccer_event(), "soccer", NOW, allow_postgame=allow)


def test_new_flag_metadata_preserves_original_identity_digest():
    import hashlib
    import json
    event = soccer_event()
    row = identity.extract_event(event, "soccer", NOW)
    original_sides = [{k: v for k, v in side.items() if k not in {"tradable_flags", "tradable"}}
                      for side in row["side_definitions"]]
    old_digest = hashlib.sha256(json.dumps(
        [identity.IDENTITY_VERSION, "soccer", "epl", row["event_id"], original_sides],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    assert row["cohort_key"] == old_digest


def test_postgame_option_is_route_not_proof_of_a_final_or_rule_equivalence():
    event = soccer_event()
    event["markets"][0]["description"] = "Unknown cancellation rule. Extra time included."
    row = identity.extract_event(event, "soccer", NOW, allow_postgame=True)
    assert row["phase"] == "POSTGAME_TRACKED" and row["eligible"]
    assert row["clock"]["live"] is True and row["clock"]["ended"] is False
    assert not row["h1_partition_comparable"]
    assert row["live_rule_equivalence"] == "UNPROVEN"


@pytest.mark.parametrize("flags", [{"cancelled": True}, {"void": True}, {"status": "CANCELED"}])
def test_postgame_does_not_reinterpret_cancellation_or_void_as_normal_completion(flags):
    event = soccer_event()
    event.update(live=False, ended=True, closed=True, **flags)
    row = identity.extract_event(event, "soccer", NOW, allow_postgame=True)
    assert not row["eligible"]
    assert row["live_rule_equivalence"] == "UNPROVEN" and row["raw"] == event

from types import SimpleNamespace

import pytest

from polybot.strategy.filters import (
    aligned_binary_reason,
    get_aligned_binary_outcomes,
    get_match_result_sides,
    get_match_result_yes,
    get_proven_resolution,
    match_result_reason,
)
from polybot.strategy.signals import evaluate_entry, evaluate_exit


def _event(parent_event_id=None):
    return {
        "id": "event-1",
        "parentEventId": parent_event_id,
        "teams": [
            {"name": "Home FC", "alias": "Home", "league": "epl"},
            {"name": "Away FC", "alias": "Away", "league": "epl"},
        ],
    }


def _binary(
    prices=(0.98, 0.02),
    *,
    closed=False,
    group="Home FC",
    parent_event_id=None,
):
    return {
        "sportsMarketType": "moneyline",
        "groupItemTitle": group,
        "description": (
            "This market refers only to the outcome within the first 90 minutes "
            "of regular play plus stoppage time."
        ),
        "outcomes": ["Yes", "No"],
        "outcomePrices": list(prices),
        "clobTokenIds": ["yes", "no"],
        "negRisk": True,
        "closed": closed,
        "events": [_event(parent_event_id)],
    }


def _direct_moneyline(family: str, prices=(0.60, 0.40), *, closed=False):
    return {
        "sportFamily": family,
        "sportsMarketType": "moneyline",
        "groupItemTitle": "",
        "question": "Home Club vs Away Club",
        "outcomes": ["Home Club", "Away Club"],
        "outcomePrices": list(prices),
        "clobTokenIds": [f"{family}-home", f"{family}-away"],
        "negRisk": False,
        "closed": closed,
        "events": [
            {
                "id": f"{family}-event",
                "parentEventId": None,
                "teams": [
                    {"name": "Home Club", "alias": "Home", "league": family},
                    {"name": "Away Club", "alias": "Away", "league": family},
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    ("group", "kind"),
    [
        ("Home FC", "HOME"),
        ("Draw (Home FC vs. Away FC)", "DRAW"),
        ("Away", "AWAY"),
    ],
)
def test_whole_match_result_market_selects_only_yes(group, kind) -> None:
    market = _binary(group=group)
    assert match_result_reason(market) == ("ok", kind)
    assert get_match_result_yes(market) == {
        "outcome": "Yes",
        "probability": 0.98,
        "token_id": "yes",
        "token_index": 0,
        "no_probability": 0.02,
        "no_token_id": "no",
        "result_kind": kind,
    }


def test_child_event_and_unrelated_proposition_fail_closed() -> None:
    assert match_result_reason(_binary(parent_event_id=99))[0] == (
        "child_event_not_whole_match"
    )
    assert match_result_reason(_binary(group="Both Teams to Score"))[0] == (
        "result_proposition_not_identified"
    )
    assert match_result_reason(_binary(group="Draw No Bet"))[0] == (
        "draw_no_bet_excluded"
    )
    dnb_question = _binary(group="Home FC")
    dnb_question["question"] = "Home FC Draw No Bet"
    assert match_result_reason(dnb_question)[0] == "draw_no_bet_excluded"


def test_unproven_extra_time_or_penalty_scope_fails_closed() -> None:
    missing = _binary()
    missing.pop("description")
    ambiguous = _binary()
    ambiguous["description"] = "Winner including extra time and penalties."
    contradictory = _binary()
    contradictory["description"] += " Extra time and penalties are included."
    mixed_contradiction = _binary()
    mixed_contradiction["description"] += (
        " Extra time is included but penalties are excluded."
    )
    explicit_exclusion = _binary()
    explicit_exclusion["description"] += (
        " Extra time and penalty shoot-outs are excluded."
    )
    negated_exclusion = _binary()
    negated_exclusion["description"] += (
        " Extra time is not excluded but penalties are excluded."
    )
    considered_extra_time = _binary()
    considered_extra_time["description"] += (
        " Extra time is considered, while penalty shoot-outs are excluded."
    )
    assert match_result_reason(missing)[0] == "settlement_description_missing"
    assert match_result_reason(ambiguous)[0] == "settlement_scope_unproven"
    assert match_result_reason(contradictory)[0] == "settlement_scope_contradictory"
    assert match_result_reason(mixed_contradiction)[0] == (
        "settlement_scope_contradictory"
    )
    assert match_result_reason(explicit_exclusion)[0] == "ok"
    assert match_result_reason(negated_exclusion)[0] == (
        "settlement_scope_contradictory"
    )
    assert match_result_reason(considered_extra_time)[0] == (
        "settlement_scope_contradictory"
    )


def test_yes_no_negrisk_alignment_and_settlement_paths() -> None:
    market = _binary()
    assert aligned_binary_reason(market) == "ok"
    assert [row["outcome"] for row in get_aligned_binary_outcomes(market)] == [
        "Yes",
        "No",
    ]
    yes = get_proven_resolution(_binary((1, 0), closed=True))
    no = get_proven_resolution(_binary((0, 1), closed=True))
    ambiguous = get_proven_resolution(_binary((0.5, 0.5), closed=True))
    resolved_void_market = _binary((0.5, 0.5), closed=True)
    resolved_void_market["umaResolutionStatus"] = "resolved"
    resolved_void = get_proven_resolution(resolved_void_market)
    assert yes["payouts_by_outcome"] == {"Yes": 1.0, "No": 0.0}
    assert no["payouts_by_outcome"] == {"Yes": 0.0, "No": 1.0}
    assert ambiguous is None
    assert resolved_void["settlement_kind"] == "VOID"
    assert resolved_void["outcome"] == "VOID"
    assert resolved_void["winner_index"] is None
    assert resolved_void["payouts_by_outcome"] == {"Yes": 0.5, "No": 0.5}
    assert resolved_void["status"] == "gamma_closed_resolved_void_0_5_0_5"
    sides = get_match_result_sides(market)
    assert [item["candidate_kind"] for item in sides] == ["YES_HOME", "NO_HOME"]
    assert [item["token_id"] for item in sides] == ["yes", "no"]


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
def test_direct_major_sport_exposes_two_team_books_without_synthetic_no(
    family,
) -> None:
    market = _direct_moneyline(family)

    assert match_result_reason(market) == ("ok", "DIRECT_TWO_TEAM")
    sides = get_match_result_sides(market)
    assert [item["result_kind"] for item in sides] == ["HOME", "AWAY"]
    assert [item["outcome_side"] for item in sides] == ["DIRECT", "DIRECT"]
    assert [item["candidate_kind"] for item in sides] == [
        "DIRECT_HOME",
        "DIRECT_AWAY",
    ]
    assert [item["token_id"] for item in sides] == [
        f"{family}-home",
        f"{family}-away",
    ]
    proof = get_proven_resolution(
        _direct_moneyline(family, prices=(1, 0), closed=True)
    )
    assert proof["payouts_by_outcome"] == {
        "Home Club": 1.0,
        "Away Club": 0.0,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcomes", ["Yes", "Yes"]),
        ("outcomePrices", [0.98]),
        ("clobTokenIds", ["same", "same"]),
        ("negRisk", False),
    ],
)
def test_malformed_result_market_fails_closed(field, value) -> None:
    market = _binary()
    market[field] = value
    assert aligned_binary_reason(market) != "ok"
    assert get_aligned_binary_outcomes(market) == []


def test_entry_is_first_current_exact_band_during_in_play() -> None:
    params = SimpleNamespace(
        prob_min=0.96,
        prob_max=0.999,
        hours_min=0,
        hours_max=None,
    )
    assert evaluate_entry(None, 0.96, 0, params).entry is True
    assert evaluate_entry(0.99, 0.995, 3.99, params).entry is True
    assert evaluate_entry(None, 0.959, 2, params).entry is False
    assert evaluate_entry(None, 1.0, 2, params).entry is False
    assert evaluate_entry(None, 0.96, 50, params).entry is True


def test_basic_replay_exit_supports_take_profit_and_stop() -> None:
    assert evaluate_exit(0.71, 0.70) is None
    assert evaluate_exit(0.70, 0.70) == "absolute_stop"
    assert evaluate_exit(0.20, 0.70) == "absolute_stop"
    assert evaluate_exit(0.83, 0.70, 0.83) == "take_profit"

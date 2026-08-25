from types import SimpleNamespace

import pytest

from polybot.strategy.filters import (
    aligned_binary_reason,
    get_aligned_binary_outcomes,
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
        "outcomes": ["Yes", "No"],
        "outcomePrices": list(prices),
        "clobTokenIds": ["yes", "no"],
        "negRisk": True,
        "closed": closed,
        "events": [_event(parent_event_id)],
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
    assert yes["payouts_by_outcome"] == {"Yes": 1.0, "No": 0.0}
    assert no["payouts_by_outcome"] == {"Yes": 0.0, "No": 1.0}
    assert ambiguous is None


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
    params = SimpleNamespace(prob_min=0.98, prob_max=0.999, hours_min=0, hours_max=4)
    assert evaluate_entry(None, 0.98, 0, params).entry is True
    assert evaluate_entry(0.99, 0.995, 3.99, params).entry is True
    assert evaluate_entry(None, 0.979, 2, params).entry is False
    assert evaluate_entry(None, 1.0, 2, params).entry is False
    assert evaluate_entry(None, 0.98, 4.001, params).entry is False


def test_emergency_stop_is_the_only_discretionary_exit() -> None:
    assert evaluate_exit(0.71, 0.70) is None
    assert evaluate_exit(0.70, 0.70) == "absolute_stop"
    assert evaluate_exit(0.20, 0.70) == "absolute_stop"

from types import SimpleNamespace

import pytest

from polybot.strategy.filters import (
    aligned_binary_reason,
    get_aligned_binary_outcomes,
    get_proven_resolution,
)
from polybot.strategy.signals import evaluate_entry, evaluate_exit


def _binary(
    prices=(0.94, 0.06),
    *,
    closed=False,
    outcomes=("Team A", "Team B"),
    neg_risk=False,
):
    return {
        "outcomes": list(outcomes),
        "outcomePrices": list(prices),
        "clobTokenIds": ["yes", "no"],
        "negRisk": neg_risk,
        "closed": closed,
    }


def test_named_binary_preserves_both_aligned_outcomes() -> None:
    outcomes = get_aligned_binary_outcomes(_binary())
    assert outcomes == [
        {"outcome": "Team A", "probability": 0.94, "token_id": "yes", "token_index": 0},
        {"outcome": "Team B", "probability": 0.06, "token_id": "no", "token_index": 1},
    ]


def test_yes_no_negrisk_binary_is_an_explicit_supported_stratum() -> None:
    market = _binary(outcomes=("Yes", "No"), neg_risk=True)
    assert aligned_binary_reason(market) == "ok"
    assert [row["outcome"] for row in get_aligned_binary_outcomes(market)] == [
        "Yes",
        "No",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcomes", ["same", "same"]),
        ("outcomePrices", [0.94]),
        ("clobTokenIds", ["same", "same"]),
        ("negRisk", None),
    ],
)
def test_malformed_two_outcome_market_fails_closed(field, value) -> None:
    market = _binary()
    market[field] = value
    assert aligned_binary_reason(market) != "ok"
    assert get_aligned_binary_outcomes(market) == []


def test_resolution_maps_selected_outcome_payouts() -> None:
    yes = get_proven_resolution(_binary((1, 0), closed=True))
    no = get_proven_resolution(_binary((0, 1), closed=True))
    ambiguous = get_proven_resolution(_binary((0.5, 0.5), closed=True))
    assert yes["payouts_by_outcome"] == {"Team A": 1.0, "Team B": 0.0}
    assert yes["outcome"] == "Team A"
    assert no["payouts_by_outcome"] == {"Team A": 0.0, "Team B": 1.0}
    assert no["outcome"] == "Team B"
    assert ambiguous["payouts_by_outcome"] == {"Team A": 0.5, "Team B": 0.5}


def test_entry_is_current_exact_band_not_midpoint_crossing() -> None:
    params = SimpleNamespace(prob_min=0.94, prob_max=0.95, hours_min=0, hours_max=6)
    assert evaluate_entry(None, 0.94, 6, params).entry is True
    assert evaluate_entry(0.99, 0.95, 0.01, params).entry is True
    assert evaluate_entry(None, 0.939, 3, params).entry is False
    assert evaluate_entry(None, 0.951, 3, params).entry is False
    assert evaluate_entry(None, 0.94, 6.001, params).entry is False


def test_pre_resolution_exit_is_disabled() -> None:
    assert evaluate_exit(0.10, 0) is None
    assert evaluate_exit(0.99, 0) is None

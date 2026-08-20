from types import SimpleNamespace

import pytest

from polybot.strategy.filters import (
    get_proven_resolution,
    get_strict_binary_outcomes,
    strict_binary_reason,
)
from polybot.strategy.signals import evaluate_entry, evaluate_exit


def _binary(prices=(0.94, 0.06), *, closed=False):
    return {
        "outcomes": ["Yes", "No"],
        "outcomePrices": list(prices),
        "clobTokenIds": ["yes", "no"],
        "negRisk": False,
        "closed": closed,
    }


def test_strict_binary_preserves_both_aligned_outcomes() -> None:
    outcomes = get_strict_binary_outcomes(_binary())
    assert outcomes == [
        {"outcome": "Yes", "probability": 0.94, "token_id": "yes", "token_index": 0},
        {"outcome": "No", "probability": 0.06, "token_id": "no", "token_index": 1},
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcomes", ["Up", "Down"]),
        ("outcomePrices", [0.94]),
        ("clobTokenIds", ["same", "same"]),
        ("negRisk", True),
        ("negRisk", None),
    ],
)
def test_nonstandard_binary_fails_closed(field, value) -> None:
    market = _binary()
    market[field] = value
    assert strict_binary_reason(market) != "ok"
    assert get_strict_binary_outcomes(market) == []


def test_resolution_maps_selected_outcome_payouts() -> None:
    yes = get_proven_resolution(_binary((1, 0), closed=True))
    no = get_proven_resolution(_binary((0, 1), closed=True))
    ambiguous = get_proven_resolution(_binary((0.5, 0.5), closed=True))
    assert yes["payouts_by_outcome"] == {"Yes": 1.0, "No": 0.0}
    assert no["payouts_by_outcome"] == {"Yes": 0.0, "No": 1.0}
    assert ambiguous["payouts_by_outcome"] == {"Yes": 0.5, "No": 0.5}


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

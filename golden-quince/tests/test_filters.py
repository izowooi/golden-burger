"""Strict binary YES extraction and neg-risk exclusion."""

from __future__ import annotations

import copy

import pytest

from polybot.strategy.filters import get_strict_binary_yes


@pytest.fixture
def binary_market():
    return {
        "conditionId": "0xcondition",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.951", "0.049"],
        "clobTokenIds": ["YES_TOKEN", "NO_TOKEN"],
        "negRisk": False,
    }


def test_exact_binary_non_neg_risk_market_returns_yes(binary_market):
    result = get_strict_binary_yes(binary_market)
    assert result["outcome"] == "Yes"
    assert result["probability"] == pytest.approx(0.951)
    assert result["token_id"] == "YES_TOKEN"
    assert result["token_index"] == 0
    assert result["no_probability"] == pytest.approx(0.049)
    assert result["no_token_id"] == "NO_TOKEN"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcomes", ["No", "Yes"]),
        ("outcomes", ["Yes", "No", "Other"]),
        ("outcomes", ["YES", "NO"]),
        ("outcomePrices", ["0.95"]),
        ("outcomePrices", ["0.95", "0.04", "0.01"]),
        ("clobTokenIds", ["YES_TOKEN"]),
        ("clobTokenIds", ["YES_TOKEN", "NO_TOKEN", "THIRD"]),
        ("clobTokenIds", ["", "NO_TOKEN"]),
        ("negRisk", True),
        ("negRisk", None),
    ],
)
def test_non_exact_or_neg_risk_markets_are_rejected(binary_market, field, value):
    market = copy.deepcopy(binary_market)
    market[field] = value
    assert get_strict_binary_yes(market) == {}


def test_missing_neg_risk_flag_is_rejected(binary_market):
    del binary_market["negRisk"]
    assert get_strict_binary_yes(binary_market) == {}


@pytest.mark.parametrize(
    "prices",
    [
        ["nan", "0.05"],
        ["inf", "0.05"],
        ["-0.01", "1.01"],
        ["1.01", "-0.01"],
        [object(), "0.05"],
    ],
)
def test_invalid_prices_fail_closed(binary_market, prices):
    binary_market["outcomePrices"] = prices
    assert get_strict_binary_yes(binary_market) == {}


def test_raw_gamma_json_strings_are_parsed_before_strict_validation(binary_market):
    binary_market["outcomes"] = '["Yes", "No"]'
    binary_market["outcomePrices"] = '["0.951", "0.049"]'
    binary_market["clobTokenIds"] = '["YES_TOKEN", "NO_TOKEN"]'
    assert get_strict_binary_yes(binary_market)["token_id"] == "YES_TOKEN"

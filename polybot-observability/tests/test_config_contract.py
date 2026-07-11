from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)


@dataclass
class Nested:
    threshold: float = 0.5


@dataclass
class Trading:
    amount: float = 5.0
    nested: Nested = field(default_factory=Nested)


def test_accepts_exact_dataclass_shape():
    payload = {"trading": {"amount": 3, "nested": {"threshold": 0.4}}}
    assert get_trading_config_mapping(payload) == payload["trading"]
    validate_yaml_config_shape(payload, Trading())


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"tradng": {}}, "unknown config keys"),
        ({"trading": {"ammount": 3}}, "unknown trading config keys"),
        (
            {"trading": {"nested": {"threshhold": 0.4}}},
            "unknown trading.nested config keys",
        ),
        ({"trading": []}, "trading config must be a mapping"),
    ],
)
def test_rejects_unknown_or_malformed_shape(payload, match):
    with pytest.raises(ValueError, match=match):
        validate_yaml_config_shape(payload, Trading())

"""Strict YAML shape validation shared by every strategy bot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any


def get_trading_config_mapping(payload: Any) -> Mapping[str, Any]:
    """Validate repository-level YAML keys and return the trading mapping."""
    if not isinstance(payload, Mapping):
        raise ValueError("config YAML root must be a mapping")
    allowed = {"trading", "simulation_mode"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(map(str, unknown))}")
    trading = payload.get("trading", {})
    if not isinstance(trading, Mapping):
        raise ValueError("trading config must be a mapping")
    return trading


def validate_yaml_config_shape(payload: Any, resolved_trading: Any) -> None:
    """Reject unknown trading/nested keys after dataclass construction.

    Environment variables remain valid overrides. This check is only about the
    YAML document's shape, preventing a typo from being silently ignored while
    a default value is traded and later recorded as though it were intentional.
    """
    trading = get_trading_config_mapping(payload)
    if not is_dataclass(resolved_trading) or isinstance(resolved_trading, type):
        raise TypeError("resolved_trading must be a dataclass instance")
    _validate_dataclass_mapping(trading, resolved_trading, path="trading")


def _validate_dataclass_mapping(
    payload: Mapping[str, Any], resolved: Any, *, path: str
) -> None:
    dataclass_fields = {field.name: field for field in fields(resolved)}
    unknown = set(payload) - set(dataclass_fields)
    if unknown:
        raise ValueError(
            f"unknown {path} config keys: {sorted(map(str, unknown))}"
        )

    for name in payload:
        child = getattr(resolved, name)
        if not is_dataclass(child) or isinstance(child, type):
            continue
        raw_child = payload[name]
        if not isinstance(raw_child, Mapping):
            raise ValueError(f"{path}.{name} config must be a mapping")
        _validate_dataclass_mapping(raw_child, child, path=f"{path}.{name}")

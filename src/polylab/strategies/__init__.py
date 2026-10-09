"""Strategy families. `build(variant)` returns the family's Strategy for a registry Variant."""

from __future__ import annotations

from polylab.strategies.ai_ou05 import AiOu05
from polylab.strategies.apricot import Apricot
from polylab.strategies.base import Strategy
from polylab.strategies.cherry import Cherry
from polylab.strategies.goal_over import GoalOver
from polylab.strategies.late_leader import LateLeader
from polylab.strategies.llm_nil import LlmNil
from polylab.strategies.plum import Plum
from polylab.strategies.watermelon import Watermelon

FAMILIES: dict[str, type[Strategy]] = {
    "watermelon": Watermelon,
    "apricot": Apricot,
    "plum": Plum,
    "cherry": Cherry,
    "llm_nil": LlmNil,
    "goal_over": GoalOver,
    "late_leader": LateLeader,
    "ai_ou05": AiOu05,
}


def build(variant) -> Strategy:
    try:
        cls = FAMILIES[variant.family]
    except KeyError:
        raise ValueError(f"unknown strategy family {variant.family!r}") from None
    return cls(variant.params, variant)

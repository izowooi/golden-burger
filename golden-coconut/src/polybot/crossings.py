"""Predeclared threshold-vector and interval-censoring rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Sequence


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("threshold timestamps must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class PriorThresholdState:
    observed_at: str
    executable_ask_vwap: float | None
    observation_status: str


@dataclass(frozen=True)
class ThresholdVector:
    states: Mapping[str, str]
    upward_crossings: tuple[Decimal, ...]
    left_censored: tuple[Decimal, ...]
    gap_censored: tuple[Decimal, ...]
    gap_seconds: float | None


def evaluate_threshold_vector(
    *,
    current_vwap: float | None,
    current_observed_at: str,
    prior: PriorThresholdState | None,
    thresholds: Sequence[Decimal],
    max_gap_seconds: float,
) -> ThresholdVector:
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    gap_seconds = None
    if prior is not None:
        gap_seconds = (_utc(current_observed_at) - _utc(prior.observed_at)).total_seconds()
        if gap_seconds < 0:
            raise ValueError("threshold observations cannot move backward in time")
    states: dict[str, str] = {}
    crossings: list[Decimal] = []
    left: list[Decimal] = []
    gaps: list[Decimal] = []
    for threshold in thresholds:
        key = f"{threshold:.2f}"
        boundary = float(threshold)
        if current_vwap is None:
            state = "NO_EXECUTABLE_OBSERVATION"
        elif prior is None:
            if current_vwap >= boundary:
                state = "LEFT_CENSORED"
                left.append(threshold)
            else:
                state = "BELOW_THRESHOLD"
        elif prior.observation_status != "FULL" or prior.executable_ask_vwap is None:
            if current_vwap >= boundary:
                state = "GAP_CENSORED"
                gaps.append(threshold)
            else:
                state = "BELOW_THRESHOLD"
        elif prior.executable_ask_vwap < boundary <= current_vwap:
            if gap_seconds is not None and gap_seconds <= max_gap_seconds:
                state = "UPWARD_CROSSING"
                crossings.append(threshold)
            else:
                state = "GAP_CENSORED"
                gaps.append(threshold)
        elif current_vwap >= boundary:
            state = "ABOVE_WITHOUT_NEW_CROSSING"
        else:
            state = "BELOW_THRESHOLD"
        states[key] = state
    return ThresholdVector(
        states=states,
        upward_crossings=tuple(crossings),
        left_censored=tuple(left),
        gap_censored=tuple(gaps),
        gap_seconds=gap_seconds,
    )

"""Recorder v2 Nations League identity overlay; frozen v7 remains unchanged."""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .classifier import EventClassification, classify_event as classify_base_event
from .registry import SportFamily, SportsRegistry


_MATCH_SLUG = re.compile(
    r"unl-[a-z0-9]+-[a-z0-9]+-[0-9]{4}-[0-9]{2}-[0-9]{2}"
)


def classify_event(
    event: Mapping[str, Any], family: SportFamily, registry: SportsRegistry
) -> EventClassification:
    result = classify_base_event(event, family, registry)
    if not result.accepted or result.competition_code != "unl":
        return result
    reasons: list[str] = []
    if not _MATCH_SLUG.fullmatch(str(event.get("slug") or "")):
        reasons.append("EVENT_COMPETITION_SLUG_MISMATCH")
    source_host = (
        urlsplit(str(event.get("resolutionSource") or "")).hostname or ""
    ).casefold()
    if source_host != "www.uefa.com":
        reasons.append("EVENT_RESOLUTION_SOURCE_MISMATCH")
    return replace(result, status="DRIFT", reasons=tuple(reasons)) if reasons else result

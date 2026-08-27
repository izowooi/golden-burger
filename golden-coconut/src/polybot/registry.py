"""Exact frozen five-family sports identity registry."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .source_digest import ACTIVE_REGISTRY, PROJECT_ROOT, sha256_file


FAMILY_ORDER = ("soccer", "mlb", "nba", "nfl", "nhl")


@dataclass(frozen=True)
class SportFamily:
    code: str
    tag_id: int
    identity_kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SportsRegistry:
    path: Path
    sha256: str
    profile: str
    classifier_version: str
    raw: Mapping[str, Any]
    families: tuple[SportFamily, ...]

    @property
    def by_code(self) -> dict[str, SportFamily]:
        return {family.code: family for family in self.families}

    def canonical_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":"))


def load_registry(
    expected_sha256: str,
    path: Path = PROJECT_ROOT / ACTIVE_REGISTRY,
) -> SportsRegistry:
    if path.is_symlink() or not path.is_file():
        raise ValueError("frozen SPORTS_REGISTRY.json is absent or unsafe")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError("SPORTS_REGISTRY.json SHA-256 differs from config")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("SPORTS_REGISTRY.json is invalid JSON") from error
    if not isinstance(raw, Mapping):
        raise ValueError("SPORTS_REGISTRY.json root must be an object")
    if raw.get("schema_version") != 3:
        raise ValueError("SPORTS_REGISTRY.json schema_version must be 3")
    profile = str(raw.get("registry_profile") or "")
    classifier = str(raw.get("classifier_version") or "")
    family_payloads = raw.get("sport_families")
    if not profile or not classifier or not isinstance(family_payloads, Mapping):
        raise ValueError("SPORTS_REGISTRY.json metadata is incomplete")
    if set(family_payloads) != set(FAMILY_ORDER):
        raise ValueError("SPORTS_REGISTRY.json must contain exactly five families")
    families: list[SportFamily] = []
    for code in FAMILY_ORDER:
        payload = family_payloads[code]
        if not isinstance(payload, Mapping):
            raise ValueError(f"registry family {code} must be an object")
        discovery = payload.get("discovery")
        if not isinstance(discovery, Mapping):
            raise ValueError(f"registry family {code} discovery is missing")
        if set(discovery) != {
            "closed",
            "include_children",
            "related_tags",
            "start_hours_after_slot",
            "start_hours_before_slot",
            "tag_id",
        }:
            raise ValueError(f"registry family {code} discovery keys differ")
        if (
            discovery.get("closed") is not False
            or discovery.get("include_children") is not False
            or discovery.get("related_tags") is not False
            or discovery.get("start_hours_before_slot") != 24
            or discovery.get("start_hours_after_slot") != 48
        ):
            raise ValueError(f"registry family {code} discovery envelope drift")
        tag_id = discovery.get("tag_id")
        if isinstance(tag_id, bool) or not isinstance(tag_id, int) or tag_id <= 0:
            raise ValueError(f"registry family {code} tag_id is invalid")
        identity_kind = str(payload.get("identity_kind") or "")
        if not identity_kind:
            raise ValueError(f"registry family {code} identity_kind is missing")
        families.append(SportFamily(code, tag_id, identity_kind, payload))
    expected_tags = {
        "soccer": 100350,
        "mlb": 100381,
        "nba": 745,
        "nfl": 450,
        "nhl": 899,
    }
    if {item.code: item.tag_id for item in families} != expected_tags:
        raise ValueError("SPORTS_REGISTRY.json discovery tags differ from v3")
    return SportsRegistry(
        path=path,
        sha256=actual_sha256,
        profile=profile,
        classifier_version=classifier,
        raw=raw,
        families=tuple(families),
    )

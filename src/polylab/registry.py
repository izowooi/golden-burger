"""Strategy variant registry: one YAML file per variant under `strategies/`.

The YAML is the single source of truth for what runs, with which params, at what stake,
on which account. Autopilot edits it through `save_variant` only after validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from polylab.settings import REPO_ROOT

REGISTRY_DIR = REPO_ROOT / "strategies"
STAKE_LADDER = (5.0, 10.0, 25.0, 50.0, 100.0)
MAX_STAKE_USDC = 100.0
MIN_STAKE_USDC = 5.0


@dataclass
class Variant:
    id: str
    family: str
    hypothesis: str
    account: str | None
    mode: str                       # live | paper | off
    sports: list[str]
    stake_usdc: float
    params: dict[str, Any]
    bounds: dict[str, list[float]] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    path: Path | None = None

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def to_yaml(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "hypothesis": self.hypothesis,
            "account": self.account,
            "mode": self.mode,
            "sports": list(self.sports),
            "stake_usdc": float(self.stake_usdc),
            "params": self.params,
            "bounds": self.bounds,
            "limits": self.limits,
            "notes": self.notes,
        }


def _validate(v: Variant) -> None:
    if v.mode not in {"live", "paper", "off"}:
        raise ValueError(f"{v.id}: bad mode {v.mode}")
    if v.stake_usdc not in STAKE_LADDER:
        raise ValueError(f"{v.id}: stake {v.stake_usdc} not on ladder {STAKE_LADDER}")
    if v.is_live and not v.account:
        raise ValueError(f"{v.id}: live variant needs an account alias")
    for name, bound in v.bounds.items():
        if name in v.params and isinstance(v.params[name], (int, float)) and len(bound) >= 2:
            lo, hi = bound[0], bound[1]
            if not lo <= v.params[name] <= hi:
                raise ValueError(f"{v.id}: param {name}={v.params[name]} outside bounds [{lo}, {hi}]")


def load_variant(path: Path) -> Variant:
    data = yaml.safe_load(path.read_text())
    v = Variant(
        id=data["id"],
        family=data["family"],
        hypothesis=data.get("hypothesis", ""),
        account=data.get("account"),
        mode=data.get("mode", "paper"),
        sports=list(data.get("sports", [])),
        stake_usdc=float(data.get("stake_usdc", MIN_STAKE_USDC)),
        params=dict(data.get("params", {})),
        bounds=dict(data.get("bounds", {})),
        limits=dict(data.get("limits", {})),
        notes=data.get("notes", ""),
        path=path,
    )
    if path.stem != v.id:
        raise ValueError(f"{path.name}: file name must equal id {v.id}")
    _validate(v)
    return v


def load_all(directory: Path | None = None, include_off: bool = False) -> list[Variant]:
    directory = directory or REGISTRY_DIR
    variants = [load_variant(p) for p in sorted(directory.glob("*.yaml"))]
    accounts = [v.account for v in variants if v.is_live]
    dupes = {a for a in accounts if accounts.count(a) > 1}
    if dupes:
        raise ValueError(f"accounts shared by several live variants: {sorted(dupes)}")
    return [v for v in variants if include_off or v.mode != "off"]


def save_variant(v: Variant, directory: Path | None = None) -> Path:
    _validate(v)
    path = (directory or REGISTRY_DIR) / f"{v.id}.yaml"
    path.write_text(yaml.safe_dump(v.to_yaml(), sort_keys=False, allow_unicode=True))
    v.path = path
    return path

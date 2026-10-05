"""Strategy variant registry: one YAML file per variant under `strategies/`.

The YAML is the single source of truth for what runs, with which params, at what stake,
on which account. Autopilot edits it through `save_variant` only after validation.

`sports` is either a list (legacy: every sport runs at the variant `mode`/`stake_usdc`) or a
mapping of per-sport settings (2026-10-05, owner decision `sports3:per-sport`):

    mode: live                 # master switch: off = all off, paper = all paper, live = per sport
    stake_usdc: 5              # default for sports without their own stake
    sports:
      soccer: {mode: live, stake_usdc: 5}
      nba: {mode: paper, stake_usdc: 5, limits: {max_open_usdc: 30}}   # limits optional

A sport's effective mode is the stricter of the master `mode` and its own `mode`. Per-sport
params live in `params.sport_overrides.<sport>` (merged over the base params by the strategy).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from polylab.settings import REPO_ROOT

REGISTRY_DIR = REPO_ROOT / "strategies"
STAKE_LADDER = (5.0, 10.0, 25.0, 50.0, 100.0)
MAX_STAKE_USDC = 100.0
MIN_STAKE_USDC = 5.0
MODES = ("off", "paper", "live")            # ordered: stricter first
SPORT_SETTING_KEYS = {"mode", "stake_usdc", "limits", "live_from"}


def stricter_mode(a: str, b: str) -> str:
    return a if MODES.index(a) <= MODES.index(b) else b


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
    # per-sport settings ({sport: {mode, stake_usdc, limits}}); empty = legacy list form
    sport_settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        """Master switch is live (the variant may hold live positions on its account)."""
        return self.mode == "live"

    @property
    def per_sport(self) -> bool:
        return bool(self.sport_settings)

    def sport_mode(self, sport: str | None, now: float | None = None) -> str:
        """Effective mode of one sport: the stricter of the master mode and the sport's own.
        A live sport with `live_from: YYYY-MM-DD` (UTC) behaves as paper before that date
        (e.g. NBA live only from the regular season, preseason stays paper)."""
        cfg = (self.sport_settings.get(sport) or {}) if sport else {}
        mode = stricter_mode(self.mode, cfg.get("mode", self.mode))
        start = cfg.get("live_from")
        if mode == "live" and start:
            import datetime as _dt
            day = _dt.datetime.fromtimestamp(now if now is not None else time.time(), _dt.timezone.utc).date()
            if day < _dt.date.fromisoformat(str(start)):
                return "paper"
        return mode

    def sport_stake(self, sport: str | None) -> float:
        own = (self.sport_settings.get(sport) or {}).get("stake_usdc") if sport else None
        return float(own if own is not None else self.stake_usdc)

    def sport_limits(self, sport: str | None) -> dict[str, Any]:
        return dict((self.sport_settings.get(sport) or {}).get("limits") or {}) if sport else {}

    def sports_in_mode(self, mode: str) -> list[str]:
        return [s for s in self.sports if self.sport_mode(s) == mode]

    @property
    def effective_mode(self) -> str:
        """Most permissive effective sport mode (what the variant does at all); master mode if no sports."""
        if not self.sports:
            return self.mode
        return max((self.sport_mode(s) for s in self.sports), key=MODES.index)

    def primary_sport(self) -> str | None:
        """Sport whose stake/ladder headline the variant: first live sport, else first non-off, else first."""
        for mode in ("live", "paper"):
            found = self.sports_in_mode(mode)
            if found:
                return found[0]
        return self.sports[0] if self.sports else None

    def sports_yaml(self) -> list[str] | dict[str, dict[str, Any]]:
        if not self.per_sport:
            return list(self.sports)
        out: dict[str, dict[str, Any]] = {}
        for s in self.sports:
            cfg = dict(self.sport_settings.get(s) or {})
            row: dict[str, Any] = {"mode": cfg.get("mode", self.mode), "stake_usdc": float(self.sport_stake(s))}
            if cfg.get("limits"):
                row["limits"] = dict(cfg["limits"])
            if cfg.get("live_from"):
                row["live_from"] = str(cfg["live_from"])
            out[s] = row
        return out

    def to_yaml(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "hypothesis": self.hypothesis,
            "account": self.account,
            "mode": self.mode,
            "sports": self.sports_yaml(),
            "stake_usdc": float(self.stake_usdc),
            "params": self.params,
            "bounds": self.bounds,
            "limits": self.limits,
            "notes": self.notes,
        }


def effective_params(params: dict[str, Any], sport: str | None) -> dict[str, Any]:
    """Base params with `sport_overrides.<sport>` merged in (what the strategy uses for that sport)."""
    out = {k: v for k, v in (params or {}).items() if k != "sport_overrides"}
    if sport:
        out.update(((params or {}).get("sport_overrides") or {}).get(sport) or {})
    return out


def _validate(v: Variant) -> None:
    if v.mode not in {"live", "paper", "off"}:
        raise ValueError(f"{v.id}: bad mode {v.mode}")
    if v.stake_usdc not in STAKE_LADDER:
        raise ValueError(f"{v.id}: stake {v.stake_usdc} not on ladder {STAKE_LADDER}")
    if v.is_live and not v.account:
        raise ValueError(f"{v.id}: live variant needs an account alias")
    for sport, cfg in v.sport_settings.items():
        if not isinstance(cfg, dict) or set(cfg) - SPORT_SETTING_KEYS:
            raise ValueError(f"{v.id}: sports.{sport} must be a mapping of {sorted(SPORT_SETTING_KEYS)}")
        if cfg.get("mode", v.mode) not in MODES:
            raise ValueError(f"{v.id}: sports.{sport} bad mode {cfg.get('mode')}")
        if float(cfg.get("stake_usdc", v.stake_usdc)) not in STAKE_LADDER:
            raise ValueError(f"{v.id}: sports.{sport} stake {cfg.get('stake_usdc')} not on ladder {STAKE_LADDER}")
        if cfg.get("limits") is not None and not isinstance(cfg["limits"], dict):
            raise ValueError(f"{v.id}: sports.{sport}.limits must be a mapping")
    for name, bound in v.bounds.items():
        if name in v.params and isinstance(v.params[name], (int, float)) and len(bound) >= 2:
            lo, hi = bound[0], bound[1]
            if not lo <= v.params[name] <= hi:
                raise ValueError(f"{v.id}: param {name}={v.params[name]} outside bounds [{lo}, {hi}]")


def load_variant(path: Path) -> Variant:
    data = yaml.safe_load(path.read_text())
    raw_sports = data.get("sports", [])
    settings: dict[str, dict[str, Any]] = {}
    if isinstance(raw_sports, dict):
        settings = {str(k): dict(cfg or {}) for k, cfg in raw_sports.items()}
        if not settings:
            raise ValueError(f"{path.name}: sports mapping is empty")
        raw_sports = list(settings)
    v = Variant(
        id=data["id"],
        family=data["family"],
        hypothesis=data.get("hypothesis", ""),
        account=data.get("account"),
        mode=data.get("mode", "paper"),
        sports=list(raw_sports),
        stake_usdc=float(data.get("stake_usdc", MIN_STAKE_USDC)),
        params=dict(data.get("params", {})),
        bounds=dict(data.get("bounds", {})),
        limits=dict(data.get("limits", {})),
        notes=data.get("notes", ""),
        path=path,
        sport_settings=settings,
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

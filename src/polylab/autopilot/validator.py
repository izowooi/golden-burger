"""Deterministic guardrails for AI / inbox / ladder proposals. Pure functions, no IO.

proposal.json (schema "polylab.proposal/v1"):
{
  "schema": "polylab.proposal/v1",
  "summary": "one line",
  "context_generated_at": "2026-10-01T00:00:00Z",      # optional, inbox stale guard
  "changes": [
    {"variant_id": "watermelon-cat", "change": "params", "values": {"prob_min": 0.93},
     "rationale": "...", "evidence": {...}},
    {"variant_id": "...", "change": "stake", "values": {"stake_usdc": 10}, ...},
    {"variant_id": "...", "change": "mode", "values": {"mode": "paper"}, ...},
    {"variant_id": "new-id", "change": "new_variant",
     "values": {"based_on": "watermelon-cat", "hypothesis": "...", "sports": ["soccer"],
                "params": {"prob_min": 0.95}, "account": null}, ...},
    {"variant_id": "...", "change": "retire", "values": {}, ...}
  ]
}
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any

from polylab.registry import MAX_STAKE_USDC, MIN_STAKE_USDC, STAKE_LADDER, Variant

SCHEMA = "polylab.proposal/v1"
CHANGE_TYPES = ("params", "stake", "mode", "new_variant", "retire")
CHANGE_KEYS = {"variant_id", "change", "values", "rationale", "evidence"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
SAFE_MODE_TRANSITIONS = {("live", "paper"), ("live", "off"), ("paper", "off")}
NEW_VARIANT_KEYS = {"based_on", "family", "hypothesis", "sports", "params", "account", "limits", "notes"}
DAY = 86400


@dataclass(frozen=True)
class Rules:
    max_changes: int = 2                 # AI/inbox changes per retro (ladder changes not counted)
    min_trades_params: int = 20          # settled trades at the current param version
    cooldown_params_s: int = 3 * DAY
    cooldown_stake_s: int = 3 * DAY
    allow_new_variants: bool = False
    max_new_variants: int = 2
    max_paper_variants: int = 12
    inbox_max_age_s: int = 2 * DAY
    default_step_fraction: float = 0.10  # max_step when bounds give only [min, max]

    @classmethod
    def for_kind(cls, kind: str) -> "Rules":
        if kind == "weekly":
            return cls(max_changes=5, allow_new_variants=True)
        if kind == "monthly":
            return cls(max_changes=4, allow_new_variants=True, max_new_variants=1)
        return cls()

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Facts:
    """Deterministic evidence per variant, computed from ledgers by the retro."""
    trades_at_version: int = 0
    last_param_change_ts: int | None = None
    last_stake_change_ts: int | None = None
    promote_ok: bool | None = None       # deterministic ladder gate (polylab.risk); None = unavailable


@dataclass
class Context:
    variants: dict[str, Variant]
    facts: dict[str, Facts]
    now: int
    families: set[str] = field(default_factory=set)
    known_aliases: set[str] = field(default_factory=set)


@dataclass
class Decision:
    change: dict
    source: str
    accepted: bool
    reason: str = ""
    summary: str = ""

    def as_dict(self) -> dict:
        return {"variant_id": self.change.get("variant_id"), "change": self.change.get("change"),
                "values": self.change.get("values"), "rationale": self.change.get("rationale"),
                "source": self.source, "accepted": self.accepted, "reason": self.reason, "summary": self.summary}


class Reject(Exception):
    pass


_MISSING = object()
SCALED_LIMITS = ("max_open_usdc", "daily_loss_stop_usdc")  # absolute USDC limits follow the stake tier


def get_path(params: dict, dotted: str, default=_MISSING):
    """`sport_overrides.nfl.prob_min` -> params["sport_overrides"]["nfl"]["prob_min"]."""
    cur = params
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None if default is _MISSING else default
        cur = cur[part]
    return cur


def set_path(params: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = params
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _parse_ts(value) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
    except ValueError:
        return None


def _step(bound: list, rules: Rules) -> float:
    if len(bound) >= 3 and _num(bound[2]):
        return float(bound[2])
    return (float(bound[1]) - float(bound[0])) * rules.default_step_fraction


def _check_params(new: dict, current: dict, bounds: dict, rules: Rules, relative: bool) -> list[str]:
    parts = []
    if not isinstance(new, dict) or not new:
        raise Reject("values must be a non-empty object of params")
    for name, value in new.items():
        if name not in bounds:
            raise Reject(f"param {name} has no bounds (not tunable)")
        bound = bounds[name]
        if not isinstance(bound, (list, tuple)) or len(bound) < 2 or not all(_num(b) for b in bound[:2]):
            raise Reject(f"param {name} has malformed bounds")
        if not _num(value):
            raise Reject(f"param {name} must be numeric")
        old = get_path(current, name)
        if isinstance(old, int) and not isinstance(old, bool) and float(value) != int(value):
            raise Reject(f"param {name} must stay an integer")
        lo, hi = float(bound[0]), float(bound[1])
        if not lo - 1e-12 <= float(value) <= hi + 1e-12:
            raise Reject(f"param {name}={value} outside bounds [{lo:g}, {hi:g}]")
        if relative and _num(old):
            if abs(float(value) - float(old)) > _step(bound, rules) + 1e-9:
                raise Reject(f"param {name} step {abs(float(value) - float(old)):g} > max_step {_step(bound, rules):g}")
            if float(value) == float(old):
                raise Reject(f"param {name} unchanged")
        parts.append(f"{name} {old}→{value}")
    return parts


def _validate_one(ch: dict, ctx: Context, rules: Rules, source: str, state: dict) -> str:
    if not isinstance(ch, dict):
        raise Reject("change must be an object")
    unknown = set(ch) - CHANGE_KEYS
    if unknown:
        raise Reject(f"unknown fields {sorted(unknown)}")
    vid, kind, values = ch.get("variant_id"), ch.get("change"), ch.get("values")
    if not isinstance(vid, str) or not ID_RE.match(vid):
        raise Reject("variant_id must match ^[a-z0-9][a-z0-9-]{1,62}$")
    if kind not in CHANGE_TYPES:
        raise Reject(f"unknown change type {kind!r}")
    if not isinstance(values, dict):
        raise Reject("values must be an object")
    if source != "ladder" and (not isinstance(ch.get("rationale"), str) or not ch["rationale"].strip()):
        raise Reject("rationale required")
    if "evidence" in ch and not isinstance(ch["evidence"], (dict, list, str)):
        raise Reject("evidence must be an object")
    if vid in state["touched"]:
        raise Reject("only one change per variant per retro")

    if kind == "new_variant":
        return _validate_new(vid, values, ctx, rules, state)

    v = ctx.variants.get(vid)
    if v is None:
        raise Reject(f"unknown variant {vid}")
    f = ctx.facts.get(vid, Facts())

    if kind == "params":
        if v.mode == "off":
            raise Reject("variant is off")
        if f.trades_at_version < rules.min_trades_params:
            raise Reject(f"only {f.trades_at_version} settled trades at current params (< {rules.min_trades_params})")
        if f.last_param_change_ts and ctx.now - f.last_param_change_ts < rules.cooldown_params_s:
            raise Reject(f"param cooldown: last change {(ctx.now - f.last_param_change_ts) / 3600:.0f}h ago")
        return ", ".join(_check_params(values.get("params", values), v.params, v.bounds, rules, relative=True))

    if kind == "stake":
        if set(values) != {"stake_usdc"} or not _num(values["stake_usdc"]):
            raise Reject("stake values must be {stake_usdc: number}")
        new = float(values["stake_usdc"])
        if new > MAX_STAKE_USDC or new not in STAKE_LADDER:
            raise Reject(f"stake {new:g} not on ladder {STAKE_LADDER}")
        if v.stake_usdc not in STAKE_LADDER:
            raise Reject("current stake not on ladder")
        step = STAKE_LADDER.index(new) - STAKE_LADDER.index(v.stake_usdc)
        if step == 0:
            raise Reject("stake unchanged")
        if abs(step) != 1:
            raise Reject("stake may move only one ladder step")
        if step > 0:
            if v.mode != "live":
                raise Reject("stake promotion only for live variants")
            if f.promote_ok is not True:
                raise Reject("ladder gate not passed (or polylab.risk unavailable): promotion refused")
            if f.last_stake_change_ts and ctx.now - f.last_stake_change_ts < rules.cooldown_stake_s:
                raise Reject(f"stake cooldown: last change {(ctx.now - f.last_stake_change_ts) / 3600:.0f}h ago")
        return f"stake {v.stake_usdc:g}→{new:g} USDC"

    if kind == "mode":
        if set(values) != {"mode"}:
            raise Reject("mode values must be {mode: paper|off}")
        target = values["mode"]
        if (v.mode, target) not in SAFE_MODE_TRANSITIONS:
            raise Reject(f"mode {v.mode}→{target} not allowed (only toward safety; live needs a human)")
        return f"mode {v.mode}→{target}"

    if kind == "retire":
        if v.mode == "off":
            raise Reject("already off")
        return f"retire ({v.mode}→off)"
    raise Reject("unreachable")


def _validate_new(vid: str, values: dict, ctx: Context, rules: Rules, state: dict) -> str:
    if not rules.allow_new_variants:
        raise Reject("new variants only in weekly/monthly retros")
    if state["new_variants"] >= rules.max_new_variants:
        raise Reject(f"max {rules.max_new_variants} new variants per retro")
    if vid in ctx.variants:
        raise Reject("variant id already exists")
    unknown = set(values) - NEW_VARIANT_KEYS
    if unknown:
        raise Reject(f"unknown new_variant fields {sorted(unknown)}")
    base = ctx.variants.get(values.get("based_on"))
    if base is None:
        raise Reject("based_on must name an existing variant (bounds are inherited from it)")
    family = values.get("family", base.family)
    if family != base.family or family not in (ctx.families or {base.family}):
        raise Reject("family must equal the based_on variant's family")
    paper_count = sum(1 for v in ctx.variants.values() if v.mode == "paper") + state["new_variants"]
    if paper_count >= rules.max_paper_variants:
        raise Reject(f"already {paper_count} paper variants (max {rules.max_paper_variants})")
    account = values.get("account")
    if account is not None:
        used = {v.account for v in ctx.variants.values() if v.account}
        if account not in ctx.known_aliases or account in used:
            raise Reject("account must be null or an unused configured alias")
    sports = values.get("sports", base.sports)
    if not isinstance(sports, list) or not sports or not set(sports) <= {"soccer", "mlb", "nba", "nfl", "nhl"}:
        raise Reject("sports must be a non-empty subset of soccer/mlb/nba/nfl/nhl")
    params = values.get("params") or {}
    if not isinstance(params, dict):
        raise Reject("params must be an object")
    extra = {k for k in params if get_path(base.params, k, _MISSING) is _MISSING}
    if extra:
        raise Reject(f"params not in based_on variant: {sorted(extra)}")
    if params:
        _check_params(params, base.params, base.bounds, rules, relative=False)
    if not isinstance(values.get("hypothesis", base.hypothesis), str):
        raise Reject("hypothesis must be text")
    return f"new paper variant from {base.id} ({', '.join(f'{k}={v}' for k, v in params.items()) or 'same params'})"


def validate(proposal: Any, ctx: Context, rules: Rules, source: str = "ai",
             state: dict | None = None) -> list[Decision]:
    """Validate every change. `state` carries budget/touched across several proposals in one retro."""
    state = state if state is not None else {"count": 0, "touched": set(), "new_variants": 0}
    if not isinstance(proposal, dict) or not isinstance(proposal.get("changes"), list):
        return [Decision({}, source, False, "proposal must be an object with a changes list")]
    if proposal.get("schema") not in (None, SCHEMA):
        return [Decision({}, source, False, f"unsupported schema {proposal.get('schema')!r}")]
    if source.startswith("inbox"):
        gen = _parse_ts(proposal.get("context_generated_at"))
        if gen is not None and ctx.now - gen > rules.inbox_max_age_s:
            return [Decision(c if isinstance(c, dict) else {}, source, False, "stale proposal (context older than "
                             f"{rules.inbox_max_age_s // 3600}h)") for c in proposal["changes"]] or \
                [Decision({}, source, False, "stale proposal")]
    out = []
    for ch in proposal["changes"]:
        counted = source != "ladder"
        try:
            if counted and state["count"] >= rules.max_changes:
                raise Reject(f"max {rules.max_changes} changes per retro")
            summary = _validate_one(ch, ctx, rules, source, state)
        except Reject as exc:
            out.append(Decision(ch if isinstance(ch, dict) else {}, source, False, str(exc)))
            continue
        state["touched"].add(ch["variant_id"])
        state["count"] += 1 if counted else 0
        if ch["change"] == "new_variant":
            state["new_variants"] += 1
        out.append(Decision(ch, source, True, "", summary))
    return out


def apply_to_variant(decision: Decision, ctx: Context) -> Variant:
    """Return the new/updated Variant for an accepted decision (does not write)."""
    ch = decision.change
    vid, kind, values = ch["variant_id"], ch["change"], ch["values"]
    if kind == "new_variant":
        base = ctx.variants[values["based_on"]]
        params = copy.deepcopy(base.params)
        for k, val in (values.get("params") or {}).items():
            set_path(params, k, val)
        limits = copy.deepcopy(base.limits)  # AI-proposed limits are ignored: inherited, scaled to stake 5
        ratio = MIN_STAKE_USDC / float(base.stake_usdc) if base.stake_usdc else 1.0
        for key in SCALED_LIMITS:
            if _num(limits.get(key)):
                limits[key] = round(float(limits[key]) * ratio, 2)
        return Variant(id=vid, family=base.family, hypothesis=values.get("hypothesis") or base.hypothesis,
                       account=values.get("account"), mode="paper", sports=list(values.get("sports") or base.sports),
                       stake_usdc=MIN_STAKE_USDC, params=params, bounds=copy.deepcopy(base.bounds), limits=limits,
                       notes=(values.get("notes") or f"autopilot paper variant from {base.id}"))
    v = copy.deepcopy(ctx.variants[vid])
    if kind == "params":
        for k, val in values.get("params", values).items():
            set_path(v.params, k, val)
    elif kind == "stake":
        new = float(values["stake_usdc"])
        ratio = new / float(v.stake_usdc) if v.stake_usdc else 1.0
        for key in SCALED_LIMITS:
            if _num(v.limits.get(key)):
                v.limits[key] = round(float(v.limits[key]) * ratio, 2)
        v.stake_usdc = new
    elif kind == "mode":
        v.mode = values["mode"]
    elif kind == "retire":
        v.mode = "off"
    return v

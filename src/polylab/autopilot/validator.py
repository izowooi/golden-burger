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
Per-sport variants (yaml `sports:` mapping, 2026-10-05) take an optional "sport" on params / stake /
mode changes: params become `sport_overrides.<sport>.<name>` (bounds: that dotted key, else the base
name), stake and mode move only that sport. On per-sport variants params and stake changes must
name a sport. paper→live is accepted only from source "promotion" (the retro's deterministic gate,
polylab.risk.promotion, 2026-10-06) with passing gate evidence the retro stored in Context.promotions;
AI and inbox proposals can never move anything to live.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from polylab.registry import MAX_STAKE_USDC, MIN_STAKE_USDC, STAKE_LADDER, Variant

SCHEMA = "polylab.proposal/v1"
CHANGE_TYPES = ("params", "stake", "mode", "new_variant", "retire")
CHANGE_KEYS = {"variant_id", "change", "values", "rationale", "evidence", "sport"}
SPORTS = ("soccer", "mlb", "nba", "nfl", "nhl")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
SAFE_MODE_TRANSITIONS = {("live", "paper"), ("live", "off"), ("paper", "off")}
PROMOTION_SOURCE = "promotion"           # only the retro's deterministic gate uses it (never AI / inbox)
UNCOUNTED_SOURCES = ("ladder", PROMOTION_SOURCE)
NEW_VARIANT_KEYS = {"based_on", "family", "hypothesis", "sports", "params", "account", "limits", "notes"}
DAY = 86400

# Values the owner fixed in reports/decisions.md. The AI may raise them via attention, never change them.
OWNER_FIXED_PARAMS: dict[str, frozenset[str]] = {
    # 2026-10-04 `track1:goal-over-exit-rules`: -10% stop and hold at bid >= 0.99 stay owner-fixed.
    # 2026-10-05: the +0.02 take-profit is NOT owner-fixed — autopilot may retune it (backtest/sample gates apply).
    "goal-over-all": frozenset({"stop_loss_pct", "hold_above_price", "stop_loss_price"}),
}


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
    # Backtest-backed retune: a params change without min_trades_params live trades (or up to
    # backtest_max_step_mult x max_step) passes only on a backtest the retro itself ran for exactly
    # these values vs the current params, split in two halves by entry time.
    backtest_retune: bool = False
    backtest_idle_only: bool = False     # daily: only variants with 0 entries for 3+ days despite target games
    backtest_min_n: int = 40             # proposed trades over the whole window
    backtest_min_half_n: int = 10        # proposed trades in each half
    backtest_max_step_mult: float = 2.0
    backtest_mdd_tolerance: float = 0.20  # proposed max drawdown <= current x 1.2
    backtest_lookback_days: int = 120
    backtest_max_runs: int = 1           # proposals backtested per retro (each = current + proposed replay)
    backtest_budget_s: int = 900
    backtest_soft_deadline_s: int = 1800  # no replay starts/continues past this much retro wall time (Jenkins timeout)
    # Deterministic paper→live promotion gate (polylab.risk.promotion): every retro evaluates the cheap paper
    # checks; only retros with promotion_replay run the 120-day replay (weekly: 150-min Jenkins timeout).
    promotion_replay: bool = False
    promotion_max_runs: int = 1
    promotion_budget_s: int = 1800

    @classmethod
    def for_kind(cls, kind: str) -> "Rules":
        if kind == "weekly":
            return cls(max_changes=5, allow_new_variants=True, backtest_retune=True, backtest_max_runs=2,
                       backtest_budget_s=1800, backtest_soft_deadline_s=3600, promotion_replay=True)
        if kind == "monthly":
            return cls(max_changes=4, allow_new_variants=True, max_new_variants=1)
        return cls(backtest_retune=True, backtest_idle_only=True)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Facts:
    """Deterministic evidence per variant, computed from ledgers by the retro."""
    trades_at_version: int = 0
    last_param_change_ts: int | None = None
    last_stake_change_ts: int | None = None
    promote_ok: bool | None = None       # deterministic ladder gate (polylab.risk); None = unavailable
    idle: bool = False                   # 0 entries for 3+ days although target games were played
    by_sport: dict[str, "Facts"] = field(default_factory=dict)   # per-sport variants only


@dataclass
class Context:
    variants: dict[str, Variant]
    facts: dict[str, Facts]
    now: int
    families: set[str] = field(default_factory=set)
    known_aliases: set[str] = field(default_factory=set)
    # evidence_key(variant_id, values) -> evidence computed by the retro (never taken from the proposal)
    backtests: dict[str, dict] = field(default_factory=dict)
    fixed_params: dict[str, frozenset[str]] = field(default_factory=lambda: dict(OWNER_FIXED_PARAMS))
    # promotion_key(variant_id, sport) -> passing gate evidence computed by the retro (polylab.risk.promotion)
    promotions: dict[str, dict] = field(default_factory=dict)


@dataclass
class Decision:
    change: dict
    source: str
    accepted: bool
    reason: str = ""
    summary: str = ""
    backtest: dict | None = None         # retro-run evidence when the backtest-backed path was used

    def as_dict(self) -> dict:
        out = {"variant_id": self.change.get("variant_id"), "change": self.change.get("change"),
               **({"sport": self.change["sport"]} if self.change.get("sport") else {}),
               "values": self.change.get("values"), "rationale": self.change.get("rationale"),
               "source": self.source, "accepted": self.accepted, "reason": self.reason, "summary": self.summary}
        if self.backtest is not None:
            out["backtest"] = self.backtest
        return out


class Reject(Exception):
    pass


class NeedsBacktest(Reject):
    """Everything else passed; only retro-run backtest evidence for these exact values is missing."""


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


def _check_params(new: dict, current: dict, bounds: dict, rules: Rules, relative: bool,
                  step_mult: float = 1.0) -> list[str]:
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
            limit = _step(bound, rules) * step_mult
            if abs(float(value) - float(old)) > limit + 1e-9:
                raise Reject(f"param {name} step {abs(float(value) - float(old)):g} > "
                             f"{'' if step_mult == 1 else f'{step_mult:g}x '}max_step {_step(bound, rules):g}")
            if float(value) == float(old):
                raise Reject(f"param {name} unchanged")
        parts.append(f"{name} {old}→{value}")
    return parts


def promotion_key(variant_id: str, sport: str | None) -> str:
    return f"{variant_id}|{sport}"


def evidence_key(variant_id: str, values: dict) -> str:
    """Canonical key binding retro-run backtest evidence to exact proposed values."""
    norm = {k: (float(v) if _num(v) else v) for k, v in (values or {}).items()}
    return f"{variant_id}|{json.dumps(norm, sort_keys=True)}"


def resolve_params(ch: dict, v: Variant) -> tuple[dict, dict, dict]:
    """(values, bounds, current params) of a params change, with a sport-targeted change mapped onto
    `sport_overrides.<sport>.<name>`: bounds from that dotted key, else the base name; the current value
    is the sport's effective one (override if present, else base)."""
    new = ch["values"].get("params", ch["values"])
    sport = ch.get("sport")
    if not sport or not isinstance(new, dict):
        return new, v.bounds, v.params
    prefix = f"sport_overrides.{sport}."
    values, bounds, current = {}, dict(v.bounds), copy.deepcopy(v.params)
    for name, value in new.items():
        if name.startswith("sport_overrides."):
            if not name.startswith(prefix):
                raise Reject(f"param {name} targets another sport than {sport}")
            base = name[len(prefix):]
        else:
            base = name
        key = prefix + base
        values[key] = value
        if key not in bounds and base in v.bounds:
            bounds[key] = v.bounds[base]
        absent = object()
        if get_path(current, key, absent) is absent and get_path(v.params, base, absent) is not absent:
            set_path(current, key, copy.deepcopy(get_path(v.params, base)))
    return values, bounds, current


def _dotted_sport(ch: dict) -> str | None:
    """The sport of a params change whose keys are all `sport_overrides.<one sport>.*`."""
    vals = ch.get("values") if ch.get("change") == "params" else None
    vals = vals.get("params", vals) if isinstance(vals, dict) else None
    if not isinstance(vals, dict) or not vals:
        return None
    sports = {k.split(".")[1] for k in vals if isinstance(k, str) and k.startswith("sport_overrides.") and k.count(".") >= 2}
    if len(sports) == 1 and all(isinstance(k, str) and k.startswith("sport_overrides.") for k in vals):
        return sports.pop()
    return None


def change_facts(ch: dict, ctx: Context) -> Facts:
    f = ctx.facts.get(ch.get("variant_id"), Facts())
    sport = ch.get("sport")
    return f.by_sport.get(sport, Facts()) if sport else f


def needs_backtest(ch: dict, ctx: Context, rules: Rules) -> bool:
    """A params change that the plain rules would refuse for sample size or step size only."""
    if not isinstance(ch, dict) or ch.get("change") != "params" or not isinstance(ch.get("values"), dict):
        return False
    v = ctx.variants.get(ch.get("variant_id"))
    if v is None:
        return False
    if change_facts(ch, ctx).trades_at_version < rules.min_trades_params:
        return True
    try:
        new, bounds, current = resolve_params(ch, v)
    except Reject:
        return False
    if not isinstance(new, dict):
        return False
    for name, value in new.items():
        bound, old = bounds.get(name), get_path(current, name)
        if isinstance(bound, (list, tuple)) and len(bound) >= 2 and _num(value) and _num(old) and \
                all(_num(b) for b in bound[:2]) and abs(float(value) - float(old)) > _step(bound, rules) + 1e-9:
            return True
    return False


def _pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.2f}%"


def backtest_gate(ev: dict, rules: Rules, stake_usdc: float) -> tuple[bool, str]:
    """Deterministic acceptance of retro-run evidence:
    {"current"|"proposed": {"n", "roi", "max_dd", "halves": [{"n", "roi", "max_dd"}, {...}]}, ...}.
    A current arm with no trades in a half counts as ROI 0 (it made nothing); a proposed half must trade."""
    if not isinstance(ev, dict) or ev.get("error"):
        return False, f"backtest error: {(ev or {}).get('error') if isinstance(ev, dict) else 'invalid'}"
    cur, new = ev.get("current") or {}, ev.get("proposed") or {}
    if len(cur.get("halves") or []) != 2 or len(new.get("halves") or []) != 2:
        return False, "evidence must have two halves per arm"
    n = int(new.get("n") or 0)
    if n < rules.backtest_min_n:
        return False, f"proposed n={n} < {rules.backtest_min_n}"
    for i, (c, p) in enumerate(zip(cur["halves"], new["halves"]), 1):
        if p.get("roi") is None or int(p.get("n") or 0) < rules.backtest_min_half_n:
            return False, f"H{i} proposed n={p.get('n') or 0} < {rules.backtest_min_half_n}"
        base = c.get("roi") if c.get("roi") is not None else 0.0
        if p["roi"] + 1e-12 < base:
            return False, f"H{i} ROI {_pct(p['roi'])} < current {_pct(c.get('roi'))}"
    cur_dd, new_dd = float(cur.get("max_dd") or 0.0), float(new.get("max_dd") or 0.0)
    allowed = cur_dd * (1 + rules.backtest_mdd_tolerance) if cur_dd > 0 else float(stake_usdc)
    if new_dd > allowed + 1e-9:
        return False, f"MDD {new_dd:.2f} > allowed {allowed:.2f} (current {cur_dd:.2f})"
    halves = " / ".join(f"H{i} {_pct(p['roi'])} vs {_pct(c.get('roi'))}"
                        for i, (c, p) in enumerate(zip(cur["halves"], new["halves"]), 1))
    return True, f"n {cur.get('n') or 0}→{n}, {halves}, MDD {cur_dd:.2f}→{new_dd:.2f}"


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
    if source not in UNCOUNTED_SOURCES and (not isinstance(ch.get("rationale"), str) or not ch["rationale"].strip()):
        raise Reject("rationale required")
    if "evidence" in ch and not isinstance(ch["evidence"], (dict, list, str)):
        raise Reject("evidence must be an object")
    sport = ch.get("sport")
    if sport is not None and (kind not in ("params", "stake", "mode") or sport not in SPORTS):
        raise Reject("sport is allowed only on params/stake/mode changes and must be one of " + "/".join(SPORTS))
    if vid in state["touched"] or (vid, sport) in state["touched"] or \
            (sport is None and any(isinstance(t, tuple) and t[0] == vid for t in state["touched"])):
        raise Reject("only one change per variant (per sport) per retro")

    if kind == "new_variant":
        return _validate_new(vid, values, ctx, rules, state)

    v = ctx.variants.get(vid)
    if v is None:
        raise Reject(f"unknown variant {vid}")
    if sport is not None and sport not in v.sports:
        raise Reject(f"variant {vid} does not cover {sport}")
    if sport is not None and kind in ("stake", "mode") and not v.per_sport:
        raise Reject("per-sport stake/mode needs a per-sport variant (yaml sports mapping)")
    if sport is None and kind in ("params", "stake") and v.per_sport:
        raise Reject(f"{vid} has per-sport settings: name the sport for a {kind} change")
    f = change_facts(ch, ctx)
    where = f"[{sport}] " if sport else ""

    if kind == "params":
        if v.mode == "off" or (sport and v.sport_mode(sport) == "off"):
            raise Reject("variant is off" if not sport else f"{sport} is off")
        raw = values.get("params", values)
        new, bounds, current = resolve_params(ch, v)
        fixed = sorted(set(raw) & set(ctx.fixed_params.get(vid, ()))) if isinstance(raw, dict) else []
        if fixed:
            raise Reject(f"owner-fixed params {fixed} (reports/decisions.md): propose via attention instead")
        if f.last_param_change_ts and ctx.now - f.last_param_change_ts < rules.cooldown_params_s:
            raise Reject(f"param cooldown: last change {(ctx.now - f.last_param_change_ts) / 3600:.0f}h ago")
        few = f.trades_at_version < rules.min_trades_params
        eligible = rules.backtest_retune and (f.idle or not rules.backtest_idle_only)
        if not few and (not eligible or not needs_backtest(ch, ctx, rules)):
            return where + ", ".join(_check_params(new, current, bounds, rules, relative=True))
        why = (f"only {f.trades_at_version} settled trades at current params (< {rules.min_trades_params})" if few
               else "step > max_step")
        if not rules.backtest_retune:
            raise Reject(why)
        if not eligible:
            raise Reject(f"{why}; backtest-backed retune in this retro only for idle variants")
        parts = _check_params(new, current, bounds, rules, relative=True, step_mult=rules.backtest_max_step_mult)
        ev = ctx.backtests.get(evidence_key(vid, new))
        if ev is None:
            raise NeedsBacktest(f"{why}; no retro-run backtest for these exact values "
                                "(AI-stated evidence is not accepted)")
        ok, verdict = backtest_gate(ev, rules, v.sport_stake(sport))
        if not ok:
            raise Reject(f"{why}; backtest gate failed: {verdict}")
        state["backtest"] = ev
        return where + ", ".join(parts) + f" [backtest: {verdict}]"

    if kind == "stake":
        if set(values) != {"stake_usdc"} or not _num(values["stake_usdc"]):
            raise Reject("stake values must be {stake_usdc: number}")
        new = float(values["stake_usdc"])
        cur = v.sport_stake(sport)
        if new > MAX_STAKE_USDC or new not in STAKE_LADDER:
            raise Reject(f"stake {new:g} not on ladder {STAKE_LADDER}")
        if cur not in STAKE_LADDER:
            raise Reject("current stake not on ladder")
        step = STAKE_LADDER.index(new) - STAKE_LADDER.index(cur)
        if step == 0:
            raise Reject("stake unchanged")
        if abs(step) != 1:
            raise Reject("stake may move only one ladder step")
        if step > 0:
            if v.sport_mode(sport) != "live":
                raise Reject("stake promotion only for live variants" + (f" ({sport} is not live)" if sport else ""))
            if f.promote_ok is not True:
                raise Reject("ladder gate not passed (or polylab.risk unavailable): promotion refused")
            if f.last_stake_change_ts and ctx.now - f.last_stake_change_ts < rules.cooldown_stake_s:
                raise Reject(f"stake cooldown: last change {(ctx.now - f.last_stake_change_ts) / 3600:.0f}h ago")
        return f"{where}stake {cur:g}→{new:g} USDC"

    if kind == "mode":
        if set(values) != {"mode"}:
            raise Reject("mode values must be {mode: paper|off}")
        target = values["mode"]
        cur = v.sport_mode(sport, ctx.now) if sport else v.mode
        if target == "live":
            if source != PROMOTION_SOURCE:
                raise Reject(f"{where}mode {cur}→live not allowed: only the deterministic promotion gate (never AI/inbox)")
            if not sport or not v.per_sport:
                raise Reject("promotion is per (variant, sport) of a per-sport variant")
            gate = ctx.promotions.get(promotion_key(vid, sport))
            if not isinstance(gate, dict) or gate.get("ok") is not True:
                raise Reject(f"{where}no passing promotion-gate evidence computed by this retro")
            if cur != "paper":
                raise Reject(f"{where}mode {cur}→live: only paper sports are promoted")
            return f"{where}mode paper→live (자동 실거래 전환, 단위 5 USDC)"
        if (cur, target) not in SAFE_MODE_TRANSITIONS:
            raise Reject(f"{where}mode {cur}→{target} not allowed (only toward safety; live only via the promotion gate)")
        return f"{where}mode {cur}→{target}"

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


def backtest_candidates(proposal: Any, ctx: Context, rules: Rules) -> list[tuple[str, dict]]:
    """(variant_id, params) of changes that would pass if the retro's own backtest evidence existed.
    Dry run: budget/one-per-variant are not consumed here."""
    out: list[tuple[str, dict]] = []
    if not isinstance(proposal, dict) or not isinstance(proposal.get("changes"), list):
        return out
    for ch in proposal["changes"]:
        try:
            _validate_one(ch, ctx, rules, "dry-run", {"count": 0, "touched": set(), "new_variants": 0})
        except NeedsBacktest:
            out.append((ch["variant_id"], dict(resolve_params(ch, ctx.variants[ch["variant_id"]])[0])))
        except Reject:
            continue
    return out


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
        counted = source not in UNCOUNTED_SOURCES
        state.pop("backtest", None)
        if isinstance(ch, dict) and ch.get("sport") is None:
            ch = {k: v for k, v in ch.items() if k != "sport"}
            target = ctx.variants.get(ch.get("variant_id"))
            inferred = _dotted_sport(ch) if target is not None and target.per_sport else None
            if inferred:   # per-sport variant: `sport_overrides.<sport>.<name>` keys name the sport themselves
                ch["sport"] = inferred
        try:
            if counted and state["count"] >= rules.max_changes:
                raise Reject(f"max {rules.max_changes} changes per retro")
            summary = _validate_one(ch, ctx, rules, source, state)
        except Reject as exc:
            out.append(Decision(ch if isinstance(ch, dict) else {}, source, False, str(exc)))
            continue
        state["touched"].add((ch["variant_id"], ch["sport"]) if ch.get("sport") else ch["variant_id"])
        state["count"] += 1 if counted else 0
        if ch["change"] == "new_variant":
            state["new_variants"] += 1
        out.append(Decision(ch, source, True, "", summary, backtest=state.pop("backtest", None)))
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
    sport = ch.get("sport")
    if kind == "params":
        for k, val in resolve_params(ch, v)[0].items():
            set_path(v.params, k, val)
    elif kind == "stake" and sport:
        new = float(values["stake_usdc"])
        active = [s for s in v.sports if v.sport_mode(s) != "off"] or v.sports
        old_max = max(v.sport_stake(s) for s in active)
        cfg = v.sport_settings.setdefault(sport, {})
        ratio_sport = new / v.sport_stake(sport)
        cfg["stake_usdc"] = new
        for key in SCALED_LIMITS:                   # the sport's own caps follow its tier
            if _num((cfg.get("limits") or {}).get(key)):
                cfg["limits"][key] = round(float(cfg["limits"][key]) * ratio_sport, 2)
        ratio = max(v.sport_stake(s) for s in active) / old_max
        for key in SCALED_LIMITS:                   # variant caps follow the largest sport tier
            if _num(v.limits.get(key)):
                v.limits[key] = round(float(v.limits[key]) * ratio, 2)
    elif kind == "stake":
        new = float(values["stake_usdc"])
        ratio = new / float(v.stake_usdc) if v.stake_usdc else 1.0
        for key in SCALED_LIMITS:
            if _num(v.limits.get(key)):
                v.limits[key] = round(float(v.limits[key]) * ratio, 2)
        v.stake_usdc = new
    elif kind == "mode" and sport and values["mode"] == "live":    # promotion gate only (validated above)
        cfg = v.sport_settings.setdefault(sport, {})
        cfg["mode"] = "live"
        cfg["stake_usdc"] = MIN_STAKE_USDC
        if v.mode == "paper":       # master switch must allow live; other sports keep their own (paper/off) mode
            for s in v.sports:
                v.sport_settings.setdefault(s, {}).setdefault("mode", "paper")
            v.mode = "live"
    elif kind == "mode" and sport:
        v.sport_settings.setdefault(sport, {})["mode"] = values["mode"]
    elif kind == "mode":
        v.mode = values["mode"]
    elif kind == "retire":
        v.mode = "off"
    return v

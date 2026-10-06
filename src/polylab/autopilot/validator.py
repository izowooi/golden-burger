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
name a sport. paper→live (per (variant, sport) of a per-sport variant, 5 USDC) is accepted from source
"promotion" (the retro's deterministic gate, polylab.risk.promotion, 2026-10-06) with passing gate evidence in
Context.promotions, and — owner decision 2026-10-06 `promotion:ai-direct` — from an AI / inbox proposal only when
the retro itself computed passing evidence for that (variant, sport): the deterministic gate (Context.promotions)
or its own replay checked by promotion.evaluate_direct (Context.live_evidence). Numbers written in a proposal are
never used. Sample minimums and replay windows are per sport (Context.sample_rules, promotion.sample_rule:
low-frequency sports such as NFL use smaller minimums and a 365-day window).
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

# Variants the owner kept off the AI-direct live path (reports/decisions.md). The deterministic promotion gate
# (>= 30 out-of-sample paper trades with an 80% lower bound > 0) may still promote them.
OWNER_NO_DIRECT_LIVE: dict[str, str] = {
    # 2026-10-06 `paper_ready:plum-king/queen`: no live at paper ROI -3.0%; the new NFL cell is an in-sample backtest
    "plum-king": "2026-10-06 paper_ready:plum-king",
    "plum-queen": "2026-10-06 paper_ready:plum-queen",
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
    # AI-direct paper→live (2026-10-06 `promotion:ai-direct`): replays the retro runs for AI/inbox live proposals
    live_evidence_max_runs: int = 1
    live_evidence_budget_s: int = 1500

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
    # promotion_key(variant_id, sport) -> retro-run AI-direct live evidence (promotion.evaluate_direct, pass or fail)
    live_evidence: dict[str, dict] = field(default_factory=dict)
    # promotion_key(variant_id, sport) -> promotion.SampleRule (per-sport minimums / replay window); absent = defaults
    sample_rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    change: dict
    source: str
    accepted: bool
    reason: str = ""
    summary: str = ""
    backtest: dict | None = None         # retro-run evidence when the backtest-backed path was used
    gate: dict | None = None             # retro-run live evidence when an AI/inbox paper→live passed

    def as_dict(self) -> dict:
        out = {"variant_id": self.change.get("variant_id"), "change": self.change.get("change"),
               **({"sport": self.change["sport"]} if self.change.get("sport") else {}),
               "values": self.change.get("values"), "rationale": self.change.get("rationale"),
               "source": self.source, "accepted": self.accepted, "reason": self.reason, "summary": self.summary}
        if self.backtest is not None:
            out["backtest"] = self.backtest
        if self.gate is not None:
            out["gate"] = self.gate
        return out


class Reject(Exception):
    pass


class NeedsBacktest(Reject):
    """Everything else passed; only retro-run backtest evidence for these exact values is missing."""


class NeedsLiveEvidence(Reject):
    """An AI/inbox paper→live proposal that the retro has not replayed yet."""


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


def sample_rule_of(ch: dict, ctx: Context):
    """The per-sport SampleRule of a change (None: variant-level defaults from Rules)."""
    sport = ch.get("sport") if isinstance(ch, dict) else None
    return ctx.sample_rules.get(promotion_key(ch.get("variant_id"), sport)) if sport else None


def min_trades_for(ch: dict, ctx: Context, rules: Rules) -> int:
    rule = sample_rule_of(ch, ctx)
    return min(rules.min_trades_params, rule.params_min_trades) if rule is not None else rules.min_trades_params


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
    if change_facts(ch, ctx).trades_at_version < min_trades_for(ch, ctx, rules):
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


def backtest_gate(ev: dict, rules: Rules, stake_usdc: float, rule=None) -> tuple[bool, str]:
    """Deterministic acceptance of retro-run evidence:
    {"current"|"proposed": {"n", "roi", "max_dd", "halves": [{"n", "roi", "max_dd"}, {...}]}, ...}.
    A current arm with no trades in a half counts as ROI 0 (it made nothing); a proposed half must trade.
    `rule` (promotion.SampleRule of the sport): its minimums apply and the evidence window must match it."""
    if not isinstance(ev, dict) or ev.get("error"):
        return False, f"backtest error: {(ev or {}).get('error') if isinstance(ev, dict) else 'invalid'}"
    min_n = rule.backtest_min_n if rule is not None else rules.backtest_min_n
    min_half = rule.backtest_min_half_n if rule is not None else rules.backtest_min_half_n
    if rule is not None and isinstance(ev.get("range"), (list, tuple)) and len(ev["range"]) == 2:
        days = round((ev["range"][1] - ev["range"][0]) / DAY)
        if days != rule.lookback_days:
            return False, f"evidence window {days}d != {rule.sport} rule {rule.lookback_days}d"
    cur, new = ev.get("current") or {}, ev.get("proposed") or {}
    if len(cur.get("halves") or []) != 2 or len(new.get("halves") or []) != 2:
        return False, "evidence must have two halves per arm"
    n = int(new.get("n") or 0)
    if n < min_n:
        return False, f"proposed n={n} < {min_n}"
    for i, (c, p) in enumerate(zip(cur["halves"], new["halves"]), 1):
        if p.get("roi") is None or int(p.get("n") or 0) < min_half:
            return False, f"H{i} proposed n={p.get('n') or 0} < {min_half}"
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
        min_trades = min_trades_for(ch, ctx, rules)
        few = f.trades_at_version < min_trades
        eligible = rules.backtest_retune and (f.idle or not rules.backtest_idle_only)
        if not few and (not eligible or not needs_backtest(ch, ctx, rules)):
            return where + ", ".join(_check_params(new, current, bounds, rules, relative=True))
        why = (f"only {f.trades_at_version} settled trades at current params (< {min_trades})" if few
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
        ok, verdict = backtest_gate(ev, rules, v.sport_stake(sport), sample_rule_of(ch, ctx))
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
            from polylab.risk import ladder as _ladder  # noqa: PLC0415
            if _ladder.STAKE_FREEZE_USDC is not None and new > _ladder.STAKE_FREEZE_USDC + 1e-9:
                raise Reject(f"stake freeze: no stake above {_ladder.STAKE_FREEZE_USDC:g} USDC "
                             "(owner 2026-10-06 stake:freeze-5)")
            if v.sport_mode(sport) != "live":
                raise Reject("stake promotion only for live variants" + (f" ({sport} is not live)" if sport else ""))
            if f.promote_ok is not True:
                raise Reject("ladder gate not passed (or polylab.risk unavailable): promotion refused")
            if f.last_stake_change_ts and ctx.now - f.last_stake_change_ts < rules.cooldown_stake_s:
                raise Reject(f"stake cooldown: last change {(ctx.now - f.last_stake_change_ts) / 3600:.0f}h ago")
        return f"{where}stake {cur:g}→{new:g} USDC"

    if kind == "mode":
        if set(values) != {"mode"}:
            raise Reject("mode values must be {mode: live|paper|off}")
        target = values["mode"]
        cur = v.sport_mode(sport, ctx.now) if sport else v.mode
        if target == "live":
            direct = source == "ai" or source.startswith(("ai:", "inbox:"))
            if source != PROMOTION_SOURCE and not direct:
                raise Reject(f"{where}mode {cur}→live not allowed from source {source}")
            if not sport or not v.per_sport:
                raise Reject(f"{where}mode {cur}→live not allowed: promotion is per (variant, sport) of a per-sport variant")
            if cur != "paper":
                raise Reject(f"{where}mode {cur}→live: only paper sports are promoted")
            if ctx.known_aliases and v.account not in ctx.known_aliases:
                raise Reject(f"{where}mode paper→live: account alias {v.account!r} is not configured")
            key = promotion_key(vid, sport)
            gate = ctx.promotions.get(key)
            if source == PROMOTION_SOURCE:
                if not isinstance(gate, dict) or gate.get("ok") is not True:
                    raise Reject(f"{where}no passing promotion-gate evidence computed by this retro")
                return f"{where}mode paper→live (자동 실거래 전환, 단위 5 USDC)"
            # AI-direct (2026-10-06 `promotion:ai-direct`): only the retro's own evidence, never proposal numbers
            if vid in OWNER_NO_DIRECT_LIVE:
                raise Reject(f"{where}mode paper→live by AI refused: owner decision {OWNER_NO_DIRECT_LIVE[vid]} "
                             "(only the deterministic promotion gate on new paper trades)")
            if isinstance(gate, dict) and gate.get("ok") is True:
                state["gate"] = gate
                return f"{where}mode paper→live (AI 실거래 전환, 결정론 게이트 근거, 단위 5 USDC)"
            direct_ev = ctx.live_evidence.get(key)
            if direct_ev is None:
                raise NeedsLiveEvidence(f"{where}mode paper→live needs the retro's own replay for this sport "
                                        "(AI-stated evidence is not accepted)")
            if direct_ev.get("ok") is not True:
                raise Reject(f"{where}mode paper→live refused: {direct_ev.get('stage')}: {direct_ev.get('reason')}")
            state["gate"] = direct_ev
            return f"{where}mode paper→live (AI 실거래 전환, 단위 5 USDC) [근거: {direct_ev.get('reason')}]"
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


def live_candidates(proposal: Any, ctx: Context, rules: Rules, source: str = "ai:dry-run") -> list[tuple[str, str]]:
    """(variant_id, sport) of AI/inbox paper→live changes that only lack the retro's own live evidence."""
    out: list[tuple[str, str]] = []
    if not isinstance(proposal, dict) or not isinstance(proposal.get("changes"), list):
        return out
    for ch in proposal["changes"]:
        try:
            _validate_one(ch, ctx, rules, source, {"count": 0, "touched": set(), "new_variants": 0})
        except NeedsLiveEvidence:
            if (ch["variant_id"], ch["sport"]) not in out:
                out.append((ch["variant_id"], ch["sport"]))
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
        state.pop("gate", None)
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
        out.append(Decision(ch, source, True, "", summary, backtest=state.pop("backtest", None),
                            gate=state.pop("gate", None)))
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

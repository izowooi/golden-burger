"""`polylab retro <daily|weekly|monthly> [--slot] [--no-ai] [--no-apply] [--no-push] [--no-publish] [--no-slack]`

1. deterministic report (window = since the last successful retro of this kind, catch-up after downtime)
2. deterministic ladder decisions (polylab.risk gate) as stake/mode changes, and the deterministic paper→live
   promotion gate per (variant, sport) (polylab.risk.promotion; the 120-day replay runs in weekly retros only)
3. private context pack → AI engine chain: claude -p → codex exec → none
   (weekly: the other engine reviews the primary proposal and may only veto changes)
4. optional external proposals from autopilot/inbox/*.json
5. validator (bounds, max_step, samples, cooldown, ladder gate, safety-only mode moves); a params change
   without enough live trades (or up to 2x max_step) needs a backtest the retro runs itself for exactly
   the proposed values vs the current params (weekly; daily only for idle (variant, sport)s); an AI paper→live
   proposal needs the retro's own replay of that sport (promotion.evaluate_direct, 2026-10-06 `promotion:ai-direct`).
   Sample minimums and replay windows are per sport (promotion.sample_rule; NFL etc. 365 days)
6. apply to strategies/*.yaml → pytest → revert on failure
7. attention inbox (reports/attention.{json,md}: deterministic rule items + at most 3 validated AI items) and the
   "오늘의 브리프" bullets at the top of the report and the Slack message
8. write reports/<kind>/<name>.md, reports/index.json, reports/changes.md, public context pack,
   docs/research/monthly/YYYY-MM.md (monthly) → commit/push → publish → Slack
AI failures never block steps 1, 2 and 5-8.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from polylab import registry, settings
from polylab.analysis import _common as C
from polylab.analysis import performance
from polylab.analysis.integrations import sport_of_params as integrations_sport
from polylab.autopilot import context as ctxpack
from polylab.autopilot import attention, gitops, monthly
from polylab.autopilot.runner import Engine, default_chain
from polylab.autopilot.validator import (PROMOTION_SOURCE, SCHEMA, Context, Decision, Facts, Rules, apply_to_variant,
                                        backtest_candidates, evidence_key, live_candidates, promotion_key, set_path,
                                        validate)
from polylab.registry import STAKE_LADDER
from polylab.reports import build as report_build
from polylab.reports import brief as brief_mod
from polylab.reports import render, slack
from polylab.reports.build import KIND_KO

CATCHUP_MAX_S = {"daily": 3 * 86400, "weekly": 21 * 86400, "monthly": 0}
CURRENT_SPORTS_FEE_RATE = 0.05   # = polylab.analysis.backtest.CURRENT_SPORTS_FEE_RATE (sports_fees_v3)
NARRATIVE_MAX_CHARS = 20_000


@dataclass
class Options:
    kind: str
    slot: str | None = None
    ai: bool = True
    apply: bool = True
    push: bool = True
    publish: bool = True
    slack: bool = True
    analysis: bool = True
    backtest: bool = True
    now: int | None = None
    ai_timeout: int = 1200


def run_backtests(jobs: list[tuple[object, dict]], start: int, end: int, timeout_s: float) -> list[dict]:
    """Replay each (variant, params override) with `polylab.analysis.backtest` in parallel subprocesses
    (hard timeout; same POLYLAB_ROOT). Returns full result dicts (with trades) or {"error": ...}."""
    day = lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts))  # noqa: E731
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="polylab-retune-") as tmp:
        procs = []
        for i, (variant, params) in enumerate(jobs):
            d = Path(tmp) / str(i)
            d.mkdir()
            registry.save_variant(copy.deepcopy(variant), d)
            nested: dict = {}
            for k, val in (params or {}).items():
                set_path(nested, k, val)
            out, err = d / "out.json", d / "stderr.txt"
            cmd = [sys.executable, "-m", "polylab.analysis.backtest", "--variant-file", str(d / f"{variant.id}.yaml"),
                   "--from", day(start), "--to", day(end), "--out", str(out)]
            if variant.family != "cherry":   # sports markets: gate evidence at the current fee schedule
                cmd += ["--fee-rate", str(CURRENT_SPORTS_FEE_RATE)]
            if nested:
                cmd += ["--params", json.dumps(nested)]
            with err.open("w") as fh:
                procs.append((subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=fh), out, err))
        deadline = time.time() + timeout_s
        for proc, out, err in procs:
            try:
                proc.wait(timeout=max(1.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                results.append({"error": f"timeout after {timeout_s:.0f}s"})
                continue
            if proc.returncode != 0:
                results.append({"error": f"exit {proc.returncode}: {err.read_text()[-200:]}"})
                continue
            try:
                results.append(json.loads(out.read_text()))
            except (OSError, json.JSONDecodeError) as exc:
                results.append({"error": f"unreadable backtest output: {exc}"})
    return results


@dataclass
class Env:
    """Injectable locations and side effects (tests replace these)."""
    paths: object
    repo: Path = settings.REPO_ROOT
    registry_dir: Path = registry.REGISTRY_DIR
    reports_dir: Path = settings.REPO_ROOT / "reports"
    research_docs_dir: Path = settings.REPO_ROOT / "docs" / "research" / "monthly"
    inbox_dir: Path = settings.REPO_ROOT / "autopilot" / "inbox"
    processed_dir: Path = settings.REPO_ROOT / "autopilot" / "processed"
    prompts_dir: Path = ctxpack.PROMPTS_DIR
    engines: Callable[[], list[Engine]] = default_chain
    run_tests: Callable[[Path], tuple[bool, str]] = gitops.run_tests
    commit: Callable[..., tuple[bool, str]] = gitops.commit_and_push
    pull: Callable[[Path], tuple[bool, str]] = gitops.pull
    backtest: Callable[..., list[dict]] = run_backtests
    publish: Callable | None = None
    post_slack: Callable | None = None
    use_jenkins: bool = True
    log: list[str] = field(default_factory=list)

    def say(self, msg: str) -> None:
        self.log.append(msg)
        print(f"retro: {msg}", flush=True)


# ------------------------------------------------------------------ state

def _state_dir(paths) -> Path:
    d = Path(paths.state) / "retro"
    d.mkdir(parents=True, exist_ok=True)
    return d


def last_success(paths, kind: str) -> int | None:
    try:
        return int(json.loads((_state_dir(paths) / f"last_{kind}.json").read_text())["until"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def catchup_since(paths, kind: str, now: int) -> int | None:
    last = last_success(paths, kind)
    if last is None or CATCHUP_MAX_S[kind] == 0:
        return None
    return max(last, now - CATCHUP_MAX_S[kind])


def record_state(paths, kind: str, now: int, result: dict) -> None:
    d = _state_dir(paths)
    (d / "last.json").write_text(json.dumps({k: result.get(k) for k in
                                             ("at", "kind", "ok", "ai", "engine", "error", "name")}, ensure_ascii=False))
    with (d / "history.jsonl").open("a") as f:
        f.write(json.dumps({"ts": now, "kind": kind, "name": result.get("name"), "ok": result.get("ok"),
                            "engine": result.get("engine"),
                            "applied": [a["summary"] for a in result.get("applied", [])]}, ensure_ascii=False) + "\n")
    if result.get("deterministic_ok"):
        (d / f"last_{kind}.json").write_text(json.dumps({"until": now, "name": result.get("name")}))


# ------------------------------------------------------------------ facts & ladder

def variant_facts(v, paths, ladder: dict, repo: Path, sport_ladders: dict | None = None) -> Facts:
    """Sample size and cooldown clocks. param_versions also bumps on stake/mode changes, so the
    "current params" epoch starts at the first version whose params equal the latest params."""
    df = performance.load_variant_positions(paths.strategy_db(v.id))
    s = performance.settled(df, "live" if v.mode == "live" else "paper")
    history = report_build.param_history(paths, v.id)
    stakes = report_build.stake_events(paths, v.id)
    last_param = None
    if history:
        current = history[-1]["params"]
        epoch = [h for h in history if h["params"] == current]
        i = len(history) - 1
        while i > 0 and history[i - 1]["params"] == current:
            i -= 1
        last_param = history[i]["ts"] if i > 0 else None  # first version = init, not a change
        versions = {h["version"] for h in history[i:]} & {h["version"] for h in epoch}
        if not s.empty:
            s = s[s["param_version"].isin(versions)]
    if last_param is None and v.path:
        last_param = gitops.last_commit_ts(Path(v.path), repo) if not history else None
    facts = Facts(trades_at_version=int(len(s)), last_param_change_ts=last_param,
                  last_stake_change_ts=max((x["ts"] for x in stakes if x["ts"]), default=None),
                  promote_ok=ladder.get("promote_ok"))
    if v.per_sport:
        facts.by_sport = sport_facts(v, df, history, stakes, sport_ladders or {})
    return facts


def sport_facts(v, df, history: list[dict], stakes: list[dict], ladders: dict) -> dict[str, Facts]:
    """Per-sport sample size and clocks. A sport's "current params" epoch starts at the first ledger
    version whose effective params for that sport (base + sport_overrides.<sport>) equal the latest,
    so adding another sport's overrides does not reset this sport's sample or cooldown."""
    out: dict[str, Facts] = {}
    for sport in v.sports:
        mode = v.sport_mode(sport)
        sub = df[df["sport"] == sport] if not df.empty and "sport" in df else df
        s = performance.settled(sub, "live" if mode == "live" else "paper")
        last_param = None
        if history:
            eff = [registry.effective_params(h["params"] or {}, sport) for h in history]
            i = len(history) - 1
            while i > 0 and eff[i - 1] == eff[-1]:
                i -= 1
            last_param = history[i]["ts"] if i > 0 else None
            if not s.empty:
                s = s[s["param_version"].isin({h["version"] for h in history[i:]})]
        own = [x["ts"] for x in stakes if x["ts"] and x.get("sport") in (None, sport)]
        out[sport] = Facts(trades_at_version=int(len(s)), last_param_change_ts=last_param,
                           last_stake_change_ts=max(own, default=None),
                           promote_ok=(ladders.get(sport) or {}).get("promote_ok"))
    return out


def _rv(report: dict, vid: str) -> dict:
    return next(s for s in report["variants"] if s["id"] == vid)


def ladder_changes(report: dict) -> dict:
    """Deterministic ladder moves from polylab.risk.ladder (no AI involved)."""
    changes = []
    for v in report["variants"]:
        if v.get("per_sport"):   # one ladder per (variant, sport)
            rows = [(d["ladder"] or {}, d["mode"], d["sport"]) for d in v.get("sports_detail") or []]
        else:
            rows = [(v["ladder"], v["mode"], None)]
        for lad, mode, sport in rows:
            action = lad.get("action")
            if not lad.get("available") or mode != "live" or action in (None, "hold"):
                continue
            evidence = {k: lad.get(k) for k in ("status", "trades_at_tier", "roi_ci_lo", "reason")}
            extra = {"sport": sport} if sport else {}
            if action == "paper":
                changes.append({"variant_id": v["id"], "change": "mode", "values": {"mode": "paper"}, **extra,
                                "rationale": f"ladder: {lad.get('reason')}", "evidence": evidence})
            elif action in ("promote", "demote") and lad.get("next_stake_usdc") is not None:
                changes.append({"variant_id": v["id"], "change": "stake", **extra,
                                "values": {"stake_usdc": float(lad["next_stake_usdc"])},
                                "rationale": f"ladder {action}: {lad.get('reason')}", "evidence": evidence})
    return {"schema": SCHEMA, "summary": "deterministic ladder", "changes": changes}


def record_stake_events(applied: list[dict], vctx_before: dict, paths, now: int) -> None:
    """Stake/mode moves go to the variant ledger's stake_events (ladder evidence via polylab.risk)."""
    from polylab import db  # noqa: PLC0415
    for a in applied:
        if a["change"] not in ("stake", "mode", "retire"):
            continue
        old = vctx_before.get(a["variant_id"])
        if old is None:
            continue
        conn = db.strategy(paths, a["variant_id"])
        sport = a.get("sport")
        try:
            from polylab.risk import ladder  # noqa: PLC0415
            if a["source"] == "ladder":
                from polylab.analysis import integrations  # noqa: PLC0415
                lad = integrations.ladder_status(old, paths, now, sport=sport)
                if lad.get("decision") is not None:
                    ladder.record_decision(conn, lad["decision"], now, sport=sport)
                    continue
            new = a["new"]
            ladder.ensure_sport_column(conn)
            if sport:
                vals = (old.sport_stake(sport), new.sport_stake(sport), old.sport_mode(sport), new.sport_mode(sport))
            else:
                vals = (old.stake_usdc, new.stake_usdc, old.mode, new.mode)
            evidence = {"rationale": a.get("rationale")}
            if a["source"] == PROMOTION_SOURCE or (a["change"] == "mode" and a.get("evidence") is not None):
                evidence["gate"] = a.get("evidence")     # retro-computed (never the AI's own numbers)
            conn.execute("INSERT INTO stake_events(ts, from_usdc, to_usdc, from_mode, to_mode, reason, evidence, sport) "
                         "VALUES(?,?,?,?,?,?,?,?)", (now, *vals, f"{a['source']}: {a['summary']}"[:500],
                                                     json.dumps(evidence, ensure_ascii=False, default=str), sport))
            conn.commit()
        finally:
            conn.close()


# ------------------------------------------------------------------ paper→live promotion gate

def promotion_sample(v, sport: str, paths) -> tuple[list, int | None]:
    """(settled paper trades of `sport` on its current param epoch opened after its last stake/mode event and
    outside the preseason window (the backtests that chose the params excluded preseason games),
    last change ts = max(param epoch start, last stake/mode event))."""
    from polylab.risk.promotion import PaperTrade, in_preseason  # noqa: PLC0415
    df = performance.load_variant_positions(paths.strategy_db(v.id))
    sub = df[df["sport"] == sport] if not df.empty and "sport" in df else df
    s = performance.settled(sub, "paper")
    history = report_build.param_history(paths, v.id)
    last_param = None
    if history:
        eff = [registry.effective_params(h["params"] or {}, sport) for h in history]
        i = len(history) - 1
        while i > 0 and eff[i - 1] == eff[-1]:
            i -= 1
        last_param = history[i]["ts"] if i > 0 else None
        if not s.empty:
            s = s[s["param_version"].isin({h["version"] for h in history[i:]})]
    own = [x["ts"] for x in report_build.stake_events(paths, v.id)
           if x["ts"] and x.get("sport") in (None, sport) and x.get("from_usdc") is not None]
    last_stake = max(own, default=None)
    if last_stake is not None and not s.empty:
        s = s[s["opened_at"] >= last_stake]
    trades = [PaperTrade(int(r["opened_at"]), int(r["closed_at"]), float(r["realized_pnl"]), float(r["cost_usdc"] or 0))
              for _, r in s.iterrows() if not in_preseason(sport, int(r["opened_at"]))]
    changes = [t for t in (last_param, last_stake) if t is not None]
    return trades, (max(changes) if changes else None)


def live_epoch_trades(v, sport: str, paths) -> list:
    """Settled LIVE trades of `sport` on its current param epoch (AI-direct: a sport the ladder just demoted for live
    losses must not be re-promoted on a backtest alone — its live record joins the contradiction check)."""
    from polylab.risk.promotion import PaperTrade  # noqa: PLC0415
    df = performance.load_variant_positions(paths.strategy_db(v.id))
    sub = df[df["sport"] == sport] if not df.empty and "sport" in df else df
    s = performance.settled(sub, "live")
    history = report_build.param_history(paths, v.id)
    if history and not s.empty:
        eff = [registry.effective_params(h["params"] or {}, sport) for h in history]
        i = len(history) - 1
        while i > 0 and eff[i - 1] == eff[-1]:
            i -= 1
        s = s[s["param_version"].isin({h["version"] for h in history[i:]})]
    return [PaperTrade(int(r["opened_at"]), int(r["closed_at"]), float(r["realized_pnl"]), float(r["cost_usdc"] or 0))
            for _, r in s.iterrows()]


def _replay_summary(result: dict, start: int, end: int) -> dict:
    """Replay arm stats plus both halves split at the median entry (AI-direct live evidence uses the halves)."""
    if result.get("error"):
        return {"error": result["error"], "range": [start, end]}
    trades = result.get("trades") or []
    arm = _arm_stats(trades)
    opens = sorted(t["opened_at"] for t in trades if t.get("opened_at") is not None)
    split = opens[len(opens) // 2] if len(opens) >= 2 else (start + end) // 2
    halves = [_arm_stats([t for t in trades if (t.get("opened_at") or 0) < split]),
              _arm_stats([t for t in trades if (t.get("opened_at") or 0) >= split])]
    return {"n": arm["n"], "roi": arm["roi"], "pnl": arm["pnl"], "max_dd": arm["max_dd"], "range": [start, end],
            "split_ts": split, "halves": halves, "fee_rate": result.get("fee_rate")}


def sample_rules(variants, paths, now: int) -> dict:
    """promotion_key(variant, sport) -> promotion.SampleRule from core.db game counts (league scope of soccer)."""
    from polylab.risk import promotion  # noqa: PLC0415
    out: dict = {}
    end = now - now % 86400
    core = C.open_ro(paths.core_db) if Path(paths.core_db).exists() else None
    try:
        for v in variants:
            if not v.per_sport:
                continue
            for sport in v.sports:
                leagues = registry.effective_params(v.params or {}, sport).get("leagues")
                try:
                    out[promotion_key(v.id, sport)] = promotion.rule_for(core, sport, end, leagues)
                except Exception:   # unreadable core: static config only
                    out[promotion_key(v.id, sport)] = promotion.sample_rule(sport)
    finally:
        if core is not None:
            core.close()
    return out


def live_evidence(proposals: list[tuple[str, dict]], vctx: Context, rules: Rules, env: Env, now: int,
                  retro_started: float | None = None) -> list[str]:
    """AI/inbox paper→live proposals (2026-10-06 `promotion:ai-direct`): the retro replays the sport itself at current
    params / current fees over the sport's window and stores promotion.evaluate_direct in vctx.live_evidence (the
    validator reads only that). Bounded like the other replays; a skipped run means rejection."""
    from polylab.risk import promotion  # noqa: PLC0415
    notes: list[str] = []
    todo: list[tuple[str, str]] = []
    for source, prop in proposals:
        for pair in live_candidates(prop, vctx, rules, source):
            if pair not in todo and promotion_key(*pair) not in vctx.live_evidence:
                todo.append(pair)
    t0 = time.time()
    started = retro_started if retro_started is not None else t0
    end = now - now % 86400
    runs = 0
    for vid, sport in todo:
        v = vctx.variants[vid]
        rule = vctx.sample_rules.get(promotion_key(vid, sport)) or promotion.sample_rule(sport)
        start = end - rule.lookback_days * 86400
        try:
            trades, last_change = promotion_sample(v, sport, env.paths)
            trades = trades + live_epoch_trades(v, sport, env.paths)   # observed record at current params
        except Exception as exc:
            notes.append(f"live evidence {vid}/{sport}: paper sample failed: {exc}")
            continue
        gate = promotion.evaluate_direct(v, sport, trades, now, last_change, None, rule)
        if gate.stage in ("eligibility", "paper"):
            vctx.live_evidence[promotion_key(vid, sport)] = {**gate.as_dict()}
            notes.append(f"live evidence {vid}/{sport}: {gate.stage}: {gate.reason}")
            continue
        left = min(rules.live_evidence_budget_s - (time.time() - t0),
                   rules.backtest_soft_deadline_s - (time.time() - started))
        if runs >= rules.live_evidence_max_runs or left < 60:
            notes.append(f"live evidence skipped for {vid}/{sport} (max {rules.live_evidence_max_runs} / time budget)")
            continue
        runs += 1
        sim = copy.deepcopy(v)
        sim.sports = [sport]
        sim.sport_settings = {k: c for k, c in sim.sport_settings.items() if k == sport}
        env.say(f"live evidence replay {vid}/{sport} ({rule.lookback_days}d, ≤{left:.0f}s)")
        try:
            result = env.backtest([(sim, {})], start, end, left)[0]
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        gate = promotion.evaluate_direct(v, sport, trades, now, last_change, _replay_summary(result, start, end), rule)
        vctx.live_evidence[promotion_key(vid, sport)] = {**gate.as_dict()}
        notes.append(f"live evidence {vid}/{sport}: {'pass' if gate.ok else 'fail'}: {gate.reason}")
    return notes


def promotion_gates(variants, vctx: Context, rules: Rules, env: Env, now: int,
                    retro_started: float | None = None) -> tuple[list, dict]:
    """Evaluate the deterministic promotion gate for every paper sport of every per-sport variant.
    Returns (gates, proposal). Passing gates are stored in vctx.promotions (the validator reads only
    those) and become "mode → live" changes with source "promotion"."""
    from polylab.risk import promotion  # noqa: PLC0415
    gates, runs = [], 0
    t0 = time.time()
    started = retro_started if retro_started is not None else t0
    end = now - now % 86400
    for v in variants:
        if not v.per_sport or v.mode == "off":
            continue
        for sport in v.sports:
            if (v.sport_settings.get(sport) or {}).get("mode", v.mode) != "paper":
                continue
            rule = vctx.sample_rules.get(promotion_key(v.id, sport)) or promotion.sample_rule(sport)
            start = end - rule.lookback_days * 86400
            try:
                trades, last_change = promotion_sample(v, sport, env.paths)
            except Exception as exc:   # an unreadable ledger never promotes
                env.say(f"promotion sample {v.id}/{sport} failed: {type(exc).__name__}: {exc}")
                continue
            gate = promotion.evaluate(v, sport, trades, now, last_change, rule=rule)
            if gate.needs_backtest and rules.promotion_replay:
                left = min(rules.promotion_budget_s - (time.time() - t0),
                           rules.backtest_soft_deadline_s - (time.time() - started))
                if runs >= rules.promotion_max_runs or left < 60:
                    gate.reason += f" (replay skipped: max {rules.promotion_max_runs} per retro / time budget)"
                else:
                    runs += 1
                    sim = copy.deepcopy(v)
                    sim.sports = [sport]
                    sim.sport_settings = {k: c for k, c in sim.sport_settings.items() if k == sport}
                    env.say(f"promotion replay {v.id}/{sport} ({rule.lookback_days}d, ≤{left:.0f}s)")
                    try:
                        result = env.backtest([(sim, {})], start, end, left)[0]
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                    gate = promotion.evaluate(v, sport, trades, now, last_change,
                                              backtest=_replay_summary(result, start, end), rule=rule)
            gates.append(gate)
    changes = []
    for g in gates:
        if not g.ok:
            continue
        vctx.promotions[promotion_key(g.variant_id, g.sport)] = {"ok": True, **g.as_dict()}
        changes.append({"variant_id": g.variant_id, "change": "mode", "sport": g.sport, "values": {"mode": "live"},
                        "rationale": f"자동 실거래 전환(결정론 승격 게이트): {g.reason}",
                        "evidence": g.evidence})
    return gates, {"schema": SCHEMA, "summary": "deterministic promotion gate", "changes": changes}


# ------------------------------------------------------------------ backtest-backed retune evidence

def _arm_stats(trades: list[dict]) -> dict:
    from polylab.analysis.backtest import max_drawdown  # noqa: PLC0415
    settled = sorted((t for t in trades if t.get("pnl") is not None and t.get("status") in ("closed", "resolved")),
                     key=lambda t: (t.get("closed_at") or 0, t.get("opened_at") or 0, t.get("token_id") or ""))
    cost = sum(t.get("cost") or 0.0 for t in settled)
    pnl = sum(t["pnl"] for t in settled)
    return {"n": len(settled), "pnl": round(pnl, 6), "roi": round(pnl / cost, 6) if cost else None,
            "max_dd": round(max_drawdown([t["pnl"] for t in settled]), 6)}


def split_evidence(current: dict, proposed: dict, start: int, end: int) -> dict:
    """Both arms split at one entry time: the median entry of the current arm (else of the proposed arm,
    else the window midpoint), so each half holds about half of the baseline's trades."""
    if current.get("error") or proposed.get("error"):
        return {"error": current.get("error") or proposed.get("error"), "range": [start, end]}
    cur_t, new_t = current.get("trades") or [], proposed.get("trades") or []
    opens = sorted(t["opened_at"] for t in (cur_t if len(cur_t) >= 2 else new_t) if t.get("opened_at") is not None)
    split = opens[len(opens) // 2] if len(opens) >= 2 else (start + end) // 2
    ev = {"range": [start, end], "split_ts": split}
    for name, trades in (("current", cur_t), ("proposed", new_t)):
        arm = _arm_stats(trades)
        arm["halves"] = [_arm_stats([t for t in trades if (t.get("opened_at") or 0) < split]),
                         _arm_stats([t for t in trades if (t.get("opened_at") or 0) >= split])]
        ev[name] = arm
    return ev


def backtest_evidence(proposals: list[dict], vctx: Context, rules: Rules, env: Env, now: int,
                      retro_started: float | None = None) -> list[str]:
    """Run current-vs-proposed replays for params changes that only lack backtest evidence and store the
    result in vctx.backtests (the validator reads only this, never numbers written in the proposal).
    Time is bounded by the evidence budget and by a soft deadline on the whole retro's wall time, so a
    slow replay cannot push apply/pytest/commit past the Jenkins timeout; a skipped run means rejection."""
    notes: list[str] = []
    if not rules.backtest_retune:
        return notes
    todo, seen = [], set()
    for prop in proposals:
        for vid, params in backtest_candidates(prop, vctx, rules):
            key = evidence_key(vid, params)
            if key not in seen and key not in vctx.backtests:
                seen.add(key)
                todo.append((vid, params, key))
    end = now - now % 86400  # completed UTC days only
    t0 = time.time()
    started = retro_started if retro_started is not None else t0
    for i, (vid, params, key) in enumerate(todo):
        rule = vctx.sample_rules.get(promotion_key(vid, integrations_sport(params)))
        lookback = rule.lookback_days if rule is not None else rules.backtest_lookback_days
        start = end - lookback * 86400
        left = min(rules.backtest_budget_s - (time.time() - t0),
                   rules.backtest_soft_deadline_s - (time.time() - started))
        if i >= rules.backtest_max_runs or left < 60:
            notes.append(f"backtest skipped for {vid} (max {rules.backtest_max_runs} runs / time budget)")
            continue
        variant = vctx.variants[vid]
        sport = integrations_sport(params)
        if sport and sport in variant.sports:   # a sport-targeted retune replays only that sport
            variant = copy.deepcopy(variant)
            variant.sports = [sport]
            variant.sport_settings = {k: v for k, v in variant.sport_settings.items() if k == sport}
        env.say(f"backtest retune evidence {vid} {params} ({lookback}d, ≤{left:.0f}s)")
        try:
            current, proposed = env.backtest([(variant, {}), (variant, params)], start, end, left)
            ev = split_evidence(current, proposed, start, end)
        except Exception as exc:
            ev = {"error": f"{type(exc).__name__}: {exc}"[:300], "range": [start, end]}
        ev["params"] = params
        vctx.backtests[key] = ev
        notes.append(f"backtest {vid}: " + ("error " + str(ev["error"]) if ev.get("error") else
                                            f"current n={ev['current']['n']} roi={ev['current']['roi']} / "
                                            f"proposed n={ev['proposed']['n']} roi={ev['proposed']['roi']}"))
    return notes


def _evidence_lines(ev: dict) -> list[str]:
    def pct(x):
        return "–" if x is None else f"{x * 100:+.2f}%"
    rng = f"{C.iso(ev['range'][0])[:10]}~{C.iso(ev['range'][1])[:10]}, split {C.iso(ev['split_ts'])[:10]}"
    out = [f"  - 백테스트 근거(retro 가 직접 재생, paper replay·실손익 아님): {rng}"]
    for arm in ("current", "proposed"):
        a = ev[arm]
        h1, h2 = a["halves"]
        out.append(f"  - {arm}: n {a['n']} ROI {pct(a['roi'])} MDD {a['max_dd']:.2f} · "
                   f"H1 n {h1['n']} ROI {pct(h1['roi'])} · H2 n {h2['n']} ROI {pct(h2['roi'])}")
    return out


# ------------------------------------------------------------------ AI

def _read_proposal(d: Path) -> dict | None:
    try:
        data = json.loads((d / "proposal.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        return None
    return data


def _read_narrative(d: Path) -> str | None:
    try:
        text = (d / "narrative.md").read_text().strip()
    except OSError:
        return None
    return slack.scrub(text[:NARRATIVE_MAX_CHARS]) if text else None


def run_ai(kind: str, cwd: Path, engines: list[Engine], env: Env, timeout: int) -> dict:
    """Try engines in order until one writes a valid proposal.json + narrative.md."""
    prompt = ctxpack.prompt_text(kind, env.prompts_dir)
    tried = []
    for engine in engines:
        ok, why = engine.available()
        if not ok:
            tried.append({"engine": engine.name, "ok": False, "reason": why})
            continue
        for stale in ("proposal.json", "narrative.md", "attention.json"):
            (cwd / stale).unlink(missing_ok=True)
        env.say(f"AI engine {engine.name} running")
        res = engine.run(prompt, cwd, timeout)
        proposal, narrative = _read_proposal(cwd), _read_narrative(cwd)
        if res.ok and proposal is not None and narrative:
            tried.append({"engine": engine.name, "ok": True, "reason": ""})
            return {"ran": True, "engine": engine.name, "proposal": proposal, "narrative": narrative, "tried": tried,
                    "reason": None}
        reason = res.reason or ("invalid proposal.json" if proposal is None else "narrative.md missing")
        if res.rate_limited:
            reason = f"rate limited: {reason}"
        tried.append({"engine": engine.name, "ok": False, "reason": reason[:300]})
        env.say(f"AI engine {engine.name} failed: {reason[:200]}")
    reasons = [t["reason"] for t in tried]
    attempted = any(t["reason"] not in ("토큰 없음", "claude CLI 없음", "codex CLI 없음", "codex 로그인 없음")
                    for t in tried)
    summary = "토큰 없음" if reasons and reasons[0] == "토큰 없음" and not attempted else \
        ("; ".join(f"{t['engine']}: {t['reason']}" for t in tried)[:300] or "엔진 없음")
    return {"ran": False, "engine": None, "proposal": None, "narrative": None, "tried": tried,
            "reason": summary, "failed": attempted}


def run_review(cwd: Path, primary: str, engines: list[Engine], env: Env, timeout: int) -> dict | None:
    """Weekly second opinion by a different engine. It can only veto (reject) or flag changes."""
    reviewer = next((e for e in engines if e.name != primary and e.available()[0]), None)
    if reviewer is None:
        return None
    (cwd / "review.json").unlink(missing_ok=True)
    env.say(f"review by {reviewer.name}")
    res = reviewer.run(ctxpack.prompt_text("review", env.prompts_dir), cwd, timeout)
    try:
        data = json.loads((cwd / "review.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"engine": reviewer.name, "ok": False, "reason": res.reason or "review.json missing"}
    reviews = data.get("reviews") if isinstance(data, dict) else None
    if not isinstance(reviews, list):
        return {"engine": reviewer.name, "ok": False, "reason": "invalid review.json"}
    return {"engine": reviewer.name, "ok": True, "reviews": reviews,
            "summary": slack.scrub(str(data.get("summary", ""))[:2000])}


def apply_review(proposal: dict, review: dict | None) -> tuple[dict, list[dict], list[str]]:
    """Drop vetoed changes. Returns (filtered proposal, vetoed decisions, notes)."""
    if not review or not review.get("ok"):
        return proposal, [], []
    verdicts = {}
    for r in review["reviews"]:
        if isinstance(r, dict) and isinstance(r.get("index"), int):
            verdicts[r["index"]] = r
    kept, vetoed, notes = [], [], []
    for i, ch in enumerate(proposal.get("changes", [])):
        r = verdicts.get(i, {})
        verdict = str(r.get("verdict", "agree")).lower()
        comment = slack.scrub(str(r.get("comment", "")))[:300]
        vid = ch.get("variant_id") if isinstance(ch, dict) else None
        if verdict == "reject":
            vetoed.append({"variant_id": vid, "change": ch.get("change") if isinstance(ch, dict) else None,
                           "reason": f"second opinion ({review['engine']}) 거부: {comment}", "source": "review"})
        else:
            kept.append(ch)
            if verdict == "flag":
                notes.append(f"`{vid}` {ch.get('change')}: {review['engine']} 이견 — {comment}")
    return {**proposal, "changes": kept}, vetoed, notes


# ------------------------------------------------------------------ inbox

def load_inbox(inbox_dir: Path) -> list[tuple[Path, dict | None]]:
    if not inbox_dir.exists():
        return []
    out = []
    for p in sorted(inbox_dir.glob("*.json")):
        try:
            out.append((p, json.loads(p.read_text())))
        except (OSError, json.JSONDecodeError):
            out.append((p, None))
    return out


def archive_inbox(items: list[tuple[Path, dict | None]], decisions: dict[str, list[Decision]], processed: Path,
                  stamp: str) -> None:
    processed.mkdir(parents=True, exist_ok=True)
    for path, _ in items:
        dest = processed / f"{stamp}-{path.name}"
        shutil.move(str(path), dest)
        result = [d.as_dict() for d in decisions.get(path.name, [])] or \
            [{"accepted": False, "reason": "unreadable json"}]
        dest.with_suffix(".result.json").write_text(json.dumps({"processed_at": C.iso(int(time.time())),
                                                                "decisions": result}, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ apply

def _check_staged(directory: Path) -> None:
    """Whole-registry checks on a staged copy: cross-file rules and each family can build its strategy."""
    variants = registry.load_all(directory, include_off=True)
    try:
        from polylab import strategies  # noqa: PLC0415
    except Exception:
        return
    for v in variants:
        strategies.build(v)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def apply_decisions(accepted: list[Decision], vctx: Context, env: Env) -> tuple[list[dict], dict[Path, str | None]]:
    """Stage all accepted changes in a temp copy of the registry, validate the whole registry there,
    then atomically replace the live files (the 1-minute tick never sees a half-written or invalid
    yaml). Returns (applied summaries, originals for revert)."""
    applied = []
    with tempfile.TemporaryDirectory(prefix="polylab-retro-") as tmp:
        stage = Path(tmp)
        for p in env.registry_dir.glob("*.yaml"):
            shutil.copy(p, stage / p.name)
        staged_vars = dict(vctx.variants)
        for d in accepted:
            new = apply_to_variant(d, Context(staged_vars, vctx.facts, vctx.now, vctx.families, vctx.known_aliases))
            registry.save_variant(new, stage)
            new.path = env.registry_dir / f"{new.id}.yaml"
            staged_vars[new.id] = new
            applied.append({"variant_id": new.id, "change": d.change["change"], "summary": d.summary,
                            **({"sport": d.change["sport"]} if d.change.get("sport") else {}),
                            "source": d.source, "rationale": d.change.get("rationale"), "new": new,
                            **({"evidence": d.gate} if d.gate is not None else
                               {"evidence": d.change.get("evidence")} if d.source == PROMOTION_SOURCE else {}),
                            **({"backtest": d.backtest} if d.backtest else {})})
        _check_staged(stage)
        originals: dict[Path, str | None] = {}
        for vid in {a["variant_id"] for a in applied}:
            live = env.registry_dir / f"{vid}.yaml"
            originals[live] = live.read_text() if live.exists() else None
            _atomic_write(live, (stage / f"{vid}.yaml").read_text())
    vctx.variants.update(staged_vars)
    return applied, originals


def revert(originals: dict[Path, str | None]) -> None:
    for path, text in originals.items():
        if text is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, text)


def append_changelog(reports_dir: Path, report: dict, applied: list[dict], engine: str | None) -> None:
    if not applied:
        return
    path = reports_dir / "changes.md"
    head = "# polylab 자동 변경 이력\n\n최신이 위. autopilot(validator 통과분)과 결정론 ladder가 기록한다.\n\n"
    old = path.read_text() if path.exists() else head
    body = old[len(head):] if old.startswith(head) else old
    entry = [f"## {report_build.kst(report['now'], '%Y-%m-%d %H:%M')} KST · {KIND_KO[report['kind']]} "
             f"{report['name']} · engine {engine or 'none'}", ""]
    for a in applied:
        entry.append(f"- `{a['variant_id']}` {a['change']}: {a['summary']} ({a['source']})"
                     + (f" — {slack.scrub(a['rationale'])[:300]}" if a.get("rationale") else ""))
        if a.get("backtest"):
            entry += _evidence_lines(a["backtest"])
    path.write_text(head + "\n".join(entry) + "\n\n" + body)


def update_index(reports_dir: Path, report: dict, rel_path: str, ai: bool, engine: str | None) -> None:
    path = reports_dir / "index.json"
    try:
        index = json.loads(path.read_text())
        if not isinstance(index, list):
            index = []
    except (OSError, json.JSONDecodeError):
        index = []
    index = [e for e in index if e.get("path") != rel_path]
    index.insert(0, {"kind": report["kind"], "date": report["date"], "slot": report.get("slot"),
                     "title": report["title"], "path": rel_path, "ai": ai, "engine": engine,
                     "generated_at": report["generated_at"]})
    index.sort(key=lambda e: (e.get("generated_at") or e.get("date") or ""), reverse=True)
    path.write_text(json.dumps(index[:2000], ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ main flow

def refresh_analysis(paths, kind: str, slot: str | None, env: Env) -> None:
    if kind == "daily" and slot != "dawn":
        return
    if not Path(paths.core_db).exists():
        return
    from polylab.analysis import cli as analysis_cli  # noqa: PLC0415
    for what in ("calibration", "events"):
        try:
            analysis_cli.run(what, paths, None, None)
        except Exception as exc:  # research refresh must not block the retro
            env.say(f"analysis {what} failed: {type(exc).__name__}: {exc}")


def run_retro(opts: Options, env: Env) -> dict:
    paths = env.paths
    wall_start = time.time()
    now = opts.now or int(wall_start)
    kind = opts.kind
    slot = (opts.slot or report_build.infer_slot(now)) if kind == "daily" else None
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result: dict = {"at": C.iso(now), "kind": kind, "ok": False, "ai": False, "engine": None, "error": None,
                    "applied": [], "rejected": [], "deterministic_ok": False}
    if opts.push:
        ok, msg = env.pull(env.repo)
        if not ok:
            env.say(f"git pull failed (continuing): {msg}")
    if opts.analysis:
        refresh_analysis(paths, kind, slot, env)

    variants = registry.load_all(env.registry_dir, include_off=True)
    report = report_build.build(kind, paths, now=now, slot=slot, use_jenkins=env.use_jenkins, variants=variants,
                                since=catchup_since(paths, kind, now))
    result["name"] = report["name"]
    rules = Rules.for_kind(kind)
    vctx = Context(variants={v.id: v for v in variants}, now=now, families={v.family for v in variants},
                   known_aliases=set(settings.account_aliases()),
                   facts={v.id: variant_facts(v, paths, _rv(report, v.id)["ladder"], env.repo,
                                              {d["sport"]: d["ladder"] for d in _rv(report, v.id).get("sports_detail") or []
                                               if d.get("ladder")}) for v in variants})
    vctx.sample_rules = sample_rules(variants, paths, now)
    idle = set(attention.idle_variants(report, paths, now)) if rules.backtest_retune else set()
    idle_sp = attention.idle_sports(report, paths, now) if rules.backtest_retune else {}
    for vid in idle & set(vctx.facts):
        vctx.facts[vid].idle = True
    for vid, sports in idle_sp.items():     # sport-targeted params changes read the sport's own facts
        if vid in vctx.facts:
            vctx.facts[vid].idle = True
            for sport in sports:
                vctx.facts[vid].by_sport.setdefault(sport, Facts()).idle = True
    eligible = sorted(v.id for v in variants if v.mode != "off" and rules.backtest_retune and not v.per_sport
                      and (not rules.backtest_idle_only or v.id in idle))
    eligible += sorted(f"{v.id}:{s}" for v in variants if v.mode != "off" and rules.backtest_retune and v.per_sport
                       for s in v.sports if v.sport_mode(s) != "off"
                       and (not rules.backtest_idle_only or s in idle_sp.get(v.id, {})))
    md0 = render.render(report)
    cwd = ctxpack.build_private(report, md0, kind, paths, variants, rules, stamp, run_backtests=opts.backtest,
                                retune_eligible=eligible)
    promo_gates, promo_proposal = promotion_gates(variants, vctx, rules, env, now, retro_started=wall_start)
    report["promotion"] = [g.as_dict() for g in promo_gates]
    try:
        (cwd / "promotion.json").write_text(json.dumps(report["promotion"], ensure_ascii=False, indent=1, default=str))
        with (cwd / "MANIFEST.md").open("a") as fh:
            fh.write("- `promotion.json`: 결정론 paper→live 승격 게이트의 (변형, 종목)별 stage·사유·paper 지표·종목 규칙"
                     "(rule: 최소 건수·재생 기간). AI 도 근거가 있으면 mode live 를 제안할 수 있다(retro 가 직접 재생해 판정)\n")
    except OSError as exc:
        env.say(f"promotion context skipped: {exc}")
    att_prev = attention.load(env.reports_dir)
    try:
        attention.write_context(cwd, att_prev, env.reports_dir)
    except OSError as exc:
        env.say(f"attention context skipped: {exc}")

    # AI chain
    ai = {"ran": False, "engine": None, "reason": "비활성(--no-ai)", "tried": [], "proposal": None, "narrative": None}
    if opts.ai:
        ai = run_ai(kind, cwd, env.engines(), env, opts.ai_timeout)
    review_notes, vetoed, review = [], [], None
    proposal = ai.get("proposal")
    if ai["ran"] and kind == "weekly" and proposal and proposal.get("changes"):
        review = run_review(cwd, ai["engine"], env.engines(), env, opts.ai_timeout)
        proposal, vetoed, review_notes = apply_review(proposal, review)
    report["ai"] = {"ran": ai["ran"], "engine": ai["engine"], "reason": ai.get("reason"), "tried": ai.get("tried"),
                    "review": {k: review.get(k) for k in ("engine", "ok", "summary", "reason")} if review else None}
    result["ai"], result["engine"] = ai["ran"], ai["engine"]
    if ai.get("failed"):
        result["error"] = f"AI 실패: {ai.get('reason')}"

    # validate: ladder first (not counted), then AI, then inbox
    inbox = load_inbox(env.inbox_dir)
    if opts.backtest:  # evidence for backtest-backed retunes is computed here, before any validation
        for note in backtest_evidence([p for p in [proposal, *(d for _, d in inbox)] if p], vctx, rules, env, now,
                                      retro_started=wall_start):
            env.say(note)
        live_props = ([(f"ai:{ai['engine']}", proposal)] if proposal else []) + \
            [(f"inbox:{path.name}", d) for path, d in inbox if d]
        for note in live_evidence(live_props, vctx, rules, env, now, retro_started=wall_start):
            env.say(note)
    state = {"count": 0, "touched": set(), "new_variants": 0}
    decisions = validate(ladder_changes(report), vctx, rules, "ladder", state)
    decisions += validate(promo_proposal, vctx, rules, PROMOTION_SOURCE, state)
    if proposal:
        decisions += validate(proposal, vctx, rules, f"ai:{ai['engine']}", state)
    inbox_decisions: dict[str, list[Decision]] = {}
    for path, data in inbox:
        ds = validate(data, vctx, rules, f"inbox:{path.name}", state) if data is not None else \
            [Decision({}, f"inbox:{path.name}", False, "unreadable json")]
        inbox_decisions[path.name] = ds
        decisions += ds
    accepted = [d for d in decisions if d.accepted]
    rejected = [{**d.as_dict(), "reason": d.reason} for d in decisions if not d.accepted and d.change] + vetoed

    applied: list[dict] = []
    before = dict(vctx.variants)
    if opts.apply and accepted:
        try:
            applied, originals = apply_decisions(accepted, vctx, env)
        except Exception as exc:
            applied, originals = [], {}
            rejected += [{**d.as_dict(), "reason": f"apply failed: {exc}"} for d in accepted]
            env.say(f"apply failed: {exc}")
        if applied:
            ok, out = env.run_tests(env.repo)
            if not ok:
                revert(originals)
                rejected += [{**a, "reason": "tests failed after apply; reverted"} for a in applied]
                applied = []
                env.say(f"tests failed, yaml reverted: {out[-200:]}")
            else:
                record_stake_events(applied, before, paths, now)
        for a in applied + rejected:
            a.pop("new", None)
    elif accepted:
        rejected += [{**d.as_dict(), "reason": "not applied (--no-apply)"} for d in accepted]
    result["applied"], result["rejected"] = applied, rejected

    # attention inbox + brief: deterministic rules, optional AI items; never blocks the retro
    att_new = None
    try:
        att_new, att_run = attention.update(att_prev, report, kind=kind, now=now, paths=paths, applied=applied,
                                            rejected=rejected, ai=ai if opts.ai else None, ai_enabled=opts.ai,
                                            ai_raw=attention.read_ai(cwd) if ai["ran"] else None,
                                            decisions=attention.load_decisions(env.reports_dir))
        report["brief"] = brief_mod.build(report, prev=att_prev.get("last_retro"), applied=applied, rejected=rejected,
                                          opens=attention.open_items(att_new), ai_items=att_run["ai_items"])
        report["thesis_sentences"] = att_run["thesis"]
        for note in att_run["notes"]:
            env.say(f"attention: {note}")
    except Exception as exc:
        env.say(f"attention/brief skipped: {type(exc).__name__}: {exc}")
        att_new = None

    # outputs
    narrative = ai.get("narrative")
    if review_notes or (review and review.get("summary")):
        narrative = (narrative or "") + "\n\n### Second opinion\n\n" + \
            "\n".join(f"- {n}" for n in review_notes) + ("\n\n" + review["summary"] if review and review.get("summary") else "")
    engine_line = f"엔진: {ai['engine'] or '없음'}" + (f" (시도: {', '.join(t['engine'] + ('✓' if t['ok'] else '×') for t in ai.get('tried', []))})"
                                                      if ai.get("tried") else "")
    if narrative:
        narrative = f"{engine_line}\n\n{narrative}"
    md = render.render(report, narrative=narrative, applied=applied, rejected=rejected)
    kind_dir = env.reports_dir / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    md_path = kind_dir / f"{report['name']}.md"
    md_path.write_text(slack.scrub(md))
    rel = f"reports/{kind}/{report['name']}.md"
    update_index(env.reports_dir, report, rel, ai["ran"], ai["engine"])
    if att_new is not None:
        try:
            attention.save(env.reports_dir, att_new)
        except OSError as exc:
            env.say(f"attention save failed: {exc}")
    append_changelog(env.reports_dir, report, applied, ai["engine"])
    if kind == "monthly":
        env.research_docs_dir.mkdir(parents=True, exist_ok=True)
        (env.research_docs_dir / f"{report['name']}.md").write_text(slack.scrub(monthly.render_monthly(report, ai.get("narrative"))))
    variants_now = registry.load_all(env.registry_dir, include_off=True)
    ctxpack.write_public(report, md, variants_now, env.reports_dir,
                         {"engine": ai["engine"], "ai": ai["ran"], "applied": len(applied)})
    if inbox:
        archive_inbox(inbox, inbox_decisions, env.processed_dir, stamp)
    (cwd / "result.json").write_text(json.dumps({"applied": applied, "rejected": rejected, "ai": report["ai"]},
                                                ensure_ascii=False, indent=1, default=str))
    result["deterministic_ok"] = True

    if opts.push:
        msg = f"autopilot {KIND_KO[kind]} 회고 {report['name']}: 변경 {len(applied)}건 (engine {ai['engine'] or 'none'})"
        ok, out = env.commit(msg, push=True, repo=env.repo)
        env.say(f"git: {out}")
        if not ok:
            result["error"] = (result["error"] + "; " if result["error"] else "") + out
    if opts.publish and env.publish is not None:
        try:
            env.publish(paths)
        except Exception as exc:
            env.say(f"publish failed: {type(exc).__name__}: {exc}")
    if opts.slack:
        url = f"{slack.DASHBOARD_URL}/reports/{kind}/{report['name']}"
        report["changes"] = report["changes"] + [{"summary": f"{a['variant_id']} {a['summary']} ({a['source']})"}
                                                 for a in applied]
        (env.post_slack or slack.post_report)(report, url)
    result["ok"] = result["error"] is None
    return result


def _default_publish(paths) -> None:
    from polylab.publish import snapshot  # noqa: PLC0415
    storage = snapshot.storage_from_env()
    if storage is not None:
        snapshot.publish(paths, storage)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab retro")
    ap.add_argument("kind", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--slot", choices=sorted(report_build.SLOTS))
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--no-push", action="store_true", help="no git pull/commit/push")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--no-slack", action="store_true")
    ap.add_argument("--no-analysis", action="store_true")
    ap.add_argument("--no-backtest", action="store_true")
    ap.add_argument("--ai-timeout", type=int, default=1200)
    args = ap.parse_args(argv)
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        print(f"retro: {exc}", file=sys.stderr)
        return 3
    opts = Options(kind=args.kind, slot=args.slot, ai=not args.no_ai, apply=not args.no_apply,
                   push=not args.no_push, publish=not args.no_publish, slack=not args.no_slack,
                   analysis=not args.no_analysis, backtest=not args.no_backtest, ai_timeout=args.ai_timeout)
    env = Env(paths=paths, publish=_default_publish)
    now = int(time.time())
    try:
        result = run_retro(opts, env)
    except Exception as exc:
        result = {"at": C.iso(now), "kind": args.kind, "ok": False, "ai": False, "engine": None,
                  "error": f"{type(exc).__name__}: {exc}"[:500], "applied": []}
        traceback.print_exc()
    record_state(paths, args.kind, now, result)
    print(json.dumps({k: result.get(k) for k in ("name", "ok", "ai", "engine", "error")}, ensure_ascii=False))
    print(f"retro: applied {len(result.get('applied', []))}, rejected {len(result.get('rejected', []))}")
    return 0 if result.get("deterministic_ok") else 1

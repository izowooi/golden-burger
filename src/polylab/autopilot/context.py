"""Retro context packs.

Private pack (<state>/retro/<stamp>-<kind>/): everything the AI engine may read, plus the
prompt; the engine writes narrative.md + proposal.json (+ optional attention.json) back into it.
Public pack (reports/context/latest/): a small, secret-free subset committed to the repo so
any reviewer that only sees GitHub can reason about the current state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from polylab import settings
from polylab.analysis import integrations
from polylab.autopilot.validator import SCHEMA, Rules, get_path
from polylab.registry import MAX_STAKE_USDC, STAKE_LADDER
from polylab.reports.slack import scrub

PROMPTS_DIR = settings.REPO_ROOT / "prompts"
DOCS_STRATEGIES = settings.REPO_ROOT / "docs" / "strategies"


def _dump(obj) -> str:
    return scrub(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def variant_metrics(v: dict) -> dict:
    return {k: v[k] for k in ("id", "family", "hypothesis", "mode", "account", "sports", "stake_usdc", "params",
                              "bounds", "limits", "ladder", "live", "paper", "primary_mode", "breakdown", "excluded",
                              "open", "param_history", "stake_events", "last_change")} | {"recent": v["recent"][:50]}


def calibration_summary(research: dict) -> dict:
    return {"generated_at": research.get("calibration_generated_at"), "top_gaps": research.get("calibration_top"),
            "brier": research.get("brier"),
            "by_sport_phase": [{"sport": c["sport"], "phase": c["phase"],
                                "buckets": [b for b in c["buckets"] if b["n"] >= 20]}
                               for c in research.get("calibration", [])],
            "notes": research.get("notes")}


def events_summary(research: dict) -> dict:
    return {"generated_at": research.get("events_generated_at"), "measured": research.get("events_measured"),
            "event_sensitivity": research.get("event_sensitivity"), "notes": research.get("notes")}


def candidate_grid(variant: dict, max_candidates: int = 8) -> list[dict]:
    """One-at-a-time ±max_step moves inside bounds (what the validator would accept), as
    {dotted_param: new_value} overrides."""
    out = []
    for name, bound in sorted((variant.get("bounds") or {}).items()):
        cur = get_path(variant["params"], name)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool) or len(bound) < 2:
            continue
        step = bound[2] if len(bound) >= 3 else (bound[1] - bound[0]) * 0.1
        for new in (cur - step, cur + step):
            new = round(min(max(new, bound[0]), bound[1]), 6)
            if isinstance(cur, int):
                new = int(round(new))
            if new != cur:
                out.append({name: new})
    return out[:max_candidates]


BACKTEST_BUDGET_S = 1200


def backtests(report: dict, variants_by_id: dict, paths, kind: str, budget_s: int = BACKTEST_BUDGET_S) -> dict:
    """Weekly/monthly: 30-day replays of the current params and ±max_step candidates (time-boxed)."""
    if kind == "daily":
        return {"available": integrations.backtest_available(), "results": [], "note": "daily retro skips backtests"}
    if not integrations.backtest_available():
        return {"available": False, "results": []}
    import time  # noqa: PLC0415
    t0 = time.time()
    until = report["now"]
    since = until - 30 * 86400
    results, truncated = [], False
    for v in report["variants"]:
        if v["mode"] == "off" or v["id"] not in variants_by_id:
            continue
        if time.time() - t0 > budget_s:
            truncated = True
            break
        variant = variants_by_id[v["id"]]
        entry = {"variant_id": v["id"], "since_days": 30, "current": integrations.backtest(variant, {}, paths, since, until),
                 "candidates": []}
        for p in candidate_grid(v, 4):
            if time.time() - t0 > budget_s:
                truncated = True
                break
            entry["candidates"].append({"params_changed": p,
                                        "result": integrations.backtest(variant, p, paths, since, until)})
        results.append(entry)
    return {"available": True, "results": results, "truncated": truncated,
            "note": "paper replay on stored bars/books; not realised P&L"}


def build_private(report: dict, markdown: str, kind: str, paths, variants, rules: Rules, stamp: str,
                  run_backtests: bool = True) -> Path:
    d = Path(paths.state) / "retro" / f"{stamp}-{kind}"
    if d.exists():
        shutil.rmtree(d)
    (d / "metrics").mkdir(parents=True)
    (d / "strategies").mkdir()
    (d / "docs").mkdir()
    (d / "report.md").write_text(scrub(markdown))
    slim = {k: report[k] for k in ("kind", "slot", "name", "title", "generated_at", "window", "git_commit", "totals",
                                   "tx_by_variant", "alerts", "changes", "stake_tiers")}
    slim["health"] = {k: report["health"].get(k) for k in ("collector", "jobs", "disk_free_gb", "kill_switch")}
    (d / "report.json").write_text(_dump(slim))
    for v in report["variants"]:
        (d / "metrics" / f"{v['id']}.json").write_text(_dump(variant_metrics(v)))
    (d / "trades_recent.json").write_text(_dump(report["transactions"]))
    (d / "calibration_summary.json").write_text(_dump(calibration_summary(report["research"])))
    (d / "events_summary.json").write_text(_dump(events_summary(report["research"])))
    if report.get("llm_forecast"):
        (d / "llm_forecast_eval.json").write_text(_dump(report["llm_forecast"]))  # paper-only side study
    by_id = {v.id: v for v in variants}
    families = set()
    for v in variants:
        if v.path and Path(v.path).exists():
            shutil.copy(v.path, d / "strategies" / Path(v.path).name)
        families.add(v.family)
    for fam in sorted(families):
        src = DOCS_STRATEGIES / f"{fam}.md"
        if src.exists():
            shutil.copy(src, d / "docs" / src.name)
    bounds = {"stake_ladder": list(STAKE_LADDER), "max_stake_usdc": MAX_STAKE_USDC, "rules": rules.as_dict(),
              "variants": {v.id: {"mode": v.mode, "stake_usdc": v.stake_usdc, "params": v.params, "bounds": v.bounds}
                           for v in variants},
              "proposal_schema": SCHEMA}
    (d / "bounds.json").write_text(_dump(bounds))
    bt = backtests(report, by_id, paths, kind) if run_backtests else {"available": False, "results": []}
    (d / "backtests.json").write_text(_dump(bt))
    manifest = ["# Context pack", "", f"kind: {kind}, generated_at: {report['generated_at']}", "",
                "Files (read-only evidence):"] + [f"- {p.relative_to(d)}" for p in sorted(d.rglob("*")) if p.is_file()]
    manifest += ["", "Write narrative.md and proposal.json here, plus the optional attention.json (see the prompt)."]
    (d / "MANIFEST.md").write_text("\n".join(manifest) + "\n")
    return d


def write_public(report: dict, markdown: str, variants, reports_dir: Path, meta_extra: dict) -> Path:
    d = reports_dir / "context" / "latest"
    if d.exists():
        shutil.rmtree(d)
    (d / "strategies").mkdir(parents=True)
    (d / "report.md").write_text(scrub(markdown))
    metrics = {v["id"]: {k: v[k] for k in ("mode", "account", "stake_usdc", "params", "bounds", "ladder", "live",
                                            "paper", "breakdown", "excluded", "last_change")}
               for v in report["variants"]}
    (d / "metrics.json").write_text(_dump({"totals": report["totals"], "variants": metrics,
                                           "stake_tiers": report.get("stake_tiers"),
                                           "research": calibration_summary(report["research"]),
                                           "events": events_summary(report["research"])}))
    for v in variants:
        if v.path and Path(v.path).exists():
            shutil.copy(v.path, d / "strategies" / Path(v.path).name)
    meta = {"generated_at": report["generated_at"], "git_commit": report.get("git_commit"), "kind": report["kind"],
            "slot": report.get("slot"), "name": report["name"], "proposal_schema": SCHEMA, **meta_extra}
    (d / "meta.json").write_text(_dump(meta))
    return d


def prompt_text(kind: str, prompts_dir: Path = PROMPTS_DIR, name: str | None = None) -> str:
    return (prompts_dir / f"{name or kind}.md").read_text()

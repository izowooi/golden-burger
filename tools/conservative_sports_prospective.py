#!/usr/bin/env python3
"""Replay a previously frozen candidate list on a later, fixed time window.

Offline only. No optimizer, order client, network, source mutation or promotion.
The source cohort and the hypothetical policy are deliberately separate fields.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

import conservative_sports_grid as grid
from conservative_sports_stop_sensitivity import Stats, paired_result, retarget_stop

SCHEMA = "prior-frozen-candidates-temporal-followup-v1"
HISTORY_START = "2026-08-30T00:00:00Z"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_manifest(path, expected_sha256, dependency_approval=None):
    path = Path(path).resolve()
    if grid.visual.sha256(path) != expected_sha256:
        raise ValueError("candidate manifest SHA mismatch")
    manifest = json.loads(path.read_text())
    if manifest.get("schema") != SCHEMA or manifest.get("new_price_or_pnl_results_used") is not False:
        raise ValueError("unsupported or outcome-selected candidate manifest")
    window = manifest["evaluation_window"]
    start, end = grid.visual.timestamp(window["start_inclusive"]), grid.visual.timestamp(window["end_exclusive"])
    if start is None or end is None or start >= end:
        raise ValueError("invalid fixed evaluation window")
    specs = manifest["focus_specifications"] + manifest["prior_named_controls"]
    if len({s["spec_id"] for s in specs}) != len(specs):
        raise ValueError("duplicate candidate spec ID")
    for s in specs:
        if (s["sport"] not in {"soccer", "mlb"} or s["target_mode"] not in {"absolute", "relative", "hold"}
                or not 0 < s["entry"] < 1 or not 1 <= s["rank"] <= (6 if s["sport"] == "soccer" else 2)
                or s["baseline_stop"] != grid.policy_knobs(s["policy"], s["sport"])):
            raise ValueError("candidate or baseline stop contract mismatch")
    root = Path(__file__).resolve().parents[1]
    approval = dependency_approval or {}
    if approval and approval.get("candidate_manifest_sha256") != expected_sha256:
        raise ValueError("dependency review belongs to another candidate manifest")
    for name, expected in manifest["current_code_sha256"].items():
        actual = grid.visual.sha256(root / name)
        reviewed = approval.get("dependencies", {}).get(name, {})
        approved = (reviewed.get("frozen_sha256") == expected and reviewed.get("approved_sha256") == actual
                    and bool(reviewed.get("review_evidence")))
        if actual != expected and not approved:
            raise ValueError("analysis dependency changed since candidate freeze: " + name)
    return manifest


def normalize_source(source):
    s = dict(source)
    job = s.get("job") or s.get("jenkins_job")
    runtime = s.get("runtime_job")
    if not job or not runtime:
        raise ValueError("source must retain discovered job/runtime identity")
    expected_id = job + ":" + runtime
    if s.get("id", expected_id) != expected_id:
        raise ValueError("source ID does not match discovered job/runtime")
    s["id"] = expected_id
    if s.get("parent_source_key") and not s.get("source_key"):
        s["source_key"] = s["parent_source_key"]
    if not s.get("pinned") or s.get("mode") != "sim" or not s.get("source_key"):
        raise ValueError("only discovered simulation pins with source_key are accepted")
    p = Path(s["local_path"]).resolve()
    manifest_path = Path(s.get("manifest") or p.with_name("manifest.json"))
    pin = json.loads(manifest_path.read_text())
    if (pin.get("source_key") != s["source_key"] or pin.get("pinned_path") != str(p)
            or pin.get("sha256") != s["local_sha256"] or pin.get("quick_check") != ["ok"]):
        raise ValueError("pin manifest/source identity mismatch")
    return s


def event_admission(event, expected, training_ids):
    cfg = event.config
    if event.sport not in expected["compatible_policy_axes"]:
        return "UNSUPPORTED_FINANCIAL_SPORT"
    if event.event in training_ids:
        return "PRIOR_TRAINING_GAME_OVERLAP"
    if (cfg.get("config_hash") != expected["config_hash"]
            or cfg.get("strategy_source_digest") != expected["source_digest"]
            or cfg.get("mode") != "sim"):
        return "SOURCE_CONFIG_EPOCH_MISMATCH"
    identity = expected.get("policy_identity_from_prior_verified_config", {})
    for key in ("sport_profile_version", "protocol_sha256", "classifier_version", "league_mapping_sha256"):
        if key in identity and cfg.get(key) != identity[key]:
            return "PROFILE_PROTOCOL_IDENTITY_MISMATCH"
    if len(cfg.get("evidence_origins", [])) > 1:
        return "MIXED_RAW_LEGACY_ORIGINS_REQUIRE_SEPARATE_VIEW"
    return None


def cutoff_event(event, end):
    """Do not let a later observation or a foreign-cohort payout finish a path."""
    config = event.config.get("config_hash")
    terminals = {key: [p for p in proofs if p.get("observer_config") == config
                       and grid.visual.timestamp(p["observed_at"]) < end]
                 for key, proofs in event.terminals.items()}
    return replace(event, groups=[g for g in event.groups if g.time < end], terminals=terminals)


def candidate_path(event, spec, start, end):
    event = cutoff_event(event, end)
    candidate, diagnostics = grid.candidates(
        event, spec["policy"], spec["rank"], spec["entry"], max_gap=90,
        entry_width=spec["entry_width"], require_clear_rank=spec["require_clear_rank"],
        selection_mode=spec["selection_mode"],
    )
    if candidate is None:
        return None, "NO_QUALIFYING_FIRST_CANDIDATE", diagnostics
    if candidate[1].time < start:
        return None, "PRE_WINDOW_FIRST_CANDIDATE", diagnostics
    if candidate[1].time >= end:
        return None, "FIRST_CANDIDATE_AFTER_CUTOFF", diagnostics
    return grid.make_path(event, spec["policy"], candidate, max_gap=90), None, diagnostics


def stop_values(spec, deltas):
    baseline = spec["baseline_stop"]["stop_delta"]
    values = deltas if spec["tier"] == "FROZEN_FOCUS_PRIMARY_BASELINE_STOP" else [baseline]
    return sorted(set([baseline, *values]))


def evaluate_path(path, spec, deltas, models):
    """All stop arms share one first candidate, actual BUY and original TP."""
    output = []
    baseline_delta = spec["baseline_stop"]["stop_delta"]
    for model in models:
        baseline = grid.replay_path(path, spec["target_mode"], spec["target"], model,
                                    tp_policy=spec["tp_policy"], fee_collection="v2_cash")
        for delta in stop_values(spec, deltas):
            altered = retarget_stop(path, delta)
            result = grid.replay_path(altered, spec["target_mode"], spec["target"], model,
                                      tp_policy=spec["tp_policy"], fee_collection="v2_cash")
            if delta == baseline_delta and result != baseline:
                raise ValueError("baseline stop exact replay mismatch")
            output.append((model, delta, result, baseline, paired_result(result, baseline)))
    return output


def load_guava_adapter(path, expected_sha256):
    path = Path(path).resolve()
    if grid.visual.sha256(path) != expected_sha256:
        raise ValueError("Guava adapter changed since candidate freeze")
    spec = importlib.util.spec_from_file_location("frozen_prospective_guava_adapter", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(manifest, sources, output, *, manifest_sha256, guava_adapter=None, white_adapter=None, dependency_approval=None):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("new or empty output directory required")
    output.mkdir(parents=True, exist_ok=True)
    expected = {s["source_id"]: s for s in manifest["expected_source_epochs"]}
    supplied = [normalize_source(s) for s in sources]
    if len({s["id"] for s in supplied}) != len(supplied):
        raise ValueError("duplicate logical source; White pair requires an explicit paired adapter")
    specs = {s["spec_id"]: s for s in manifest["focus_specifications"] + manifest["prior_named_controls"]}
    start = grid.visual.timestamp(manifest["evaluation_window"]["start_inclusive"])
    end = grid.visual.timestamp(manifest["evaluation_window"]["end_exclusive"])
    training_ids = set(manifest["population"]["previous_analysis_event_ids"])
    deltas = manifest["stop_sensitivity"]["values"]
    stats, signatures, meta = {}, {}, {}
    source_audits, event_exclusions, coverage, paths_written = [], [], [], set()
    outcome_count = 0
    seen_events = set()
    write_json(output / "CANDIDATES.json", manifest)
    write_json(output / "SOURCES.json", supplied)
    write_json(output / "DEPENDENCY_APPROVAL.json", dependency_approval or {})
    with gzip.open(output / "event-outcomes.jsonl.gz", "wt") as outcomes, gzip.open(output / "paths.jsonl.gz", "wt") as paths:
        for source in supplied:
            sid = source["id"]
            if sid not in expected:
                coverage.append({"source": sid, "status": "UNDECLARED_SOURCE_NO_REPLAY"})
                continue
            epoch = expected[sid]
            if source["strategy"] != epoch["strategy"]:
                raise ValueError("discovered strategy differs from frozen source epoch")
            if not epoch["admitted_financial_evaluation"]:
                coverage.append({"source": sid, "status": "COVERAGE_ONLY_SPORT"})
                continue
            if source["strategy"] == "golden-watermelon":
                if white_adapter is None:
                    coverage.append({"source": sid, "status": "WHITE_PAIRED_GRID_ADAPTER_NOT_SUPPLIED"})
                    continue
                if not all(source.get(k) for k in ("parent_source_key", "sidecar_path", "sidecar_sha256", "sidecar_source_key")):
                    coverage.append({"source": sid, "status": "WHITE_PAIR_PIN_REQUIRED"})
                    continue
                events, audit = white_adapter.read_source(source, HISTORY_START, manifest["evaluation_window"]["end_exclusive"])
            elif source["strategy"] == "golden-guava":
                if guava_adapter is None:
                    coverage.append({"source": sid, "status": "GUAVA_EXPLICIT_ADAPTER_REQUIRED"})
                    continue
                events, audit = guava_adapter.read_source(source, HISTORY_START, manifest["evaluation_window"]["end_exclusive"])
            else:
                events, audit = grid.read_source(source, HISTORY_START, manifest["evaluation_window"]["end_exclusive"])
            source_audits.append(audit)
            admitted = 0
            allowed = epoch["focus_spec_ids"] + epoch["control_spec_ids"]
            for event in sorted(events, key=lambda e: (e.cohort, e.sport, e.event)):
                reason = event_admission(event, epoch, training_ids)
                if reason is None and not any(start <= g.time < end for g in event.groups):
                    reason = "NO_OBSERVATION_IN_EVALUATION_WINDOW"
                if reason is not None:
                    event_exclusions.append({"source": sid, "cohort": event.cohort, "event": event.event, "sport": event.sport, "reason": reason})
                    continue
                event_key = (sid, event.cohort, event.sport, event.event)
                if event_key in seen_events:
                    raise ValueError("duplicate source/cohort/event")
                seen_events.add(event_key)
                admitted += 1
                primary_fee = "recorded_schedule" if grid.uses_recorded_fees(event) else "sports_005"
                models = tuple(dict.fromkeys([primary_fee, *manifest["fees"]["secondary_models"]]))
                origin = "+".join(event.config.get("evidence_origins", [])) or ("guava" if source["strategy"] == "golden-guava" else "legacy")
                cache = {}
                for spec_id in allowed:
                    spec = specs[spec_id]
                    if spec["sport"] != event.sport:
                        continue
                    cache_key = tuple(spec[k] for k in ("policy", "rank", "entry", "entry_width", "selection_mode", "require_clear_rank"))
                    if cache_key not in cache:
                        cache[cache_key] = candidate_path(event, spec, start, end)
                    path, no_entry, diagnostics = cache[cache_key]
                    if path is not None:
                        path_key = (path["id"], canonical(cache_key))
                        if path_key not in paths_written:
                            paths.write(canonical({"candidate_cache_key": cache_key, "path": path}) + "\n")
                            paths_written.add(path_key)
                        results = evaluate_path(path, spec, deltas, models)
                    else:
                        result = {"status": "NO_ENTRY", "reason": no_entry, "net": None}
                        results = [(model, delta, result, result, paired_result(result, result)) for model in models for delta in stop_values(spec, deltas)]
                    for model, delta, result, baseline, paired in results:
                        key = (sid, event.cohort, event.sport, origin, spec_id, delta, model, model == primary_fee)
                        if key not in stats:
                            stats[key], signatures[key] = Stats(), hashlib.sha256()
                            meta[key] = {"source": sid, "cohort": event.cohort, "sport": event.sport, "evidence_origin": origin,
                                         "config_hash": event.config["config_hash"], "source_digest": event.config["strategy_source_digest"],
                                         "spec_id": spec_id, "policy": spec["policy"], "rank": spec["rank"], "entry_threshold": spec["entry"],
                                         "entry_width": spec["entry_width"], "target_mode": spec["target_mode"], "target": spec["target"],
                                         "tp_policy": spec["tp_policy"], "stop_delta": delta, "baseline_stop_delta": spec["baseline_stop"]["stop_delta"],
                                         "is_baseline_stop": delta == spec["baseline_stop"]["stop_delta"], "fee_model": model,
                                         "primary_fee": model == primary_fee, "fee_collection": "v2_cash", "tier": spec["tier"]}
                        stats[key].add(result, baseline)
                        signature = [event.event, path["entry_token"] if path else None, path["entry_utc"] if path else None,
                                     result.get("exit_utc"), result.get("exit_vwap"), result.get("net"), result["status"], result["reason"]]
                        signatures[key].update((canonical(signature) + "\n").encode())
                        outcomes.write(canonical({**meta[key], "event": event.event, "title": event.title,
                                                  "path_id": path["id"] if path else None,
                                                  "entry_utc": path["entry_utc"] if path else None, "entry_vwap": path["entry_vwap"] if path else None,
                                                  "entry_token": path["entry_token"] if path else None,
                                                  "entry_outcome_label": path.get("entry_outcome_label") if path else None,
                                                  "entry_verified_role": path.get("entry_verified_role") if path else None,
                                                  "candidate_diagnostics": diagnostics, "result": result, "baseline": baseline, "paired": paired}) + "\n")
                        outcome_count += 1
            coverage.append({"source": sid, "status": "REPLAYED", "loaded_source_cohort_events": len(events), "admitted_independent_games": admitted})
            write_json(output / "PROGRESS.json", {"source_coverage": coverage, "event_outcome_rows": outcome_count})
            print(canonical(coverage[-1]), flush=True)
    supplied_ids = {s["id"] for s in supplied}
    coverage += [{"source": sid, "status": "SOURCE_PIN_NOT_SUPPLIED"} for sid in expected if sid not in supplied_ids]
    rows = []
    trials = manifest["statistics"]["family_trials_before_execution"]
    for key in sorted(stats):
        row = {**meta[key], **stats[key].row(grid), "execution_signature": signatures[key].hexdigest()}
        m = trials["primary_plus_controls"] if row["is_baseline_stop"] else trials["all_focus_stop_cells_plus_controls"]
        row["nominal_family_trials"] = m
        row["family95_hoeffding_lower"] = max(0., row["positive"] / row["entered"] - math.sqrt(math.log(m / .05) / (2 * row["entered"]))) if row["entered"] else None
        rows.append(row)
    write_csv(output / "all-summaries.csv", rows)
    primary = [r for r in rows if r["primary_fee"] and r["is_baseline_stop"]]
    write_csv(output / "primary-baseline.csv", primary)
    write_json(output / "SOURCE_COVERAGE.json", coverage)
    write_json(output / "EVENT_EXCLUSIONS.json", event_exclusions)
    write_json(output / "SOURCE_AUDITS.json", source_audits)
    summary = {"candidate_manifest_sha256": manifest_sha256, "driver_sha256": grid.visual.sha256(Path(__file__)),
               "dependency_approval_sha256": hashlib.sha256(canonical(dependency_approval or {}).encode()).hexdigest(),
               "dependency_hashes_used": {name: grid.visual.sha256(Path(__file__).resolve().parents[1] / name)
                                          for name in manifest.get("current_code_sha256", {})},
               "created_at_utc": datetime.now(timezone.utc).isoformat(), "window": manifest["evaluation_window"],
               "history_read_start": HISTORY_START, "history_is_feature_only_before_window": True,
               "financial_replay": "displayed-book counterfactual, not actual fills", "promotion_allowed": False,
               "fixed_family_trials": trials, "source_coverage": coverage, "event_exclusion_counts": dict(Counter(e["reason"] for e in event_exclusions)),
               "independent_source_cohort_event_units": len(seen_events), "distinct_new_game_ids": len({x[3] for x in seen_events}),
               "cached_candidate_paths": len(paths_written), "event_outcome_rows": outcome_count, "summary_rows": len(rows),
               "primary_baseline_summary_rows": len(primary), "primary_baseline_summaries": primary,
               "no_winner_selection_performed": True, "no_live_parameter_change": True}
    write_json(output / "SUMMARY.json", summary)
    write_json(output / "OUTPUT_SHA256.json", {p.name: grid.visual.sha256(p) for p in output.iterdir() if p.is_file()})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candidates-sha256", required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guava-adapter", type=Path)
    parser.add_argument("--white-adapter", type=Path)
    parser.add_argument("--dependency-approval", type=Path,
                        help="Explicit reviewed code-hash supplement; candidate manifest remains unchanged")
    args = parser.parse_args(argv)
    approval = json.loads(args.dependency_approval.read_text()) if args.dependency_approval else None
    manifest = read_manifest(args.candidates, args.candidates_sha256, approval)
    adapter = load_guava_adapter(args.guava_adapter, manifest["reader_support"]["guava"]["sha256"]) if args.guava_adapter else None
    white = None
    if args.white_adapter:
        review = (approval or {}).get("white_adapter", {})
        if not review.get("review_evidence") or Path(review.get("path", "")).resolve() != args.white_adapter.resolve():
            raise ValueError("White paired adapter needs explicit reviewed hash supplement")
        white = load_guava_adapter(args.white_adapter, review.get("sha256"))
    return run(manifest, json.loads(args.sources.read_text()), args.output,
               manifest_sha256=args.candidates_sha256, guava_adapter=adapter,
               white_adapter=white, dependency_approval=approval)


if __name__ == "__main__":
    main()

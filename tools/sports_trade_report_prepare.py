#!/usr/bin/env python3
"""Discover current trading jobs and prepare verified local report inputs.

Uses the repository's redacting Jenkins reader and standard daily-rsync service.
It never starts jobs, changes config, reads credentials, or submits orders.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def verify_repository(repo=ROOT):
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    normalized = remote.replace("git@github.com:", "https://github.com/").removesuffix(".git").rstrip("/")
    if normalized != "https://github.com/izowooi/golden-burger":
        raise ValueError("sports report requires the Golden Burger repository")


def classify_operation(config):
    """Conservative candidate routing; actual DB mode is checked again later."""
    commands = []
    for builder in config.get("builders", []):
        commands.extend(line.strip() for line in builder.get("script", "").splitlines()
                        if line.strip() and not line.lstrip().startswith("#"))
    script = "\n".join(commands)
    live_run = any(re.search(r"\bpolybot\s+(?:run|run-account)\b[^\n]*--live\b", line) for line in commands)
    sim_run = any(re.search(r"\bpolybot\s+(?:run|run-account)\b[^\n]*--(?:simulate|shadow)\b", line) for line in commands)
    config_only = not live_run and not sim_run and bool(re.search(r"\bpolybot\s+config\b", script))
    sim_env = bool(re.search(r"(?:POLYBOT_SIMULATION_MODE|SIMULATION_MODE)\s*=\s*(?:true|1)\b", script, re.I))
    scheduled = bool(config.get("triggers")) and config.get("disabled") is not True
    if config_only:
        kind = "CONFIGURATION_ONLY"
    elif live_run:
        kind = "LIVE_DECLARED" if not sim_run else "MIXED_MODES_DECLARED"
    elif sim_run or sim_env:
        kind = "SIMULATION_DECLARED"
    else:
        kind = "MODE_REQUIRES_DB_CHECK"
    return {"operation": kind, "scheduled": scheduled, "disabled": config.get("disabled"),
            "live_command_declared": live_run, "simulation_command_declared": sim_run or sim_env,
            "config_only": config_only,
            "declared_runtime_jobs": sorted(set(re.findall(r"--job\s+([a-zA-Z0-9_-]+)", script)))}


def inspector_module(repo=ROOT):
    path = repo / "tools/jenkins-lan-inspector/skills/inspect-jenkins-job/scripts/jenkins_job.py"
    spec = importlib.util.spec_from_file_location("sports_report_jenkins_reader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def service(repo=ROOT):
    from daily_rsync.config import load_config
    from daily_rsync.sync import SyncService
    # All service paths are resolved by its maintained configuration loader.
    return SyncService(load_config(repo / "daily-rsync/config.local.toml"))


def discover(output, *, repo=ROOT, start=None, end=None):
    verify_repository(repo)
    svc = service(repo)
    doctor = svc.doctor()
    inventories = svc.scan(days=2)
    inspector = inspector_module(repo)
    client = inspector.JenkinsClient(inspector.DEFAULT_BASE_URL)
    rows = []
    for item in inventories:
        strategy = item.current_strategy
        row = {"job": item.name, "strategy": strategy, "known_strategies": list(item.strategies),
               "workspace": item.workspace, "selected": False}
        if not strategy or not strategy.startswith("golden-"):
            row["reason"] = "NOT_A_CURRENT_STRATEGY_JOB"
            rows.append(row)
            continue
        try:
            inspected = inspector.inspect_job(client, item.name)
            config = inspected["config"]
            operation = classify_operation(config)
            row.update(operation)
            row.update(config_sha256=config["sha256"], observed_at=inspected["observed_at_utc"],
                       latest_build=inspected["builds"]["lastBuild"],
                       strategy_evidence=item.strategy_evidence)
            # Include uncertain scheduled modes for an actual DB-mode check;
            # never infer live from a wallet/job name or a historical strategy.
            row["selected"] = (operation["scheduled"] and operation["operation"]
                               in {"LIVE_DECLARED", "MIXED_MODES_DECLARED", "MODE_REQUIRES_DB_CHECK"})
            row["reason"] = "CURRENT_TRADING_CANDIDATE_CHECK_SPORTS_IN_DB" if row["selected"] else operation["operation"]
            if not operation["scheduled"]:
                row["reason"] = "UNSCHEDULED_OR_DISABLED_" + operation["operation"]
        except inspector.JenkinsInspectorError as error:
            row.update(reason="JENKINS_READ_UNAVAILABLE", inspection_error=type(error).__name__)
        rows.append(row)
    result = {"schema": "sports-trade-report-discovery-v1", "observed_at": datetime.now(timezone.utc).isoformat(),
              "review_start": start, "review_end_exclusive": end, "repository_remote_verified": True,
              "doctor": doctor, "jobs": rows,
              "selection_scope": "current scheduled trading candidates; sport/profile and actual live DB checked before analysis",
              "inactive_scope_note": "Unscheduled/disabled jobs remain listed. Include an additional relevant historical job explicitly if window activity or carry-in requires it."}
    write_json(output, result)
    return result


def plan(discovery_path, output, *, repo=ROOT):
    verify_repository(repo)
    discovery = json.loads(Path(discovery_path).read_text())
    if discovery.get("schema") != "sports-trade-report-discovery-v1":
        raise ValueError("unsupported discovery document")
    svc = service(repo)
    plans, gaps = [], []
    for row in discovery["jobs"]:
        if not row.get("selected"):
            continue
        job, strategy = row["job"], row["strategy"]
        inventories = svc.scan(job=job, days=2)
        if len(inventories) != 1 or inventories[0].current_strategy != strategy:
            raise ValueError("current strategy changed after discovery")
        live = [a for a in inventories[0].artifacts if a.kind == "database_live" and a.canonical
                and a.strategy == strategy and a.mode in (None, "live")]
        if not live:
            gaps.append({"job": job, "strategy": strategy, "reason": "NO_DISCOVERED_CURRENT_LIVE_DATABASE"})
            continue
        planned = svc.create_plan(job=job, strategy=strategy, include_console_logs=False, days=2)
        item = asdict(planned)
        item["live_runtime_candidates"] = sorted(set(a.runtime_job for a in live if a.runtime_job))
        item["non_live_selected_artifacts"] = [{"kind": a.kind, "runtime_job": a.runtime_job,
                                                 "size_bytes": a.size_bytes}
                                                for a in planned.artifacts if a.kind != "database_live"]
        plans.append(item)
    total = sum(p["estimated_bytes"] for p in plans)
    result = {"schema": "sports-trade-report-plans-v1", "discovery": str(Path(discovery_path).resolve()),
              "review_start": discovery.get("review_start"), "review_end_exclusive": discovery.get("review_end_exclusive"),
              "plans": plans, "gaps": gaps, "total_estimated_bytes": total,
              "local_free_bytes": shutil.disk_usage(svc.config.data_root).free,
              "minimum_free_bytes": svc.config.minimum_free_bytes}
    write_json(output, result)
    return result


def sync(plans_path, output, *, repo=ROOT):
    verify_repository(repo)
    document = json.loads(Path(plans_path).read_text())
    if document.get("schema") != "sports-trade-report-plans-v1":
        raise ValueError("unsupported plans document")
    svc = service(repo)
    free = shutil.disk_usage(svc.config.data_root).free
    if free - document["total_estimated_bytes"] < svc.config.minimum_free_bytes:
        raise ValueError("report synchronization would violate local free-space reserve")
    sources, results = [], []
    for declared in document["plans"]:
        planned = svc.load_plan(declared["plan_id"])
        if planned.jenkins_job != declared["jenkins_job"] or planned.strategy != declared["strategy"]:
            raise ValueError("persisted plan identity mismatch")
        prior = svc.catalog.latest_sync_run(source=svc.config.ssh_host, job=planned.jenkins_job,
                                             strategy=planned.strategy)
        reused = bool(prior and prior["plan_id"] == planned.plan_id and prior["status"] == "SUCCESS")
        result = dict(prior) if reused else asdict(svc.execute(planned))
        if result["status"] != "SUCCESS":
            raise RuntimeError("standard daily-rsync execution failed")
        verified = svc.verify(job=planned.jenkins_job, strategy=planned.strategy)
        if verified["status"] != "SUCCESS":
            raise RuntimeError("standard daily-rsync verification failed")
        eligible = [a for a in svc.catalog.list_artifacts(source=svc.config.ssh_host,
                    job=planned.jenkins_job, strategy=planned.strategy)
                    if a["kind"] == "database_live" and a["runtime_job"] in declared["live_runtime_candidates"]
                    and a["status"] == "SYNCED"]
        for artifact in eligible:
            artifact = dict(artifact)
            path = None
            # Reuse only a pin of these exact verified catalog bytes. This also
            # recovers an interruption between pin creation and report writing.
            for pin in svc.catalog.list_pins(source=svc.config.ssh_host, job=planned.jenkins_job, strategy=planned.strategy):
                if pin["source_key"] != artifact["source_key"]:
                    continue
                candidate = Path(pin["pinned_path"])
                if not candidate.is_file():
                    continue
                pm = json.loads(candidate.with_name("manifest.json").read_text())
                with candidate.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if (pm.get("source_key") == artifact["source_key"] and pm.get("pinned_path") == str(candidate)
                        and pm.get("quick_check") == ["ok"] and digest == pm.get("sha256") == artifact["local_sha256"]):
                    path = candidate
                    break
            if path is None:
                path = Path(svc.pin_database(artifact["source_key"]))
            manifest = json.loads(path.with_name("manifest.json").read_text())
            metadata = json.loads(artifact.get("metadata_json") or "{}")
            sources.append({"source_key": artifact["source_key"], "jenkins_job": planned.jenkins_job,
                            "strategy": planned.strategy, "runtime_job": artifact["runtime_job"],
                            "mode": "live", "pinned": True, "db_path": str(path),
                            "sha256": manifest["sha256"], "manifest": str(path.with_name("manifest.json")),
                            "source_completed_at": metadata.get("completed_at"),
                            "source_cutoff_basis": "STANDARD_SNAPSHOT_METADATA" if metadata.get("completed_at") else "UNKNOWN",
                            "timestamp_contract": "repository_sqlite_utc", "maker_zero_fee_contract": False})
        results.append({"job": planned.jenkins_job, "strategy": planned.strategy,
                        "sync": {k: result.get(k) for k in ("run_id", "plan_id", "status", "started_at", "finished_at", "transferred", "skipped", "failed", "bytes_written")},
                        "reused_successful_plan": reused, "verify": verified})
        write_json(Path(output).with_name("sync-progress.json"), {"jobs": results, "sources": sources})
    report = {"schema": "sports-trade-report-inputs-v1", "review_start": document.get("review_start"),
              "review_end_exclusive": document.get("review_end_exclusive"), "discovery": document["discovery"],
              "discovery_jobs": json.loads(Path(document['discovery']).read_text()).get('jobs', []) if Path(document['discovery']).is_file() else [],
              "sources": sources, "preparation": results, "gaps": document["gaps"]}
    write_json(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    discover_parser = subs.add_parser("discover")
    discover_parser.add_argument("--output", type=Path, required=True)
    discover_parser.add_argument("--start")
    discover_parser.add_argument("--end")
    plan_parser = subs.add_parser("plan")
    plan_parser.add_argument("--discovery", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    sync_parser = subs.add_parser("sync")
    sync_parser.add_argument("--plans", type=Path, required=True)
    sync_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "discover":
        result = discover(args.output, start=args.start, end=args.end)
        print(json.dumps({"jobs": len(result["jobs"]), "selected": sum(r["selected"] for r in result["jobs"])}))
    elif args.command == "plan":
        result = plan(args.discovery, args.output)
        print(json.dumps({"plans": len(result["plans"]), "estimated_bytes": result["total_estimated_bytes"], "local_free_bytes": result["local_free_bytes"]}))
    else:
        result = sync(args.plans, args.output)
        print(json.dumps({"verified_live_sources": len(result["sources"])}))


if __name__ == "__main__":
    main()

"""Python 3.9-compatible helper executed over SSH stdin.

This module intentionally uses only the standard library. It is not installed on the
remote host; :class:`RemoteClient` sends this source to ``python3 -`` for each command.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

STRATEGY_PATTERN = re.compile(rb"golden-[a-z0-9-]+", re.I)
AUDIT_PATTERN = re.compile(rb"\[RUN_AUDIT\].{0,160}?strategy=(golden-[a-z0-9-]+)", re.I)
RUNTIME_PATTERN = re.compile(rb"(?:--job[ =]|Job: |job=)([A-Za-z0-9_.-]+)", re.I)
TEXT_STRATEGY_PATTERN = re.compile(r"(?<![A-Za-z0-9-])(golden-[a-z0-9-]+)(?![A-Za-z0-9-])", re.I)
SHELL_STRATEGY_TAGS = {"command", "script"}


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def iso_from_ns(value):
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_log_sample(path):
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(min(size, 512 * 1024))
        if size > 1024 * 1024:
            handle.seek(max(0, size - 512 * 1024))
            tail = handle.read(512 * 1024)
        else:
            tail = b""
    return head + b"\n" + tail


def classify_log_details(path):
    try:
        data = safe_log_sample(path)
    except OSError:
        return None, None, None
    audit = AUDIT_PATTERN.findall(data)
    tokens = STRATEGY_PATTERN.findall(data)
    runtime = RUNTIME_PATTERN.findall(data)
    structured_strategy = (
        audit[-1].decode("ascii", "replace").lower() if audit else None
    )
    legacy_strategy = tokens[-1].decode("ascii", "replace").lower() if tokens else None
    strategy = structured_strategy or legacy_strategy
    runtime_job = runtime[-1].decode("ascii", "replace") if runtime else None
    return strategy, runtime_job, structured_strategy


def classify_log(path):
    strategy, runtime_job, _structured_strategy = classify_log_details(path)
    return strategy, runtime_job


def _local_tag(value):
    return value.rsplit("}", 1)[-1]


def _shell_segments(command):
    variables = {}
    candidates = set()
    command = command.replace("\\\n", "")
    for raw_line in command.splitlines():
        try:
            lexer = shlex.shlex(raw_line, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError:
            continue
        segment = []
        segments = []
        for token in tokens:
            if token and all(character in ";&|" for character in token):
                if segment:
                    segments.append(segment)
                    segment = []
            else:
                segment.append(token)
        if segment:
            segments.append(segment)
        for values in segments:
            if not values:
                continue
            start = 0
            if values[0] == "export":
                start = 1
            for token in values[start:]:
                match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", token)
                if not match:
                    break
                name, literal = match.groups()
                if "$(" not in literal and "`" not in literal:
                    variables[name] = literal
                start += 1
            if start >= len(values):
                continue
            if values[start] in ("builtin", "command"):
                start += 1
            if start >= len(values) or values[start] not in ("cd", "pushd"):
                continue
            start += 1
            if start < len(values) and values[start] == "--":
                start += 1
            if start >= len(values):
                continue
            target = values[start]
            if "$(" in target or "`" in target:
                continue
            for name, literal in variables.items():
                target = target.replace("${" + name + "}", literal)
                target = re.sub(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", literal, target)
            candidates.update(value.lower() for value in TEXT_STRATEGY_PATTERN.findall(target))
    return candidates


def configured_strategies(job_dir):
    config_path = job_dir / "config.xml"
    if not config_path.is_file() or config_path.is_symlink():
        return set()
    try:
        root = ET.parse(str(config_path)).getroot()
    except (ET.ParseError, OSError):
        return set()
    candidates = set()
    for element in root.iter():
        if _local_tag(element.tag) not in SHELL_STRATEGY_TAGS or not element.text:
            continue
        candidates.update(_shell_segments(element.text))
    return candidates


def build_result(build_dir, log_path):
    build_xml = build_dir / "build.xml"
    if build_xml.is_file():
        try:
            data = build_xml.read_bytes()
            match = re.search(rb"<result>([^<]+)</result>", data)
            if match:
                return match.group(1).decode("ascii", "replace")
        except OSError:
            pass
    try:
        tail = safe_log_sample(log_path)[-64 * 1024 :]
    except OSError:
        return None
    match = re.search(rb"Finished: ([A-Z_]+)", tail)
    return match.group(1).decode("ascii", "replace") if match else None


def stat_record(
    path,
    kind,
    job,
    strategy=None,
    runtime_job=None,
    build_number=None,
    status=None,
    canonical=True,
):
    value = path.stat()
    return {
        "kind": kind,
        "remote_path": str(path),
        "size_bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "jenkins_job": job,
        "strategy": strategy,
        "runtime_job": runtime_job,
        "build_number": build_number,
        "completed_at": iso_from_ns(value.st_mtime_ns),
        "status": status,
        "canonical": canonical,
    }


def database_identity(path):
    strategy = path.parents[2].name if len(path.parents) >= 3 else None
    runtime_job = path.parent.name
    mode = "sim" if path.name == "trades_sim.db" else "live"
    try:
        connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True, timeout=2)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "run_audits" in tables:
            row = connection.execute(
                """
                SELECT strategy_name, job_name, mode
                FROM run_audits ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
            if row:
                strategy, runtime_job, mode = row
        connection.close()
    except (OSError, sqlite3.Error):
        pass
    return strategy, runtime_job, mode


def scan(args):
    home = Path(args.jenkins_home).expanduser().resolve()
    jobs_root = home / "jobs"
    workspace_root = home / "workspace"
    if not jobs_root.is_dir() or not workspace_root.is_dir():
        raise RuntimeError("Jenkins jobs/workspace directory not found")
    disk = shutil.disk_usage(str(home))
    selected = (
        [args.job]
        if args.job
        else sorted(path.name for path in jobs_root.iterdir() if path.is_dir())
    )
    jobs = []
    cutoff_ns = int(args.cutoff_epoch * 1_000_000_000)
    for job in selected:
        job_dir = jobs_root / job
        workspace = workspace_root / job
        if not job_dir.is_dir():
            continue
        config_candidates = configured_strategies(job_dir)
        configured_strategy = (
            next(iter(config_candidates)) if len(config_candidates) == 1 else None
        )
        artifacts = []
        builds_dir = job_dir / "builds"
        build_numbers = []
        latest_build_strategy = None
        latest_build_number = None
        latest_build_result = None
        latest_successful_strategy = None
        latest_successful_build = None
        detailed = bool(args.job)
        if builds_dir.is_dir():
            if detailed:
                for build_dir in builds_dir.iterdir():
                    if build_dir.is_dir() and build_dir.name.isdigit():
                        build_numbers.append(int(build_dir.name))
                build_numbers.sort()
            else:
                next_build_path = job_dir / "nextBuildNumber"
                try:
                    latest_number = int(next_build_path.read_text().strip()) - 1
                except (OSError, ValueError):
                    latest_number = 0
                if latest_number > 0:
                    build_numbers = [latest_number]
            if detailed:
                numbers_to_scan = build_numbers
            elif build_numbers:
                latest_number = build_numbers[-1]
                numbers_to_scan = range(latest_number, max(0, latest_number - 25), -1)
            else:
                numbers_to_scan = []
            for number in numbers_to_scan:
                log_path = builds_dir / str(number) / "log"
                if not log_path.is_file():
                    continue
                value = log_path.stat()
                if detailed and value.st_mtime_ns < cutoff_ns:
                    continue
                result = build_result(log_path.parent, log_path)
                if not result:
                    continue
                strategy, runtime_job, structured_strategy = classify_log_details(log_path)
                if structured_strategy and (
                    latest_build_number is None or number > latest_build_number
                ):
                    latest_build_strategy = structured_strategy
                    latest_build_number = number
                    latest_build_result = result
                if (
                    result == "SUCCESS"
                    and structured_strategy
                    and (latest_successful_build is None or number > latest_successful_build)
                ):
                    latest_successful_strategy = structured_strategy
                    latest_successful_build = number
                if detailed:
                    artifacts.append(
                        stat_record(
                            log_path,
                            "jenkins_console",
                            job,
                            strategy=strategy,
                            runtime_job=runtime_job,
                            build_number=number,
                            status=result,
                        )
                    )
        strategies = set(config_candidates)
        if latest_build_strategy:
            strategies.add(latest_build_strategy)
        latest_db = None
        if workspace.is_dir():
            for path in workspace.glob("golden-*/data/*/trades*.db"):
                if not path.is_file() or path.is_symlink():
                    continue
                strategy, runtime_job, mode = database_identity(path)
                if strategy:
                    strategies.add(strategy)
                canonical = path.name in ("trades.db", "trades_sim.db")
                kind = "database_sim" if mode == "sim" else "database_live"
                if not canonical:
                    kind = "database_safety"
                if detailed:
                    artifacts.append(
                        stat_record(
                            path,
                            kind,
                            job,
                            strategy=strategy,
                            runtime_job=runtime_job,
                            canonical=canonical,
                        )
                    )
                if canonical:
                    candidate = (path.stat().st_mtime_ns, strategy)
                    if latest_db is None or candidate[0] > latest_db[0]:
                        latest_db = candidate
            for path in workspace.glob("golden-*/data/*/logs/*") if detailed else []:
                if not path.is_file() or path.is_symlink():
                    continue
                value = path.stat()
                if value.st_mtime_ns < cutoff_ns:
                    continue
                strategy = path.parents[3].name
                runtime_job = path.parents[1].name
                strategies.add(strategy)
                if detailed:
                    artifacts.append(
                        stat_record(
                            path,
                            "bot_log",
                            job,
                            strategy=strategy,
                            runtime_job=runtime_job,
                        )
                    )
            for path in workspace.glob("golden-*/data/*/trades_*.csv") if detailed else []:
                if not path.is_file() or path.is_symlink():
                    continue
                strategy = path.parents[2].name
                runtime_job = path.parent.name
                strategies.add(strategy)
                if detailed:
                    artifacts.append(
                        stat_record(
                            path,
                            "trade_csv",
                            job,
                            strategy=strategy,
                            runtime_job=runtime_job,
                        )
                    )
        latest_database_strategy = latest_db[1] if latest_db else None
        if latest_database_strategy:
            strategies.add(latest_database_strategy)
        if configured_strategy:
            current_strategy = configured_strategy
            current_source = "jenkins_config"
        elif latest_build_strategy:
            current_strategy = latest_build_strategy
            current_source = "structured_run_audit"
        elif latest_database_strategy:
            current_strategy = latest_database_strategy
            current_source = "database_run_audit"
        else:
            current_strategy = None
            current_source = "unknown"
        signals = {
            value
            for value in (
                configured_strategy,
                latest_build_strategy,
                latest_database_strategy,
            )
            if value
        }
        conflict = len(config_candidates) > 1 or len(signals) > 1
        if len(config_candidates) > 1:
            strategy_state = "AMBIGUOUS_CONFIG"
        elif configured_strategy and latest_build_strategy:
            strategy_state = (
                "CONFIRMED"
                if configured_strategy == latest_build_strategy
                else "PENDING_DEPLOYMENT"
            )
        elif configured_strategy:
            strategy_state = "CONFIGURED_ONLY"
        elif latest_build_strategy:
            strategy_state = "OBSERVED"
        elif latest_database_strategy:
            strategy_state = "DATABASE_ONLY"
        else:
            strategy_state = "UNKNOWN"
        strategy_evidence = {
            "configured_strategy": configured_strategy,
            "configured_candidates": sorted(config_candidates),
            "latest_build_strategy": latest_build_strategy,
            "latest_build_number": latest_build_number,
            "latest_build_result": latest_build_result,
            "latest_successful_strategy": latest_successful_strategy,
            "latest_successful_build": latest_successful_build,
            "latest_database_strategy": latest_database_strategy,
            "current_source": current_source,
            "state": strategy_state,
            "conflict": conflict,
        }
        jobs.append(
            {
                "name": job,
                "workspace": str(workspace),
                "build_count": (
                    len(build_numbers) if detailed else (build_numbers[-1] if build_numbers else 0)
                ),
                "min_build": min(build_numbers) if build_numbers else None,
                "max_build": max(build_numbers) if build_numbers else None,
                "current_strategy": current_strategy,
                "strategies": sorted(value for value in strategies if value),
                "strategy_evidence": strategy_evidence,
                "artifacts": artifacts,
                "remote_free_bytes": disk.free,
            }
        )
    emit(
        {
            "schema_version": 1,
            "hostname": socket.gethostname(),
            "jenkins_home": str(home),
            "remote_free_bytes": disk.free,
            "jobs": jobs,
        }
    )


def doctor(args):
    home = Path(args.jenkins_home).expanduser().resolve()
    disk = shutil.disk_usage(str(home))
    emit(
        {
            "schema_version": 1,
            "hostname": socket.gethostname(),
            "username": os.getenv("USER") or "",
            "jenkins_home": str(home),
            "jobs_exists": (home / "jobs").is_dir(),
            "workspace_exists": (home / "workspace").is_dir(),
            "free_bytes": disk.free,
            "python": sys.version.split()[0],
        }
    )


def snapshot(args):
    home = Path(args.jenkins_home).expanduser().resolve()
    workspace = (home / "workspace").resolve()
    source = Path(args.source).expanduser().resolve()
    try:
        source.relative_to(workspace)
    except ValueError:
        raise RuntimeError("database source is outside Jenkins workspace") from None
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("database source must be a regular file")

    staging_root = Path(args.staging_root).expanduser().resolve()
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(str(staging_root)).free < source.stat().st_size + 10 * 1024**3:
        raise RuntimeError("remote staging would violate the 10 GiB free-space reserve")
    run_dir = staging_root / uuid.uuid4().hex
    run_dir.mkdir(mode=0o700)
    target = run_dir / "snapshot.db"
    started = time.time()
    source_connection = sqlite3.connect("file:{}?mode=ro".format(source), uri=True, timeout=30)
    destination = sqlite3.connect(str(target), timeout=30)
    try:
        source_connection.execute("PRAGMA busy_timeout=30000")
        source_connection.backup(destination, pages=4096, sleep=0.02)
    finally:
        destination.close()
        source_connection.close()
    check = sqlite3.connect("file:{}?mode=ro".format(target), uri=True)
    try:
        integrity = [row[0] for row in check.execute("PRAGMA quick_check")]
    finally:
        check.close()
    if integrity != ["ok"]:
        shutil.rmtree(str(run_dir), ignore_errors=True)
        raise RuntimeError("snapshot quick_check failed: {}".format(integrity))
    digest = sha256(target)
    target.chmod(0o600)
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "snapshot": str(target),
        "source_size_bytes": source.stat().st_size,
        "snapshot_size_bytes": target.stat().st_size,
        "sha256": digest,
        "quick_check": integrity,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path.chmod(0o600)
    emit(manifest)


def cleanup(args):
    root = Path(args.staging_root).expanduser().resolve()
    target = Path(args.path).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise RuntimeError("cleanup target is outside staging root") from None
    if target == root:
        raise RuntimeError("refusing to remove staging root")
    shutil.rmtree(str(target), ignore_errors=True)
    emit({"removed": str(target)})


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--jenkins-home", required=True)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--jenkins-home", required=True)
    scan_parser.add_argument("--job")
    scan_parser.add_argument("--cutoff-epoch", type=float, default=0)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--jenkins-home", required=True)
    snapshot_parser.add_argument("--source", required=True)
    snapshot_parser.add_argument("--staging-root", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--staging-root", required=True)
    cleanup_parser.add_argument("--path", required=True)

    args = parser.parse_args()
    if args.command == "doctor":
        doctor(args)
    elif args.command == "scan":
        scan(args)
    elif args.command == "snapshot":
        snapshot(args)
    elif args.command == "cleanup":
        cleanup(args)


if __name__ == "__main__":
    main()

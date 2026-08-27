"""Python 3.9-compatible helper executed over SSH stdin.

This module intentionally uses only the standard library. It is not installed on the
remote host; :class:`RemoteClient` sends this source to ``python3 -`` for each command.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import stat
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

STRATEGY_PATTERN = re.compile(rb"golden-[a-z0-9-]+", re.I)
AUDIT_PATTERN = re.compile(rb"\[RUN_AUDIT\].{0,160}?strategy=(golden-[a-z0-9-]+)", re.I)
RUNTIME_PATTERN = re.compile(rb"(?:--job[ =]|Job: |job=)([A-Za-z0-9_.-]+)", re.I)
TEXT_STRATEGY_PATTERN = re.compile(r"(?<![A-Za-z0-9-])(golden-[a-z0-9-]+)(?![A-Za-z0-9-])", re.I)
SHELL_STRATEGY_TAGS = {"command", "script"}
RESEARCH_ARCHIVE_PATTERN = re.compile(r"^trades_sim_(\d{8})\.db$")
CANONICAL_DATABASE_NAMES = frozenset(("trades.db", "trades_sim.db", "shadow.db"))
WORKSPACE_MARKER_NAME = ".daily-rsync-workspace.json"
WORKSPACE_MARKER_SCHEMA_VERSION = 1
WORKSPACE_MARKER_MAX_BYTES = 4096


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
    structured_strategy = audit[-1].decode("ascii", "replace").lower() if audit else None
    legacy_strategy = tokens[-1].decode("ascii", "replace").lower() if tokens else None
    strategy = structured_strategy or legacy_strategy
    runtime_job = runtime[-1].decode("ascii", "replace") if runtime else None
    return strategy, runtime_job, structured_strategy


def classify_log(path):
    strategy, runtime_job, _structured_strategy = classify_log_details(path)
    return strategy, runtime_job


def _local_tag(value):
    return value.rsplit("}", 1)[-1]


def _absolute_path(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("{} must be absolute: {}".format(label, value))
    return Path(os.path.abspath(str(path)))


def workspace_roots(args, require_available=True):
    home = _absolute_path(args.jenkins_home, "Jenkins home")
    values = getattr(args, "workspace_root", None) or [str(home / "workspace")]
    roots = []
    seen = set()
    for value in values:
        lexical = _absolute_path(value, "workspace root")
        if lexical.is_symlink():
            raise RuntimeError(
                "allowlisted workspace root must be a real directory, not a symlink: {}".format(
                    lexical
                )
            )
        available = lexical.is_dir()
        if require_available and not available:
            raise RuntimeError("allowlisted workspace root is not mounted: {}".format(lexical))
        resolved = lexical.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(
            {
                "path": lexical,
                "realpath": resolved,
                "available": available,
            }
        )
    if not roots:
        raise RuntimeError("at least one workspace root must be allowlisted")
    return roots


def _safe_job_name(job):
    if not job or job in (".", "..") or Path(job).name != job:
        raise RuntimeError("unsafe Jenkins job name: {}".format(job))
    return job


def configured_workspace(job_dir):
    config_path = job_dir / "config.xml"
    if not config_path.is_file() or config_path.is_symlink():
        return None
    try:
        root = ET.parse(str(config_path)).getroot()
    except (ET.ParseError, OSError):
        return None
    values = {
        element.text.strip()
        for element in root.iter()
        if _local_tag(element.tag) == "customWorkspace" and element.text and element.text.strip()
    }
    if len(values) > 1:
        raise RuntimeError("Jenkins config has multiple customWorkspace values")
    return next(iter(values)) if values else None


def _validate_job_workspace(workspace, root, job, require_exists=True):
    expected = root["path"] / job
    if workspace != expected:
        raise RuntimeError(
            "job workspace must be the exact allowlisted <root>/<job> path: {}".format(workspace)
        )
    if not workspace.exists() and not workspace.is_symlink():
        if require_exists:
            raise RuntimeError("configured job workspace is not mounted: {}".format(workspace))
        return workspace
    if workspace.is_symlink() or not workspace.is_dir():
        raise RuntimeError(
            "job workspace must be a real directory, not a symlink: {}".format(workspace)
        )
    resolved = workspace.resolve()
    expected_resolved = root["realpath"] / job
    if resolved != expected_resolved or resolved.parent != root["realpath"]:
        raise RuntimeError(
            "job workspace realpath escapes its allowlisted root: {}".format(workspace)
        )
    return workspace


def workspace_attestation(workspace, job, required=False):
    """Read and validate a job-owned workspace selection marker without symlinks."""
    marker = workspace / WORKSPACE_MARKER_NAME
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(marker), flags)
    except FileNotFoundError:
        if required:
            raise RuntimeError(
                "workspace selection marker is required: {}".format(marker)
            ) from None
        return None
    except OSError as error:
        raise RuntimeError(
            "workspace selection marker cannot be opened safely: {} ({})".format(marker, error)
        ) from None
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise RuntimeError(
                "workspace selection marker must be a regular file: {}".format(marker)
            )
        if value.st_size > WORKSPACE_MARKER_MAX_BYTES:
            raise RuntimeError("workspace selection marker is too large: {}".format(marker))
        chunks = []
        remaining = WORKSPACE_MARKER_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > WORKSPACE_MARKER_MAX_BYTES:
        raise RuntimeError("workspace selection marker is too large: {}".format(marker))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RuntimeError(
            "workspace selection marker is not valid UTF-8 JSON: {}".format(marker)
        ) from None
    expected = {
        "schema_version": WORKSPACE_MARKER_SCHEMA_VERSION,
        "job": job,
        "workspace": str(workspace),
    }
    if payload != expected:
        raise RuntimeError(
            "workspace selection marker payload mismatch: expected={} actual={}".format(
                expected, payload
            )
        )
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "name": WORKSPACE_MARKER_NAME,
        "schema_version": WORKSPACE_MARKER_SCHEMA_VERSION,
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def select_job_workspace(job_dir, job, roots):
    job = _safe_job_name(job)
    custom = configured_workspace(job_dir)
    if custom:
        # Jenkins Freestyle custom workspaces commonly persist this safe,
        # job-scoped variable literally in config.xml. Expand only JOB_NAME;
        # every path still has to equal an allowlisted <root>/<job> below.
        custom = custom.replace("${JOB_NAME}", job).replace("$JOB_NAME", job)
        candidate = _absolute_path(custom, "customWorkspace")
        for root in roots:
            if candidate == root["path"] / job:
                selected = _validate_job_workspace(candidate, root, job)
                workspace_attestation(selected, job)
                return selected
        raise RuntimeError(
            "customWorkspace is outside the exact allowlisted <root>/<job> paths: {}".format(
                candidate
            )
        )

    matches = []
    for root in roots:
        candidate = root["path"] / job
        if candidate.exists() or candidate.is_symlink():
            matches.append(_validate_job_workspace(candidate, root, job))
    if len(matches) > 1:
        marked = [
            candidate for candidate in matches if workspace_attestation(candidate, job) is not None
        ]
        if len(marked) != 1:
            raise RuntimeError(
                "job workspace is ambiguous across allowlisted roots; exactly one valid {} "
                "is required: {}".format(WORKSPACE_MARKER_NAME, job)
            )
        return marked[0]
    if matches:
        workspace_attestation(matches[0], job)
        return matches[0]
    return _validate_job_workspace(roots[0]["path"] / job, roots[0], job, require_exists=False)


def workspace_mount_identity(workspace, roots):
    """Bind a workspace path to the mounted filesystem selected during scan."""
    if not workspace.is_dir() or workspace.is_symlink():
        return {}
    for root in roots:
        if workspace == root["path"] / workspace.name:
            root_stat = root["path"].stat()
            workspace_stat = workspace.stat()
            identity = {
                "root_path": str(root["path"]),
                "root_realpath": str(root["realpath"]),
                "root_st_dev": int(root_stat.st_dev),
                "workspace_st_dev": int(workspace_stat.st_dev),
                "selection_contract": "allowlisted-root-job-v1",
            }
            marker = workspace_attestation(workspace, workspace.name)
            if marker is not None:
                identity["workspace_marker"] = marker
            return identity
    raise RuntimeError("selected workspace has no allowlisted root identity: {}".format(workspace))


def research_archive_date(path):
    match = RESEARCH_ARCHIVE_PATTERN.fullmatch(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def supported_database_name(name):
    """Return whether a runtime SQLite filename is part of the sync contract."""
    return name == "shadow.db" or (name.startswith("trades") and name.endswith(".db"))


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
    archive_date=None,
    mode=None,
    data_contract=None,
    database_utc_date=None,
):
    value = path.stat()
    fingerprint = None
    size_bytes = value.st_size
    mtime_ns = value.st_mtime_ns
    if kind.startswith("database"):
        state = sqlite_source_state(path)
        fingerprint = state["fingerprint"]
        size_bytes = state["size_bytes"]
        mtime_ns = state["mtime_ns"]
    completed_ns = mtime_ns
    if kind == "database_research_archive":
        # Pomegranate publishes a completed UTC-day shard with a hard link.
        # A hard link preserves data mtime but advances inode ctime, so the
        # latter is the source-side proof that the immutable name existed only
        # after rotation. Preserve it separately from the durable data
        # fingerprint/mtime used for incremental sync.
        completed_ns = max(completed_ns, value.st_ctime_ns)
    return {
        "kind": kind,
        "remote_path": str(path),
        "size_bytes": size_bytes,
        "mtime_ns": mtime_ns,
        "jenkins_job": job,
        "strategy": strategy,
        "runtime_job": runtime_job,
        "build_number": build_number,
        "completed_at": iso_from_ns(completed_ns),
        "status": status,
        "canonical": canonical,
        "archive_date": archive_date.isoformat() if archive_date else None,
        "mode": mode,
        "data_contract": data_contract,
        "database_utc_date": database_utc_date,
        "fingerprint": fingerprint,
    }


def _sqlite_source_state_once(path):
    members = []
    total = 0
    newest = 0
    # ``-shm`` is volatile coordination state. Merely opening a read-only WAL
    # database can create/touch it, so including its inode/mtime manufactures
    # immutable conflicts. Main + WAL contain the durable database evidence.
    for suffix in ("", "-wal"):
        candidate = Path(str(path) + suffix)
        if not candidate.is_file() or candidate.is_symlink():
            continue
        value = candidate.stat()
        member = {
            "suffix": suffix or "main",
            "size_bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns,
            "inode": value.st_ino,
        }
        members.append(member)
        total += value.st_size
        newest = max(newest, value.st_mtime_ns)
    if not members or members[0]["suffix"] != "main":
        raise RuntimeError("database source disappeared while fingerprinting: {}".format(path))
    encoded = json.dumps(members, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": total,
        "mtime_ns": newest,
        "members": members,
    }


def sqlite_source_state(path, attempts=6, delay_seconds=0.01):
    """Return a bounded, double-read stable SQLite main/WAL state."""
    if attempts < 2:
        raise ValueError("sqlite source state requires at least two attempts")
    previous = None
    last_error = None
    for attempt in range(attempts):
        try:
            current = _sqlite_source_state_once(path)
            last_error = None
        except (OSError, RuntimeError) as error:
            # A writer can create/remove a WAL sidecar between is_file() and
            # stat(). Retry the entire composite read instead of accepting a
            # partial main/WAL identity.
            previous = None
            last_error = error
        else:
            if previous is not None and current["members"] == previous["members"]:
                return current
            previous = current
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    message = "database source remained unstable while fingerprinting: {}".format(path)
    if last_error is not None:
        raise RuntimeError(message) from last_error
    raise RuntimeError(message)


def database_identity(path):
    strategy = path.parents[2].name if len(path.parents) >= 3 else None
    runtime_job = path.parent.name
    archive_date = research_archive_date(path)
    mode = "sim" if path.name in ("trades_sim.db", "shadow.db") or archive_date else "live"
    data_contract = None
    database_utc_date = None
    connection = None
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
        if "collection_contracts" in tables:
            rows = list(
                connection.execute(
                """
                SELECT contract_name, database_utc_date
                FROM collection_contracts
                """
                )
            )
            if len(rows) != 1:
                raise RuntimeError(
                    "research database must contain exactly one collection_contracts row: "
                    "{}".format(path)
                )
            data_contract = rows[0][0]
            database_utc_date = rows[0][1]
    except (OSError, sqlite3.Error) as error:
        if archive_date is not None:
            raise RuntimeError(
                "research archive identity could not be read: {}".format(path)
            ) from error
    finally:
        if connection is not None:
            connection.close()
    if path.name in ("trades_sim.db", "shadow.db") or archive_date:
        mode = "sim"
    if archive_date is not None and data_contract != "research-full-v1":
        raise RuntimeError(
            "research archive is missing the required research-full-v1 "
            "collection_contracts row: {}".format(path)
        )
    if data_contract == "research-full-v1":
        try:
            database_day = datetime.strptime(str(database_utc_date), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise RuntimeError(
                "research database has invalid database_utc_date: {}".format(path)
            ) from None
        if str(database_utc_date) != database_day.isoformat():
            raise RuntimeError(
                "research database date is not canonical YYYY-MM-DD: {}".format(path)
            )
        if archive_date is not None and database_day != archive_date:
            raise RuntimeError(
                "research archive filename date does not match database_utc_date: "
                "path={} filename_date={} database_utc_date={}".format(
                    path, archive_date.isoformat(), database_day.isoformat()
                )
            )
    return strategy, runtime_job, mode, data_contract, database_utc_date


def scan(args):
    home = _absolute_path(args.jenkins_home, "Jenkins home").resolve()
    jobs_root = home / "jobs"
    roots = workspace_roots(args)
    if not jobs_root.is_dir():
        raise RuntimeError("Jenkins jobs directory not found")
    disk = shutil.disk_usage(str(home))
    selected = (
        [args.job]
        if args.job
        else sorted(path.name for path in jobs_root.iterdir() if path.is_dir())
    )
    jobs = []
    cutoff_ns = int(args.cutoff_epoch * 1_000_000_000)
    archive_from_date = (
        datetime.strptime(args.archive_from_date, "%Y-%m-%d").date()
        if args.archive_from_date
        else None
    )
    archive_to_date = (
        datetime.strptime(args.archive_to_date, "%Y-%m-%d").date() if args.archive_to_date else None
    )
    if archive_from_date and archive_to_date and archive_from_date > archive_to_date:
        raise RuntimeError("archive date range is reversed")
    for job in selected:
        _safe_job_name(job)
        job_dir = jobs_root / job
        if not job_dir.is_dir():
            continue
        workspace = select_job_workspace(job_dir, job, roots)
        workspace_identity = workspace_mount_identity(workspace, roots)
        config_candidates = configured_strategies(job_dir)
        configured_strategy = next(iter(config_candidates)) if len(config_candidates) == 1 else None
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
        current_utc_day = datetime.now(timezone.utc).date()
        if workspace.is_dir():
            for path in workspace.glob("golden-*/data/*/*.db"):
                if not supported_database_name(path.name):
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                archive_date = research_archive_date(path)
                (
                    strategy,
                    runtime_job,
                    mode,
                    data_contract,
                    database_utc_date,
                ) = database_identity(path)
                # ``trades_sim.db`` is mutable and can cover only the current UTC
                # day. Historical Pomegranate ranges require immutable dated
                # archives.  This also excludes pre-contract Pomegranate smoke
                # databases such as ``pomegranate-local/trades_sim.db``; mixing
                # those runtimes made an otherwise complete archive sync PARTIAL.
                if (
                    path.name == "trades_sim.db"
                    and archive_from_date
                    and archive_to_date
                ):
                    if strategy == "golden-pomegranate" and data_contract != "research-full-v1":
                        continue
                    if data_contract == "research-full-v1" and not (
                        database_utc_date
                        and datetime.strptime(database_utc_date, "%Y-%m-%d").date()
                        == current_utc_day
                        and archive_from_date <= current_utc_day <= archive_to_date
                    ):
                        continue
                if archive_date and (
                    (archive_from_date and archive_date < archive_from_date)
                    or (archive_to_date and archive_date > archive_to_date)
                ):
                    continue
                if strategy:
                    strategies.add(strategy)
                canonical = path.name in CANONICAL_DATABASE_NAMES
                kind = "database_sim" if mode == "sim" else "database_live"
                if archive_date:
                    kind = "database_research_archive"
                elif not canonical:
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
                            archive_date=(
                                archive_date
                                or (
                                    datetime.strptime(database_utc_date, "%Y-%m-%d").date()
                                    if data_contract == "research-full-v1" and database_utc_date
                                    else None
                                )
                            ),
                            mode=mode,
                            data_contract=data_contract,
                            database_utc_date=database_utc_date,
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
                "workspace_identity": workspace_identity,
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
    home = _absolute_path(args.jenkins_home, "Jenkins home").resolve()
    roots = workspace_roots(args, require_available=False)
    disk = shutil.disk_usage(str(home))
    emit(
        {
            "schema_version": 1,
            "hostname": socket.gethostname(),
            "username": os.getenv("USER") or "",
            "jenkins_home": str(home),
            "jobs_exists": (home / "jobs").is_dir(),
            "workspace_exists": all(root["available"] for root in roots),
            "workspace_roots": [
                {
                    "path": str(root["path"]),
                    "realpath": str(root["realpath"]),
                    "available": root["available"],
                }
                for root in roots
            ],
            "workspace_marker_contract": {
                "name": WORKSPACE_MARKER_NAME,
                "schema_version": WORKSPACE_MARKER_SCHEMA_VERSION,
                "required_when_multiple_candidates": True,
                "payload_keys": ["schema_version", "job", "workspace"],
            },
            "free_bytes": disk.free,
            "python": sys.version.split()[0],
        }
    )


def validated_expected_workspace(args):
    """Re-resolve and compare a persisted workspace/mount attestation."""

    home = _absolute_path(args.jenkins_home, "Jenkins home").resolve()
    roots = workspace_roots(args)
    job = _safe_job_name(args.job)
    selected = select_job_workspace(home / "jobs" / job, job, roots)
    expected = _absolute_path(args.expected_workspace, "expected workspace")
    if selected != expected:
        raise RuntimeError(
            "persisted plan workspace is stale: expected={} current={}".format(expected, selected)
        )
    try:
        expected_identity = json.loads(args.expected_identity)
    except (TypeError, ValueError):
        raise RuntimeError("persisted plan workspace identity is missing or invalid") from None
    if not isinstance(expected_identity, dict) or not expected_identity:
        raise RuntimeError("persisted plan workspace identity is missing or invalid")
    current_identity = workspace_mount_identity(selected, roots)
    required = (
        "root_path",
        "root_realpath",
        "root_st_dev",
        "workspace_st_dev",
        "selection_contract",
    )
    if any(key not in expected_identity for key in required):
        raise RuntimeError("persisted plan workspace identity is incomplete")
    if expected_identity != current_identity:
        raise RuntimeError(
            "persisted plan workspace mount identity changed: expected={} current={}".format(
                expected_identity, current_identity
            )
        )
    return job, selected, current_identity, roots


def validate_workspace(args):
    """Re-resolve a persisted plan's Jenkins workspace without transferring data."""

    job, selected, current_identity, _roots = validated_expected_workspace(args)
    emit(
        {
            "job": job,
            "workspace": str(selected),
            "workspace_identity": current_identity,
            "validated": True,
        }
    )


RESEARCH_COLLECTOR_LOCKS = {
    "golden-pomegranate": ".pomegranate.lock",
    "golden-coconut": ".coconut-cycle.lock",
}


@contextmanager
def snapshot_read_lock(
    source, timeout_seconds=30.0, required=False, lock_name=None
):
    """Share the collector lock so rotation cannot race an online backup."""
    if required and lock_name not in set(RESEARCH_COLLECTOR_LOCKS.values()):
        raise RuntimeError("research collector snapshot lock contract is unsupported")
    lock_path = source.parent / (lock_name or ".pomegranate.lock")
    if not lock_path.exists():
        if required:
            raise RuntimeError("collector snapshot lock is required: {}".format(lock_path))
        yield
        return
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError("collector lock must be a regular file: {}".format(lock_path))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(lock_path), flags)
    deadline = time.monotonic() + timeout_seconds
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise RuntimeError("collector lock must be a regular file: {}".format(lock_path))
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "timed out waiting for collector snapshot lock: {}".format(lock_path)
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def snapshot(args):
    if not args.job or not args.expected_workspace or not args.expected_identity:
        raise RuntimeError("database snapshot requires expected workspace mount identity")
    expected_job, selected_workspace, current_identity, roots = validated_expected_workspace(args)
    source_path = _absolute_path(args.source, "database source")
    if not source_path.is_file() or source_path.is_symlink():
        raise RuntimeError("database source must be a regular file")
    source = source_path.resolve()
    allowed = False
    strategy_directory = None
    for root in roots:
        try:
            lexical_relative = source_path.relative_to(root["path"])
            resolved_relative = source.relative_to(root["realpath"])
        except ValueError:
            continue
        if len(lexical_relative.parts) < 5 or len(resolved_relative.parts) < 5:
            continue
        job = lexical_relative.parts[0]
        if job != expected_job:
            continue
        if source_path.parent != selected_workspace:
            try:
                source_path.relative_to(selected_workspace)
            except ValueError:
                continue
        if resolved_relative.parts[0] != job:
            continue
        if not lexical_relative.parts[1].startswith("golden-"):
            continue
        if lexical_relative.parts[2] != "data" or not supported_database_name(source.name):
            continue
        _validate_job_workspace(root["path"] / job, root, job)
        strategy_directory = lexical_relative.parts[1]
        allowed = True
        break
    if not allowed:
        raise RuntimeError("database source is outside an exact allowlisted job workspace")

    with snapshot_read_lock(
        source,
        required=args.expected_data_contract == "research-full-v1",
        lock_name=(
            RESEARCH_COLLECTOR_LOCKS.get(strategy_directory)
            if args.expected_data_contract == "research-full-v1"
            else None
        ),
    ):
        # The marker and st_dev values are re-read in this same SSH helper
        # immediately before opening SQLite, closing the scan/plan TOCTOU gap.
        _job, current_workspace, rechecked_identity, _roots = validated_expected_workspace(args)
        if current_workspace != selected_workspace or rechecked_identity != current_identity:
            raise RuntimeError("workspace identity changed before database snapshot")
        _snapshot_database_source(args, source)


def _snapshot_database_source(args, source):
    staging_root = Path(args.staging_root).expanduser().resolve()
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_state_before = sqlite_source_state(source)
    if shutil.disk_usage(str(staging_root)).free < source_state_before["size_bytes"] + 10 * 1024**3:
        raise RuntimeError("remote staging would violate the 10 GiB free-space reserve")
    run_dir = staging_root / uuid.uuid4().hex
    run_dir.mkdir(mode=0o700)
    target = run_dir / "snapshot.db"
    started = time.time()
    try:
        (
            snapshot_journal_mode,
            snapshot_open_retry_count,
            snapshot_source_open_mode,
        ) = _backup_database(
            source, target, source_state_before
        )
        source_state_after = sqlite_source_state(source)
        if (
            snapshot_source_open_mode == "immutable_stable_main"
            and source_state_after["fingerprint"]
            != source_state_before["fingerprint"]
        ):
            raise RuntimeError(
                "database source changed during immutable stable-main snapshot"
            )
        # macOS SQLite may retain an empty shared-memory file even after the
        # destination reports DELETE mode.  Both SQLite connections are closed
        # and DELETE mode has checkpointed the private destination, so these
        # staging-only sidecars are no longer part of the snapshot.
        for sidecar in (Path(str(target) + "-wal"), Path(str(target) + "-shm")):
            sidecar.unlink(missing_ok=True)
        check = sqlite3.connect("file:{}?mode=ro".format(target), uri=True)
        try:
            integrity = [row[0] for row in check.execute("PRAGMA quick_check")]
        finally:
            check.close()
        if integrity != ["ok"]:
            raise RuntimeError("snapshot quick_check failed: {}".format(integrity))
        (
            _strategy,
            _runtime_job,
            _mode,
            data_contract,
            database_utc_date,
        ) = database_identity(target)
        if args.expected_data_contract and data_contract != args.expected_data_contract:
            raise RuntimeError(
                "snapshot data contract changed: expected={} actual={}".format(
                    args.expected_data_contract, data_contract
                )
            )
        if (
            args.expected_database_utc_date
            and database_utc_date != args.expected_database_utc_date
        ):
            raise RuntimeError(
                "snapshot database UTC date changed: expected={} actual={}".format(
                    args.expected_database_utc_date, database_utc_date
                )
            )
        digest = sha256(target)
        target.chmod(0o600)
        manifest = {
            "schema_version": 2,
            "source": str(source),
            "snapshot": str(target),
            "source_size_bytes": source.stat().st_size,
            "source_storage_bytes": source_state_after["size_bytes"],
            "source_fingerprint_before": source_state_before["fingerprint"],
            "source_fingerprint_after": source_state_after["fingerprint"],
            "source_members_after": source_state_after["members"],
            "snapshot_size_bytes": target.stat().st_size,
            "sha256": digest,
            "quick_check": integrity,
            "snapshot_journal_mode": snapshot_journal_mode,
            "snapshot_open_retry_count": snapshot_open_retry_count,
            "snapshot_source_open_mode": snapshot_source_open_mode,
            "data_contract": data_contract,
            "database_utc_date": database_utc_date,
            "elapsed_seconds": round(time.time() - started, 3),
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        manifest_path.chmod(0o600)
        emit(manifest)
    except BaseException:
        # The caller cannot discover a staging path when this helper fails, so
        # cleanup must happen here rather than relying on a later SSH request.
        shutil.rmtree(str(run_dir), ignore_errors=True)
        raise


def _remove_partial_snapshot(target):
    for partial in (
        target,
        Path(str(target) + "-wal"),
        Path(str(target) + "-shm"),
    ):
        partial.unlink(missing_ok=True)


def _backup_once(source_uri, target):
    source_connection = None
    destination = None
    try:
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=30)
        destination = sqlite3.connect(str(target), timeout=30)
        source_connection.execute("PRAGMA busy_timeout=30000")
        source_connection.backup(destination, pages=4096, sleep=0.02)
        # SQLite's backup API copies the source database's persistent WAL
        # journal mode.  The transfer contract, however, moves only the
        # completed snapshot.db file.  Normalize the private destination
        # while its connection is still open so the snapshot cannot depend
        # on transient -wal/-shm sidecars and a read-only quick_check does
        # not race their teardown on macOS.
        journal_row = destination.execute("PRAGMA journal_mode=DELETE").fetchone()
        snapshot_journal_mode = str(journal_row[0]).lower() if journal_row else ""
        if snapshot_journal_mode != "delete":
            raise RuntimeError(
                "snapshot journal normalization failed: {}".format(
                    snapshot_journal_mode or "missing"
                )
            )
        return snapshot_journal_mode
    finally:
        if destination is not None:
            destination.close()
        if source_connection is not None:
            source_connection.close()


def _backup_database(source, target, source_state_before):
    """Create a source-safe snapshot across regular and removable volumes.

    A normal SQLite read-only connection is tried twice.  Some removable
    filesystems reject SQLite's read lock even though a fully checkpointed main
    file is readable.  Only that exact ``SQLITE_CANTOPEN`` case, with no WAL
    member, may fall back to immutable access.  The caller then proves that the
    composite source fingerprint stayed unchanged for the whole backup.
    """

    source_uri = "file:{}?mode=ro".format(source)
    failure = None
    for retry_count in range(2):
        try:
            return (
                _backup_once(source_uri, target),
                retry_count,
                "read_only_locked",
            )
        except sqlite3.OperationalError as error:
            failure = error
        if "unable to open database file" not in str(failure).lower():
            raise failure
        _remove_partial_snapshot(target)
        if retry_count == 0:
            time.sleep(0.25)

    if {member["suffix"] for member in source_state_before["members"]} != {"main"}:
        raise failure
    immutable_uri = source_uri + "&immutable=1"
    try:
        journal_mode = _backup_once(immutable_uri, target)
    except BaseException:
        _remove_partial_snapshot(target)
        raise
    return journal_mode, 2, "immutable_stable_main"


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


def existing_files(args):
    """Return the Jenkins console paths that still exist at transfer time.

    Jenkins LogRotator can delete the oldest build between scan/plan and rsync.
    Keep this helper narrowly scoped to console logs below JENKINS_HOME so a
    caller cannot use the generic SSH helper as an arbitrary file oracle.
    """

    home = _absolute_path(args.jenkins_home, "Jenkins home")
    try:
        requested = json.loads(args.paths_json)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("paths-json must be a JSON array") from None
    if not isinstance(requested, list) or not all(
        isinstance(value, str) for value in requested
    ):
        raise RuntimeError("paths-json must contain only strings")

    existing = []
    missing = []
    for value in requested:
        path = _absolute_path(value, "console path")
        try:
            relative = path.relative_to(home)
        except ValueError:
            raise RuntimeError("console path is outside Jenkins home") from None
        parts = relative.parts
        if (
            len(parts) < 5
            or parts[0] != "jobs"
            or parts[-3] != "builds"
            or not parts[-2].isdigit()
            or parts[-1] != "log"
        ):
            raise RuntimeError("path is not a Jenkins build console log")
        if path.is_file() and not path.is_symlink():
            existing.append(str(path))
        else:
            missing.append(str(path))
    emit({"existing": existing, "missing": missing})


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--jenkins-home", required=True)
    doctor_parser.add_argument("--workspace-root", action="append")

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--jenkins-home", required=True)
    scan_parser.add_argument("--workspace-root", action="append")
    scan_parser.add_argument("--job")
    scan_parser.add_argument("--cutoff-epoch", type=float, default=0)
    scan_parser.add_argument("--archive-from-date")
    scan_parser.add_argument("--archive-to-date")

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--jenkins-home", required=True)
    snapshot_parser.add_argument("--workspace-root", action="append")
    snapshot_parser.add_argument("--job")
    snapshot_parser.add_argument("--source", required=True)
    snapshot_parser.add_argument("--staging-root", required=True)
    snapshot_parser.add_argument("--expected-workspace")
    snapshot_parser.add_argument("--expected-identity")
    snapshot_parser.add_argument("--expected-data-contract")
    snapshot_parser.add_argument("--expected-database-utc-date")

    validate_parser = subparsers.add_parser("validate-workspace")
    validate_parser.add_argument("--jenkins-home", required=True)
    validate_parser.add_argument("--workspace-root", action="append")
    validate_parser.add_argument("--job", required=True)
    validate_parser.add_argument("--expected-workspace", required=True)
    validate_parser.add_argument("--expected-identity", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--staging-root", required=True)
    cleanup_parser.add_argument("--path", required=True)

    existing_files_parser = subparsers.add_parser("existing-files")
    existing_files_parser.add_argument("--jenkins-home", required=True)
    existing_files_parser.add_argument("--paths-json", required=True)

    args = parser.parse_args()
    if args.command == "doctor":
        doctor(args)
    elif args.command == "scan":
        scan(args)
    elif args.command == "snapshot":
        snapshot(args)
    elif args.command == "validate-workspace":
        validate_workspace(args)
    elif args.command == "cleanup":
        cleanup(args)
    elif args.command == "existing-files":
        existing_files(args)


if __name__ == "__main__":
    main()

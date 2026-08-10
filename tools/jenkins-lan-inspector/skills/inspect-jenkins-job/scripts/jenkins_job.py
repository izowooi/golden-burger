#!/usr/bin/env python3
"""Read-only Jenkins job inspection with defensive secret redaction."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://192.168.50.23:8080"
USER_AGENT = "jenkins-lan-inspector/0.1"
MAX_CONFIG_BYTES = 2_000_000
MAX_API_BYTES = 2_000_000
MAX_LOG_BYTES = 10_000_000
MAX_WORKSPACE_BYTES = 2_000_000

SENSITIVE_NAME_RE = re.compile(
    r"(?:PRIVATE|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|MNEMONIC|SEED|"
    r"FUNDER[_-]?ADDRESS|API[_-]?KEY)",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(
    r"^(?P<prefix>\s*(?:\+\s*)?(?:(?:export|setenv)\s+)?)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<separator>\s*=\s*)(?P<value>.*)$"
)
INLINE_ASSIGNMENT_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s;]+)"
)
AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)([^\s'\"]+)"
)
CLI_SECRET_RE = re.compile(
    r"(?i)(--(?:token|api-key|secret|password)(?:=|\s+))([^\s'\"]+)"
)
JSON_SECRET_RE = re.compile(
    r'(?i)([\"\'](?:token|api[_-]?key|secret|password|private[_-]?key)[\"\']\s*:\s*)'
    r'([\"\'])[^\"\']*\2'
)
PEM_RE = re.compile(
    r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----",
    re.DOTALL,
)

ALLOWED_BUILD_SELECTORS = {
    "lastBuild",
    "lastCompletedBuild",
    "lastSuccessfulBuild",
    "lastFailedBuild",
    "lastStableBuild",
}


class JenkinsInspectorError(RuntimeError):
    """Base error safe to present to a user."""


class JenkinsInputError(JenkinsInspectorError):
    """Raised for an unsafe or malformed user argument."""


class JenkinsHTTPError(JenkinsInspectorError):
    """Raised when Jenkins returns an error or cannot be reached."""


def normalize_base_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise JenkinsInputError("Jenkins URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise JenkinsInputError("Do not put credentials in the Jenkins URL")
    if parsed.query or parsed.fragment:
        raise JenkinsInputError("Jenkins URL must not contain a query or fragment")
    return candidate.rstrip("/")


def job_path(job_name: str) -> str:
    name = job_name.strip()
    if not name or name.startswith("/") or name.endswith("/"):
        raise JenkinsInputError("Job name must be a non-empty Jenkins full name")
    segments = name.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise JenkinsInputError("Job name contains an unsafe path segment")
    if any("\\" in segment or any(ord(char) < 32 for char in segment) for segment in segments):
        raise JenkinsInputError("Job name contains unsupported characters")
    return "/job/" + "/job/".join(quote(segment, safe="") for segment in segments)


def build_selector_path(selector: str) -> str:
    if selector in ALLOWED_BUILD_SELECTORS:
        return selector
    if selector.isdigit() and int(selector) > 0:
        return selector
    raise JenkinsInputError("Build must be a positive number or an allowed Jenkins build alias")


def _unquote_shell_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _discover_sensitive_assignments(source: str) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    values: set[str] = set()
    for line in source.splitlines():
        match = ASSIGNMENT_RE.match(line)
        if match and SENSITIVE_NAME_RE.search(match.group("name")):
            names.add(match.group("name"))
            value = _unquote_shell_value(match.group("value"))
            if len(value) >= 4 and value not in {"[REDACTED]", "****", "***"}:
                values.add(value)
        for inline in INLINE_ASSIGNMENT_RE.finditer(line):
            if not SENSITIVE_NAME_RE.search(inline.group("name")):
                continue
            names.add(inline.group("name"))
            value = _unquote_shell_value(inline.group("value"))
            if len(value) >= 4 and value not in {"[REDACTED]", "****", "***"}:
                values.add(value)
    return names, values


def sanitize_text(source: str, extra_secrets: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Redact likely credentials and return sensitive variable names only."""

    names, discovered_values = _discover_sensitive_assignments(source)
    secrets = {
        value
        for value in (*discovered_values, *extra_secrets)
        if value and len(value) >= 4 and value not in {"[REDACTED]", "****", "***"}
    }

    sanitized = PEM_RE.sub("[REDACTED PEM]", source)
    for secret in sorted(secrets, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")

    output: list[str] = []
    for line in sanitized.splitlines():
        assignment = ASSIGNMENT_RE.match(line)
        if assignment and SENSITIVE_NAME_RE.search(assignment.group("name")):
            names.add(assignment.group("name"))
            line = (
                f'{assignment.group("prefix")}{assignment.group("name")}'
                f'{assignment.group("separator")}[REDACTED]'
            )

        def redact_inline(match: re.Match[str]) -> str:
            name = match.group("name")
            if not SENSITIVE_NAME_RE.search(name):
                return match.group(0)
            names.add(name)
            return f"{name}=[REDACTED]"

        line = INLINE_ASSIGNMENT_RE.sub(redact_inline, line)
        line = AUTHORIZATION_RE.sub(r"\1[REDACTED]", line)
        line = CLI_SECRET_RE.sub(r"\1[REDACTED]", line)
        line = JSON_SECRET_RE.sub(r"\1\2[REDACTED]\2", line)
        output.append(line)

    trailing_newline = "\n" if sanitized.endswith("\n") else ""
    return "\n".join(output) + trailing_newline, sorted(names)


def sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        sanitized, _ = sanitize_text(value)
        return sanitized

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    userinfo = "[REDACTED]@" if parsed.username is not None or parsed.password is not None else ""
    netloc = f"{userinfo}{host}{port}"

    query = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if SENSITIVE_NAME_RE.search(key) else item_value))
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_at(node: ET.Element, path: str) -> str | None:
    child = node.find(path)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _url_contains_credentials(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        return True
    return any(SENSITIVE_NAME_RE.search(key) for key, _ in parse_qsl(parsed.query))


def parse_config(
    xml_bytes: bytes,
    *,
    anonymous_read: bool,
    base_scheme: str,
) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise JenkinsInspectorError("Jenkins returned invalid job configuration XML") from exc

    description, _ = sanitize_text(_text_at(root, "description") or "")
    result: dict[str, Any] = {
        "type": _local_name(root.tag),
        "sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "description": description,
        "disabled": _optional_bool(_text_at(root, "disabled")),
        "concurrent_build": _optional_bool(_text_at(root, "concurrentBuild")),
        "can_roam": _optional_bool(_text_at(root, "canRoam")),
        "assigned_node": _text_at(root, "assignedNode"),
        "block_when_downstream_building": _optional_bool(
            _text_at(root, "blockBuildWhenDownstreamBuilding")
        ),
        "block_when_upstream_building": _optional_bool(
            _text_at(root, "blockBuildWhenUpstreamBuilding")
        ),
    }

    scm = root.find("scm")
    raw_remotes: list[str] = []
    if scm is not None:
        raw_remotes = _unique(
            (node.text or "").strip()
            for node in scm.iter()
            if _local_name(node.tag) == "url" and (node.text or "").strip()
        )
        branches = _unique(
            (node.text or "").strip()
            for node in scm.iter()
            if _local_name(node.tag) == "name" and (node.text or "").strip()
        )
        credentials_configured = any(
            _local_name(node.tag) == "credentialsId" and bool((node.text or "").strip())
            for node in scm.iter()
        )
        result["scm"] = {
            "type": scm.attrib.get("class") or _local_name(scm.tag),
            "remotes": [sanitize_url(remote) for remote in raw_remotes],
            "branches": branches,
            "credentials_configured": credentials_configured,
        }
    else:
        result["scm"] = None

    triggers: list[dict[str, str | None]] = []
    trigger_root = root.find("triggers")
    if trigger_root is not None:
        for trigger in trigger_root:
            triggers.append(
                {"type": _local_name(trigger.tag), "spec": _text_at(trigger, "spec")}
            )
    result["triggers"] = triggers

    builders: list[dict[str, str]] = []
    raw_scripts: list[str] = []
    builder_root = root.find("builders")
    if builder_root is not None:
        for builder in builder_root:
            item: dict[str, str] = {"type": _local_name(builder.tag)}
            script = _text_at(builder, "command") or _text_at(builder, "script")
            if script is not None:
                raw_scripts.append(script)
                item["script"], _ = sanitize_text(script)
            builders.append(item)

    definition = root.find("definition")
    if definition is not None:
        pipeline: dict[str, str] = {
            "type": definition.attrib.get("class") or _local_name(definition.tag)
        }
        script = _text_at(definition, "script")
        if script is not None:
            raw_scripts.append(script)
            pipeline["script"], _ = sanitize_text(script)
        builders.append(pipeline)
    result["builders"] = builders

    publishers = root.find("publishers")
    result["publishers"] = (
        [_local_name(node.tag) for node in publishers] if publishers is not None else []
    )
    wrappers = root.find("buildWrappers")
    result["build_wrappers"] = (
        [_local_name(node.tag) for node in wrappers] if wrappers is not None else []
    )

    sensitive_names: set[str] = set()
    for script in raw_scripts:
        names, _ = _discover_sensitive_assignments(script)
        sensitive_names.update(names)
    result["inline_sensitive_variables"] = sorted(sensitive_names)

    findings: list[dict[str, str]] = []
    if sensitive_names:
        findings.append(
            {
                "severity": "CRITICAL",
                "code": "INLINE_SECRET_IN_JOB_CONFIG",
                "message": "Sensitive values are assigned inline: "
                + ", ".join(sorted(sensitive_names)),
            }
        )
        if any(script and not script.startswith("#!") for script in raw_scripts):
            findings.append(
                {
                    "severity": "CRITICAL",
                    "code": "SHELL_XTRACE_SECRET_RISK",
                    "message": "A shell step has inline secrets but no first-line shebang; Jenkins may run it with shell tracing.",
                }
            )
    if anonymous_read:
        findings.append(
            {
                "severity": "HIGH",
                "code": "ANONYMOUS_CONFIG_READ",
                "message": "config.xml was readable without authentication.",
            }
        )
    if base_scheme == "http":
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "PLAINTEXT_HTTP",
                "message": "Jenkins is being accessed over plaintext HTTP.",
            }
        )
    if any(_url_contains_credentials(remote) for remote in raw_remotes):
        findings.append(
            {
                "severity": "CRITICAL",
                "code": "SCM_URL_CONTAINS_CREDENTIAL",
                "message": "An SCM URL contains userinfo or a sensitive query parameter.",
            }
        )
    result["security_findings"] = findings
    return result


class _WorkspaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def parse_workspace_entries(html_bytes: bytes) -> list[dict[str, str]]:
    parser = _WorkspaceParser()
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for href in parser.hrefs:
        if href.startswith(("#", "/", "?", "http://", "https://", "mailto:")):
            continue
        parsed = urlsplit(href)
        if parsed.query or parsed.fragment:
            continue
        path = unquote(parsed.path)
        marker = None
        for candidate in ("/*view*/", "/*plain*/", "/*zip*/"):
            if path.endswith(candidate):
                marker = candidate
                path = path[: -len(candidate)]
                break
        is_directory = marker is None and path.endswith("/")
        name = path.strip("/")
        if not name or "/" in name or name in {".", ".."}:
            continue
        item = (name, "directory" if is_directory else "file")
        if item in seen:
            continue
        seen.add(item)
        entries.append({"name": item[0], "kind": item[1]})
    return entries


def _workspace_path(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or value.startswith("/"):
        raise JenkinsInputError("Workspace path must be a relative directory path")
    segments = value.strip("/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise JenkinsInputError("Workspace path contains an unsafe segment")
    return "/".join(quote(segment, safe="") for segment in segments) + "/"


class JenkinsClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = timeout
        self.scheme = urlsplit(self.base_url).scheme

    def get_bytes(self, path: str, *, limit: int) -> tuple[int, str, bytes]:
        request = Request(
            self.base_url + path,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/plain, text/html"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise JenkinsHTTPError(
                        f"Jenkins response exceeded the {limit}-byte safety limit"
                    )
                return response.status, response.headers.get_content_type(), body
        except HTTPError as exc:
            raise JenkinsHTTPError(f"Jenkins returned HTTP {exc.code} for {path}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise JenkinsHTTPError(f"Could not reach Jenkins for {path}: {type(exc).__name__}") from exc

    def get_json(self, path: str) -> tuple[int, dict[str, Any]]:
        status, _, body = self.get_bytes(path, limit=MAX_API_BYTES)
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JenkinsHTTPError("Jenkins returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise JenkinsHTTPError("Jenkins returned an unexpected JSON payload")
        return status, value


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def inspect_job(client: JenkinsClient, job_name: str) -> dict[str, Any]:
    path = job_path(job_name)
    tree = (
        "name,displayName,fullName,url,color,buildable,inQueue,nextBuildNumber,"
        "lastBuild[number,url,result,building,timestamp,duration],"
        "lastCompletedBuild[number],lastSuccessfulBuild[number],lastFailedBuild[number]"
    )
    api_status, metadata = client.get_json(f"{path}/api/json?tree={tree}")
    config_status, _, xml_bytes = client.get_bytes(
        f"{path}/config.xml", limit=MAX_CONFIG_BYTES
    )
    config = parse_config(
        xml_bytes,
        anonymous_read=True,
        base_scheme=client.scheme,
    )
    return {
        "observed_at_utc": _utc_now(),
        "jenkins_url": client.base_url,
        "job": job_name,
        "access": {
            "authentication_sent": False,
            "api_http_status": api_status,
            "config_http_status": config_status,
        },
        "state": {
            "display_name": metadata.get("displayName"),
            "full_name": metadata.get("fullName"),
            "reported_url": sanitize_url(str(metadata.get("url") or "")),
            "color": metadata.get("color"),
            "buildable": metadata.get("buildable"),
            "in_queue": metadata.get("inQueue"),
            "next_build_number": metadata.get("nextBuildNumber"),
        },
        "builds": {
            key: metadata.get(key)
            for key in (
                "lastBuild",
                "lastCompletedBuild",
                "lastSuccessfulBuild",
                "lastFailedBuild",
            )
        },
        "config": config,
    }


def inspect_log(
    client: JenkinsClient,
    job_name: str,
    *,
    build: str,
    tail: int,
) -> dict[str, Any]:
    if tail < 1 or tail > 5_000:
        raise JenkinsInputError("Log tail must be between 1 and 5000 lines")
    path = job_path(job_name)
    selector = build_selector_path(build)

    known_secrets: set[str] = set()
    try:
        _, _, config_bytes = client.get_bytes(f"{path}/config.xml", limit=MAX_CONFIG_BYTES)
        root = ET.fromstring(config_bytes)
        for node in root.iter():
            if _local_name(node.tag) not in {"command", "script"} or not node.text:
                continue
            _, values = _discover_sensitive_assignments(node.text)
            known_secrets.update(values)
    except (JenkinsInspectorError, ET.ParseError):
        pass

    status, _, body = client.get_bytes(
        f"{path}/{selector}/consoleText", limit=MAX_LOG_BYTES
    )
    raw = body.decode("utf-8", errors="replace")
    sanitized, names = sanitize_text(raw, known_secrets)
    lines = sanitized.splitlines()
    return {
        "observed_at_utc": _utc_now(),
        "jenkins_url": client.base_url,
        "job": job_name,
        "build": selector,
        "access": {"authentication_sent": False, "http_status": status},
        "sensitive_variables_redacted": names,
        "line_count": len(lines),
        "tail": lines[-tail:],
    }


def inspect_workspace(
    client: JenkinsClient,
    job_name: str,
    *,
    workspace_path: str,
    max_entries: int,
) -> dict[str, Any]:
    if max_entries < 1 or max_entries > 1_000:
        raise JenkinsInputError("Workspace max entries must be between 1 and 1000")
    path = job_path(job_name)
    relative = _workspace_path(workspace_path)
    status, _, body = client.get_bytes(
        f"{path}/ws/{relative}", limit=MAX_WORKSPACE_BYTES
    )
    entries = parse_workspace_entries(body)
    return {
        "observed_at_utc": _utc_now(),
        "jenkins_url": client.base_url,
        "job": job_name,
        "workspace_path": workspace_path or ".",
        "access": {"authentication_sent": False, "http_status": status},
        "entry_count": len(entries),
        "entries": entries[:max_entries],
        "entries_truncated": len(entries) > max_entries,
    }


def _bool_text(value: Any) -> str:
    if value is None:
        return "unset"
    return str(value).lower() if isinstance(value, bool) else str(value)


def render_inspection(result: dict[str, Any]) -> str:
    state = result["state"]
    config = result["config"]
    lines = [
        f'Jenkins: {result["jenkins_url"]}',
        f'Job: {result["job"]}',
        f'Observed (UTC): {result["observed_at_utc"]}',
        "Access: anonymous read "
        f'(API {result["access"]["api_http_status"]}, config.xml {result["access"]["config_http_status"]})',
        f'State: color={state.get("color")}, buildable={_bool_text(state.get("buildable"))}, '
        f'in_queue={_bool_text(state.get("in_queue"))}, next_build={state.get("next_build_number")}',
    ]

    last_build = result["builds"].get("lastBuild")
    if isinstance(last_build, dict):
        lines.append(
            "Last build: "
            f'#{last_build.get("number")} result={last_build.get("result")} '
            f'building={_bool_text(last_build.get("building"))} '
            f'duration_ms={last_build.get("duration")}'
        )

    lines.extend(
        [
            "",
            f'Config: type={config["type"]}, sha256={config["sha256"]}',
            f'Description: {config.get("description") or "(empty)"}',
            "Execution: "
            f'disabled={_bool_text(config.get("disabled"))}, '
            f'concurrent={_bool_text(config.get("concurrent_build"))}, '
            f'can_roam={_bool_text(config.get("can_roam"))}, '
            f'assigned_node={config.get("assigned_node") or "unset"}',
        ]
    )

    scm = config.get("scm")
    if isinstance(scm, dict):
        lines.append(
            f'SCM: {scm.get("type")}; branches={scm.get("branches") or []}; '
            f'credentials_configured={_bool_text(scm.get("credentials_configured"))}'
        )
        for remote in scm.get("remotes") or []:
            lines.append(f"  remote: {remote}")
    else:
        lines.append("SCM: none")

    lines.append("Triggers:")
    if config["triggers"]:
        for trigger in config["triggers"]:
            lines.append(f'  - {trigger["type"]}: {trigger.get("spec") or "(no spec)"}')
    else:
        lines.append("  (none)")

    lines.append("Builders:")
    if config["builders"]:
        for index, builder in enumerate(config["builders"], start=1):
            lines.append(f'  [{index}] {builder["type"]}')
            if builder.get("script"):
                lines.extend(f"      {line}" for line in builder["script"].splitlines())
    else:
        lines.append("  (none)")

    lines.append("Security findings:")
    if config["security_findings"]:
        for finding in config["security_findings"]:
            lines.append(
                f'  - [{finding["severity"]}] {finding["code"]}: {finding["message"]}'
            )
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def render_log(result: dict[str, Any]) -> str:
    lines = [
        f'Jenkins: {result["jenkins_url"]}',
        f'Job: {result["job"]}',
        f'Build: {result["build"]}',
        f'Observed (UTC): {result["observed_at_utc"]}',
        f'Access: anonymous read (HTTP {result["access"]["http_status"]})',
        f'Sensitive variables redacted: {result["sensitive_variables_redacted"]}',
        f'Console lines: {result["line_count"]}; showing last {len(result["tail"])}',
        "--- redacted console tail ---",
        *result["tail"],
    ]
    return "\n".join(lines)


def render_workspace(result: dict[str, Any]) -> str:
    lines = [
        f'Jenkins: {result["jenkins_url"]}',
        f'Job: {result["job"]}',
        f'Workspace: {result["workspace_path"]}',
        f'Observed (UTC): {result["observed_at_utc"]}',
        f'Access: anonymous read (HTTP {result["access"]["http_status"]})',
        f'Entries: {result["entry_count"]}',
    ]
    for entry in result["entries"]:
        suffix = "/" if entry["kind"] == "directory" else ""
        lines.append(f'  - {entry["name"]}{suffix}')
    if result["entries_truncated"]:
        lines.append("  ... truncated ...")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Jenkins job inspection with secret redaction."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("JENKINS_URL", DEFAULT_BASE_URL),
        help=f"Jenkins base URL (default: JENKINS_URL or {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect job metadata and config")
    inspect_parser.add_argument("job")

    log_parser = subparsers.add_parser("log", help="Read a redacted console-log tail")
    log_parser.add_argument("job")
    log_parser.add_argument("--build", default="lastBuild")
    log_parser.add_argument("--tail", type=int, default=80)

    workspace_parser = subparsers.add_parser(
        "workspace", help="List an anonymously readable workspace directory"
    )
    workspace_parser.add_argument("job")
    workspace_parser.add_argument("--path", default="")
    workspace_parser.add_argument("--max-entries", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = JenkinsClient(args.base_url, timeout=args.timeout)
        if args.command == "inspect":
            result = inspect_job(client, args.job)
            rendered = render_inspection(result)
        elif args.command == "log":
            result = inspect_log(client, args.job, build=args.build, tail=args.tail)
            rendered = render_log(result)
        else:
            result = inspect_workspace(
                client,
                args.job,
                workspace_path=args.path,
                max_entries=args.max_entries,
            )
            rendered = render_workspace(result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(rendered)
        return 0
    except JenkinsInspectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Collect read-only Jenkins job metadata into the dashboard's Supabase tables.

This standard-library implementation exists for the Mac mini Jenkins service account,
which does not require Node.js to run the lightweight metadata collector.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


COLLECTOR_NAME = "polymarket-dashboard-jenkins-lan-v1"
CONCURRENCY = 6
JENKINS_TREE = (
    "name,url,color,buildable,inQueue,"
    "lastBuild[number,building,result,timestamp,duration],"
    "lastCompletedBuild[number,building,result,timestamp,duration]"
)
SECRET_PATTERN = re.compile(r"sb_secret_[A-Za-z0-9._-]+")


def main() -> int:
    supabase_url = required_env("SUPABASE_URL")
    supabase_secret_key = required_env("SUPABASE_SECRET_KEY")
    jenkins_url = normalize_base_url(required_env("JENKINS_URL"))
    jenkins_user = optional_env("JENKINS_USER")
    jenkins_api_token = optional_env("JENKINS_API_TOKEN")
    timeout_ms = positive_integer(os.environ.get("JENKINS_REQUEST_TIMEOUT_MS"), 10_000)

    if bool(jenkins_user) != bool(jenkins_api_token):
        raise RuntimeError(
            "JENKINS_USER와 JENKINS_API_TOKEN은 둘 다 설정하거나 둘 다 생략해야 합니다."
        )

    secrets = tuple(value for value in (supabase_secret_key, jenkins_api_token) if value)
    supabase = SupabaseRestClient(
        base_url=supabase_url,
        secret_key=supabase_secret_key,
        timeout_seconds=timeout_ms / 1_000,
        secrets=secrets,
    )
    started_at = utc_now_iso()
    jobs_payload = supabase.request_json(
        "pd_jenkins_jobs",
        query={"select": "job_name", "order": "job_name.asc"},
    )
    jobs = [row["job_name"] for row in jobs_payload or []]
    expected = len(jobs)

    sync_rows = supabase.request_json(
        "pd_sync_runs",
        method="POST",
        query={"select": "sync_run_id"},
        payload={
            "collector_name": COLLECTOR_NAME,
            "started_at": started_at,
            "status": "RUNNING",
            "jobs_expected": expected,
        },
        prefer="return=representation",
    )
    if not isinstance(sync_rows, list) or len(sync_rows) != 1:
        raise RuntimeError("Sync run start failed: missing run id")
    sync_run_id = sync_rows[0]["sync_run_id"]

    observed = 0
    failures: list[str] = []

    try:
        def collect(job_name: str) -> tuple[str, str | None]:
            try:
                payload = fetch_jenkins_job(
                    base_url=jenkins_url,
                    job_name=job_name,
                    timeout_seconds=timeout_ms / 1_000,
                    user=jenkins_user,
                    api_token=jenkins_api_token,
                )
                supabase.request_json(
                    "pd_jenkins_jobs",
                    method="PATCH",
                    query={"job_name": f"eq.{job_name}"},
                    payload=job_update_payload(payload, utc_now_iso()),
                    prefer="return=minimal",
                )
                return job_name, None
            except Exception as error:  # Per-job failures must not abort the fleet snapshot.
                return job_name, safe_message(error, secrets)

        workers = min(CONCURRENCY, max(1, expected))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for job_name, error_message in executor.map(collect, jobs):
                if error_message is None:
                    observed += 1
                else:
                    failures.append(f"{job_name}: {error_message}")

        finished_at = utc_now_iso()
        status = "SUCCESS" if not failures else "FAILED" if observed == 0 else "PARTIAL"
        supabase.request_json(
            "pd_sync_runs",
            method="PATCH",
            query={"sync_run_id": f"eq.{sync_run_id}"},
            payload={
                "finished_at": finished_at,
                "status": status,
                "jobs_observed": observed,
                "jobs_failed": len(failures),
                "error_summary": "\n".join(failures[:20])[:4_000] if failures else None,
            },
            prefer="return=minimal",
        )

        elapsed_ms = iso_elapsed_ms(started_at, finished_at)
        print(
            f"Jenkins metadata sync {status}: {observed}/{expected} observed, "
            f"{len(failures)} failed, {elapsed_ms}ms"
        )
        for failure in failures:
            print(failure, file=sys.stderr)
        return 0 if not failures else 1
    except Exception as error:
        finished_at = utc_now_iso()
        try:
            supabase.request_json(
                "pd_sync_runs",
                method="PATCH",
                query={"sync_run_id": f"eq.{sync_run_id}"},
                payload={
                    "finished_at": finished_at,
                    "status": "FAILED",
                    "jobs_observed": observed,
                    "jobs_failed": min(expected - observed, max(0, len(failures))),
                    "error_summary": safe_message(error, secrets)[:4_000],
                },
                prefer="return=minimal",
            )
        except Exception:
            pass
        raise


class SupabaseRestClient:
    def __init__(
        self,
        *,
        base_url: str,
        secret_key: str,
        timeout_seconds: float,
        secrets: tuple[str, ...],
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.secrets = secrets
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {secret_key}",
            "apikey": secret_key,
            "Content-Type": "application/json",
            "X-Client-Info": "polymarket-dashboard-jenkins-collector-python/1.0",
        }

    def request_json(
        self,
        table: str,
        *,
        method: str = "GET",
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.base_url}/rest/v1/{quote(table, safe='')}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read()
        except HTTPError as error:
            detail = error.read(1_000).decode("utf-8", errors="replace")
            raise RuntimeError(
                safe_message(f"Supabase HTTP {error.code}: {detail}", self.secrets)
            ) from None
        except URLError as error:
            raise RuntimeError(f"Supabase request failed: {error.reason}") from None

        if not response_body:
            return None
        return json.loads(response_body)


def fetch_jenkins_job(
    *,
    base_url: str,
    job_name: str,
    timeout_seconds: float,
    user: str | None,
    api_token: str | None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if user and api_token:
        encoded = base64.b64encode(f"{user}:{api_token}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    request = Request(job_api_url(base_url, job_name), headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read())
    except HTTPError as error:
        raise RuntimeError(f"Jenkins HTTP {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"Jenkins request failed: {error.reason}") from None


def job_api_url(base_url: str, job_name: str) -> str:
    job_path = "".join(f"/job/{quote(part, safe='')}" for part in job_name.split("/"))
    return f"{normalize_base_url(base_url)}{job_path}/api/json?{urlencode({'tree': JENKINS_TREE})}"


def job_update_payload(payload: dict[str, Any], observed_at: str) -> dict[str, Any]:
    last_build = payload.get("lastBuild") or payload.get("lastCompletedBuild") or {}
    color = string_or_none(payload.get("color"))
    buildable = boolean_or_none(payload.get("buildable"))
    building = boolean_or_none(last_build.get("building"))
    return {
        "buildable": buildable,
        "enabled": buildable is not False and not (color or "").lower().startswith("disabled"),
        "in_queue": boolean_or_none(payload.get("inQueue")),
        "building": building,
        "job_color": color,
        "last_build_number": integer_or_none(last_build.get("number")),
        "last_build_status": "BUILDING" if building else string_or_none(last_build.get("result")),
        "last_build_started_at": epoch_to_iso(last_build.get("timestamp")),
        "last_build_duration_ms": non_negative_integer_or_none(last_build.get("duration")),
        "observed_at": observed_at,
    }


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL은 http(s) 형식이어야 합니다.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def required_env(name: str) -> str:
    value = optional_env(name)
    if not value:
        raise RuntimeError(f"{name} 환경변수가 필요합니다.")
    return value


def optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def positive_integer(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise ValueError("JENKINS_REQUEST_TIMEOUT_MS는 양의 정수여야 합니다.") from error
    if parsed <= 0:
        raise ValueError("JENKINS_REQUEST_TIMEOUT_MS는 양의 정수여야 합니다.")
    return parsed


def boolean_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def non_negative_integer_or_none(value: Any) -> int | None:
    parsed = integer_or_none(value)
    return parsed if parsed is not None and parsed >= 0 else None


def epoch_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1_000, timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_elapsed_ms(started_at: str, finished_at: str) -> int:
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return round((finished - started).total_seconds() * 1_000)


def safe_message(error: object, secrets: tuple[str, ...] = ()) -> str:
    message = SECRET_PATTERN.sub("[REDACTED]", str(error))
    for secret in secrets:
        message = message.replace(secret, "[REDACTED]")
    return message[:1_000]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Jenkins metadata sync failed: {safe_message(error)}", file=sys.stderr)
        raise SystemExit(1) from None

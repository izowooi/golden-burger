"""Owner-private, cross-strategy cache for public Gamma keyset sweeps.

Several independent strategy jobs intentionally archive the same broad Gamma
universe before applying their own signal rules.  On one Jenkins host that can
turn one public-data request into ten identical 300+ page sweeps.  This cache
shares only requests whose complete filter identity is equal; strategy entry
logic and SQLite evidence remain independent.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Optional
from uuid import uuid4


SHARED_CACHE_ENV = "POLYBOT_GAMMA_SHARED_CACHE_DIR"
SHARED_CACHE_SCHEMA_VERSION = 1
SHARED_CACHE_BUCKET_SECONDS = 300
SHARED_CACHE_LOCK_TIMEOUT_SECONDS = 1_200.0
SHARED_CACHE_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_JENKINS_CACHE = Path(".cache/polybot/gamma-sweeps-v1")


def _membership_digest(memberships: list[dict]) -> str:
    qualified = sorted(
        (item for item in memberships if item.get("qualified") is True),
        key=lambda item: item["condition_id"],
    )
    encoded = json.dumps(
        qualified,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_payload(
    payload: object,
    *,
    cache_key: str,
    bucket: int,
    filters: dict,
) -> tuple[list[dict], dict]:
    if not isinstance(payload, dict):
        raise ValueError("Gamma shared cache payload must be an object")
    if payload.get("schema_version") != SHARED_CACHE_SCHEMA_VERSION:
        raise ValueError("Gamma shared cache schema mismatch")
    if payload.get("cache_key") != cache_key or payload.get("bucket") != bucket:
        raise ValueError("Gamma shared cache identity mismatch")
    if payload.get("filters") != filters:
        raise ValueError("Gamma shared cache filters mismatch")

    markets = payload.get("markets")
    attestation = payload.get("attestation")
    if not isinstance(markets, list) or any(
        not isinstance(market, dict) for market in markets
    ):
        raise ValueError("Gamma shared cache markets are invalid")
    if not isinstance(attestation, dict):
        raise ValueError("Gamma shared cache attestation is invalid")
    if attestation.get("cursor_complete") is not True:
        raise ValueError("Gamma shared cache is not cursor-complete")

    memberships = attestation.get("memberships")
    if not isinstance(memberships, list) or any(
        not isinstance(item, dict) for item in memberships
    ):
        raise ValueError("Gamma shared cache memberships are invalid")
    if attestation.get("membership_digest_sha256") != _membership_digest(memberships):
        raise ValueError("Gamma shared cache membership digest mismatch")

    qualified_ids = {
        str(item.get("condition_id") or "")
        for item in memberships
        if item.get("qualified") is True
    }
    market_ids = [str(market.get("conditionId") or "") for market in markets]
    if (
        "" in qualified_ids
        or "" in market_ids
        or len(market_ids) != len(set(market_ids))
        or set(market_ids) != qualified_ids
    ):
        raise ValueError("Gamma shared cache market membership mismatch")
    if int(attestation.get("qualified_market_count", -1)) != len(markets):
        raise ValueError("Gamma shared cache qualified count mismatch")
    return markets, attestation


class GammaSweepCache:
    """Share a cursor-complete public Gamma sweep for one five-minute bucket."""

    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def from_environment(cls) -> Optional["GammaSweepCache"]:
        configured = os.getenv(SHARED_CACHE_ENV)
        if configured is None:
            if not os.getenv("JENKINS_URL"):
                return None
            candidate = Path.home() / DEFAULT_JENKINS_CACHE
        else:
            raw = configured.strip()
            if not raw or raw.casefold() in {"off", "disabled", "none"}:
                return None
            candidate = Path(raw).expanduser()

        if not candidate.is_absolute():
            raise ValueError(f"{SHARED_CACHE_ENV} must be an absolute path")
        if candidate.is_symlink():
            raise RuntimeError("Gamma shared cache directory must not be a symlink")
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = candidate.stat()
        if metadata.st_uid != os.getuid():
            raise RuntimeError("Gamma shared cache directory is not owned by this user")
        os.chmod(candidate, 0o700)
        return cls(candidate)

    @staticmethod
    def _identity(filters: dict, bucket: int) -> tuple[str, str]:
        encoded = json.dumps(
            filters, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        filter_digest = hashlib.sha256(encoded).hexdigest()
        return f"sweep-{bucket}-{filter_digest[:24]}", filter_digest[:24]

    @staticmethod
    def _acquire_lock(path: Path):
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+")
        deadline = time.monotonic() + SHARED_CACHE_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError("Gamma shared cache lock timed out")
                time.sleep(0.25)

    @staticmethod
    def _read(path: Path, *, cache_key: str, bucket: int, filters: dict):
        if not path.is_file():
            return None
        if path.is_symlink() or path.stat().st_size > SHARED_CACHE_MAX_BYTES:
            raise ValueError("Gamma shared cache file is unsafe or too large")
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        return _validate_payload(
            payload, cache_key=cache_key, bucket=bucket, filters=filters
        )

    @staticmethod
    def _write(
        path: Path,
        *,
        cache_key: str,
        bucket: int,
        filters: dict,
        markets: list[dict],
        attestation: dict,
    ) -> None:
        payload = {
            "schema_version": SHARED_CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "bucket": bucket,
            "filters": filters,
            "markets": markets,
            "attestation": attestation,
        }
        temporary_name: Optional[str] = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{cache_key}-",
                suffix=".tmp",
            )
            os.fchmod(descriptor, 0o600)
            os.close(descriptor)
            with gzip.open(temporary_name, mode="wt", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass

    def get_or_create(
        self,
        *,
        filters: dict,
        producer: Callable[[], tuple[list[dict], dict]],
    ) -> tuple[list[dict], dict, bool]:
        bucket = int(time.time()) // SHARED_CACHE_BUCKET_SECONDS
        cache_key, filter_digest = self._identity(filters, bucket)
        cache_path = self.root / f"{cache_key}.json.gz"
        lock_path = self.root / f"sweep-filter-{filter_digest}.lock"
        lock = self._acquire_lock(lock_path)
        try:
            try:
                cached = self._read(
                    cache_path,
                    cache_key=cache_key,
                    bucket=bucket,
                    filters=filters,
                )
            except (OSError, ValueError, json.JSONDecodeError):
                try:
                    cache_path.unlink()
                except FileNotFoundError:
                    pass
                cached = None

            if cached is not None:
                markets, source_attestation = cached
                attestation = dict(source_attestation)
                source_sweep_id = str(
                    attestation.get("source_sweep_id")
                    or attestation.get("sweep_id")
                    or ""
                )
                if not source_sweep_id:
                    raise ValueError("Gamma shared cache source sweep is missing")
                attestation.update(
                    {
                        "sweep_id": str(uuid4()),
                        "source_sweep_id": source_sweep_id,
                        "shared_cache_hit": True,
                        "shared_cache_bucket": bucket,
                    }
                )
                return markets, attestation, True

            markets, attestation = producer()
            _validate_payload(
                {
                    "schema_version": SHARED_CACHE_SCHEMA_VERSION,
                    "cache_key": cache_key,
                    "bucket": bucket,
                    "filters": filters,
                    "markets": markets,
                    "attestation": attestation,
                },
                cache_key=cache_key,
                bucket=bucket,
                filters=filters,
            )
            source_sweep_id = str(attestation.get("sweep_id") or "")
            if not source_sweep_id:
                raise ValueError("Gamma source sweep ID is missing")
            attestation.update(
                {
                    "source_sweep_id": source_sweep_id,
                    "shared_cache_hit": False,
                    "shared_cache_bucket": bucket,
                }
            )
            self._write(
                cache_path,
                cache_key=cache_key,
                bucket=bucket,
                filters=filters,
                markets=markets,
                attestation=attestation,
            )
            for old_path in self.root.glob(f"sweep-*-{filter_digest}.json.gz"):
                if old_path != cache_path and not old_path.is_symlink():
                    old_path.unlink()
            return markets, attestation, False
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

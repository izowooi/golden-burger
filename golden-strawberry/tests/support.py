from __future__ import annotations

import gzip
import hashlib
from uuid import uuid4

from polybot.utils.retry import canonical_json


def api_receipt(repository, *, run_id: str, request_id: str, kind: str, raw: bytes):
    repository.record_api_request(
        {
            "request_id": request_id,
            "run_id": run_id,
            "request_kind": kind,
            "page_number": 1,
            "attempt_number": 1,
            "method": "GET" if kind.startswith("gamma") else "POST",
            "url": "https://example.test/public",
            "params_json": "{}",
            "body_sha256": None,
            "request_hash": hashlib.sha256(request_id.encode()).hexdigest(),
            "started_at": "2026-08-15T02:00:00Z",
            "completed_at": "2026-08-15T02:00:01Z",
            "elapsed_ms": 1000.0,
            "status": "SUCCESS",
            "http_status": 200,
            "retryable": 0,
            "retry_after_seconds": None,
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_bytes": len(raw),
            "error_type": None,
            "error_message": None,
        }
    )


def minimal_bundle(config, repository, *, run_id="run-1", cycle_number=1):
    request_id = f"gamma-{cycle_number}"
    raw = b'{"markets":[]}'
    api_receipt(
        repository,
        run_id=run_id,
        request_id=request_id,
        kind="gamma_markets_keyset",
        raw=raw,
    )
    compressed_raw = gzip.compress(raw, mtime=0)
    membership_raw = canonical_json([]).encode()
    membership_blob = gzip.compress(membership_raw, mtime=0)
    sweep_id = f"sweep-{cycle_number}"
    now = f"2026-08-15T02:{(cycle_number - 1) * 10:02d}:01Z"
    payload_id = f"raw-{request_id}"
    return {
        "sweep": {
            "sweep_id": sweep_id,
            "run_id": run_id,
            "cycle_number": cycle_number,
            "config_hash": config.config_hash,
            "strategy_source_digest": config.trading.strategy_source_digest,
            "data_contract": "last-mile-v1",
            "started_at": now,
            "completed_at": now,
            "published_at": now,
            "cursor_complete": 1,
            "page_count": 1,
            "membership_count": 0,
            "unique_condition_count": 0,
            "aligned_outcome_count": 0,
            "tradable_market_count": 0,
            "membership_sha256": hashlib.sha256(membership_raw).hexdigest(),
            "request_lineage_sha256": "lineage",
        },
        "membership": {
            "membership_id": uuid4().hex,
            "sweep_id": sweep_id,
            "encoding": "gzip-json-v1",
            "membership_sha256": hashlib.sha256(membership_raw).hexdigest(),
            "uncompressed_bytes": len(membership_raw),
            "compressed_bytes": len(membership_blob),
            "membership_blob": membership_blob,
            "recorded_at": now,
        },
        "raw_payloads": [
            {
                "payload_id": payload_id,
                "run_id": run_id,
                "request_id": request_id,
                "payload_kind": "gamma_markets_keyset_page",
                "source_received_at": now,
                "content_encoding": "gzip",
                "payload_sha256": hashlib.sha256(raw).hexdigest(),
                "uncompressed_bytes": len(raw),
                "compressed_bytes": len(compressed_raw),
                "payload_blob": compressed_raw,
                "recorded_at": now,
            }
        ],
        "pages": [
            {
                "page_id": uuid4().hex,
                "sweep_id": sweep_id,
                "run_id": run_id,
                "page_number": 1,
                "cursor_in": None,
                "cursor_out": None,
                "market_count": 0,
                "request_id": request_id,
                "raw_payload_id": payload_id,
                "request_hash": hashlib.sha256(request_id.encode()).hexdigest(),
                "source_received_at": now,
                "response_sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
        "catalog": [],
        "outcomes": [],
        "crossing_decisions": [],
        "clob_attempts": [],
        "clob_snapshots": [],
        "clob_levels": [],
        "episodes": [],
        "paths": [],
        "threshold_events": [],
        "resolutions": [],
        "quality_issues": [],
        "cycle_stats": {
            "cycle_stat_id": uuid4().hex,
            "run_id": run_id,
            "sweep_id": sweep_id,
            "cycle_number": cycle_number,
            "started_at": now,
            "completed_at": now,
            "runtime_seconds": 1.0,
            "page_count": 1,
            "membership_count": 0,
            "crossing_count": 0,
            "executable_episode_count": 0,
            "clob_requested_count": 0,
            "path_observation_count": 0,
            "resolution_observation_count": 0,
            "stats_json": "{}",
        },
        "latest_states": [],
    }

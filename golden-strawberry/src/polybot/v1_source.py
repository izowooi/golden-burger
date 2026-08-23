"""Read-only deterministic seed reader for the frozen Last Mile v1 database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote

from .followup_config import V1SourceConfig
from .utils.retry import canonical_json, iso_utc


_REQUIRED_TABLES = frozenset(
    {
        "schema_metadata",
        "experiment_contracts",
        "research_config_versions",
        "research_run_events",
        "market_sweeps",
        "hypothetical_episodes",
        "episode_path_observations",
        "episode_threshold_events",
        "resolution_observations",
    }
)
_EPISODE_FIELDS = (
    "episode_id",
    "decision_id",
    "originating_sweep_id",
    "run_id",
    "condition_id",
    "market_id",
    "event_id",
    "event_cluster_id",
    "token_id",
    "outcome_index",
    "outcome_label",
    "outcome_type",
    "neg_risk",
    "sports_classification",
    "metadata_observation_id",
    "metadata_status",
    "entry_threshold",
    "crossing_prior_probability",
    "crossing_probability",
    "crossing_gap_minutes",
    "interval_censored",
    "entry_observed_at",
    "entry_status",
    "entry_censor_reason",
    "entry_snapshot_id",
    "entry_notional_usdc",
    "entry_ask_vwap",
    "fixed_shares",
    "best_ask",
    "spread",
    "ask_depth_notional",
    "source_tick_size",
    "source_min_order_size",
    "source_fee_rate_bps",
    "liquidity",
    "volume_total",
    "volume_24h",
    "end_date",
    "category",
    "tags_json",
    "created_at",
)
_THRESHOLD_FIELDS = (
    "threshold_event_id",
    "episode_id",
    "path_observation_id",
    "sweep_id",
    "event_kind",
    "threshold",
    "observed_at",
    "executable_bid_vwap",
    "prior_executable_bid_vwap",
    "interval_censored",
    "conservative_priority",
)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _chunks(values: Sequence[str], size: int = 400) -> Iterator[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm")
    )


def _stat_payload(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("v1 source timestamp is missing an explicit timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class V1SeedSnapshot:
    anchor: Mapping[str, Any]
    episodes: tuple[Mapping[str, Any], ...]
    condition_statuses: tuple[Mapping[str, Any], ...]
    threshold_events: tuple[Mapping[str, Any], ...]

    @property
    def anchor_sha256(self) -> str:
        return str(self.anchor["anchor_sha256"])


class V1SourceReader:
    """Capture one stable v1 source snapshot without ever opening it writable."""

    def __init__(self, config: V1SourceConfig) -> None:
        self.config = config
        self.path = config.db_path

    def _assert_path(self) -> Path:
        if self.path.is_symlink() or not self.path.is_file():
            raise RuntimeError("pinned v1 source DB is absent or unsafe")
        resolved = self.path.resolve(strict=True)
        if resolved != self.path:
            raise RuntimeError("pinned v1 source DB canonical path changed")
        if self.config.require_no_sidecars:
            present = [str(path) for path in _sidecars(self.path) if path.exists()]
            if present:
                raise RuntimeError("v1 source has SQLite sidecars: " + ", ".join(present))
        return resolved

    def _connect(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{quote(str(path))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _schema_hash(connection: sqlite3.Connection) -> tuple[str, set[str]]:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT type,name,tbl_name,sql FROM sqlite_master
                WHERE sql IS NOT NULL AND type IN ('table','index','trigger')
                ORDER BY type,name
                """
            )
        ]
        tables = {str(row["name"]) for row in rows if row["type"] == "table"}
        return _sha256_json(rows), tables

    @staticmethod
    def _latest_paths(
        connection: sqlite3.Connection, episode_ids: Sequence[str]
    ) -> dict[str, Mapping[str, Any]]:
        latest: dict[str, Mapping[str, Any]] = {}
        for chunk in _chunks(episode_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT p.* FROM episode_path_observations p
                JOIN market_sweeps s ON s.sweep_id=p.sweep_id
                WHERE p.path_status='EXECUTABLE'
                  AND p.episode_id IN ({placeholders})
                  AND EXISTS (
                    SELECT 1 FROM research_run_events e
                    WHERE e.run_id=s.run_id AND e.event_type='SUCCEEDED'
                  )
                ORDER BY p.episode_id,p.observed_at DESC,p.path_observation_id DESC
                """,
                tuple(chunk),
            )
            for row in rows:
                episode_id = str(row["episode_id"])
                latest.setdefault(episode_id, dict(row))
        return latest

    @staticmethod
    def _threshold_rows(
        connection: sqlite3.Connection, episode_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for chunk in _chunks(episode_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT t.* FROM episode_threshold_events t
                JOIN market_sweeps s ON s.sweep_id=t.sweep_id
                WHERE t.episode_id IN ({placeholders})
                  AND EXISTS (
                    SELECT 1 FROM research_run_events e
                    WHERE e.run_id=s.run_id AND e.event_type='SUCCEEDED'
                  )
                ORDER BY t.episode_id,t.event_kind,t.threshold,t.observed_at,
                         t.threshold_event_id
                """,
                tuple(chunk),
            )
            for row in rows:
                result.append({field: row[field] for field in _THRESHOLD_FIELDS})
        return result

    @staticmethod
    def _resolved_conditions(
        connection: sqlite3.Connection, condition_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(condition_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT r.* FROM resolution_observations r
                JOIN market_sweeps s ON s.sweep_id=r.sweep_id
                WHERE r.condition_id IN ({placeholders})
                  AND r.resolution_status='RESOLVED'
                  AND EXISTS (
                    SELECT 1 FROM research_run_events e
                    WHERE e.run_id=s.run_id AND e.event_type='SUCCEEDED'
                  )
                ORDER BY r.condition_id,r.observed_at DESC,
                         r.resolution_observation_id DESC
                """,
                tuple(chunk),
            )
            for row in rows:
                condition_id = str(row["condition_id"])
                if condition_id in result:
                    continue
                payouts = json.loads(str(row["token_payouts_json"]))
                if (
                    not isinstance(payouts, dict)
                    or not payouts
                    or any(value not in {0, 1} for value in payouts.values())
                    or sum(int(value) for value in payouts.values()) != 1
                    or str(row["winning_token_id"]) not in payouts
                    or payouts[str(row["winning_token_id"])] != 1
                ):
                    raise RuntimeError(
                        f"v1 terminal payout is not unique one-hot: {condition_id}"
                    )
                result[condition_id] = {
                    "condition_id": condition_id,
                    "terminal_at_handoff": 1,
                    "source_resolution_observation_id": row[
                        "resolution_observation_id"
                    ],
                    "source_sweep_id": row["sweep_id"],
                    "source_run_id": row["run_id"],
                    "observed_at": row["observed_at"],
                    "winning_outcome_index": row["winning_outcome_index"],
                    "winning_outcome_label": row["winning_outcome_label"],
                    "winning_token_id": row["winning_token_id"],
                    "token_payouts_json": canonical_json(payouts),
                    "raw_market_sha256": row["raw_market_sha256"],
                }
        return result

    def capture(self) -> V1SeedSnapshot:
        source = self._assert_path()
        before = _stat_payload(source)
        with self._connect(source) as connection:
            schema_sha256, tables = self._schema_hash(connection)
            missing = sorted(_REQUIRED_TABLES - tables)
            if missing:
                raise RuntimeError("v1 source schema is missing: " + ", ".join(missing))
            metadata = dict(
                connection.execute("SELECT key,value FROM schema_metadata")
            )
            if metadata.get("schema_version") != str(
                self.config.expected_schema_version
            ):
                raise RuntimeError("v1 source schema version changed")
            if metadata.get("data_contract") != self.config.expected_data_contract:
                raise RuntimeError("v1 source data contract changed")
            contract_rows = connection.execute(
                "SELECT * FROM experiment_contracts"
            ).fetchall()
            if len(contract_rows) != 1:
                raise RuntimeError("v1 source must contain exactly one experiment contract")
            contract = dict(contract_rows[0])
            if contract["job_name"] != self.config.expected_job_name:
                raise RuntimeError("v1 source runtime job changed")
            if contract["data_contract"] != self.config.expected_data_contract:
                raise RuntimeError("v1 experiment data contract changed")
            expected_clocks = {
                "entry_start": self.config.expected_entry_start_utc,
                "entry_end": self.config.expected_entry_end_utc,
                "followup_end": self.config.expected_followup_end_utc,
            }
            changed_clocks = [
                key
                for key, expected in expected_clocks.items()
                if _parse_utc(str(contract[key])) != expected
            ]
            if changed_clocks:
                raise RuntimeError(
                    "v1 experiment window changed: " + ", ".join(changed_clocks)
                )

            latest_success = connection.execute(
                """
                WITH terminal AS (
                    SELECT run_id,MAX(event_at) AS succeeded_at
                    FROM research_run_events WHERE event_type='SUCCEEDED'
                    GROUP BY run_id
                )
                SELECT s.*,t.succeeded_at,
                       c.strategy_source_digest AS config_strategy_source_digest,
                       c.config_json,
                       c.job_name AS config_job_name,c.data_contract AS config_contract
                FROM market_sweeps s
                JOIN terminal t ON t.run_id=s.run_id
                JOIN research_config_versions c ON c.config_hash=s.config_hash
                ORDER BY s.cycle_number DESC LIMIT 1
                """
            ).fetchone()
            if latest_success is None:
                raise RuntimeError("v1 source has no successful sweep")
            sweep = dict(latest_success)
            latest_published_cycle = int(
                connection.execute("SELECT MAX(cycle_number) FROM market_sweeps").fetchone()[
                    0
                ]
            )
            if latest_published_cycle != int(sweep["cycle_number"]):
                raise RuntimeError(
                    "v1 source contains a published sweep after the last successful run"
                )
            if sweep["config_job_name"] != self.config.expected_job_name:
                raise RuntimeError("v1 source config runtime job changed")
            if sweep["config_contract"] != self.config.expected_data_contract:
                raise RuntimeError("v1 source config data contract changed")
            if sweep["strategy_source_digest"] != sweep[
                "config_strategy_source_digest"
            ]:
                raise RuntimeError("v1 sweep/config source digest mismatch")
            if _parse_utc(str(sweep["succeeded_at"])) < (
                self.config.minimum_successful_cutoff_utc
            ):
                raise RuntimeError("v1 source cutoff predates the frozen entry-window end")
            if _parse_utc(str(sweep["completed_at"])) < (
                self.config.minimum_successful_cutoff_utc
            ):
                raise RuntimeError(
                    "v1 successful sweep completed before the frozen entry-window end"
                )

            fields = ",".join(f"e.{field}" for field in _EPISODE_FIELDS)
            episode_rows = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT {fields} FROM hypothetical_episodes e
                    JOIN market_sweeps s ON s.sweep_id=e.originating_sweep_id
                    WHERE e.entry_status='EXECUTABLE'
                      AND e.fixed_shares IS NOT NULL AND e.fixed_shares>0
                      AND EXISTS (
                        SELECT 1 FROM research_run_events r
                        WHERE r.run_id=s.run_id AND r.event_type='SUCCEEDED'
                      )
                    ORDER BY e.episode_id
                    """
                )
            ]
            if not episode_rows:
                raise RuntimeError("v1 source has no executable episodes to follow")
            episode_ids = [str(row["episode_id"]) for row in episode_rows]
            latest_paths = self._latest_paths(connection, episode_ids)
            episodes: list[dict[str, Any]] = []
            for row in episode_rows:
                path = latest_paths.get(str(row["episode_id"]))
                row.update(
                    {
                        "source_last_path_observation_id": (
                            path["path_observation_id"] if path else None
                        ),
                        "source_last_path_observed_at": (
                            path["observed_at"] if path else None
                        ),
                        "source_last_executable_bid_vwap": (
                            path["exit_bid_vwap"] if path else None
                        ),
                    }
                )
                row["seed_row_sha256"] = _sha256_json(row)
                episodes.append(row)

            threshold_events = self._threshold_rows(connection, episode_ids)
            for row in threshold_events:
                row["seed_row_sha256"] = _sha256_json(row)
            condition_ids = sorted({str(row["condition_id"]) for row in episodes})
            resolved = self._resolved_conditions(connection, condition_ids)
            condition_statuses: list[dict[str, Any]] = []
            for condition_id in condition_ids:
                row = resolved.get(
                    condition_id,
                    {
                        "condition_id": condition_id,
                        "terminal_at_handoff": 0,
                        "source_resolution_observation_id": None,
                        "source_sweep_id": None,
                        "source_run_id": None,
                        "observed_at": None,
                        "winning_outcome_index": None,
                        "winning_outcome_label": None,
                        "winning_token_id": None,
                        "token_payouts_json": "{}",
                        "raw_market_sha256": None,
                    },
                )
                row["seed_row_sha256"] = _sha256_json(row)
                condition_statuses.append(row)

            relevant_counts = {
                "market_sweeps": int(
                    connection.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[
                        0
                    ]
                ),
                "executable_episodes": len(episodes),
                "episode_threshold_events": len(threshold_events),
                "terminal_conditions": sum(
                    int(row["terminal_at_handoff"]) for row in condition_statuses
                ),
                "imported_conditions": len(condition_statuses),
            }

        after = _stat_payload(source)
        if before != after:
            raise RuntimeError("v1 source changed while the seed snapshot was read")
        if self.config.require_no_sidecars and any(
            path.exists() for path in _sidecars(source)
        ):
            raise RuntimeError("v1 source created a SQLite sidecar during read")
        file_fingerprint = _sha256_json({"path": str(source), **after})
        episodes_sha256 = _sha256_json(episodes)
        conditions_sha256 = _sha256_json(condition_statuses)
        thresholds_sha256 = _sha256_json(threshold_events)
        anchor_core = {
            "source_path": str(source),
            "source_file_fingerprint_sha256": file_fingerprint,
            "source_db_size_bytes": after["size_bytes"],
            "source_db_mtime_ns": after["mtime_ns"],
            "source_schema_version": int(metadata["schema_version"]),
            "source_schema_sha256": schema_sha256,
            "source_data_contract": metadata["data_contract"],
            "source_job_name": contract["job_name"],
            "source_entry_start": contract["entry_start"],
            "source_entry_end": contract["entry_end"],
            "source_followup_end": contract["followup_end"],
            "source_sweep_id": sweep["sweep_id"],
            "source_cycle_number": int(sweep["cycle_number"]),
            "source_sweep_completed_at": sweep["completed_at"],
            "source_successful_at": sweep["succeeded_at"],
            "source_config_hash": sweep["config_hash"],
            "source_strategy_digest": sweep["strategy_source_digest"],
            "source_counts_json": canonical_json(relevant_counts),
            "episode_seed_sha256": episodes_sha256,
            "condition_seed_sha256": conditions_sha256,
            "threshold_seed_sha256": thresholds_sha256,
            "executable_episode_count": len(episodes),
            "condition_count": len(condition_statuses),
            "terminal_condition_count": relevant_counts["terminal_conditions"],
            "threshold_event_count": len(threshold_events),
        }
        anchor = {
            **anchor_core,
            "anchor_sha256": _sha256_json(anchor_core),
            "captured_at": iso_utc(),
        }
        return V1SeedSnapshot(
            anchor=anchor,
            episodes=tuple(episodes),
            condition_statuses=tuple(condition_statuses),
            threshold_events=tuple(threshold_events),
        )


def compare_anchor(
    stored: Mapping[str, Any],
    observed: Mapping[str, Any],
    *,
    include_file_fingerprint: bool = True,
) -> None:
    keys = {
        "source_schema_version",
        "source_schema_sha256",
        "source_data_contract",
        "source_job_name",
        "source_entry_start",
        "source_entry_end",
        "source_followup_end",
        "source_sweep_id",
        "source_cycle_number",
        "source_sweep_completed_at",
        "source_successful_at",
        "source_config_hash",
        "source_strategy_digest",
        "source_counts_json",
        "episode_seed_sha256",
        "condition_seed_sha256",
        "threshold_seed_sha256",
        "executable_episode_count",
        "condition_count",
        "terminal_condition_count",
        "threshold_event_count",
    }
    if include_file_fingerprint:
        keys |= {
            "source_path",
            "source_file_fingerprint_sha256",
            "source_db_size_bytes",
            "source_db_mtime_ns",
            "anchor_sha256",
        }
    changed = sorted(
        key for key in keys if str(stored.get(key)) != str(observed.get(key))
    )
    if changed:
        raise RuntimeError("v1 source anchor drift: " + ", ".join(changed))


__all__ = ["V1SeedSnapshot", "V1SourceReader", "compare_anchor"]

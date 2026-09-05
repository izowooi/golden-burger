"""Compact unresolved-episode collector for Last Mile follow-up v2a."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import time
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from .api.clob_client import BookCollection, ClobBookClient
from .api.gamma_client import GammaClient, ResolutionLookup
from .collector import _resolution_result, normalize_book, walk_bids
from .db.followup_repository import FollowupRepository, PUBLICATION_CACHE_KIB
from .followup_config import FollowupConfig
from .followup_run_audit import FollowupRunAudit
from .utils.retry import (
    CooperativeDeadline,
    CycleDeadlineExceeded,
    PublicJsonTransport,
    canonical_json,
    iso_utc,
)


BOOK_ENCODING = "gzip-json-v1"
BOOK_SCHEMA = "last-mile-compact-book-v1"


def _decimal_text(value: Any, label: str) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} is not a decimal") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    normalized = format(parsed.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _canonical_side(value: Any, *, reverse: bool, label: str) -> list[list[str]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    rows: list[tuple[Decimal, Decimal]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} level must be an object")
        try:
            price = Decimal(str(item.get("price")))
            size = Decimal(str(item.get("size")))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"{label} level is not decimal") from error
        if (
            not price.is_finite()
            or not size.is_finite()
            or price <= 0
            or price > 1
            or size <= 0
        ):
            raise ValueError(f"{label} level has invalid price or size")
        rows.append((price, size))
    rows.sort(key=lambda row: row[0], reverse=reverse)
    return [
        [_decimal_text(price, "price"), _decimal_text(size, "size")]
        for price, size in rows
    ]


def encode_compact_book(token_id: str, book: Mapping[str, Any]) -> dict[str, Any]:
    """Encode every source level once in a deterministic gzip token blob."""

    source_token = str(book.get("asset_id") or token_id)
    if source_token != token_id:
        raise ValueError("book asset_id does not match requested token")
    bids = _canonical_side(book.get("bids"), reverse=True, label="bids")
    asks = _canonical_side(book.get("asks"), reverse=False, label="asks")
    metadata = {
        str(key): value
        for key, value in book.items()
        if key not in {"bids", "asks", "asset_id"}
    }
    payload = {
        "schema": BOOK_SCHEMA,
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
        "source_metadata": metadata,
    }
    raw = canonical_json(payload).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    return {
        "payload": payload,
        "raw": raw,
        "blob": compressed,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "bid_level_count": len(bids),
        "ask_level_count": len(asks),
    }


def decode_compact_book(blob: bytes, *, expected_sha256: str | None = None) -> dict:
    raw = gzip.decompress(blob)
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("compact book SHA-256 mismatch")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema") != BOOK_SCHEMA:
        raise ValueError("compact book schema changed")
    if canonical_json(payload).encode("utf-8") != raw:
        raise ValueError("compact book is not canonical JSON")
    for side in ("bids", "asks"):
        levels = payload.get(side)
        if not isinstance(levels, list) or any(
            not isinstance(level, list) or len(level) != 2 for level in levels
        ):
            raise ValueError("compact book side is malformed")
    return payload


@dataclass(frozen=True)
class PhaseRecord:
    name: str
    started_at: str
    completed_at: str
    elapsed_seconds: float
    details: Mapping[str, Any]


class FollowupCollector:
    def __init__(
        self,
        config: FollowupConfig,
        *,
        repository: FollowupRepository,
        clob_client: ClobBookClient | None = None,
        gamma_client: GammaClient | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.config = config
        self.repository = repository
        self.monotonic = monotonic
        if clob_client is None or gamma_client is None:
            gamma = config.trading.gamma
            transport = PublicJsonTransport(
                connect_timeout_seconds=gamma.connect_timeout_seconds,
                read_timeout_seconds=gamma.read_timeout_seconds,
                max_retries=gamma.max_retries,
                retry_base_seconds=gamma.retry_base_seconds,
                retry_max_seconds=gamma.retry_max_seconds,
                receipt_sink=repository.record_api_request,
                monotonic=monotonic,
            )
            clob_client = clob_client or ClobBookClient(
                config.trading.orderbook, transport
            )
            gamma_client = gamma_client or GammaClient(gamma, transport)
        self.clob_client = clob_client
        self.gamma_client = gamma_client
        self._phases: list[PhaseRecord] = []
        self._deadline: CooperativeDeadline | None = None

    @contextmanager
    def _phase(
        self, name: str, details: Mapping[str, Any] | None = None
    ) -> Iterator[None]:
        if self._deadline is not None:
            self._deadline.check(f"{name} phase")
        started_at = iso_utc()
        started = self.monotonic()
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            self._phases.append(
                PhaseRecord(
                    name=name,
                    started_at=started_at,
                    completed_at=iso_utc(),
                    elapsed_seconds=max(0.0, self.monotonic() - started),
                    details=dict(details or {}),
                )
            )
        if succeeded and self._deadline is not None:
            self._deadline.check(f"{name} phase completion")

    @staticmethod
    def _response_sha_by_request(books: BookCollection) -> dict[str, str]:
        return {
            str(row.request_id): str(row.response_sha256)
            for row in books.raw_payloads
        }

    @staticmethod
    def _resolution_blob(lookup: ResolutionLookup) -> dict[str, Any]:
        if lookup.market is None:
            return {
                "raw_market_sha256": None,
                "encoding": None,
                "uncompressed_bytes": None,
                "compressed_bytes": None,
                "market_blob": None,
            }
        raw = canonical_json(dict(lookup.market)).encode("utf-8")
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        return {
            "raw_market_sha256": hashlib.sha256(raw).hexdigest(),
            "encoding": "gzip-json-v1",
            "uncompressed_bytes": len(raw),
            "compressed_bytes": len(compressed),
            "market_blob": compressed,
        }

    def run_cycle(
        self,
        run_id: str,
        *,
        anchor: Mapping[str, Any],
        audit: FollowupRunAudit,
        deadline: CooperativeDeadline,
        validation_mode: str,
        seed_integrity: Mapping[str, Any] | None = None,
        initial_phases: Sequence[PhaseRecord] = (),
    ) -> dict[str, Any]:
        if validation_mode not in {"FULL_SEED", "PINNED_FAST"}:
            raise ValueError("follow-up validation mode is invalid")
        self._deadline = deadline
        self._phases = list(initial_phases)
        cycle_started_at = iso_utc()
        total_started = self.monotonic()
        initial_elapsed_seconds = sum(
            phase.elapsed_seconds for phase in initial_phases
        )
        cycle_number = self.repository.next_cycle_number()
        cycle_id = uuid4().hex

        with self._phase("load_unresolved"):
            episodes = self.repository.unresolved_episodes(deadline=deadline)
            episode_ids = [str(row["episode_id"]) for row in episodes]
            tokens = sorted({str(row["token_id"]) for row in episodes})
            conditions = sorted({str(row["condition_id"]) for row in episodes})

        with self._phase("clob_books", {"token_count": len(tokens)}):
            books = self.clob_client.fetch_books(
                run_id, tokens, deadline=deadline
            )
        response_sha = self._response_sha_by_request(books)
        normalized = {}
        compact_rows: list[dict[str, Any]] = []
        attempt_rows: list[dict[str, Any]] = []
        book_ids: dict[str, str] = {}
        normalization_errors: dict[str, str] = {}
        with self._phase("normalize_compact_books", {"token_count": len(tokens)}):
            for token in tokens:
                attempt = books.attempts[token]
                status = attempt.status
                book = books.books.get(token)
                encoded = None
                normalized_book = None
                if status == "OBSERVED" and book is not None:
                    try:
                        if attempt.request_id is None or attempt.received_at is None:
                            raise ValueError("observed book lacks request lineage")
                        normalized_book = normalize_book(
                            token,
                            book,
                            request_id=attempt.request_id,
                            observed_at=attempt.received_at,
                        )
                        encoded = encode_compact_book(token, book)
                    except (TypeError, ValueError) as error:
                        status = "MALFORMED"
                        normalization_errors[token] = str(error)[:500]
                attempt_rows.append(
                    {
                        "attempt_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "token_id": token,
                        "status": status,
                        "request_id": attempt.request_id,
                        "request_started_at": attempt.request_started_at,
                        "received_at": attempt.received_at,
                        "error_type": (
                            "BookNormalizationError"
                            if token in normalization_errors
                            else attempt.error_type
                        ),
                        "error_message": normalization_errors.get(
                            token, attempt.error_message
                        ),
                    }
                )
                if status != "OBSERVED" or normalized_book is None or encoded is None:
                    continue
                if attempt.request_id not in response_sha:
                    raise RuntimeError("observed book is missing response SHA lineage")
                book_id = uuid4().hex
                book_ids[token] = book_id
                normalized[token] = normalized_book
                compact_rows.append(
                    {
                        "book_id": book_id,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "token_id": token,
                        "request_id": attempt.request_id,
                        "source_received_at": attempt.received_at,
                        "source_response_sha256": response_sha[attempt.request_id],
                        "encoding": BOOK_ENCODING,
                        "book_sha256": encoded["sha256"],
                        "uncompressed_bytes": encoded["uncompressed_bytes"],
                        "compressed_bytes": encoded["compressed_bytes"],
                        "book_blob": encoded["blob"],
                        "bid_level_count": encoded["bid_level_count"],
                        "ask_level_count": encoded["ask_level_count"],
                        "best_bid": normalized_book.best_bid,
                        "best_ask": normalized_book.best_ask,
                        "bid_depth_notional": normalized_book.bid_depth_notional,
                        "ask_depth_notional": normalized_book.ask_depth_notional,
                        "source_timestamp": normalized_book.source_timestamp,
                        "tick_size": normalized_book.tick_size,
                        "min_order_size": normalized_book.min_order_size,
                        "fee_rate_bps": normalized_book.fee_rate_bps,
                    }
                )

        with self._phase("fixed_share_paths", {"episode_count": len(episodes)}):
            prior_vwaps = self.repository.latest_path_vwaps(episode_ids, deadline=deadline)
            path_rows: list[dict[str, Any]] = []
            for episode in episodes:
                episode_id = str(episode["episode_id"])
                token = str(episode["token_id"])
                book = normalized.get(token)
                attempt = books.attempts[token]
                if book is None:
                    status = "CENSORED"
                    censor_reason = (
                        "MALFORMED_BOOK"
                        if token in normalization_errors
                        else attempt.status
                    )
                    observed_at = attempt.received_at or iso_utc()
                    bid_walk = {
                        "vwap": None,
                        "proceeds": None,
                        "covered_shares": 0.0,
                    }
                else:
                    bid_walk = walk_bids(book.bids, float(episode["fixed_shares"]))
                    status = str(bid_walk["status"])
                    censor_reason = None if status == "EXECUTABLE" else status
                    observed_at = book.observed_at
                path_rows.append(
                    {
                        "path_observation_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "episode_id": episode_id,
                        "book_id": book_ids.get(token),
                        "observed_at": observed_at,
                        "path_status": status,
                        "censor_reason": censor_reason,
                        "fixed_shares": episode["fixed_shares"],
                        "best_bid": book.best_bid if book else None,
                        "exit_bid_vwap": bid_walk.get("vwap"),
                        "exit_proceeds_usdc": bid_walk.get("proceeds"),
                        "covered_shares": bid_walk.get("covered_shares"),
                        "bid_depth_notional": (
                            book.bid_depth_notional if book else None
                        ),
                        "prior_executable_bid_vwap": prior_vwaps.get(episode_id),
                        "interval_censored": 1,
                        "details_json": canonical_json(
                            {
                                "displayed_book_counterfactual": True,
                                "fixed_shares": episode["fixed_shares"],
                                "shared_token_book": True,
                                "row_per_level": False,
                            }
                        ),
                    }
                )

        with self._phase("threshold_transitions"):
            existing_keys = self.repository.threshold_event_keys(episode_ids, deadline=deadline)
            threshold_rows: list[dict[str, Any]] = []
            for path in path_rows:
                if path["path_status"] != "EXECUTABLE" or path["exit_bid_vwap"] is None:
                    continue
                episode_id = str(path["episode_id"])
                value = float(path["exit_bid_vwap"])
                for kind, thresholds in (
                    ("STOP", self.config.trading.experiment.stop_thresholds),
                    ("TARGET", self.config.trading.experiment.target_thresholds),
                ):
                    for threshold in thresholds:
                        key = (episode_id, kind, float(threshold))
                        observed = value <= threshold if kind == "STOP" else value >= threshold
                        if not observed or key in existing_keys:
                            continue
                        threshold_rows.append(
                            {
                                "threshold_event_id": uuid4().hex,
                                "cycle_id": cycle_id,
                                "episode_id": episode_id,
                                "path_observation_id": path["path_observation_id"],
                                "event_kind": kind,
                                "threshold": threshold,
                                "observed_at": path["observed_at"],
                                "executable_bid_vwap": value,
                                "prior_executable_bid_vwap": path[
                                    "prior_executable_bid_vwap"
                                ],
                                "interval_censored": 1,
                                "conservative_priority": 0 if kind == "STOP" else 1,
                            }
                        )
                        existing_keys.add(key)

        with self._phase("gamma_resolutions", {"condition_count": len(conditions)}):
            lookups = self.gamma_client.fetch_resolutions(
                run_id, conditions, deadline=deadline
            )
        episodes_by_condition: dict[str, list[Mapping[str, Any]]] = {}
        for episode in episodes:
            episodes_by_condition.setdefault(str(episode["condition_id"]), []).append(
                episode
            )
        resolution_rows: list[dict[str, Any]] = []
        newly_resolved = 0
        with self._phase("normalize_resolutions"):
            for lookup in lookups:
                parsed = _resolution_result(lookup)
                expected_tokens = {
                    str(row["token_id"])
                    for row in episodes_by_condition.get(lookup.condition_id, ())
                }
                if parsed["resolution_status"] == "RESOLVED" and not expected_tokens.issubset(
                    parsed["token_payouts"]
                ):
                    parsed = {
                        **parsed,
                        "resolution_status": "MALFORMED",
                        "winning_outcome_index": None,
                        "winning_outcome_label": None,
                        "winning_token_id": None,
                        "token_payouts": {},
                    }
                if parsed["resolution_status"] == "RESOLVED":
                    newly_resolved += 1
                jumps: dict[str, list[float]] = {}
                if parsed["resolution_status"] == "RESOLVED":
                    for episode in episodes_by_condition.get(lookup.condition_id, ()):
                        if parsed["token_payouts"].get(str(episode["token_id"])) != 1:
                            continue
                        missing = [
                            threshold
                            for threshold in self.config.trading.experiment.target_thresholds
                            if (
                                str(episode["episode_id"]),
                                "TARGET",
                                float(threshold),
                            )
                            not in existing_keys
                        ]
                        if missing:
                            jumps[str(episode["episode_id"])] = missing
                blob = self._resolution_blob(lookup)
                resolution_rows.append(
                    {
                        "resolution_observation_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "condition_id": lookup.condition_id,
                        "requested_at": lookup.requested_at,
                        "observed_at": lookup.observed_at,
                        "lookup_status": lookup.lookup_status,
                        "resolution_status": parsed["resolution_status"],
                        "request_id": lookup.request_id,
                        "raw_market_sha256": blob["raw_market_sha256"],
                        "encoding": blob["encoding"],
                        "uncompressed_bytes": blob["uncompressed_bytes"],
                        "compressed_bytes": blob["compressed_bytes"],
                        "market_blob": blob["market_blob"],
                        "winning_outcome_index": parsed["winning_outcome_index"],
                        "winning_outcome_label": parsed["winning_outcome_label"],
                        "winning_token_id": parsed["winning_token_id"],
                        "token_payouts_json": canonical_json(parsed["token_payouts"]),
                        "resolution_jump_without_target_json": canonical_json(jumps),
                        "error_type": lookup.error_type,
                        "error_message": lookup.error_message,
                    }
                )

        quality_rows: list[dict[str, Any]] = []
        censored_books = sum(row["status"] != "OBSERVED" for row in attempt_rows)
        if censored_books:
            quality_rows.append(
                {
                    "issue_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "severity": "WARN",
                    "issue_code": "FOLLOWUP_BOOK_CENSORING",
                    "recorded_at": iso_utc(),
                    "details_json": canonical_json({"token_count": censored_books}),
                }
            )
        resolution_errors = sum(
            row["resolution_status"] in {"ERROR", "MALFORMED"}
            for row in resolution_rows
        )
        if resolution_errors:
            quality_rows.append(
                {
                    "issue_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "severity": "WARN",
                    "issue_code": "FOLLOWUP_RESOLUTION_CENSORING",
                    "recorded_at": iso_utc(),
                    "details_json": canonical_json(
                        {"condition_count": resolution_errors}
                    ),
                }
            )

        prepublication_seconds = max(0.0, self.monotonic() - total_started)
        completed_at = iso_utc()
        summary_base = {
            "cycle_number": cycle_number,
            "anchor_sha256": anchor["anchor_sha256"],
            "validation_mode": validation_mode,
            "unresolved_episodes": len(episodes),
            "distinct_tokens": len(tokens),
            "distinct_conditions": len(conditions),
            "books_observed": len(compact_rows),
            "paths": len(path_rows),
            "resolutions": len(resolution_rows),
            "newly_resolved_conditions": newly_resolved,
            "prepublication_seconds": round(prepublication_seconds, 6),
        }
        bundle = {
            "expected_tokens": tokens,
            "expected_conditions": conditions,
            "expected_episode_ids": episode_ids,
            "cycle": {
                "cycle_id": cycle_id,
                "run_id": run_id,
                "cycle_number": cycle_number,
                "config_hash": self.config.config_hash,
                "strategy_source_digest": self.config.trading.strategy_source_digest,
                "anchor_id": "v1-seed",
                "anchor_sha256": anchor["anchor_sha256"],
                "validation_mode": validation_mode,
                "started_at": cycle_started_at,
                "completed_at": completed_at,
                "published_at": iso_utc(),
                "unresolved_episode_count": len(episodes),
                "distinct_token_count": len(tokens),
                "distinct_condition_count": len(conditions),
                "book_observed_count": len(compact_rows),
                "path_observation_count": len(path_rows),
                "resolution_observation_count": len(resolution_rows),
                "newly_resolved_condition_count": newly_resolved,
                "prepublication_seconds": prepublication_seconds,
                "summary_json": canonical_json(summary_base),
            },
            "book_attempts": attempt_rows,
            "compact_books": compact_rows,
            "paths": path_rows,
            "threshold_events": threshold_rows,
            "resolutions": resolution_rows,
            "quality_issues": quality_rows,
        }
        deadline.check("atomic successful publication")
        publication_started_at = iso_utc()

        def finalize(
            publication_seconds: float,
            storage_row: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            deadline.check("atomic successful publication finalization")
            collector_seconds = max(0.0, self.monotonic() - total_started)
            total_seconds = initial_elapsed_seconds + collector_seconds
            runtime = self.config.trading.runtime
            if (
                validation_mode == "PINNED_FAST"
                and total_seconds >= runtime.pinned_fast_hard_sla_seconds
            ):
                raise CycleDeadlineExceeded(
                    "PINNED_FAST cycle reached the 480-second hard SLA before commit"
                )
            if (
                validation_mode == "FULL_SEED"
                and total_seconds >= runtime.full_seed_budget_seconds
            ):
                raise CycleDeadlineExceeded(
                    "FULL_SEED cycle reached the 1800-second maintenance budget "
                    "before commit"
                )
            phases = list(self._phases)
            phases.append(
                PhaseRecord(
                    name="atomic_publication",
                    started_at=publication_started_at,
                    completed_at=iso_utc(),
                    elapsed_seconds=publication_seconds,
                    details={
                        "transaction_boundary": (
                            "cycle_evidence+phase_timings+storage+SUCCEEDED"
                        ),
                        "sqlite_cache_kib": PUBLICATION_CACHE_KIB,
                        "sqlite_write_deadline_enforced": True,
                    },
                )
            )
            phase_rows = [
                {
                    "phase_timing_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "phase_name": phase.name,
                    "started_at": phase.started_at,
                    "completed_at": phase.completed_at,
                    "elapsed_seconds": phase.elapsed_seconds,
                    "details_json": canonical_json(dict(phase.details)),
                }
                for phase in phases
            ]
            phase_rows.append(
                {
                    "phase_timing_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "phase_name": "total",
                    "started_at": (
                        initial_phases[0].started_at
                        if initial_phases
                        else cycle_started_at
                    ),
                    "completed_at": iso_utc(),
                    "elapsed_seconds": total_seconds,
                    "details_json": canonical_json(
                        {
                            "collector_seconds": collector_seconds,
                            "initial_phase_seconds": initial_elapsed_seconds,
                            "measurement": (
                                "v1_anchor_start_to_success_transaction_precommit"
                            ),
                            "validation_mode": validation_mode,
                            "network_cycle_deadline_seconds": (
                                runtime.network_cycle_deadline_seconds
                            ),
                            "pinned_fast_hard_sla_seconds": (
                                runtime.pinned_fast_hard_sla_seconds
                            ),
                            "full_seed_budget_seconds": (
                                runtime.full_seed_budget_seconds
                            ),
                        }
                    ),
                }
            )
            storage_summary = {
                "db_bytes": storage_row["db_bytes"],
                "journal_bytes": storage_row["journal_bytes"],
                "filesystem_free_bytes": storage_row["filesystem_free_bytes"],
                "filesystem_used_ratio": storage_row["filesystem_used_ratio"],
                "guard_state": storage_row["guard_state"],
                "measurement_phase": storage_row["phase"],
            }
            summary = {
                **summary_base,
                "seed_integrity": dict(seed_integrity or {}),
                "total_seconds": round(total_seconds, 6),
                "network_cycle_deadline_seconds": (
                    runtime.network_cycle_deadline_seconds
                ),
                "runtime_sla_seconds": runtime.pinned_fast_hard_sla_seconds,
                "runtime_sla_met": (
                    total_seconds < runtime.pinned_fast_hard_sla_seconds
                    if validation_mode == "PINNED_FAST"
                    else None
                ),
                "full_seed_budget_seconds": runtime.full_seed_budget_seconds,
                "storage": storage_summary,
                "phase_seconds": {
                    row["phase_name"]: round(float(row["elapsed_seconds"]), 6)
                    for row in phase_rows
                },
            }
            return {
                "summary": summary,
                "phase_timings": phase_rows,
                "terminal_event": audit.success_event_row(summary),
            }

        summary = self.repository.publish_successful_cycle(
            bundle,
            storage=self.config.trading.storage,
            finalize=finalize,
            deadline=deadline,
            monotonic=self.monotonic,
        )
        audit.mark_succeeded()
        return summary


__all__ = [
    "BOOK_ENCODING",
    "BOOK_SCHEMA",
    "FollowupCollector",
    "PhaseRecord",
    "decode_compact_book",
    "encode_compact_book",
]

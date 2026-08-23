from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from polybot.api.clob_client import BookAttempt, BookCollection, RawBookPayload
from polybot.api.gamma_client import ResolutionLookup
from polybot.collector import ResearchCollector
from polybot.db.followup_repository import FollowupRepository
from polybot.db.repository import ResearchRepository
from polybot.followup_collector import FollowupCollector, PhaseRecord
from polybot.followup_run_audit import FollowupRunAudit
from polybot.run_audit import ResearchRunAudit
from polybot.utils.retry import iso_utc
from tests.integration_support import FakeBooks, FakeGamma, FakeSampling
from tests.support import api_receipt


def build_v1_handoff(config, *, cycles: int = 2):
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    sampling = FakeSampling(
        repository,
        probabilities=(0.89, 0.98, 0.99),
        sweep_completed_times=(
            "2026-08-22T03:50:01Z",
            "2026-08-22T04:00:01Z",
            "2026-08-22T04:10:01Z",
        ),
    )
    collector = ResearchCollector(
        config,
        repository=repository,
        sampling_client=sampling,
        gamma_client=FakeGamma(repository, sampling),
        clob_client=FakeBooks(repository),
    )
    summaries = []
    for _ in range(cycles):
        audit = ResearchRunAudit.start(config, repository=repository)
        summary = collector.run_cycle(audit.run_id)
        audit.succeed(summary)
        summaries.append(summary)
    return repository, collector, sampling, summaries


class FollowupBooks:
    def __init__(self, repository: FollowupRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[str, ...]] = []
        self.counter = 0

    def fetch_books(self, run_id: str, token_ids: list[str]) -> BookCollection:
        tokens = tuple(token_ids)
        self.calls.append(tokens)
        if not tokens:
            return BookCollection(books={}, attempts={}, raw_payloads=())
        self.counter += 1
        request_id = f"followup-books-{self.counter}"
        books = {
            token: {
                "asset_id": token,
                "bids": [
                    {"price": "0.84", "size": "20"},
                    {"price": "0.83", "size": "10"},
                ],
                "asks": [
                    {"price": "0.86", "size": "20"},
                    {"price": "0.87", "size": "10"},
                ],
                "tick_size": "0.01",
                "min_order_size": "1",
                "fee_rate_bps": "20",
                "timestamp": iso_utc(),
            }
            for token in tokens
        }
        raw = json.dumps(list(books.values()), sort_keys=True).encode()
        api_receipt(
            self.repository,
            run_id=run_id,
            request_id=request_id,
            kind="clob_books",
            raw=raw,
        )
        received_at = iso_utc()
        return BookCollection(
            books=books,
            attempts={
                token: BookAttempt(
                    token_id=token,
                    status="OBSERVED",
                    request_id=request_id,
                    request_started_at=received_at,
                    received_at=received_at,
                )
                for token in tokens
            },
            raw_payloads=(
                RawBookPayload(
                    request_id=request_id,
                    received_at=received_at,
                    response_sha256=hashlib.sha256(raw).hexdigest(),
                    raw=raw,
                ),
            ),
        )


class FollowupGamma:
    def __init__(self, repository: FollowupRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[str, ...]] = []
        self.counter = 0

    def fetch_resolutions(
        self, run_id: str, condition_ids: Sequence[str]
    ) -> list[ResolutionLookup]:
        conditions = tuple(condition_ids)
        self.calls.append(conditions)
        if not conditions:
            return []
        self.counter += 1
        resolved = self.counter >= 2
        rows = []
        for condition in conditions:
            market = (
                {
                    "conditionId": condition,
                    "closed": True,
                    "outcomes": ["YES", "NO"],
                    "clobTokenIds": ["token-yes", "token-no"],
                    "outcomePrices": [1, 0],
                }
                if resolved
                else None
            )
            raw = json.dumps([market] if market else [], sort_keys=True).encode()
            request_id = f"followup-resolution-{self.counter}-{condition}"
            api_receipt(
                self.repository,
                run_id=run_id,
                request_id=request_id,
                kind="gamma_resolution_lookup",
                raw=raw,
            )
            observed_at = iso_utc()
            rows.append(
                ResolutionLookup(
                    condition_id=condition,
                    lookup_status="OBSERVED" if market else "MISSING",
                    requested_at=observed_at,
                    observed_at=observed_at,
                    request_id=request_id,
                    response_sha256=hashlib.sha256(raw).hexdigest(),
                    raw=raw,
                    market=market,
                )
            )
        return rows


@dataclass
class FollowupEvidence:
    repository: FollowupRepository
    collector: FollowupCollector
    books: FollowupBooks
    gamma: FollowupGamma
    summaries: list[dict]


def build_followup_evidence(config, snapshot, *, cycles: int = 1) -> FollowupEvidence:
    repository = FollowupRepository(config.db_path)
    repository.initialize(config)
    repository.ensure_seed(snapshot)
    books = FollowupBooks(repository)
    gamma = FollowupGamma(repository)
    collector = FollowupCollector(
        config,
        repository=repository,
        clob_client=books,
        gamma_client=gamma,
    )
    summaries = []
    for _ in range(cycles):
        audit = FollowupRunAudit.start(
            config,
            repository=repository,
            anchor_sha256=snapshot.anchor_sha256,
        )
        summary = collector.run_cycle(
            audit.run_id,
            anchor=snapshot.anchor,
            initial_phases=(
                PhaseRecord(
                    name="v1_anchor_validation",
                    started_at=iso_utc(),
                    completed_at=iso_utc(),
                    elapsed_seconds=0.01,
                    details={},
                ),
            ),
        )
        audit.succeed(summary)
        summaries.append(summary)
    return FollowupEvidence(repository, collector, books, gamma, summaries)

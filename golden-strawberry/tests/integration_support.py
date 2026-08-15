from __future__ import annotations

import hashlib
import json

from polybot.api.clob_client import BookAttempt, BookCollection, RawBookPayload
from polybot.api.gamma_client import ResolutionLookup
from polybot.api.sampling_client import SamplingPage, SamplingSweep
from polybot.collector import ResearchCollector
from polybot.db.repository import ResearchRepository
from tests.support import api_receipt


TIMES = (
    "2026-08-15T04:00:01Z",
    "2026-08-15T04:10:01Z",
    "2026-08-15T04:20:01Z",
)


def market(probability: float):
    return {
        "condition_id": "condition",
        "question_id": "question",
        "market_slug": "market",
        "question": "Will YES occur?",
        "active": True,
        "closed": False,
        "enable_order_book": True,
        "accepting_orders": True,
        "tokens": [
            {"outcome": "YES", "token_id": "token-yes", "price": probability},
            {"outcome": "NO", "token_id": "token-no", "price": 1 - probability},
        ],
        "neg_risk": False,
        "tags": ["Politics", "Elections"],
        "end_date_iso": "2026-08-16T00:00:00Z",
    }


class FakeSampling:
    def __init__(self, repository):
        self.repository = repository
        self.cycle = 0

    def collect_market_sweep(self, run_id):
        self.cycle += 1
        probability = (0.94, 0.96, 0.99)[self.cycle - 1]
        payload = {"data": [market(probability)], "next_cursor": "LTE="}
        raw = json.dumps(payload, sort_keys=True).encode()
        request_id = f"sampling-{self.cycle}"
        api_receipt(
            self.repository,
            run_id=run_id,
            request_id=request_id,
            kind="clob_sampling_markets",
            raw=raw,
        )
        page = SamplingPage(
            page_number=1,
            cursor_in=None,
            cursor_out=None,
            request_id=request_id,
            request_hash=f"request-hash-{self.cycle}",
            received_at=TIMES[self.cycle - 1],
            response_sha256=hashlib.sha256(raw).hexdigest(),
            raw=raw,
            markets=(market(probability),),
        )
        return SamplingSweep(
            started_at=TIMES[self.cycle - 1],
            completed_at=TIMES[self.cycle - 1],
            pages=(page,),
            cursor_complete=True,
        )


class FakeGamma:
    def __init__(self, repository, sampling):
        self.repository = repository
        self.sampling = sampling

    @property
    def cycle(self):
        return self.sampling.cycle

    def fetch_metadata(self, run_id, condition_ids):
        if not condition_ids:
            return []
        request_id = f"metadata-{self.cycle}"
        market_row = {
            "id": "market",
            "conditionId": "condition",
            "eventId": "event-cluster",
            "liquidity": 50_000,
            "volume": 200_000,
            "volume24hr": 25_000,
            "category": "Politics",
            "tags": [{"label": "elections"}],
            "endDate": "2026-08-16T00:00:00Z",
        }
        raw = json.dumps([market_row], sort_keys=True).encode()
        api_receipt(
            self.repository,
            run_id=run_id,
            request_id=request_id,
            kind="gamma_candidate_metadata",
            raw=raw,
        )
        return [
            ResolutionLookup(
                condition_id="condition",
                lookup_status="OBSERVED",
                requested_at=TIMES[self.cycle - 1],
                observed_at=TIMES[self.cycle - 1],
                request_id=request_id,
                response_sha256=hashlib.sha256(raw).hexdigest(),
                raw=raw,
                market=market_row,
            )
        ]

    def fetch_resolutions(self, run_id, condition_ids):
        if not condition_ids:
            return []
        request_id = f"resolution-{self.cycle}"
        if self.cycle < 3:
            payload = []
            market_row = None
            status = "MISSING"
        else:
            market_row = {
                "conditionId": "condition",
                "closed": True,
                "outcomes": ["YES", "NO"],
                "clobTokenIds": ["token-yes", "token-no"],
                "outcomePrices": [1, 0],
            }
            payload = [market_row]
            status = "OBSERVED"
        raw = json.dumps(payload, sort_keys=True).encode()
        api_receipt(
            self.repository,
            run_id=run_id,
            request_id=request_id,
            kind="gamma_resolution_lookup",
            raw=raw,
        )
        return [
            ResolutionLookup(
                condition_id="condition",
                lookup_status=status,
                requested_at=TIMES[self.cycle - 1],
                observed_at=TIMES[self.cycle - 1],
                request_id=request_id,
                response_sha256=hashlib.sha256(raw).hexdigest(),
                raw=raw,
                market=market_row,
            )
        ]


class FakeBooks:
    def __init__(self, repository):
        self.repository = repository
        self.cycle = 0

    def fetch_books(self, run_id, token_ids):
        self.cycle += 1
        if not token_ids:
            return BookCollection(books={}, attempts={}, raw_payloads=())
        request_id = f"books-{self.cycle}"
        bid = "0.94" if self.cycle == 2 else "0.84"
        ask = "0.96" if self.cycle == 2 else "0.99"
        book = {
            "asset_id": "token-yes",
            "bids": [{"price": bid, "size": "20"}],
            "asks": [{"price": ask, "size": "20"}],
            "tick_size": "0.01",
            "min_order_size": "1",
            "fee_rate_bps": "20",
        }
        payload = [book]
        raw = json.dumps(payload, sort_keys=True).encode()
        api_receipt(
            self.repository,
            run_id=run_id,
            request_id=request_id,
            kind="clob_books",
            raw=raw,
        )
        return BookCollection(
            books={"token-yes": book},
            attempts={
                "token-yes": BookAttempt(
                    token_id="token-yes",
                    status="OBSERVED",
                    request_id=request_id,
                    request_started_at=TIMES[self.cycle - 1],
                    received_at=TIMES[self.cycle - 1],
                )
            },
            raw_payloads=(
                RawBookPayload(
                    request_id=request_id,
                    received_at=TIMES[self.cycle - 1],
                    response_sha256=hashlib.sha256(raw).hexdigest(),
                    raw=raw,
                ),
            ),
        )


def build_three_cycle_evidence(config):
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    repository.register_config(config, git_commit=None)
    sampling = FakeSampling(repository)
    collector = ResearchCollector(
        config,
        repository=repository,
        sampling_client=sampling,
        gamma_client=FakeGamma(repository, sampling),
        clob_client=FakeBooks(repository),
    )
    summaries = [collector.run_cycle(f"run-{number}") for number in (1, 2, 3)]
    return repository, summaries

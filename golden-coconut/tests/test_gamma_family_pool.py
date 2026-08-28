from __future__ import annotations

from threading import Barrier, Lock, get_ident
import time

import pytest

from polybot.api.gamma_client import EventFollowup, EventSweep, GammaFamilyPool
from polybot.api.transport import CycleBudget


class BarrierGammaClient:
    def __init__(self, barrier: Barrier, thread_ids: set[int], lock: Lock) -> None:
        self.barrier = barrier
        self.thread_ids = thread_ids
        self.lock = lock

    def fetch_family_events(self, run_id, family, *, budget, slot_start):
        del run_id, budget
        with self.lock:
            self.thread_ids.add(get_ident())
        self.barrier.wait(timeout=2)
        return EventSweep(
            family=family.code,
            tag_id=family.tag_id,
            pages=(),
            cursor_complete=True,
            terminal_cursor=None,
            start_time_min=slot_start,
            start_time_max=slot_start,
        )

    def fetch_event(self, run_id, event_id, family, *, budget):
        del run_id, budget
        with self.lock:
            self.thread_ids.add(get_ident())
        self.barrier.wait(timeout=2)
        return EventFollowup(
            str(event_id),
            f"followup-{family}-{event_id}",
            "2026-08-27T17:30:00Z",
            "a" * 64,
            b"{}",
            {"id": str(event_id)},
        )


def test_five_family_pool_starts_isolated_clients_concurrently_in_frozen_order(config):
    families = config.registry.families
    barrier = Barrier(len(families))
    thread_ids: set[int] = set()
    lock = Lock()
    pool = GammaFamilyPool(
        {
            family.code: BarrierGammaClient(barrier, thread_ids, lock)
            for family in families
        },
        max_workers=5,
    )

    results = pool.fetch_families_events(
        "run",
        families,
        budget=CycleBudget(time.monotonic()),
        slot_start="2026-08-27T17:30:00Z",
    )

    assert tuple(result.family.code for result in results) == (
        "soccer",
        "mlb",
        "nba",
        "nfl",
        "nhl",
    )
    assert tuple(result.sweep.family for result in results) == tuple(
        family.code for family in families
    )
    assert len(thread_ids) == 5


def test_family_pool_rejects_missing_client_before_network(config):
    families = config.registry.families
    pool = GammaFamilyPool({}, max_workers=5)
    with pytest.raises(ValueError, match="clients differ"):
        pool.fetch_families_events(
            "run",
            families,
            budget=CycleBudget(time.monotonic()),
            slot_start="2026-08-27T17:30:00Z",
        )


def test_followups_run_concurrently_by_family_and_restore_request_order(config):
    families = config.registry.families
    barrier = Barrier(len(families))
    thread_ids: set[int] = set()
    lock = Lock()
    pool = GammaFamilyPool(
        {
            family.code: BarrierGammaClient(barrier, thread_ids, lock)
            for family in families
        },
        max_workers=len(families),
    )
    requests = tuple(
        (family.code, str(910000 + index))
        for index, family in enumerate(reversed(families), start=1)
    )

    attempts = pool.fetch_events(
        "run",
        requests,
        budget=CycleBudget(time.monotonic()),
    )

    assert tuple((row.family, row.event_id) for row in attempts) == requests
    assert all(row.followup is not None for row in attempts)
    assert len(thread_ids) == len(families)


def test_followup_batch_rejects_duplicate_key_before_network(config):
    pool = GammaFamilyPool({}, max_workers=0)
    with pytest.raises(ValueError, match="duplicate"):
        pool.fetch_events(
            "run",
            (("soccer", "910001"), ("soccer", "910001")),
            budget=CycleBudget(time.monotonic()),
        )

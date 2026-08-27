from __future__ import annotations

from threading import Barrier, Lock, get_ident
import time

import pytest

from polybot.api.gamma_client import EventSweep, GammaFamilyPool
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

from __future__ import annotations

import hashlib
import json

from polybot_observability.gamma_sweep_cache import GammaSweepCache


def _evidence(condition_id: str = "condition-1") -> tuple[list[dict], dict]:
    markets = [{"conditionId": condition_id, "active": True}]
    memberships = [
        {
            "condition_id": condition_id,
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        }
    ]
    encoded = json.dumps(
        memberships,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    attestation = {
        "sweep_id": "source-sweep",
        "cursor_complete": True,
        "qualified_market_count": 1,
        "membership_digest_sha256": hashlib.sha256(encoded).hexdigest(),
        "memberships": memberships,
    }
    return markets, attestation


def test_cache_is_disabled_outside_jenkins_without_explicit_path(monkeypatch):
    monkeypatch.delenv("POLYBOT_GAMMA_SHARED_CACHE_DIR", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)

    assert GammaSweepCache.from_environment() is None


def test_complete_sweep_is_reused_with_independent_attestation(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYBOT_GAMMA_SHARED_CACHE_DIR", str(tmp_path))
    calls = []
    filters = {"base_url": "https://gamma.invalid", "min_volume": 0.0}

    first_cache = GammaSweepCache.from_environment()
    assert first_cache is not None
    first = first_cache.get_or_create(
        filters=filters,
        producer=lambda: (calls.append("producer") or _evidence()),
    )

    second_cache = GammaSweepCache.from_environment()
    assert second_cache is not None
    second = second_cache.get_or_create(
        filters=filters,
        producer=lambda: (_ for _ in ()).throw(
            AssertionError("cache hit must not call producer")
        ),
    )

    assert calls == ["producer"]
    assert first[2] is False
    assert second[2] is True
    assert first[0] == second[0]
    assert first[1]["source_sweep_id"] == "source-sweep"
    assert second[1]["source_sweep_id"] == "source-sweep"
    assert second[1]["sweep_id"] != first[1]["sweep_id"]
    assert list(tmp_path.glob("sweep-*.json.gz"))


def test_filter_identity_does_not_cross_contaminate(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYBOT_GAMMA_SHARED_CACHE_DIR", str(tmp_path))
    cache = GammaSweepCache.from_environment()
    assert cache is not None

    one = cache.get_or_create(filters={"min_volume": 0.0}, producer=_evidence)
    two = cache.get_or_create(
        filters={"min_volume": 5_000.0},
        producer=lambda: _evidence("condition-2"),
    )

    assert one[0][0]["conditionId"] == "condition-1"
    assert two[0][0]["conditionId"] == "condition-2"
    assert len(list(tmp_path.glob("sweep-*.json.gz"))) == 2

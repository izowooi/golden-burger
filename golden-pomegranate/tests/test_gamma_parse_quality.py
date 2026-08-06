"""Loss-visible Gamma typed-field normalization."""

from __future__ import annotations

import hashlib
import json

from polybot.api.gamma_client import GammaPage, GammaSweep
from polybot.collector import _market_bundle


def test_typed_parse_failures_preserve_raw_preview_and_reason_per_market():
    attestation = "[]"
    page = GammaPage(
        page_number=1,
        cursor_requested=None,
        next_cursor=None,
        received_at="2026-08-06T00:00:01+00:00",
        request_id="request-1",
        request_hash="request-hash",
        raw_payload_id="raw-page-1",
        markets=(
            {
                "id": "market-1",
                "conditionId": "condition-1",
                "outcomes": "not-json",
                "clobTokenIds": '["yes","no"]',
                "outcomePrices": ["0.5", "not-a-price"],
                "volume": "not-a-volume",
                "active": "true",
                "closed": False,
                "tags": {"not": "an-array"},
            },
        ),
    )
    sweep = GammaSweep(
        sweep_id="sweep-1",
        cycle_number=1,
        started_at="2026-08-06T00:00:00+00:00",
        completed_at="2026-08-06T00:00:02+00:00",
        pages=(page,),
        request_attestation_json=attestation,
        request_attestation_sha256=hashlib.sha256(attestation.encode()).hexdigest(),
    )

    bundle = _market_bundle(sweep, run_id="run-1", cycle_number=1)

    observation = bundle["market_observations"][0]
    quality = json.loads(observation["parse_quality_json"])
    assert quality["outcomes"] == {
        "reason": "invalid_json_array",
        "raw_preview": "not-json",
        "source_type": "str",
    }
    assert quality["outcomePrices[1]"]["reason"] == "invalid_numeric"
    assert quality["outcomePrices[1]"]["raw_preview"] == "not-a-price"
    assert quality["volume"]["reason"] == "invalid_numeric"
    assert quality["active"]["reason"] == "invalid_boolean"
    assert quality["tags"]["reason"] == "invalid_array_type"
    assert observation["volume_total_raw"] == "not-a-volume"
    assert observation["volume_total"] is None
    assert observation["active"] is None
    prices = bundle["outcome_observations"]
    assert [(row["price_raw"], row["price"]) for row in prices] == [
        ("0.5", 0.5),
        ("not-a-price", None),
    ]

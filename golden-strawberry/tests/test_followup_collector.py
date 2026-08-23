from __future__ import annotations

import json

import pytest

from polybot.api.sampling_client import SamplingMarketClient
from polybot.db.followup_repository import FollowupRepository
from polybot.followup_collector import FollowupCollector
from polybot.followup_run_audit import FollowupRunAudit
from polybot.v1_source import V1SourceReader
from tests.followup_support import (
    FollowupBooks,
    FollowupGamma,
    build_followup_evidence,
    build_v1_handoff,
)
from tests.support import api_receipt


def _snapshot(config, followup_config):
    build_v1_handoff(config)
    return V1SourceReader(followup_config.trading.v1_source).capture()


def test_followup_never_invokes_sampling_client(
    config, followup_config, monkeypatch
):
    snapshot = _snapshot(config, followup_config)

    def forbidden(*args, **kwargs):
        raise AssertionError("/sampling-markets must not be invoked by follow-up v2")

    monkeypatch.setattr(SamplingMarketClient, "collect_market_sweep", forbidden)
    evidence = build_followup_evidence(followup_config, snapshot)
    assert evidence.summaries[0]["distinct_tokens"] == 1


def test_runtime_sla_fixture_and_phase_timing_are_end_to_end(
    config, followup_config
):
    snapshot = _snapshot(config, followup_config)
    evidence = build_followup_evidence(followup_config, snapshot)
    summary = evidence.summaries[0]
    assert summary["runtime_sla_met"] is True
    assert summary["total_seconds"] < 480
    assert summary["phase_seconds"]["v1_anchor_validation"] == 0.01
    assert summary["phase_seconds"]["total"] >= 0.01
    assert {
        "load_unresolved",
        "clob_books",
        "normalize_compact_books",
        "fixed_share_paths",
        "threshold_transitions",
        "gamma_resolutions",
        "normalize_resolutions",
        "atomic_publication",
    } <= summary["phase_seconds"].keys()


def test_failed_run_keeps_api_receipt_without_partial_cycle(
    config, followup_config
):
    snapshot = _snapshot(config, followup_config)
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    repository.ensure_seed(snapshot)

    class ReceiptThenFailure(FollowupBooks):
        def fetch_books(self, run_id, token_ids):
            api_receipt(
                self.repository,
                run_id=run_id,
                request_id="durable-failed-request",
                kind="clob_books",
                raw=b"[]",
            )
            raise RuntimeError("injected public API failure")

    collector = FollowupCollector(
        followup_config,
        repository=repository,
        clob_client=ReceiptThenFailure(repository),
        gamma_client=FollowupGamma(repository),
    )
    audit = FollowupRunAudit.start(
        followup_config,
        repository=repository,
        anchor_sha256=snapshot.anchor_sha256,
    )
    with pytest.raises(RuntimeError, match="injected") as caught:
        collector.run_cycle(audit.run_id, anchor=snapshot.anchor)
    audit.fail(caught.value)
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM followup_cycles").fetchone()[0] == 0
        terminal = connection.execute(
            "SELECT event_type FROM research_run_events ORDER BY event_at DESC LIMIT 1"
        ).fetchone()[0]
    assert terminal == "FAILED"

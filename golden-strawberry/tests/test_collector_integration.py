from __future__ import annotations

import json

import pytest

from tests.integration_support import build_three_cycle_evidence


def test_collector_publishes_left_censor_crossing_path_and_resolution(config):
    repository, summaries = build_three_cycle_evidence(config)
    assert [summary["new_executable_episodes"] for summary in summaries] == [0, 1, 1]
    with repository._read_connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0] == 3
        )
        left = connection.execute(
            "SELECT COUNT(*) FROM crossing_decisions WHERE decision_status='LEFT_CENSORED'"
        ).fetchone()[0]
        episodes = connection.execute(
            "SELECT entry_threshold,entry_status,source_fee_rate_bps,source_tick_size "
            "FROM hypothetical_episodes ORDER BY entry_threshold"
        ).fetchall()
        paths = connection.execute(
            "SELECT path_status,entry_cycle_baseline,exit_bid_vwap "
            "FROM episode_path_observations ORDER BY observed_at,episode_id"
        ).fetchall()
        stop = connection.execute(
            "SELECT threshold,executable_bid_vwap FROM episode_threshold_events "
            "WHERE event_kind='STOP' ORDER BY threshold"
        ).fetchall()
        resolution = connection.execute(
            "SELECT * FROM resolution_observations WHERE resolution_status='RESOLVED'"
        ).fetchone()
        cache = connection.execute(
            "SELECT probability FROM latest_outcome_state WHERE token_id='token-yes'"
        ).fetchone()[0]
    assert left == 2  # 0.90 and 0.92 started above; neither became an episode.
    assert [(row[0], row[1]) for row in episodes] == [
        (0.95, "EXECUTABLE"),
        (0.97, "EXECUTABLE"),
    ]
    assert all(row[2] == 20 and row[3] == 0.01 for row in episodes)
    assert any(row[0] == "EXECUTABLE" and row[1] == 0 for row in paths)
    assert [row[0] for row in stop] == [0.85, 0.9]
    assert [row[1] for row in stop] == pytest.approx([0.84, 0.84])
    assert json.loads(resolution["token_payouts_json"]) == {
        "token-no": 0,
        "token-yes": 1,
    }
    jumps = json.loads(resolution["resolution_jump_without_target_json"])
    assert jumps
    assert all(values == [0.98, 0.99] for values in jumps.values())
    assert cache == 0.99
    assert repository.unresolved_episodes() == []


def test_only_crossing_and_unresolved_tokens_receive_books(config):
    repository, summaries = build_three_cycle_evidence(config)
    with repository._read_connect() as connection:
        attempts = connection.execute(
            "SELECT token_id,attempt_role FROM clob_token_attempts ORDER BY sweep_id"
        ).fetchall()
    assert {row["token_id"] for row in attempts} == {"token-yes"}
    assert {row["attempt_role"] for row in attempts} == {"CROSSING", "BOTH"}

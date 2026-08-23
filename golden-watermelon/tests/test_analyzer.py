from __future__ import annotations

import json
import sqlite3

import pytest

from polybot.analyzer import analyze_database, analyze_databases
from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    FROZEN_LEAGUE_IDENTITIES,
    LEAGUE_MAPPING_SHA256,
    SCHEMA_PROFILE,
    UNIVERSE_PROFILE,
    league_registry_payload,
)
from polybot.db.repository import ResearchRepository


def seeded_database(
    tmp_path,
    name: str,
    job: str,
    cadence: int,
    arm: str,
    *,
    source_digest: str = "source-v3a",
    entry_end_utc: str = "2026-08-31T00:00:00Z",
) -> ResearchRepository:
    repository = ResearchRepository(
        tmp_path / name,
        busy_timeout_ms=1000,
        data_contract=DATA_CONTRACT,
        schema_profile=SCHEMA_PROFILE,
        universe_profile=UNIVERSE_PROFILE,
        classifier_version=CLASSIFIER_VERSION,
        league_mapping_sha256=LEAGUE_MAPPING_SHA256,
        league_mapping_json=json.dumps(
            league_registry_payload(), sort_keys=True, separators=(",", ":")
        ),
    )
    repository.record_config(
        {
            "config_hash": f"config-{arm}",
            "strategy_source_digest": source_digest,
            "preregistration_sha256": "pre",
            "job_name": job,
            "mode": "sim",
            "config_json": json.dumps(
                {
                    "trading": {
                        "cadence_minutes": cadence,
                        "cadence_arm": arm,
                        "experiment": {
                            "start_utc": "2026-08-24T00:00:00Z",
                            "entry_end_utc": entry_end_utc,
                        },
                    }
                }
            ),
            "first_seen_at": "2026-08-24T00:00:00Z",
        }
    )
    repository.record_run_event(
        {
            "event_id": f"success-{arm}",
            "run_id": "run",
            "event_type": "SUCCEEDED",
            "observed_at": "2026-08-24T00:01:00Z",
            "config_hash": f"config-{arm}",
            "strategy_source_digest": source_digest,
            "detail_json": "{}",
        }
    )
    return repository


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, object]) -> None:
    columns = tuple(row)
    connection.execute(
        f"INSERT INTO {table}({','.join(columns)}) "
        f"VALUES({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def add_winning_episode(
    repository: ResearchRepository,
    *,
    league_code: str = "epl",
    entered_at: str = "2026-08-26T00:00:00Z",
    include_stop: bool = False,
    classifier_version: str = CLASSIFIER_VERSION,
    mapping_sha256: str = LEAGUE_MAPPING_SHA256,
    episode_key_suffix: str | None = None,
) -> None:
    identity = next(
        identity for identity in FROZEN_LEAGUE_IDENTITIES if identity.code == league_code
    )
    suffix = episode_key_suffix or league_code
    shares = 5 / 0.97
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        config_json = connection.execute(
            "SELECT config_json FROM research_config_versions"
        ).fetchone()[0]
        cadence_arm = json.loads(config_json)["trading"]["cadence_arm"]
        _insert(
            connection,
            "event_observations",
            {
                "event_observation_id": f"event-observation-{suffix}",
                "sweep_id": f"sweep-{suffix}",
                "run_id": "run",
                "source_payload_id": f"payload-{suffix}",
                "page_number": 1,
                "request_id": f"request-{suffix}",
                "observed_at": entered_at,
                "event_id": f"event-{suffix}",
                "event_title": f"Home vs Away {suffix}",
                "event_slug": f"{suffix}-home-away",
                "canonical_event_sha256": "a" * 64,
                "sport_id": str(identity.sport_id),
                "sport_code": identity.code,
                "sport_name": identity.name,
                "sport_primary_tag_id": str(identity.primary_tag_id),
                "sport_series_id": identity.series_id,
                "series_slug": identity.series_slug,
                "tag_ids_json": "[]",
                "tag_slugs_json": "[]",
                "series_ids_json": "[]",
                "series_slugs_json": "[]",
                "team_leagues_json": "[]",
                "sport_json": "{}",
                "tags_json": "[]",
                "series_json": "[]",
                "teams_json": "[]",
                "classifier_version": classifier_version,
                "league_mapping_sha256": mapping_sha256,
                "league_code": identity.code,
                "league_name": identity.name,
                "classification_status": "ACCEPTED",
                "rejection_reason": "ELIGIBLE",
                "classification_evidence_json": "{}",
            },
        )
        _insert(
            connection,
            "hypothetical_episodes",
            {
                "episode_id": f"episode-{suffix}",
                "decision_id": f"decision-{suffix}",
                "event_observation_id": f"event-observation-{suffix}",
                "run_id": "run",
                "condition_id": f"condition-{suffix}",
                "event_id": f"event-{suffix}",
                "event_title": f"Home vs Away {suffix}",
                "question": f"Home vs Away {suffix}",
                "token_id": f"token-{suffix}",
                "outcome_index": 0,
                "outcome_label": "Home",
                "threshold": 0.97,
                "cadence_arm": cadence_arm,
                "match_winner_class": "ALIGNED_TWO_TEAM_MONEYLINE",
                "league_code": identity.code,
                "league_name": identity.name,
                "classifier_version": classifier_version,
                "league_mapping_sha256": mapping_sha256,
                "entry_provenance": "UPWARD_CROSS",
                "entered_at": entered_at,
                "end_date": "2026-08-26T04:00:00Z",
                "game_start_time": "2026-08-25T23:00:00Z",
                "sports_phase": "IN_PLAY_EXPLICIT",
                "liquidity": 100,
                "volume_total": 10,
                "fee_rate": 0.05,
                "entry_best_ask": 0.97,
                "entry_vwap": 0.97,
                "entry_shares": shares,
                "entry_cost": 5,
            },
        )
        _insert(
            connection,
            "resolution_observations",
            {
                "resolution_id": f"resolution-{suffix}",
                "run_id": "resolution-run",
                "condition_id": f"condition-{suffix}",
                "observed_at": "2026-08-26T05:00:00Z",
                "winner_index": 0,
                "request_id": f"resolution-request-{suffix}",
                "raw_market_sha256": "b" * 64,
                "evidence_json": "{}",
            },
        )
        _insert(
            connection,
            "counterfactual_exit_policies",
            {
                "policy_id": f"hold-{suffix}",
                "episode_id": f"episode-{suffix}",
                "created_run_id": "run",
                "policy_key": "HOLD_TO_RESOLUTION",
                "stop_price": None,
                "created_at": entered_at,
            },
        )
        if not include_stop:
            return
        _insert(
            connection,
            "counterfactual_exit_policies",
            {
                "policy_id": f"stop-{suffix}",
                "episode_id": f"episode-{suffix}",
                "created_run_id": "run",
                "policy_key": "STOP_0.80",
                "stop_price": 0.80,
                "created_at": entered_at,
            },
        )
        gross = shares * 0.78
        fee = shares * 0.05 * 0.78 * 0.22
        _insert(
            connection,
            "stop_execution_attempts",
            {
                "attempt_id": f"attempt-{suffix}",
                "policy_id": f"stop-{suffix}",
                "episode_id": f"episode-{suffix}",
                "run_id": "stop-run",
                "snapshot_id": None,
                "observed_at": "2026-08-26T00:35:00Z",
                "stop_price": 0.80,
                "prior_best_bid": 0.93,
                "trigger_best_bid": 0.79,
                "requested_shares": shares,
                "filled_shares": shares,
                "remaining_shares": 0,
                "exit_vwap": 0.78,
                "gross_proceeds": gross,
                "fee_rate": 0.05,
                "estimated_fee": fee,
                "net_proceeds": gross - fee,
                "levels_used": 2,
                "status": "FULL_EXIT",
                "gap_from_stop": 0.02,
                "drop_from_prior": 0.14,
            },
        )
        _insert(
            connection,
            "counterfactual_stop_exits",
            {
                "exit_id": f"exit-{suffix}",
                "policy_id": f"stop-{suffix}",
                "episode_id": f"episode-{suffix}",
                "completed_run_id": "stop-run",
                "completed_attempt_id": f"attempt-{suffix}",
                "first_triggered_at": "2026-08-26T00:35:00Z",
                "completed_at": "2026-08-26T00:35:00Z",
                "stop_price": 0.80,
                "first_trigger_best_bid": 0.79,
                "exit_vwap": 0.78,
                "requested_shares": shares,
                "filled_shares": shares,
                "gross_proceeds": gross,
                "estimated_fee": fee,
                "net_proceeds": gross - fee,
                "attempt_count": 1,
                "gap_from_stop": 0.02,
            },
        )


def test_analyzer_uses_fee_resolution_stop_depth_and_null_macro(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    add_winning_episode(repository, include_stop=True)
    result = analyze_database(repository.path)
    assert result["quick_check"] == "ok"
    assert result["analyzer_contract"] == "soccer-major-league-analyzer-v3a"
    assert result["classifier_version"] == CLASSIFIER_VERSION
    assert result["league_mapping_sha256"] == LEAGUE_MAPPING_SHA256
    assert result["league_coverage"]["episodes"][0]["league_code"] == "epl"
    threshold = result["entry_thresholds"]["0.97"]["all"]
    assert threshold["resolved"] == 1
    assert threshold["wins"] == 1
    assert threshold["event_equal_fee_net_roi_pct"] > 0
    assert threshold["macro_estimable"] is False
    assert set(threshold["missing_leagues"]) == {"bun", "fl1", "lal", "mls"}
    assert threshold["macro_league_equal_fee_net_roi_pct"] is None
    policies = result["stop_policy_comparison"]["0.97"]
    assert policies["HOLD_TO_RESOLUTION"]["event_equal_fee_net_roi_pct"] > 0
    assert policies["STOP_0.80"]["event_equal_fee_net_roi_pct"] < 0
    assert policies["STOP_0.80"]["gap_below_stop_p50"] == pytest.approx(0.02)


def test_macro_estimator_requires_and_equal_weights_all_five_leagues(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    for identity in FROZEN_LEAGUE_IDENTITIES:
        add_winning_episode(repository, league_code=identity.code)
    threshold = analyze_database(repository.path)["entry_thresholds"]["0.97"]["all"]
    assert threshold["macro_estimable"] is True
    assert threshold["missing_leagues"] == []
    assert set(threshold["league_event_equal_fee_net_roi_pct"]) == {
        "epl",
        "bun",
        "fl1",
        "lal",
        "mls",
    }
    assert threshold["macro_league_equal_fee_net_roi_pct"] == pytest.approx(
        threshold["event_equal_fee_net_roi_pct"]
    )
    assert len(threshold["macro_league_equal_fee_net_roi_bootstrap_95ci_pct"]) == 2


def test_analyzer_rejects_row_classifier_or_mapping_drift(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    add_winning_episode(repository, classifier_version="drifted-classifier")
    with pytest.raises(ValueError, match="classifier/mapping contract drift"):
        analyze_database(repository.path)


def test_analyzer_excludes_failed_prior_source_cohort(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    repository.record_run_event(
        {
            "event_id": "old-failure",
            "run_id": "old-run",
            "event_type": "FAILED",
            "observed_at": "2026-08-24T00:00:00Z",
            "config_hash": "old-config",
            "strategy_source_digest": "old-source",
            "detail_json": "{}",
        }
    )
    repository.record_issue(
        run_id="old-run",
        severity="CRITICAL",
        issue_type="GAMMA_CURSOR_INCOMPLETE",
        detail={"pages": 10},
    )
    result = analyze_database(repository.path)
    assert result["cohort_run_count"] == 1
    assert result["run_events"] == {"SUCCEEDED": 1}
    assert result["issues"] == []


def test_multi_database_analyzer_enforces_pair_contract_and_episode_league(tmp_path) -> None:
    white = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v3a",
        5,
        "CONTROL_5M",
    )
    add_winning_episode(white, entered_at="2026-08-26T00:01:00Z")
    add_winning_episode(grey, entered_at="2026-08-26T00:05:00Z")
    result = analyze_databases([white.path, grey.path])
    assert result["analyzer_contract"] == "soccer-major-league-cadence-pair-v3a"
    assert result["pairing"]["matched_episode_keys"] == 1
    assert result["pairing"]["entry_time_delta_seconds_p50"] == 240
    assert result["pairing"]["matched_by_league"]["epl"] == 1


def test_multi_database_analyzer_rejects_source_digest_mismatch(tmp_path) -> None:
    white = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
        source_digest="source-a",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v3a",
        5,
        "CONTROL_5M",
        source_digest="source-b",
    )
    with pytest.raises(ValueError, match="strategy_source_digest mismatch"):
        analyze_databases([white.path, grey.path])


def test_multi_database_analyzer_rejects_non_cadence_config_mismatch(tmp_path) -> None:
    white = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v3a",
        5,
        "CONTROL_5M",
        entry_end_utc="2026-09-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="paired_config_sha256 mismatch"):
        analyze_databases([white.path, grey.path])


def test_multi_database_analyzer_rejects_paired_episode_league_mismatch(tmp_path) -> None:
    white = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v3a",
        5,
        "CONTROL_5M",
    )
    add_winning_episode(white, league_code="epl", episode_key_suffix="pair")
    add_winning_episode(grey, league_code="bun", episode_key_suffix="pair")
    with pytest.raises(ValueError, match="paired episode league mismatch"):
        analyze_databases([white.path, grey.path])

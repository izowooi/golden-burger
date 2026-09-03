from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.analyzer import (
    V3A_PROFILE,
    V3B_PROFILE,
    V3C_PROFILE,
    V3C_NOTIONAL_LADDER_USDC,
    V3D_PROFILE,
    V4A_PROFILE,
    V4B_PROFILE,
    analyze_database,
    analyze_databases,
)
from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    FROZEN_CUP_IDENTITIES,
    FROZEN_DIRECT_SPORT_IDENTITIES,
    FROZEN_LEAGUE_IDENTITIES,
    LATE_ENTRY_MINUTE_FLOORS,
    LEAGUE_MAPPING_SHA256,
    NOTIONAL_LADDER_USDC,
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
    source_digest: str = "source-v4b",
    entry_end_utc: str = "2026-08-31T00:00:00Z",
    profile=V4B_PROFILE,
) -> ResearchRepository:
    registry = league_registry_payload(
        profile.identities, profile.cup_identities, profile.direct_identities
    )
    if not profile.cup_identities:
        registry.pop("uefa_competitions", None)
    if not profile.direct_identities:
        registry.pop("direct_sports", None)
        registry.pop("sport_family_tag_ids", None)
    repository = ResearchRepository(
        tmp_path / name,
        busy_timeout_ms=1000,
        data_contract=profile.data_contract,
        schema_profile=profile.schema_profile,
        universe_profile=profile.universe_profile,
        classifier_version=profile.classifier_version,
        league_mapping_sha256=profile.league_mapping_sha256,
        league_mapping_json=json.dumps(
            registry,
            sort_keys=True,
            separators=(",", ":"),
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
                            "notional_ladder_usdc": list(
                                V3C_NOTIONAL_LADDER_USDC
                                if profile is V3C_PROFILE
                                else NOTIONAL_LADDER_USDC
                            ),
                            "late_entry_minute_floors": list(
                                LATE_ENTRY_MINUTE_FLOORS
                            ),
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
    if profile is not V4B_PROFILE:
        migration = (
            Path(__file__).resolve().parents[1]
            / "src" / "polybot" / "db" / "migrations"
            / profile.migration_filename
        )
        with sqlite3.connect(repository.path) as connection:
            connection.execute("DROP TRIGGER schema_metadata_forbid_update")
            connection.execute(
                "UPDATE schema_metadata SET migration_sha256=?",
                (hashlib.sha256(migration.read_bytes()).hexdigest(),),
            )
            connection.execute(f"PRAGMA application_id={profile.application_id}")
            connection.execute(f"PRAGMA user_version={profile.user_version}")
            connection.execute(
                "CREATE TRIGGER schema_metadata_forbid_update "
                "BEFORE UPDATE ON schema_metadata "
                "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"
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
        identity
        for identity in (
            *FROZEN_LEAGUE_IDENTITIES,
            *FROZEN_CUP_IDENTITIES,
            *FROZEN_DIRECT_SPORT_IDENTITIES,
        )
        if identity.code == league_code
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
                "sport_id": str(getattr(identity, "sport_id", "")) or None,
                "sport_code": identity.code,
                "sport_name": identity.name,
                "sport_primary_tag_id": str(
                    getattr(identity, "primary_tag_id", "")
                ) or None,
                "sport_series_id": str(
                    getattr(identity, "series_id", getattr(identity, "root_series_id", ""))
                ),
                "series_slug": getattr(identity, "series_slug", f"{identity.code}-2026"),
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
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    add_winning_episode(repository, include_stop=True)
    result = analyze_database(repository.path)
    assert result["quick_check"] == "ok"
    assert result["analyzer_contract"] == "watermelon-major-sports-analyzer-v4b"
    assert result["classifier_version"] == CLASSIFIER_VERSION
    assert result["league_mapping_sha256"] == LEAGUE_MAPPING_SHA256
    assert result["league_coverage"]["episodes"][0]["league_code"] == "epl"
    threshold = result["entry_thresholds"]["0.97"]["all"]
    assert threshold["resolved"] == 1
    assert threshold["wins"] == 1
    assert threshold["event_equal_fee_net_roi_pct"] > 0
    assert threshold["macro_estimable"] is False
    assert set(threshold["missing_leagues"]) == {
        "bun", "fl1", "lal", "mls", "sea", "ucl", "uel", "mlb",
        "nba", "nfl", "nhl"
    }
    assert threshold["macro_league_equal_fee_net_roi_pct"] is None
    policies = result["stop_policy_comparison"]["0.97"]
    assert policies["HOLD_TO_RESOLUTION"]["event_equal_fee_net_roi_pct"] > 0
    assert policies["STOP_0.80"]["event_equal_fee_net_roi_pct"] < 0
    assert policies["STOP_0.80"]["gap_below_stop_p50"] == pytest.approx(0.02)


def test_v4b_analyzer_reports_source_clock_strata_identity_and_notional_depth(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "clock-depth.db",
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    add_winning_episode(repository)
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        _insert(
            connection,
            "market_observations",
            {
                "observation_id": "market-clock",
                "event_observation_id": "event-observation-epl",
                "sweep_id": "sweep-clock",
                "run_id": "run",
                "event_id": "event-epl",
                "event_title": "Home vs Away",
                "condition_id": "condition-epl",
                "market_id": "market-epl",
                "question": "Will Home win?",
                "group_item_title": "Home",
                "sports_market_type": "moneyline",
                "observed_at": "2026-08-26T01:22:31Z",
                "end_date": "2026-08-26T04:00:00Z",
                "game_start_time": "2026-08-26T00:00:00Z",
                "hours_until_end": 2.6,
                "sports_phase": "IN_PLAY_EXPLICIT",
                "event_live": 1,
                "event_ended": 0,
                "event_game_status": "2H",
                "liquidity": 1000,
                "volume_total": 1000,
                "active": 1,
                "closed": 0,
                "accepting_orders": 1,
                "enable_order_book": 1,
                "neg_risk": 1,
                "match_winner_class": "ALIGNED_TWO_TEAM_MONEYLINE",
                "eligible_outcome_indices_json": "[0]",
                "classification_evidence_json": "{}",
                "cadence_arm": "FAST_1M",
                "fee_rate": 0.05,
                "fee_schedule_json": "{}",
                "outcome_labels_json": '["Home","Away"]',
                "token_ids_json": '["token-epl","token-away"]',
                "outcome_prices_json": "[0.96,0.04]",
                "eligible": 1,
                "exclusion_reason": "ELIGIBLE",
                "normalized_json": json.dumps(
                    {
                        "sports_clock": {
                            "join_status": "OBSERVED",
                            "period": "2H",
                            "elapsed_raw": "82:31",
                        }
                    }
                ),
            },
        )
        _insert(
            connection,
            "orderbook_snapshots",
            {
                "snapshot_id": "snapshot-depth",
                "run_id": "run",
                "token_id": "token-epl",
                "request_id": "book-request",
                "observed_at": "2026-08-26T01:22:31Z",
                "raw_book_sha256": "c" * 64,
                "best_bid": 0.95,
                "best_ask": 0.96,
                "bid_level_count": 1,
                "ask_level_count": 2,
                "source_timestamp": "1",
                "tick_size": 0.01,
                "min_order_size": 5,
            },
        )
        for level_id, side, index, price, size in (
            ("ask-0", "ASK", 0, 0.96, 10),
            ("ask-1", "ASK", 1, 0.97, 100),
            ("bid-0", "BID", 0, 0.95, 200),
        ):
            _insert(
                connection,
                "orderbook_levels",
                {
                    "level_id": level_id,
                    "snapshot_id": "snapshot-depth",
                    "side": side,
                    "level_index": index,
                    "price": price,
                    "size": size,
                },
            )
        _insert(
            connection,
            "signal_decisions",
            {
                "decision_id": "decision-depth",
                "run_id": "run",
                "market_observation_id": "market-clock",
                "snapshot_id": "snapshot-depth",
                "condition_id": "condition-epl",
                "event_id": "event-epl",
                "token_id": "token-epl",
                "outcome_index": 0,
                "threshold": 0.95,
                "decided_at": "2026-08-26T01:22:31Z",
                "best_ask": 0.96,
                "entry_vwap": 0.96,
                "entry_shares": 5 / 0.96,
                "entry_cost": 5,
                "prior_entry_vwap": 0.94,
                "entry_provenance": "UPWARD_CROSS",
                "decision_status": "OPENED_UPWARD_CROSS",
                "details_json": "{}",
                "episode_id": None,
            },
        )

    result = analyze_database(repository.path)
    clock = result["sports_clock_evidence"]
    assert clock["source_clock_observations"] == 1
    assert clock["sports_ws_observations"] == 1
    assert clock["elapsed_parseable"] == 1
    assert clock["late_entry_replay_floors"]["75"]["unique_events"] == 1
    assert clock["late_entry_replay_floors"]["80"]["unique_events"] == 1
    assert clock["late_entry_replay_floors"]["85"]["unique_events"] == 0

    depth = result["notional_depth_evidence"]["by_notional_usdc"]
    assert depth["5"]["full_ask_depth"] == 1
    assert depth["100"]["full_ask_depth"] == 1
    assert depth["150"]["full_ask_depth"] == 0
    assert depth["10"]["vwap_increase_vs_5_usdc_bps_p95"] > 0
    assert result["result_triad_evidence"]["triad_gaps"] == 0


def test_analyzer_keeps_v3a_archive_readable(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "legacy-v3a.db",
        "watermelon-white-1m-v3a",
        1,
        "FAST_1M",
        source_digest="source-v3a",
        profile=V3A_PROFILE,
    )
    add_winning_episode(
        repository,
        classifier_version=V3A_PROFILE.classifier_version,
        mapping_sha256=V3A_PROFILE.league_mapping_sha256,
    )

    result = analyze_database(repository.path)

    assert result["analyzer_contract"] == "soccer-major-league-analyzer-v3a"
    assert result["universe_profile"] == V3A_PROFILE.universe_profile
    assert result["estimator_contract"]["required_league_codes"] == [
        "epl", "bun", "fl1", "lal", "mls"
    ]


def test_analyzer_keeps_v4a_archive_readable(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "legacy-v4a.db",
        "watermelon-white-1m-v4a",
        1,
        "FAST_1M",
        source_digest="source-v4a",
        profile=V4A_PROFILE,
    )
    add_winning_episode(
        repository,
        classifier_version=V4A_PROFILE.classifier_version,
        mapping_sha256=V4A_PROFILE.league_mapping_sha256,
    )

    result = analyze_database(repository.path)

    assert result["analyzer_contract"] == "watermelon-major-sports-analyzer-v4a"
    assert result["universe_profile"] == V4A_PROFILE.universe_profile


def test_analyzer_keeps_v3b_archive_readable(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "legacy-v3b.db",
        "watermelon-white-1m-v3b",
        1,
        "FAST_1M",
        source_digest="source-v3b",
        profile=V3B_PROFILE,
    )
    add_winning_episode(
        repository,
        classifier_version=V3B_PROFILE.classifier_version,
        mapping_sha256=V3B_PROFILE.league_mapping_sha256,
    )
    result = analyze_database(repository.path)
    assert result["analyzer_contract"] == "soccer-major-league-analyzer-v3b"
    assert result["sports_clock_evidence"]["status"].startswith("NOT_COLLECTED")


def test_analyzer_keeps_v3c_archive_readable(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "legacy-v3c.db",
        "watermelon-white-1m-v3c",
        1,
        "FAST_1M",
        source_digest="source-v3c",
        profile=V3C_PROFILE,
    )
    add_winning_episode(
        repository,
        classifier_version=V3C_PROFILE.classifier_version,
        mapping_sha256=V3C_PROFILE.league_mapping_sha256,
    )
    result = analyze_database(repository.path)
    assert result["analyzer_contract"] == "soccer-elite-competition-analyzer-v3c"
    assert result["universe_profile"] == V3C_PROFILE.universe_profile
    assert result["notional_depth_evidence"]["ladder_usdc"][-1] == 500


def test_analyzer_keeps_v3d_archive_readable(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "legacy-v3d.db",
        "watermelon-white-1m-v3d",
        1,
        "FAST_1M",
        source_digest="source-v3d",
        profile=V3D_PROFILE,
    )
    add_winning_episode(
        repository,
        classifier_version=V3D_PROFILE.classifier_version,
        mapping_sha256=V3D_PROFILE.league_mapping_sha256,
    )
    result = analyze_database(repository.path)
    assert result["analyzer_contract"] == "soccer-elite-competition-analyzer-v3d"


@pytest.mark.parametrize(
    ("job", "cadence", "arm"),
    [
        ("watermelon-white-1m-v4b", 5, "CONTROL_5M"),
        ("unexpected-job", 1, "FAST_1M"),
    ],
)
def test_single_database_analyzer_rejects_job_cadence_drift(
    tmp_path, job, cadence, arm
) -> None:
    repository = seeded_database(
        tmp_path,
        f"drift-{cadence}-{arm}.db",
        job,
        cadence,
        arm,
    )
    with pytest.raises(ValueError, match="job/cadence contract mismatch"):
        analyze_database(repository.path)


def test_macro_estimator_requires_and_equal_weights_all_twelve_competitions(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    for identity in (
        *FROZEN_LEAGUE_IDENTITIES,
        *FROZEN_CUP_IDENTITIES,
        *FROZEN_DIRECT_SPORT_IDENTITIES,
    ):
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
        "sea",
        "ucl",
        "uel",
        "mlb",
        "nba",
        "nfl",
        "nhl",
    }
    assert threshold["macro_league_equal_fee_net_roi_pct"] == pytest.approx(
        threshold["event_equal_fee_net_roi_pct"]
    )
    assert len(threshold["macro_league_equal_fee_net_roi_bootstrap_95ci_pct"]) == 2


def test_analyzer_rejects_row_classifier_or_mapping_drift(tmp_path) -> None:
    repository = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v4b",
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
        "watermelon-white-1m-v4b",
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
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v4b",
        5,
        "CONTROL_5M",
    )
    add_winning_episode(white, entered_at="2026-08-26T00:01:00Z")
    add_winning_episode(grey, entered_at="2026-08-26T00:05:00Z")
    result = analyze_databases([white.path, grey.path])
    assert result["analyzer_contract"] == "watermelon-major-sports-cadence-pair-v4b"
    assert result["pairing"]["matched_episode_keys"] == 1
    assert result["pairing"]["entry_time_delta_seconds_p50"] == 240
    assert result["pairing"]["matched_by_league"]["epl"] == 1


def test_multi_database_analyzer_rejects_source_digest_mismatch(tmp_path) -> None:
    white = seeded_database(
        tmp_path,
        "white.db",
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
        source_digest="source-a",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v4b",
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
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v4b",
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
        "watermelon-white-1m-v4b",
        1,
        "FAST_1M",
    )
    grey = seeded_database(
        tmp_path,
        "grey.db",
        "watermelon-grey-5m-v4b",
        5,
        "CONTROL_5M",
    )
    add_winning_episode(white, league_code="epl", episode_key_suffix="pair")
    add_winning_episode(grey, league_code="bun", episode_key_suffix="pair")
    with pytest.raises(ValueError, match="paired episode league mismatch"):
        analyze_databases([white.path, grey.path])

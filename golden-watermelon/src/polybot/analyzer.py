"""Read-only collection-health and cadence-paired analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Any, Iterable

from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    FROZEN_LEAGUE_IDENTITIES,
    LEAGUE_MAPPING_SHA256,
    LeagueIdentity,
    SCHEMA_PROFILE,
    UNIVERSE_PROFILE,
    league_registry_payload,
)
from polybot.db.repository import (
    APPLICATION_ID,
    EXPECTED_SCHEMA_SHA256,
    MIGRATION_PATH,
    SCHEMA_USER_VERSION,
)


ANALYZER_CONTRACT = "soccer-major-league-analyzer-v3b"
PAIR_ANALYZER_CONTRACT = "soccer-major-league-cadence-pair-v3b"
# Legacy strings remain explicit so existing v2/v3 evidence can be identified in
# reports without being opened by the v3b writer.
LEGACY_ANALYZER_CONTRACT = "inplay-match-winner-analyzer-v2"
LEGACY_PAIR_ANALYZER_CONTRACT = "inplay-match-winner-cadence-pair-v2"


@dataclass(frozen=True)
class AnalyzerProfile:
    universe_profile: str
    classifier_version: str
    identities: tuple[LeagueIdentity, ...]
    league_mapping_sha256: str
    analyzer_contract: str
    pair_analyzer_contract: str
    fast_job: str
    control_job: str

    @property
    def league_codes(self) -> tuple[str, ...]:
        return tuple(identity.code for identity in self.identities)

    @property
    def jobs(self) -> frozenset[str]:
        return frozenset((self.fast_job, self.control_job))

    def expected_treatment(self, job_name: str) -> tuple[str, int] | None:
        return {
            self.fast_job: ("FAST_1M", 1),
            self.control_job: ("CONTROL_5M", 5),
        }.get(job_name)


def _mapping_sha256(
    classifier_version: str,
    identities: tuple[LeagueIdentity, ...],
) -> str:
    payload = {
        "classifier_version": classifier_version,
        **league_registry_payload(identities),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


V3A_IDENTITIES = (
    LeagueIdentity(
        "epl", 2, "Premier League", 306, "10188",
        "premier-league-2025", "epl", (82, 306),
    ),
    LeagueIdentity(
        "bun", 7, "Bundesliga", 1494, "10194",
        "bundesliga-2025", "bun", (1494,),
    ),
    LeagueIdentity(
        "fl1", 11, "Ligue 1", 102070, "10195",
        "ligue-1-2025", "fl1", (102070,),
    ),
    LeagueIdentity(
        "lal", 3, "LaLiga", 780, "10193",
        "la-liga-2025", "lal", (780,),
    ),
    LeagueIdentity(
        "mls", 33, "MLS", 100100, "10189",
        "mls-2025", "mls", (100100,),
    ),
)
V3A_PROFILE = AnalyzerProfile(
    universe_profile="soccer-major-leagues-2026-08-v3a",
    classifier_version="soccer-major-league-identity-v1",
    identities=V3A_IDENTITIES,
    league_mapping_sha256=_mapping_sha256(
        "soccer-major-league-identity-v1", V3A_IDENTITIES
    ),
    analyzer_contract="soccer-major-league-analyzer-v3a",
    pair_analyzer_contract="soccer-major-league-cadence-pair-v3a",
    fast_job="watermelon-white-1m-v3a",
    control_job="watermelon-grey-5m-v3a",
)
V3B_PROFILE = AnalyzerProfile(
    universe_profile=UNIVERSE_PROFILE,
    classifier_version=CLASSIFIER_VERSION,
    identities=FROZEN_LEAGUE_IDENTITIES,
    league_mapping_sha256=LEAGUE_MAPPING_SHA256,
    analyzer_contract=ANALYZER_CONTRACT,
    pair_analyzer_contract=PAIR_ANALYZER_CONTRACT,
    fast_job="watermelon-white-1m-v3b",
    control_job="watermelon-grey-5m-v3b",
)
ANALYZER_PROFILES = {
    profile.universe_profile: profile for profile in (V3A_PROFILE, V3B_PROFILE)
}


def _analyzer_profile(metadata: dict[str, Any]) -> AnalyzerProfile:
    universe_profile = str(metadata.get("universe_profile") or "")
    profile = ANALYZER_PROFILES.get(universe_profile)
    if profile is None:
        raise ValueError(f"unsupported analyzer universe profile: {universe_profile!r}")
    return profile


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _wilson(wins: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    probability = wins / total
    denominator = 1 + z * z / total
    center = (probability + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            (probability * (1 - probability) + z * z / (4 * total)) / total
        )
        / denominator
    )
    return [(center - margin) * 100, (center + margin) * 100]


def _bootstrap_mean_ci(
    values: list[float], *, seed: int, samples: int = 10_000
) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        value = values[0] * 100
        return [value, value]
    generator = random.Random(seed)
    count = len(values)
    means = sorted(
        statistics.fmean(values[generator.randrange(count)] for _ in range(count))
        for _ in range(samples)
    )
    return [
        means[int(samples * 0.025)] * 100,
        means[min(samples - 1, int(samples * 0.975))] * 100,
    ]


def _league_macro(
    event_roi_by_league: dict[str, list[float]],
    *,
    required_league_codes: tuple[str, ...],
    seed: int,
) -> dict[str, Any]:
    missing = [
        code for code in required_league_codes if not event_roi_by_league.get(code)
    ]
    by_league = {
        code: statistics.fmean(event_roi_by_league[code]) * 100
        for code in required_league_codes
        if event_roi_by_league.get(code)
    }
    if missing:
        return {
            "league_event_equal_fee_net_roi_pct": by_league,
            "macro_league_equal_fee_net_roi_pct": None,
            "macro_league_equal_fee_net_roi_bootstrap_95ci_pct": None,
            "macro_estimable": False,
            "missing_leagues": missing,
        }

    macro = statistics.fmean(by_league.values())
    generator = random.Random(seed)
    samples = 2_000
    draws: list[float] = []
    for _ in range(samples):
        league_means: list[float] = []
        for code in required_league_codes:
            values = event_roi_by_league[code]
            count = len(values)
            league_means.append(
                statistics.fmean(values[generator.randrange(count)] for _ in range(count))
            )
        draws.append(statistics.fmean(league_means))
    draws.sort()
    return {
        "league_event_equal_fee_net_roi_pct": by_league,
        "macro_league_equal_fee_net_roi_pct": macro,
        "macro_league_equal_fee_net_roi_bootstrap_95ci_pct": [
            draws[int(samples * 0.025)] * 100,
            draws[min(samples - 1, int(samples * 0.975))] * 100,
        ],
        "macro_estimable": True,
        "missing_leagues": [],
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _live_schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type,name,tbl_name,sql FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY type,name,tbl_name
        """
    ).fetchall()
    payload = [tuple(str(value) for value in row) for row in rows]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _paired_config_sha256(config_json: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(config_json))
    for key in ("config_hash", "db_path", "job_name"):
        payload.pop(key, None)
    trading = payload.get("trading")
    if isinstance(trading, dict):
        trading.pop("cadence_arm", None)
        trading.pop("cadence_minutes", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _entry_total_cost(row: sqlite3.Row) -> float:
    shares = float(row["entry_shares"])
    price = float(row["entry_vwap"])
    fee_rate = float(row["fee_rate"])
    fee = shares * fee_rate * price * (1 - price)
    return float(row["entry_cost"]) + fee


def _policy_roi(row: sqlite3.Row) -> tuple[float, str] | None:
    shares = float(row["entry_shares"])
    filled = min(shares, float(row["stop_filled_shares"] or 0))
    proceeds = float(row["stop_net_proceeds"] or 0)
    remaining = max(0.0, shares - filled)
    if remaining <= 1e-7:
        settlement = proceeds
        exit_kind = "STOP_FULL"
    elif row["winner_index"] is not None:
        won = int(row["outcome_index"]) == int(row["winner_index"])
        settlement = proceeds + (remaining if won else 0.0)
        exit_kind = (
            "RESOLUTION_AFTER_PARTIAL_STOP" if filled > 0 else "RESOLUTION"
        )
    else:
        return None
    return settlement / _entry_total_cost(row) - 1, exit_kind


def _cadence_summary(times: list[datetime], minutes: int) -> dict[str, Any]:
    ordered = sorted(set(times))
    if not ordered:
        return {
            "successful_runs": 0,
            "first_success_at": None,
            "last_success_at": None,
            "expected_slots_between_first_and_last": 0,
            "coverage_pct": None,
            "gap_seconds_p50": None,
            "gap_seconds_p95": None,
            "max_gap_seconds": None,
            "gaps_over_1_5x_cadence": 0,
        }
    gaps = [
        (current - prior).total_seconds()
        for prior, current in zip(ordered, ordered[1:])
    ]
    expected = (
        math.floor((ordered[-1] - ordered[0]).total_seconds() / (minutes * 60))
        + 1
    )
    return {
        "successful_runs": len(ordered),
        "first_success_at": ordered[0].isoformat().replace("+00:00", "Z"),
        "last_success_at": ordered[-1].isoformat().replace("+00:00", "Z"),
        "expected_slots_between_first_and_last": expected,
        "coverage_pct": len(ordered) / expected * 100 if expected else None,
        "gap_seconds_p50": _percentile(gaps, 0.50),
        "gap_seconds_p95": _percentile(gaps, 0.95),
        "max_gap_seconds": max(gaps) if gaps else None,
        "gaps_over_1_5x_cadence": sum(
            gap > minutes * 60 * 1.5 for gap in gaps
        ),
    }


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator * 100 if denominator else None


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def analyze_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        metadata_row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        if metadata_row is None:
            raise ValueError("database has no schema_metadata")
        metadata = {key: metadata_row[key] for key in metadata_row.keys()}
        profile = _analyzer_profile(metadata)
        required_league_codes = profile.league_codes
        if metadata.get("schema_profile") is not None:
            expected_metadata = {
                "data_contract": DATA_CONTRACT,
                "schema_profile": SCHEMA_PROFILE,
                "universe_profile": profile.universe_profile,
                "classifier_version": profile.classifier_version,
                "league_mapping_sha256": profile.league_mapping_sha256,
            }
            actual_metadata = {
                key: metadata.get(key) for key in expected_metadata
            }
            if actual_metadata != expected_metadata:
                raise ValueError(
                    f"analyzer metadata contract mismatch: {actual_metadata!r}"
                )
            migration_sha256 = hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
            if (
                (application_id, user_version) != (APPLICATION_ID, SCHEMA_USER_VERSION)
                or str(metadata.get("migration_sha256")) != migration_sha256
                or str(metadata.get("schema_sha256")) != EXPECTED_SCHEMA_SHA256
                or _live_schema_sha256(connection) != EXPECTED_SCHEMA_SHA256
            ):
                raise ValueError("analyzer application/migration/schema contract mismatch")
            registry_row = connection.execute(
                "SELECT * FROM league_registry_versions WHERE league_mapping_sha256=?",
                (profile.league_mapping_sha256,),
            ).fetchone()
            if registry_row is None:
                raise ValueError("database has no exact frozen league registry")
            expected_registry = league_registry_payload(profile.identities)
            if (
                str(registry_row["classifier_version"]) != profile.classifier_version
                or str(registry_row["universe_profile"]) != profile.universe_profile
                or json.loads(str(registry_row["mapping_json"])) != expected_registry
            ):
                raise ValueError("database league registry content drift")
        config_row = connection.execute(
            """
            SELECT config_hash,strategy_source_digest,preregistration_sha256,
                   job_name,config_json,first_seen_at
            FROM research_config_versions
            ORDER BY first_seen_at DESC LIMIT 1
            """
        ).fetchone()
        if config_row is None:
            raise ValueError("database has no research_config_versions")
        connection.execute(
            "CREATE TEMP TABLE cohort_runs(run_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            """
            INSERT INTO cohort_runs(run_id)
            SELECT DISTINCT run_id
            FROM research_run_events
            WHERE config_hash=? AND strategy_source_digest=?
            """,
            (
                str(config_row["config_hash"]),
                str(config_row["strategy_source_digest"]),
            ),
        )
        cohort_run_count = int(
            connection.execute("SELECT COUNT(*) FROM cohort_runs").fetchone()[0]
        )
        config_json = json.loads(str(config_row["config_json"]))
        trading = config_json["trading"]
        cadence_minutes = int(trading["cadence_minutes"])
        cadence_arm = str(trading["cadence_arm"])
        job_name = str(config_row["job_name"])
        expected_treatment = profile.expected_treatment(job_name)
        if expected_treatment is None or (cadence_arm, cadence_minutes) != expected_treatment:
            raise ValueError(
                "analyzer job/cadence contract mismatch: "
                f"job={job_name!r} cadence={(cadence_arm, cadence_minutes)!r} "
                f"expected={expected_treatment!r}"
            )
        experiment = trading.get("experiment", {})
        experiment_start = _utc(
            str(experiment.get("start_utc", config_row["first_seen_at"]))
        )
        experiment_end = _utc(
            str(experiment.get("entry_end_utc", config_row["first_seen_at"]))
        )
        validation_start = experiment_start + (experiment_end - experiment_start) / 2

        run_rows = connection.execute(
            "SELECT event_type,COUNT(*) AS count FROM research_run_events "
            "WHERE run_id IN (SELECT run_id FROM cohort_runs) "
            "GROUP BY event_type"
        ).fetchall()
        success_times = [
            _utc(str(row[0]))
            for row in connection.execute(
                "SELECT observed_at FROM research_run_events "
                "WHERE event_type='SUCCEEDED' "
                "AND run_id IN (SELECT run_id FROM cohort_runs) "
                "ORDER BY observed_at"
            )
        ]
        sweep_columns = _table_columns(connection, "market_sweeps")
        if {
            "accepted_event_count",
            "rejected_event_count",
            "drift_event_count",
            "source_market_count",
        } <= sweep_columns:
            sweep = connection.execute(
                """
                SELECT COUNT(*) AS sweeps,
                       COALESCE(SUM(cursor_complete),0) AS cursor_complete,
                       COALESCE(SUM(event_count),0) AS events,
                       COALESCE(SUM(accepted_event_count),0) AS accepted_events,
                       COALESCE(SUM(rejected_event_count),0) AS rejected_events,
                       COALESCE(SUM(drift_event_count),0) AS drift_events,
                       COALESCE(SUM(source_market_count),0) AS source_markets,
                       COALESCE(SUM(market_count),0) AS markets,
                       COALESCE(SUM(eligible_market_count),0) AS eligible_markets,
                       COALESCE(SUM(eligible_outcome_count),0) AS eligible_outcomes,
                       COALESCE(MAX(page_count),0) AS max_pages
                FROM market_sweeps
                WHERE run_id IN (SELECT run_id FROM cohort_runs)
                """
            ).fetchone()
        else:
            sweep = connection.execute(
                """
                SELECT COUNT(*) AS sweeps,
                       COALESCE(SUM(cursor_complete),0) AS cursor_complete,
                       COALESCE(SUM(event_count),0) AS events,
                       NULL AS accepted_events,NULL AS rejected_events,
                       NULL AS drift_events,NULL AS source_markets,
                       COALESCE(SUM(market_count),0) AS markets,
                       COALESCE(SUM(eligible_market_count),0) AS eligible_markets,
                       COALESCE(SUM(eligible_outcome_count),0) AS eligible_outcomes,
                       COALESCE(MAX(page_count),0) AS max_pages
                FROM market_sweeps
                WHERE run_id IN (SELECT run_id FROM cohort_runs)
                """
            ).fetchone()
        eligible_observations = int(
            connection.execute(
                "SELECT COUNT(*) FROM outcome_observations "
                "WHERE entry_eligible=1 "
                "AND run_id IN (SELECT run_id FROM cohort_runs)"
            ).fetchone()[0]
        )
        observed_books = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM outcome_observations o
                JOIN orderbook_token_attempts a
                  ON a.run_id=o.run_id AND a.token_id=o.token_id
                WHERE o.entry_eligible=1 AND a.status='OBSERVED'
                  AND o.run_id IN (SELECT run_id FROM cohort_runs)
                """
            ).fetchone()[0]
        )
        full_depth_quotes = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT run_id,token_id
                    FROM signal_decisions
                    WHERE run_id IN (SELECT run_id FROM cohort_runs)
                    GROUP BY run_id,token_id
                    HAVING MAX(CASE WHEN entry_vwap IS NOT NULL THEN 1 ELSE 0 END)=1
                )
                """
            ).fetchone()[0]
        )
        class_rows = connection.execute(
            """
            SELECT match_winner_class,eligible,COUNT(*) AS count
            FROM market_observations
            WHERE run_id IN (SELECT run_id FROM cohort_runs)
            GROUP BY match_winner_class,eligible
            ORDER BY match_winner_class,eligible
            """
        ).fetchall()
        market_columns = _table_columns(connection, "market_observations")
        episode_columns = _table_columns(connection, "hypothetical_episodes")
        has_event_observations = _table_exists(connection, "event_observations")
        event_classification_rows = (
            connection.execute(
                """
                SELECT classification_status,rejection_reason,league_code,league_name,
                       COUNT(*) AS observations,COUNT(DISTINCT event_id) AS events
                FROM event_observations
                WHERE run_id IN (SELECT run_id FROM cohort_runs)
                GROUP BY classification_status,rejection_reason,league_code,league_name
                ORDER BY classification_status,rejection_reason,league_code
                """
            ).fetchall()
            if has_event_observations
            else []
        )
        if has_event_observations:
            league_observation_rows = connection.execute(
                """
                SELECT e.league_code,e.league_name,
                       COUNT(m.observation_id) AS observations,
                       COALESCE(SUM(m.eligible),0) AS eligible_observations,
                       COUNT(DISTINCT e.event_id) AS events
                FROM event_observations e
                LEFT JOIN market_observations m
                  ON m.event_observation_id=e.event_observation_id
                WHERE e.classification_status='ACCEPTED'
                  AND e.run_id IN (SELECT run_id FROM cohort_runs)
                GROUP BY e.league_code,e.league_name
                ORDER BY e.league_code,e.league_name
                """
            ).fetchall()
        elif {"league_code", "league_name"} <= market_columns:
            league_observation_rows = connection.execute(
                """
                SELECT league_code,league_name,
                       COUNT(*) AS observations,
                       SUM(eligible) AS eligible_observations,
                       COUNT(DISTINCT event_id) AS events
                FROM market_observations
                WHERE run_id IN (SELECT run_id FROM cohort_runs)
                GROUP BY league_code,league_name
                ORDER BY league_code,league_name
                """
            ).fetchall()
        else:
            league_observation_rows = []
        league_episode_rows = (
            connection.execute(
                """
                SELECT e.league_code,e.league_name,
                       COUNT(*) AS episodes,
                       COUNT(DISTINCT e.event_id) AS events,
                       SUM(CASE WHEN r.condition_id IS NOT NULL THEN 1 ELSE 0 END)
                           AS resolved,
                       SUM(CASE WHEN r.condition_id IS NOT NULL
                                     AND e.outcome_index=r.winner_index
                                THEN 1 ELSE 0 END) AS wins
                FROM hypothetical_episodes e
                LEFT JOIN resolution_observations r USING(condition_id)
                WHERE e.run_id IN (SELECT run_id FROM cohort_runs)
                GROUP BY e.league_code,e.league_name
                ORDER BY e.league_code,e.league_name
                """
            ).fetchall()
            if {"league_code", "league_name"} <= episode_columns
            else []
        )
        exclusion_counter: Counter[str] = Counter()
        for row in connection.execute(
            "SELECT exclusion_reason FROM market_observations WHERE eligible=0 "
            "AND run_id IN (SELECT run_id FROM cohort_runs)"
        ):
            exclusion_counter.update(str(row[0]).split(";"))
        issues = connection.execute(
            "SELECT severity,issue_type,COUNT(*) AS count "
            "FROM data_quality_issues "
            "WHERE run_id IN (SELECT run_id FROM cohort_runs) "
            "GROUP BY severity,issue_type"
        ).fetchall()
        check_rows = connection.execute(
            "SELECT check_type,result,COUNT(*) AS count,MAX(completed_at) AS latest,"
            "MAX(elapsed_ms) AS max_elapsed FROM database_checks "
            "WHERE run_id IN (SELECT run_id FROM cohort_runs) "
            "GROUP BY check_type,result ORDER BY check_type,result"
        ).fetchall()
        storage_rows = connection.execute(
            "SELECT observed_at,db_bytes,free_bytes,total_bytes,used_ratio "
            "FROM storage_metrics "
            "WHERE run_id IN (SELECT run_id FROM cohort_runs) "
            "ORDER BY observed_at"
        ).fetchall()
        episode_rows = connection.execute(
            """
            SELECT e.*,r.winner_index,r.observed_at AS resolved_at
            FROM hypothetical_episodes e
            LEFT JOIN resolution_observations r USING(condition_id)
            WHERE e.run_id IN (SELECT run_id FROM cohort_runs)
            ORDER BY e.entered_at,e.episode_id
            """
        ).fetchall()
        policy_rows = connection.execute(
            """
            SELECT
                p.policy_id,p.policy_key,p.stop_price,
                e.episode_id,e.event_id,e.condition_id,e.token_id,e.outcome_index,
                e.threshold,e.entered_at,e.entry_vwap,e.entry_shares,e.entry_cost,
                e.fee_rate,e.cadence_arm,e.entry_provenance,
                e.league_code,
                r.winner_index,
                COUNT(a.attempt_id) AS stop_attempt_count,
                COALESCE(SUM(a.filled_shares),0) AS stop_filled_shares,
                COALESCE(SUM(a.net_proceeds),0) AS stop_net_proceeds,
                SUM(CASE WHEN a.status='PARTIAL_FILL' THEN 1 ELSE 0 END)
                    AS partial_attempt_count,
                SUM(CASE WHEN a.status='NO_BID_DEPTH' THEN 1 ELSE 0 END)
                    AS no_bid_attempt_count,
                x.exit_vwap AS completed_stop_vwap,
                x.gap_from_stop AS completed_gap_from_stop,
                x.attempt_count AS completed_attempt_count
            FROM counterfactual_exit_policies p
            JOIN hypothetical_episodes e USING(episode_id)
            LEFT JOIN stop_execution_attempts a USING(policy_id)
            LEFT JOIN counterfactual_stop_exits x USING(policy_id)
            LEFT JOIN resolution_observations r ON r.condition_id=e.condition_id
            WHERE e.run_id IN (SELECT run_id FROM cohort_runs)
            GROUP BY p.policy_id
            ORDER BY e.entered_at,p.policy_key
            """
        ).fetchall()
        if metadata.get("schema_profile") is not None:
            for table in ("event_observations", "hypothetical_episodes"):
                contract_rows = connection.execute(
                    f"""
                    SELECT DISTINCT classifier_version,league_mapping_sha256
                    FROM {table}
                    WHERE run_id IN (SELECT run_id FROM cohort_runs)
                    """
                ).fetchall()
                if any(
                    str(row["classifier_version"]) != profile.classifier_version
                    or str(row["league_mapping_sha256"])
                    != profile.league_mapping_sha256
                    for row in contract_rows
                ):
                    raise ValueError(f"{table} classifier/mapping contract drift")
            observed_episode_leagues = {
                str(row["league_code"]) for row in episode_rows
            }
            unknown_leagues = observed_episode_leagues - set(required_league_codes)
            if unknown_leagues:
                raise ValueError(
                    f"hypothetical_episodes contains unknown leagues: {unknown_leagues!r}"
                )
            if any(str(row["cadence_arm"]) != cadence_arm for row in episode_rows):
                raise ValueError("hypothetical_episodes cadence arm contract drift")
    finally:
        connection.close()

    storage: dict[str, Any]
    if storage_rows:
        first = storage_rows[0]
        last = storage_rows[-1]
        elapsed_days = max(
            (_utc(str(last["observed_at"])) - _utc(str(first["observed_at"]))).total_seconds()
            / 86400,
            0,
        )
        delta = int(last["db_bytes"]) - int(first["db_bytes"])
        storage = {
            "samples": len(storage_rows),
            "first_observed_at": first["observed_at"],
            "last_observed_at": last["observed_at"],
            "first_db_bytes": int(first["db_bytes"]),
            "last_db_bytes": int(last["db_bytes"]),
            "growth_bytes": delta,
            "growth_bytes_per_day": delta / elapsed_days if elapsed_days > 0 else None,
            "latest_free_bytes": int(last["free_bytes"]),
            "latest_used_ratio": float(last["used_ratio"]),
        }
    else:
        storage = {"samples": 0}

    result: dict[str, Any] = {
        "analyzer_contract": profile.analyzer_contract,
        "db": str(path.resolve()),
        "quick_check": quick_check,
        "application_id": application_id,
        "user_version": user_version,
        "data_contract": metadata.get("data_contract"),
        "schema_profile": metadata.get("schema_profile"),
        "universe_profile": metadata.get("universe_profile"),
        "classifier_version": metadata.get("classifier_version"),
        "league_mapping_sha256": metadata.get("league_mapping_sha256"),
        "migration_sha256": metadata.get("migration_sha256"),
        "schema_sha256": metadata.get("schema_sha256"),
        "job_name": job_name,
        "cadence_arm": cadence_arm,
        "cadence_minutes": cadence_minutes,
        "config_hash": str(config_row["config_hash"]),
        "strategy_source_digest": str(config_row["strategy_source_digest"]),
        "preregistration_sha256": str(config_row["preregistration_sha256"]),
        "paired_config_sha256": _paired_config_sha256(config_json),
        "config_first_seen_at": str(config_row["first_seen_at"]),
        "cohort_run_count": cohort_run_count,
        "run_events": {str(row["event_type"]): int(row["count"]) for row in run_rows},
        "cadence": _cadence_summary(success_times, cadence_minutes),
        "collection": {
            "sweeps": int(sweep["sweeps"]),
            "cursor_complete": int(sweep["cursor_complete"]),
            "cursor_complete_pct": _safe_ratio(
                int(sweep["cursor_complete"]), int(sweep["sweeps"])
            ),
            "events": int(sweep["events"]),
            "accepted_events": _optional_int(sweep["accepted_events"]),
            "rejected_events": _optional_int(sweep["rejected_events"]),
            "drift_events": _optional_int(sweep["drift_events"]),
            "source_markets": _optional_int(sweep["source_markets"]),
            "markets": int(sweep["markets"]),
            "eligible_markets": int(sweep["eligible_markets"]),
            "eligible_outcomes": int(sweep["eligible_outcomes"]),
            "max_pages": int(sweep["max_pages"]),
        },
        "book_coverage": {
            "eligible_outcome_observations": eligible_observations,
            "observed_books": observed_books,
            "observed_book_pct": _safe_ratio(
                observed_books, eligible_observations
            ),
            "full_5_usdc_depth_quotes": full_depth_quotes,
            "full_5_usdc_depth_pct": _safe_ratio(
                full_depth_quotes, eligible_observations
            ),
        },
        "classification": {
            "event_rows": [
                {
                    "status": str(row["classification_status"]),
                    "reason": str(row["rejection_reason"]),
                    "league_code": row["league_code"],
                    "league_name": row["league_name"],
                    "observations": int(row["observations"]),
                    "events": int(row["events"]),
                }
                for row in event_classification_rows
            ],
            "rows": [
                {
                    "match_winner_class": str(row["match_winner_class"]),
                    "eligible": bool(row["eligible"]),
                    "count": int(row["count"]),
                }
                for row in class_rows
            ],
            "exclusions": dict(sorted(exclusion_counter.items())),
        },
        "league_coverage": {
            "required_leagues": list(required_league_codes),
            "observed_accepted_leagues": sorted(
                {
                    str(row["league_code"])
                    for row in league_observation_rows
                    if row["league_code"] is not None
                }
            ),
            "missing_accepted_leagues": [
                code
                for code in required_league_codes
                if code
                not in {
                    str(row["league_code"])
                    for row in league_observation_rows
                    if row["league_code"] is not None
                }
            ],
            "market_observations": [
                {
                    "league_code": row["league_code"],
                    "league_name": row["league_name"],
                    "observations": int(row["observations"]),
                    "eligible_observations": int(
                        row["eligible_observations"] or 0
                    ),
                    "events": int(row["events"]),
                }
                for row in league_observation_rows
            ],
            "episodes": [
                {
                    "league_code": row["league_code"],
                    "league_name": row["league_name"],
                    "episodes": int(row["episodes"]),
                    "events": int(row["events"]),
                    "resolved": int(row["resolved"] or 0),
                    "wins": int(row["wins"] or 0),
                    "resolution_coverage_pct": _safe_ratio(
                        int(row["resolved"] or 0), int(row["episodes"])
                    ),
                }
                for row in league_episode_rows
            ],
        },
        "issues": [
            {
                "severity": str(row["severity"]),
                "type": str(row["issue_type"]),
                "count": int(row["count"]),
            }
            for row in issues
        ],
        "database_checks": [
            {
                "check_type": str(row["check_type"]),
                "result": str(row["result"]),
                "count": int(row["count"]),
                "latest_completed_at": row["latest"],
                "max_elapsed_ms": row["max_elapsed"],
            }
            for row in check_rows
        ],
        "storage": storage,
        "entry_thresholds": {},
        "stop_policy_comparison": {},
        "estimator_contract": {
            "unit": "condition_id × token_id × entry_threshold paired across cadence arms",
            "within_event": "equal weight across eligible outcome episodes",
            "within_league": "equal weight across resolved events",
            "macro": "equal weight across every frozen league",
            "macro_requires_all_leagues": True,
            "required_league_codes": list(required_league_codes),
        },
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_ONLY",
        "actual_fill_or_realized_pnl": False,
    }

    thresholds = sorted({float(row["threshold"]) for row in episode_rows})
    for threshold in thresholds:
        selected = [
            row for row in episode_rows if float(row["threshold"]) == threshold
        ]
        partitions: dict[str, Any] = {}
        for partition_index, (label, subset) in enumerate(
            (
                ("all", selected),
                (
                    "calibration",
                    [
                        row
                        for row in selected
                        if _utc(str(row["entered_at"])) < validation_start
                    ],
                ),
                (
                    "confirmation",
                    [
                        row
                        for row in selected
                        if _utc(str(row["entered_at"])) >= validation_start
                    ],
                ),
            )
        ):
            resolved = [row for row in subset if row["winner_index"] is not None]
            by_event: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
            wins = 0
            for row in resolved:
                won = int(row["outcome_index"]) == int(row["winner_index"])
                wins += int(won)
                settlement = float(row["entry_shares"]) if won else 0.0
                roi = settlement / _entry_total_cost(row) - 1
                by_event[(str(row["league_code"]), str(row["event_id"]))].append(roi)
            event_roi = [statistics.fmean(values) for values in by_event.values()]
            event_roi_by_league: defaultdict[str, list[float]] = defaultdict(list)
            for (league_code, _event_id), values in by_event.items():
                event_roi_by_league[league_code].append(statistics.fmean(values))
            seed = 20_260_823 + int(round(threshold * 100)) * 10 + partition_index
            partitions[label] = {
                "episodes": len(subset),
                "events": len(
                    {
                        (str(row["league_code"]), str(row["event_id"]))
                        for row in subset
                    }
                ),
                "resolved": len(resolved),
                "resolution_coverage_pct": _safe_ratio(len(resolved), len(subset)),
                "wins": wins,
                "win_rate_pct": _safe_ratio(wins, len(resolved)),
                "win_rate_wilson_95ci_pct": _wilson(wins, len(resolved)),
                "event_equal_fee_net_roi_pct": (
                    statistics.fmean(event_roi) * 100 if event_roi else None
                ),
                "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_roi, seed=seed
                ),
                **_league_macro(
                    dict(event_roi_by_league),
                    required_league_codes=required_league_codes,
                    seed=seed + 500_000,
                ),
                "entry_provenance": dict(
                    Counter(str(row["entry_provenance"]) for row in subset)
                ),
            }
        result["entry_thresholds"][f"{threshold:.2f}"] = partitions

    for threshold in sorted({float(row["threshold"]) for row in policy_rows}):
        threshold_rows = [
            row for row in policy_rows if float(row["threshold"]) == threshold
        ]
        policy_result: dict[str, Any] = {}
        for policy_key in sorted(
            {str(row["policy_key"]) for row in threshold_rows}
        ):
            selected = [
                row
                for row in threshold_rows
                if str(row["policy_key"]) == policy_key
            ]
            evaluated: list[tuple[sqlite3.Row, float, str]] = []
            for row in selected:
                outcome = _policy_roi(row)
                if outcome is not None:
                    evaluated.append((row, outcome[0], outcome[1]))
            by_event: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
            exit_kinds: Counter[str] = Counter()
            for row, roi, kind in evaluated:
                by_event[(str(row["league_code"]), str(row["event_id"]))].append(roi)
                exit_kinds[kind] += 1
            event_roi = [statistics.fmean(values) for values in by_event.values()]
            event_roi_by_league: defaultdict[str, list[float]] = defaultdict(list)
            for (league_code, _event_id), values in by_event.items():
                event_roi_by_league[league_code].append(statistics.fmean(values))
            seed = (
                20_260_823
                + int(round(threshold * 100)) * 100
                + sum(ord(character) for character in policy_key)
            )
            gaps = [
                float(row["completed_gap_from_stop"])
                for row in selected
                if row["completed_gap_from_stop"] is not None
            ]
            policy_result[policy_key] = {
                "stop_price": selected[0]["stop_price"] if selected else None,
                "episodes": len(selected),
                "evaluable": len(evaluated),
                "events": len(by_event),
                "evaluable_coverage_pct": _safe_ratio(
                    len(evaluated), len(selected)
                ),
                "triggered_policies": sum(
                    int(row["stop_attempt_count"] or 0) > 0 for row in selected
                ),
                "completed_stop_exits": sum(
                    row["completed_stop_vwap"] is not None for row in selected
                ),
                "partial_attempts": sum(
                    int(row["partial_attempt_count"] or 0) for row in selected
                ),
                "no_bid_attempts": sum(
                    int(row["no_bid_attempt_count"] or 0) for row in selected
                ),
                "exit_kinds": dict(sorted(exit_kinds.items())),
                "event_equal_fee_net_roi_pct": (
                    statistics.fmean(event_roi) * 100 if event_roi else None
                ),
                "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_roi,
                    seed=seed,
                ),
                **_league_macro(
                    dict(event_roi_by_league),
                    required_league_codes=required_league_codes,
                    seed=seed + 500_000,
                ),
                "gap_below_stop_p50": _percentile(gaps, 0.50),
                "gap_below_stop_p95": _percentile(gaps, 0.95),
            }
        result["stop_policy_comparison"][f"{threshold:.2f}"] = policy_result
    return result


def _episode_index(path: Path) -> dict[tuple[str, str, float], sqlite3.Row]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        config_row = connection.execute(
            "SELECT config_hash,strategy_source_digest "
            "FROM research_config_versions ORDER BY first_seen_at DESC LIMIT 1"
        ).fetchone()
        if config_row is None:
            return {}
        rows = connection.execute(
            """
            SELECT condition_id,token_id,threshold,entered_at,entry_vwap,
                   league_code,cadence_arm,classifier_version,league_mapping_sha256
            FROM hypothetical_episodes
            WHERE run_id IN (
                SELECT DISTINCT run_id FROM research_run_events
                WHERE config_hash=? AND strategy_source_digest=?
            )
            """,
            (str(config_row["config_hash"]), str(config_row["strategy_source_digest"])),
        ).fetchall()
    finally:
        connection.close()
    return {
        (str(row["condition_id"]), str(row["token_id"]), float(row["threshold"])): row
        for row in rows
    }


def analyze_databases(paths: Iterable[Path]) -> dict[str, Any]:
    resolved_paths = [Path(path).resolve() for path in paths]
    if len(resolved_paths) != 2:
        raise ValueError("cadence pair analysis requires exactly two databases")
    databases = [analyze_database(path) for path in resolved_paths]
    result: dict[str, Any] = {
        "analyzer_contract": None,
        "databases": databases,
        "pairing": None,
        "interpretation": "CADENCE_PAIRED_DISPLAYED_BOOK_COUNTERFACTUAL_ONLY",
    }
    exact_pair_fields = (
        "data_contract",
        "schema_profile",
        "universe_profile",
        "classifier_version",
        "league_mapping_sha256",
        "migration_sha256",
        "schema_sha256",
        "application_id",
        "user_version",
        "strategy_source_digest",
        "preregistration_sha256",
        "paired_config_sha256",
    )
    for field in exact_pair_fields:
        values = [database.get(field) for database in databases]
        if any(value is None for value in values) or values[0] != values[1]:
            raise ValueError(f"cadence pair {field} mismatch: {values!r}")
    profile = _analyzer_profile(
        {"universe_profile": databases[0]["universe_profile"]}
    )
    required_league_codes = profile.league_codes
    result["analyzer_contract"] = profile.pair_analyzer_contract
    arms = {str(database["cadence_arm"]) for database in databases}
    if arms != {"FAST_1M", "CONTROL_5M"}:
        raise ValueError(f"cadence pair must contain FAST_1M and CONTROL_5M: {arms!r}")
    jobs = {str(database["job_name"]) for database in databases}
    if jobs != profile.jobs:
        raise ValueError(f"cadence pair contains wrong jobs: {jobs!r}")

    left_index = _episode_index(resolved_paths[0])
    right_index = _episode_index(resolved_paths[1])
    common = sorted(set(left_index) & set(right_index))
    expected_classifier = str(databases[0]["classifier_version"])
    expected_mapping = str(databases[0]["league_mapping_sha256"])
    for side_index, (side, index) in enumerate(
        (("left", left_index), ("right", right_index))
    ):
        expected_arm = str(databases[side_index]["cadence_arm"])
        for key, row in index.items():
            if (
                str(row["cadence_arm"]) != expected_arm
                or str(row["classifier_version"]) != expected_classifier
                or str(row["league_mapping_sha256"]) != expected_mapping
            ):
                raise ValueError(f"{side} episode classifier/mapping drift at {key!r}")
    for key in common:
        if str(left_index[key]["league_code"]) != str(right_index[key]["league_code"]):
            raise ValueError(f"paired episode league mismatch at {key!r}")
    time_deltas = [
        abs(
            (
                _utc(str(left_index[key]["entered_at"]))
                - _utc(str(right_index[key]["entered_at"]))
            ).total_seconds()
        )
        for key in common
    ]
    price_deltas = [
        abs(
            float(left_index[key]["entry_vwap"])
            - float(right_index[key]["entry_vwap"])
        )
        for key in common
    ]
    result["pairing"] = {
        "left_job": databases[0]["job_name"],
        "right_job": databases[1]["job_name"],
        "left_episode_keys": len(left_index),
        "right_episode_keys": len(right_index),
        "matched_episode_keys": len(common),
        "matched_pct_of_smaller_arm": _safe_ratio(
            len(common), min(len(left_index), len(right_index))
        ),
        "matched_by_league": {
            code: sum(str(left_index[key]["league_code"]) == code for key in common)
            for code in required_league_codes
        },
        "missing_pair_leagues": [
            code
            for code in required_league_codes
            if not any(str(left_index[key]["league_code"]) == code for key in common)
        ],
        "entry_time_delta_seconds_p50": _percentile(time_deltas, 0.50),
        "entry_time_delta_seconds_p95": _percentile(time_deltas, 0.95),
        "entry_vwap_absolute_delta_p50": _percentile(price_deltas, 0.50),
        "entry_vwap_absolute_delta_p95": _percentile(price_deltas, 0.95),
    }
    return result

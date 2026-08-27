PRAGMA foreign_keys=ON;
PRAGMA application_id=1195593521;
PRAGMA user_version=1;

CREATE TABLE schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    database_utc_date TEXT NOT NULL,
    data_contract TEXT NOT NULL,
    schema_profile TEXT NOT NULL,
    universe_profile TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    sports_registry_sha256 TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE sports_registry_versions (
    sports_registry_sha256 TEXT PRIMARY KEY,
    universe_profile TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    registry_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    sports_registry_sha256 TEXT NOT NULL,
    job_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('sim','shadow')),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode='archive_only'),
    config_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE research_run_events (
    run_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    observed_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX research_run_event_run_time_idx
ON research_run_events(run_id, observed_at);

CREATE TABLE slot_claims (
    slot_claim_id TEXT PRIMARY KEY,
    slot_start_utc TEXT NOT NULL UNIQUE,
    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes=5),
    run_id TEXT NOT NULL UNIQUE,
    job_name TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);

CREATE TABLE collection_cycles (
    cycle_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    slot_start_utc TEXT NOT NULL UNIQUE,
    job_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('sim','shadow')),
    started_at TEXT NOT NULL,
    cooperative_deadline_at TEXT NOT NULL,
    request_stop_at TEXT NOT NULL,
    hard_deadline_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_seconds REAL NOT NULL,
    receipt_skew_seconds REAL NOT NULL,
    all_families_cursor_complete INTEGER NOT NULL CHECK (all_families_cursor_complete IN (0,1)),
    request_envelope_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE sport_sweeps (
    sweep_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL CHECK (sport_family IN ('soccer','mlb','nba','nfl','nhl')),
    tag_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    source_event_count INTEGER NOT NULL,
    accepted_event_count INTEGER NOT NULL,
    rejected_event_count INTEGER NOT NULL,
    drift_event_count INTEGER NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete IN (0,1)),
    terminal_cursor TEXT,
    request_envelope_json TEXT NOT NULL,
    UNIQUE (cycle_id, sport_family)
);

CREATE TABLE api_requests (
    api_attempt_id TEXT PRIMARY KEY,
    logical_request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    sport_family TEXT,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('GET','POST','WSS')),
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    body_sha256 TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    response_sha256 TEXT,
    response_bytes INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (logical_request_id, attempt_number)
);
CREATE INDEX api_request_run_kind_idx ON api_requests(run_id, request_kind);

CREATE TABLE raw_payloads (
    raw_payload_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    payload_kind TEXT NOT NULL,
    sport_family TEXT,
    logical_request_id TEXT,
    observed_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    gzip_bytes INTEGER NOT NULL,
    payload_gzip BLOB NOT NULL,
    UNIQUE (cycle_id, payload_kind, logical_request_id, sha256)
);

CREATE TABLE event_observations (
    event_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    sweep_id TEXT NOT NULL REFERENCES sport_sweeps(sweep_id),
    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(raw_payload_id),
    run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    event_id TEXT NOT NULL,
    game_id TEXT,
    event_cluster_id TEXT NOT NULL,
    title TEXT,
    slug TEXT,
    observed_at TEXT NOT NULL,
    competition_code TEXT,
    competition_name TEXT,
    season_phase TEXT NOT NULL CHECK (season_phase IN ('PRESEASON','REGULAR','POSTSEASON','UNKNOWN','NOT_APPLICABLE')),
    classification_status TEXT NOT NULL CHECK (classification_status IN ('ACCEPTED','REJECTED','DRIFT')),
    classification_reason TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    sports_registry_sha256 TEXT NOT NULL,
    volume_num REAL,
    volume_24hr REAL,
    liquidity REAL,
    liquidity_num REAL,
    active INTEGER,
    closed INTEGER,
    live INTEGER,
    ended INTEGER,
    game_start_time TEXT,
    end_date TEXT,
    sport_json TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    UNIQUE (cycle_id, sport_family, event_id)
);
CREATE INDEX event_family_cluster_time_idx
ON event_observations(sport_family, event_cluster_id, observed_at);
CREATE INDEX event_season_phase_time_idx
ON event_observations(sport_family, season_phase, observed_at);

CREATE TABLE event_tag_observations (
    event_tag_observation_id TEXT PRIMARY KEY,
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    tag_index INTEGER NOT NULL,
    tag_id TEXT,
    tag_slug TEXT,
    tag_label TEXT,
    tag_json TEXT NOT NULL,
    UNIQUE (event_observation_id, tag_index)
);

CREATE TABLE event_series_observations (
    event_series_observation_id TEXT PRIMARY KEY,
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    series_index INTEGER NOT NULL,
    series_id TEXT,
    series_slug TEXT,
    series_title TEXT,
    series_json TEXT NOT NULL,
    UNIQUE (event_observation_id, series_index)
);

CREATE TABLE event_team_observations (
    event_team_observation_id TEXT PRIMARY KEY,
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    team_index INTEGER NOT NULL,
    team_id TEXT,
    team_name TEXT,
    team_alias TEXT,
    team_abbreviation TEXT,
    team_league TEXT,
    team_json TEXT NOT NULL,
    UNIQUE (event_observation_id, team_index)
);

CREATE TABLE market_observations (
    market_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    question TEXT,
    group_item_title TEXT,
    sports_market_type TEXT,
    structure_kind TEXT NOT NULL,
    result_kind TEXT,
    neg_risk INTEGER,
    observed_at TEXT NOT NULL,
    active INTEGER,
    closed INTEGER,
    accepting_source_activity INTEGER,
    public_book_enabled INTEGER,
    volume_num REAL,
    volume_24hr REAL,
    liquidity REAL,
    liquidity_num REAL,
    event_volume_num REAL,
    event_volume_24hr REAL,
    event_liquidity REAL,
    event_liquidity_num REAL,
    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),
    exclusion_reason TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    probabilities_json TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    UNIQUE (cycle_id, sport_family, event_id, condition_id)
);
CREATE INDEX market_family_cluster_time_idx
ON market_observations(sport_family, event_cluster_id, observed_at);

CREATE TABLE outcome_observations (
    outcome_observation_id TEXT PRIMARY KEY,
    market_observation_id TEXT NOT NULL REFERENCES market_observations(market_observation_id),
    cycle_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL CHECK (outcome_index IN (0,1)),
    outcome_label TEXT NOT NULL,
    gamma_probability REAL,
    threshold_eligible INTEGER NOT NULL CHECK (threshold_eligible IN (0,1)),
    observed_at TEXT NOT NULL,
    UNIQUE (cycle_id, token_id)
);
CREATE INDEX outcome_token_time_idx ON outcome_observations(token_id, observed_at);

CREATE TABLE book_token_attempts (
    book_attempt_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    status TEXT NOT NULL,
    logical_request_id TEXT,
    observed_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (cycle_id, token_id)
);

CREATE TABLE book_snapshots (
    book_snapshot_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    logical_request_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_timestamp TEXT,
    canonical_sha256 TEXT NOT NULL,
    canonical_bytes INTEGER NOT NULL,
    gzip_bytes INTEGER NOT NULL,
    book_gzip BLOB NOT NULL,
    best_bid REAL,
    best_ask REAL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    tick_size REAL,
    min_size REAL,
    fee_status TEXT NOT NULL,
    public_fee_rate_bps REAL,
    UNIQUE (cycle_id, token_id)
);
CREATE INDEX book_snapshot_token_time_idx ON book_snapshots(token_id, observed_at);

CREATE TABLE book_ladder_observations (
    ladder_observation_id TEXT PRIMARY KEY,
    book_snapshot_id TEXT NOT NULL REFERENCES book_snapshots(book_snapshot_id),
    cycle_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    notional_usdc REAL NOT NULL,
    ask_status TEXT NOT NULL CHECK (ask_status IN ('FULL','PARTIAL','EMPTY')),
    ask_filled_usdc REAL NOT NULL,
    ask_remaining_usdc REAL NOT NULL,
    ask_shares REAL NOT NULL,
    ask_vwap REAL,
    ask_worst_price REAL,
    ask_levels_used INTEGER NOT NULL,
    immediate_bid_status TEXT NOT NULL CHECK (immediate_bid_status IN ('FULL','PARTIAL','EMPTY','NOT_APPLICABLE')),
    immediate_bid_filled_shares REAL NOT NULL,
    immediate_bid_remaining_shares REAL NOT NULL,
    immediate_bid_vwap REAL,
    immediate_bid_worst_price REAL,
    immediate_bid_levels_used INTEGER NOT NULL,
    UNIQUE (book_snapshot_id, notional_usdc)
);
CREATE INDEX ladder_notional_status_idx
ON book_ladder_observations(notional_usdc, ask_status);

CREATE TABLE threshold_vectors (
    threshold_vector_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),
    observed_at TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    executable_ask_vwap_5 REAL,
    prior_observed_at TEXT,
    prior_observation_status TEXT,
    prior_executable_ask_vwap_5 REAL,
    observation_gap_seconds REAL,
    states_json TEXT NOT NULL,
    upward_crossings_json TEXT NOT NULL,
    left_censored_json TEXT NOT NULL,
    gap_censored_json TEXT NOT NULL,
    UNIQUE (cycle_id, token_id)
);
CREATE INDEX threshold_vector_token_time_idx
ON threshold_vectors(token_id, observed_at);

CREATE TABLE threshold_state_carryovers (
    threshold_state_carryover_id TEXT PRIMARY KEY,
    carried_from_utc_date TEXT NOT NULL,
    token_id TEXT NOT NULL UNIQUE,
    condition_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    executable_ask_vwap_5 REAL,
    prior_vector_sha256 TEXT NOT NULL,
    carried_at TEXT NOT NULL
);

CREATE TABLE threshold_episodes (
    episode_id TEXT PRIMARY KEY,
    threshold_vector_id TEXT NOT NULL REFERENCES threshold_vectors(threshold_vector_id),
    origin_utc_date TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    competition_code TEXT,
    event_id TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    threshold REAL NOT NULL,
    crossed_at TEXT NOT NULL,
    entry_ask_vwap REAL NOT NULL,
    entry_shares_5 REAL NOT NULL,
    entry_book_snapshot_id TEXT NOT NULL REFERENCES book_snapshots(book_snapshot_id),
    liquidity REAL,
    volume_num REAL,
    volume_24hr REAL,
    UNIQUE (condition_id, token_id, threshold)
);
CREATE INDEX threshold_episode_family_phase_idx
ON threshold_episodes(sport_family, season_phase, threshold, crossed_at);

CREATE TABLE episode_carryovers (
    episode_carryover_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL UNIQUE,
    origin_utc_date TEXT NOT NULL,
    carried_from_utc_date TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    season_phase TEXT NOT NULL,
    competition_code TEXT,
    event_id TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    threshold REAL NOT NULL,
    crossed_at TEXT NOT NULL,
    entry_ask_vwap REAL NOT NULL,
    entry_shares_5 REAL NOT NULL,
    liquidity REAL,
    volume_num REAL,
    volume_24hr REAL,
    carried_at TEXT NOT NULL
);

CREATE TABLE episode_path_observations (
    episode_path_observation_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),
    observed_at TEXT NOT NULL,
    path_status TEXT NOT NULL CHECK (path_status IN ('FULL','PARTIAL','EMPTY','BOOK_UNAVAILABLE')),
    best_bid REAL,
    requested_shares REAL NOT NULL,
    filled_shares REAL NOT NULL,
    remaining_shares REAL NOT NULL,
    executable_bid_vwap REAL,
    worst_bid REAL,
    levels_used INTEGER NOT NULL,
    UNIQUE (episode_id, cycle_id)
);

CREATE TABLE resolution_attempts (
    resolution_attempt_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    logical_request_id TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (cycle_id, condition_id)
);

CREATE TABLE resolution_observations (
    resolution_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('RESOLVED','VOID','TIE','OPEN','CLOSED_UNRESOLVED','MALFORMED')),
    winner_indices_json TEXT NOT NULL,
    logical_request_id TEXT,
    raw_sha256 TEXT,
    evidence_json TEXT NOT NULL,
    UNIQUE (cycle_id, condition_id)
);
CREATE INDEX resolution_condition_time_idx
ON resolution_observations(condition_id, observed_at);

CREATE TABLE sports_clock_observations (
    sports_clock_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),
    run_id TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    game_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    period_raw TEXT,
    elapsed_raw TEXT,
    score_raw TEXT,
    live INTEGER,
    ended INTEGER,
    logical_request_id TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    clock_json TEXT NOT NULL,
    UNIQUE (cycle_id, game_id)
);

CREATE TABLE data_quality_issues (
    data_quality_issue_id TEXT PRIMARY KEY,
    cycle_id TEXT,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),
    issue_type TEXT NOT NULL,
    sport_family TEXT,
    detail_json TEXT NOT NULL
);

CREATE TABLE storage_metrics (
    storage_metric_id TEXT PRIMARY KEY,
    cycle_id TEXT,
    run_id TEXT,
    phase TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    database_bytes INTEGER NOT NULL,
    wal_bytes INTEGER NOT NULL,
    filesystem_total_bytes INTEGER NOT NULL,
    filesystem_used_bytes INTEGER NOT NULL,
    filesystem_free_bytes INTEGER NOT NULL,
    filesystem_used_ratio REAL NOT NULL,
    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK','WARN','STOP'))
);

CREATE TABLE database_checks (
    database_check_id TEXT PRIMARY KEY,
    cycle_id TEXT,
    run_id TEXT,
    check_type TEXT NOT NULL CHECK (check_type IN ('QUICK_CHECK','SCHEMA_FINGERPRINT')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    result TEXT NOT NULL,
    database_bytes INTEGER NOT NULL
);

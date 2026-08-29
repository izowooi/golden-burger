PRAGMA foreign_keys=ON;
PRAGMA application_id=1196903732;
PRAGMA user_version=401;

CREATE TABLE schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    data_contract TEXT NOT NULL UNIQUE,
    schema_profile TEXT NOT NULL,
    universe_profile TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    league_mapping_sha256 TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE league_registry_versions (
    league_mapping_sha256 TEXT PRIMARY KEY,
    classifier_version TEXT NOT NULL,
    universe_profile TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    job_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    config_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    observed_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX run_events_run_idx ON research_run_events(run_id, observed_at);

CREATE TABLE api_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL,
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
    error_message TEXT
);

CREATE TABLE raw_payloads (
    payload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload_kind TEXT NOT NULL,
    request_id TEXT,
    observed_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    gzip_bytes INTEGER NOT NULL,
    payload_gzip BLOB NOT NULL,
    UNIQUE (run_id, payload_kind, request_id, sha256)
);

CREATE TABLE market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    accepted_event_count INTEGER NOT NULL,
    rejected_event_count INTEGER NOT NULL,
    drift_event_count INTEGER NOT NULL,
    source_market_count INTEGER NOT NULL,
    market_count INTEGER NOT NULL,
    eligible_market_count INTEGER NOT NULL,
    eligible_outcome_count INTEGER NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete IN (0,1)),
    request_envelope_json TEXT NOT NULL
);

CREATE TABLE event_observations (
    event_observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    source_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),
    page_number INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_title TEXT,
    event_slug TEXT,
    canonical_event_sha256 TEXT NOT NULL,
    sport_id TEXT,
    sport_code TEXT,
    sport_name TEXT,
    sport_primary_tag_id TEXT,
    sport_series_id TEXT,
    series_slug TEXT,
    tag_ids_json TEXT NOT NULL,
    tag_slugs_json TEXT NOT NULL,
    series_ids_json TEXT NOT NULL,
    series_slugs_json TEXT NOT NULL,
    team_leagues_json TEXT NOT NULL,
    sport_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    series_json TEXT NOT NULL,
    teams_json TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    league_mapping_sha256 TEXT NOT NULL,
    league_code TEXT,
    league_name TEXT,
    classification_status TEXT NOT NULL CHECK (classification_status IN ('ACCEPTED','REJECTED','DRIFT')),
    rejection_reason TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    UNIQUE (sweep_id, event_id)
);
CREATE INDEX event_league_time_idx ON event_observations(league_code, observed_at);
CREATE INDEX event_status_time_idx ON event_observations(classification_status, observed_at);

CREATE TABLE market_observations (
    observation_id TEXT PRIMARY KEY,
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_title TEXT,
    condition_id TEXT,
    market_id TEXT,
    question TEXT,
    group_item_title TEXT,
    sports_market_type TEXT,
    observed_at TEXT NOT NULL,
    end_date TEXT,
    game_start_time TEXT,
    hours_until_end REAL,
    sports_phase TEXT NOT NULL,
    event_live INTEGER,
    event_ended INTEGER,
    event_game_status TEXT,
    liquidity REAL,
    volume_total REAL,
    active INTEGER,
    closed INTEGER,
    accepting_orders INTEGER,
    enable_order_book INTEGER,
    neg_risk INTEGER,
    match_winner_class TEXT NOT NULL,
    eligible_outcome_indices_json TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    cadence_arm TEXT NOT NULL,
    fee_rate REAL,
    fee_schedule_json TEXT NOT NULL,
    outcome_labels_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),
    exclusion_reason TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    UNIQUE (sweep_id, event_id, condition_id)
);
CREATE INDEX market_condition_time_idx ON market_observations(condition_id, observed_at);
CREATE INDEX market_event_observation_idx ON market_observations(event_observation_id, observed_at);

CREATE TABLE outcome_observations (
    outcome_observation_id TEXT PRIMARY KEY,
    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    sweep_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    entry_eligible INTEGER NOT NULL CHECK (entry_eligible IN (0,1)),
    gamma_probability REAL,
    observed_at TEXT NOT NULL,
    UNIQUE (sweep_id, token_id)
);
CREATE INDEX outcome_token_time_idx ON outcome_observations(token_id, observed_at);

CREATE TABLE orderbook_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    status TEXT NOT NULL,
    request_id TEXT,
    observed_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (run_id, token_id)
);

CREATE TABLE orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_book_sha256 TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    source_timestamp TEXT,
    tick_size REAL,
    min_order_size REAL,
    UNIQUE (run_id, token_id)
);
CREATE INDEX book_token_time_idx ON orderbook_snapshots(token_id, observed_at);

CREATE TABLE orderbook_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),
    level_index INTEGER NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    UNIQUE (snapshot_id, side, level_index)
);

CREATE TABLE signal_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    threshold REAL NOT NULL,
    decided_at TEXT NOT NULL,
    best_ask REAL,
    entry_vwap REAL,
    entry_shares REAL,
    entry_cost REAL,
    prior_entry_vwap REAL,
    entry_provenance TEXT,
    decision_status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    episode_id TEXT,
    UNIQUE (run_id, token_id, threshold)
);
CREATE INDEX decisions_status_idx ON signal_decisions(decision_status, decided_at, threshold);

CREATE TABLE hypothetical_episodes (
    episode_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES signal_decisions(decision_id),
    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_title TEXT,
    question TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    threshold REAL NOT NULL,
    cadence_arm TEXT NOT NULL,
    match_winner_class TEXT NOT NULL,
    league_code TEXT NOT NULL,
    league_name TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    league_mapping_sha256 TEXT NOT NULL,
    entry_provenance TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    end_date TEXT NOT NULL,
    game_start_time TEXT,
    sports_phase TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    fee_rate REAL NOT NULL,
    entry_best_ask REAL NOT NULL,
    entry_vwap REAL NOT NULL,
    entry_shares REAL NOT NULL,
    entry_cost REAL NOT NULL,
    UNIQUE (condition_id, token_id, threshold)
);
CREATE INDEX episodes_threshold_time_idx ON hypothetical_episodes(threshold, entered_at);
CREATE INDEX episodes_league_time_idx ON hypothetical_episodes(league_code, entered_at);

CREATE TABLE counterfactual_exit_policies (
    policy_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    created_run_id TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    stop_price REAL,
    created_at TEXT NOT NULL,
    CHECK ((policy_key='HOLD_TO_RESOLUTION' AND stop_price IS NULL)
        OR (policy_key LIKE 'STOP_%' AND stop_price>0 AND stop_price<1)),
    UNIQUE (episode_id, policy_key)
);
CREATE INDEX exit_policy_episode_idx ON counterfactual_exit_policies(episode_id, policy_key);

CREATE TABLE episode_path_observations (
    path_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    best_bid REAL,
    executable_bid_vwap REAL,
    executable_proceeds REAL,
    status TEXT NOT NULL,
    UNIQUE (episode_id, run_id)
);

CREATE TABLE stop_execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES counterfactual_exit_policies(policy_id),
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    stop_price REAL NOT NULL,
    prior_best_bid REAL,
    trigger_best_bid REAL,
    requested_shares REAL NOT NULL,
    filled_shares REAL NOT NULL,
    remaining_shares REAL NOT NULL,
    exit_vwap REAL,
    gross_proceeds REAL NOT NULL,
    fee_rate REAL NOT NULL,
    estimated_fee REAL NOT NULL,
    net_proceeds REAL NOT NULL,
    levels_used INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('FULL_EXIT','PARTIAL_FILL','NO_BID_DEPTH')),
    gap_from_stop REAL,
    drop_from_prior REAL,
    UNIQUE (policy_id, run_id)
);
CREATE INDEX stop_attempt_policy_time_idx ON stop_execution_attempts(policy_id, observed_at);

CREATE TABLE counterfactual_stop_exits (
    exit_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL UNIQUE REFERENCES counterfactual_exit_policies(policy_id),
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    completed_run_id TEXT NOT NULL,
    completed_attempt_id TEXT NOT NULL UNIQUE REFERENCES stop_execution_attempts(attempt_id),
    first_triggered_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    stop_price REAL NOT NULL,
    first_trigger_best_bid REAL,
    exit_vwap REAL NOT NULL,
    requested_shares REAL NOT NULL,
    filled_shares REAL NOT NULL,
    gross_proceeds REAL NOT NULL,
    estimated_fee REAL NOT NULL,
    net_proceeds REAL NOT NULL,
    attempt_count INTEGER NOT NULL,
    gap_from_stop REAL NOT NULL
);
CREATE INDEX stop_exit_episode_idx ON counterfactual_stop_exits(episode_id, stop_price);

CREATE TABLE resolution_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    request_id TEXT,
    winner_index INTEGER,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (run_id, condition_id)
);
CREATE INDEX resolution_attempt_time_idx ON resolution_attempts(condition_id, attempted_at);

CREATE TABLE resolution_observations (
    resolution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL UNIQUE,
    observed_at TEXT NOT NULL,
    winner_index INTEGER NOT NULL CHECK (winner_index IN (0,1)),
    request_id TEXT NOT NULL,
    raw_market_sha256 TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TABLE data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE TABLE storage_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    free_bytes INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    used_ratio REAL NOT NULL
);

CREATE TABLE database_checks (
    check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    check_type TEXT NOT NULL CHECK (check_type='QUICK_CHECK'),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    result TEXT NOT NULL,
    db_bytes INTEGER NOT NULL
);
CREATE INDEX database_check_time_idx ON database_checks(check_type, completed_at);

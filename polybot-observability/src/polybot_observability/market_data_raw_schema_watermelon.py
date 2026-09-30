"""Reviewed Watermelon v401 physical schema and four immutable parent substitutions.

Original authority: golden-watermelon migration 0002 plus append-only guards.
Generated literals are deliberate contracts; no runtime SQL constraint inference.
"""

WATERMELON_SCHEMA_OBJECTS = (('index',
  'book_token_time_idx',
  'orderbook_snapshots',
  'CREATE INDEX book_token_time_idx ON orderbook_snapshots(token_id, observed_at)'),
 ('index',
  'database_check_time_idx',
  'database_checks',
  'CREATE INDEX database_check_time_idx ON database_checks(check_type, completed_at)'),
 ('index',
  'decisions_status_idx',
  'signal_decisions',
  'CREATE INDEX decisions_status_idx ON signal_decisions(decision_status, decided_at, threshold)'),
 ('index',
  'episodes_league_time_idx',
  'hypothetical_episodes',
  'CREATE INDEX episodes_league_time_idx ON hypothetical_episodes(league_code, entered_at)'),
 ('index',
  'episodes_threshold_time_idx',
  'hypothetical_episodes',
  'CREATE INDEX episodes_threshold_time_idx ON hypothetical_episodes(threshold, entered_at)'),
 ('index',
  'event_league_time_idx',
  'event_observations',
  'CREATE INDEX event_league_time_idx ON event_observations(league_code, observed_at)'),
 ('index',
  'event_status_time_idx',
  'event_observations',
  'CREATE INDEX event_status_time_idx ON event_observations(classification_status, observed_at)'),
 ('index',
  'exit_policy_episode_idx',
  'counterfactual_exit_policies',
  'CREATE INDEX exit_policy_episode_idx ON counterfactual_exit_policies(episode_id, policy_key)'),
 ('index',
  'market_condition_time_idx',
  'market_observations',
  'CREATE INDEX market_condition_time_idx ON market_observations(condition_id, observed_at)'),
 ('index',
  'market_event_observation_idx',
  'market_observations',
  'CREATE INDEX market_event_observation_idx ON market_observations(event_observation_id, observed_at)'),
 ('index',
  'outcome_token_time_idx',
  'outcome_observations',
  'CREATE INDEX outcome_token_time_idx ON outcome_observations(token_id, observed_at)'),
 ('index',
  'resolution_attempt_time_idx',
  'resolution_attempts',
  'CREATE INDEX resolution_attempt_time_idx ON resolution_attempts(condition_id, attempted_at)'),
 ('index',
  'run_events_run_idx',
  'research_run_events',
  'CREATE INDEX run_events_run_idx ON research_run_events(run_id, observed_at)'),
 ('index',
  'stop_attempt_policy_time_idx',
  'stop_execution_attempts',
  'CREATE INDEX stop_attempt_policy_time_idx ON stop_execution_attempts(policy_id, observed_at)'),
 ('index',
  'stop_exit_episode_idx',
  'counterfactual_stop_exits',
  'CREATE INDEX stop_exit_episode_idx ON counterfactual_stop_exits(episode_id, stop_price)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    request_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  '    method TEXT NOT NULL,\n'
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    body_sha256 TEXT,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    http_status INTEGER,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'counterfactual_exit_policies',
  'counterfactual_exit_policies',
  'CREATE TABLE counterfactual_exit_policies (\n'
  '    policy_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    created_run_id TEXT NOT NULL,\n'
  '    policy_key TEXT NOT NULL,\n'
  '    stop_price REAL,\n'
  '    created_at TEXT NOT NULL,\n'
  "    CHECK ((policy_key='HOLD_TO_RESOLUTION' AND stop_price IS NULL)\n"
  "        OR (policy_key LIKE 'STOP_%' AND stop_price>0 AND stop_price<1)),\n"
  '    UNIQUE (episode_id, policy_key)\n'
  ')'),
 ('table',
  'counterfactual_stop_exits',
  'counterfactual_stop_exits',
  'CREATE TABLE counterfactual_stop_exits (\n'
  '    exit_id TEXT PRIMARY KEY,\n'
  '    policy_id TEXT NOT NULL UNIQUE REFERENCES counterfactual_exit_policies(policy_id),\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    completed_run_id TEXT NOT NULL,\n'
  '    completed_attempt_id TEXT NOT NULL UNIQUE REFERENCES stop_execution_attempts(attempt_id),\n'
  '    first_triggered_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    stop_price REAL NOT NULL,\n'
  '    first_trigger_best_bid REAL,\n'
  '    exit_vwap REAL NOT NULL,\n'
  '    requested_shares REAL NOT NULL,\n'
  '    filled_shares REAL NOT NULL,\n'
  '    gross_proceeds REAL NOT NULL,\n'
  '    estimated_fee REAL NOT NULL,\n'
  '    net_proceeds REAL NOT NULL,\n'
  '    attempt_count INTEGER NOT NULL,\n'
  '    gap_from_stop REAL NOT NULL\n'
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    severity TEXT NOT NULL,\n'
  '    issue_type TEXT NOT NULL,\n'
  '    detail_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'database_checks',
  'database_checks',
  'CREATE TABLE database_checks (\n'
  '    check_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  "    check_type TEXT NOT NULL CHECK (check_type='QUICK_CHECK'),\n"
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL NOT NULL,\n'
  '    result TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL\n'
  ')'),
 ('table',
  'episode_path_observations',
  'episode_path_observations',
  'CREATE TABLE episode_path_observations (\n'
  '    path_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    best_bid REAL,\n'
  '    executable_bid_vwap REAL,\n'
  '    executable_proceeds REAL,\n'
  '    status TEXT NOT NULL,\n'
  '    UNIQUE (episode_id, run_id)\n'
  ')'),
 ('table',
  'event_observations',
  'event_observations',
  'CREATE TABLE event_observations (\n'
  '    event_observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    source_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
  '    page_number INTEGER NOT NULL,\n'
  '    request_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_title TEXT,\n'
  '    event_slug TEXT,\n'
  '    canonical_event_sha256 TEXT NOT NULL,\n'
  '    sport_id TEXT,\n'
  '    sport_code TEXT,\n'
  '    sport_name TEXT,\n'
  '    sport_primary_tag_id TEXT,\n'
  '    sport_series_id TEXT,\n'
  '    series_slug TEXT,\n'
  '    tag_ids_json TEXT NOT NULL,\n'
  '    tag_slugs_json TEXT NOT NULL,\n'
  '    series_ids_json TEXT NOT NULL,\n'
  '    series_slugs_json TEXT NOT NULL,\n'
  '    team_leagues_json TEXT NOT NULL,\n'
  '    sport_json TEXT NOT NULL,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    series_json TEXT NOT NULL,\n'
  '    teams_json TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    league_mapping_sha256 TEXT NOT NULL,\n'
  '    league_code TEXT,\n'
  '    league_name TEXT,\n'
  '    classification_status TEXT NOT NULL CHECK (classification_status IN '
  "('ACCEPTED','REJECTED','DRIFT')),\n"
  '    rejection_reason TEXT NOT NULL,\n'
  '    classification_evidence_json TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, event_id)\n'
  ')'),
 ('table',
  'hypothetical_episodes',
  'hypothetical_episodes',
  'CREATE TABLE hypothetical_episodes (\n'
  '    episode_id TEXT PRIMARY KEY,\n'
  '    decision_id TEXT NOT NULL UNIQUE REFERENCES signal_decisions(decision_id),\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_title TEXT,\n'
  '    question TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    threshold REAL NOT NULL,\n'
  '    cadence_arm TEXT NOT NULL,\n'
  '    match_winner_class TEXT NOT NULL,\n'
  '    league_code TEXT NOT NULL,\n'
  '    league_name TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    league_mapping_sha256 TEXT NOT NULL,\n'
  '    entry_provenance TEXT NOT NULL,\n'
  '    entered_at TEXT NOT NULL,\n'
  '    end_date TEXT NOT NULL,\n'
  '    game_start_time TEXT,\n'
  '    sports_phase TEXT NOT NULL,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    fee_rate REAL NOT NULL,\n'
  '    entry_best_ask REAL NOT NULL,\n'
  '    entry_vwap REAL NOT NULL,\n'
  '    entry_shares REAL NOT NULL,\n'
  '    entry_cost REAL NOT NULL,\n'
  '    UNIQUE (condition_id, token_id, threshold)\n'
  ')'),
 ('table',
  'league_registry_versions',
  'league_registry_versions',
  'CREATE TABLE league_registry_versions (\n'
  '    league_mapping_sha256 TEXT PRIMARY KEY,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    universe_profile TEXT NOT NULL,\n'
  '    mapping_json TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_observations',
  'market_observations',
  'CREATE TABLE market_observations (\n'
  '    observation_id TEXT PRIMARY KEY,\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_title TEXT,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    question TEXT,\n'
  '    group_item_title TEXT,\n'
  '    sports_market_type TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    end_date TEXT,\n'
  '    game_start_time TEXT,\n'
  '    hours_until_end REAL,\n'
  '    sports_phase TEXT NOT NULL,\n'
  '    event_live INTEGER,\n'
  '    event_ended INTEGER,\n'
  '    event_game_status TEXT,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    accepting_orders INTEGER,\n'
  '    enable_order_book INTEGER,\n'
  '    neg_risk INTEGER,\n'
  '    match_winner_class TEXT NOT NULL,\n'
  '    eligible_outcome_indices_json TEXT NOT NULL,\n'
  '    classification_evidence_json TEXT NOT NULL,\n'
  '    cadence_arm TEXT NOT NULL,\n'
  '    fee_rate REAL,\n'
  '    fee_schedule_json TEXT NOT NULL,\n'
  '    outcome_labels_json TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    outcome_prices_json TEXT NOT NULL,\n'
  '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
  '    exclusion_reason TEXT NOT NULL,\n'
  '    normalized_json TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, event_id, condition_id)\n'
  ')'),
 ('table',
  'market_sweeps',
  'market_sweeps',
  'CREATE TABLE market_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    page_count INTEGER NOT NULL,\n'
  '    event_count INTEGER NOT NULL,\n'
  '    accepted_event_count INTEGER NOT NULL,\n'
  '    rejected_event_count INTEGER NOT NULL,\n'
  '    drift_event_count INTEGER NOT NULL,\n'
  '    source_market_count INTEGER NOT NULL,\n'
  '    market_count INTEGER NOT NULL,\n'
  '    eligible_market_count INTEGER NOT NULL,\n'
  '    eligible_outcome_count INTEGER NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete IN (0,1)),\n'
  '    request_envelope_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'orderbook_levels',
  'orderbook_levels',
  'CREATE TABLE orderbook_levels (\n'
  '    level_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),\n"
  '    level_index INTEGER NOT NULL,\n'
  '    price REAL NOT NULL,\n'
  '    size REAL NOT NULL,\n'
  '    UNIQUE (snapshot_id, side, level_index)\n'
  ')'),
 ('table',
  'orderbook_snapshots',
  'orderbook_snapshots',
  'CREATE TABLE orderbook_snapshots (\n'
  '    snapshot_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    raw_book_sha256 TEXT NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    tick_size REAL,\n'
  '    min_order_size REAL,\n'
  '    UNIQUE (run_id, token_id)\n'
  ')'),
 ('table',
  'orderbook_token_attempts',
  'orderbook_token_attempts',
  'CREATE TABLE orderbook_token_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    observed_at TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (run_id, token_id)\n'
  ')'),
 ('table',
  'outcome_observations',
  'outcome_observations',
  'CREATE TABLE outcome_observations (\n'
  '    outcome_observation_id TEXT PRIMARY KEY,\n'
  '    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),\n'
  '    sweep_id TEXT NOT NULL,\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    entry_eligible INTEGER NOT NULL CHECK (entry_eligible IN (0,1)),\n'
  '    gamma_probability REAL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, token_id)\n'
  ')'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads (\n'
  '    payload_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    payload_kind TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    sha256 TEXT NOT NULL,\n'
  '    raw_bytes INTEGER NOT NULL,\n'
  '    gzip_bytes INTEGER NOT NULL,\n'
  '    payload_gzip BLOB NOT NULL,\n'
  '    UNIQUE (run_id, payload_kind, request_id, sha256)\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  '    mode TEXT NOT NULL,\n'
  '    config_json TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_run_events',
  'research_run_events',
  'CREATE TABLE research_run_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),\n"
  '    observed_at TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    detail_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'resolution_attempts',
  'resolution_attempts',
  'CREATE TABLE resolution_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    attempted_at TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    winner_index INTEGER,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (run_id, condition_id)\n'
  ')'),
 ('table',
  'resolution_observations',
  'resolution_observations',
  'CREATE TABLE resolution_observations (\n'
  '    resolution_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL UNIQUE,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    winner_index INTEGER NOT NULL CHECK (winner_index IN (0,1)),\n'
  '    request_id TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    evidence_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'schema_metadata',
  'schema_metadata',
  'CREATE TABLE schema_metadata (\n'
  '    singleton INTEGER PRIMARY KEY CHECK (singleton=1),\n'
  '    data_contract TEXT NOT NULL UNIQUE,\n'
  '    schema_profile TEXT NOT NULL,\n'
  '    universe_profile TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    league_mapping_sha256 TEXT NOT NULL,\n'
  '    migration_sha256 TEXT NOT NULL,\n'
  '    schema_sha256 TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'signal_decisions',
  'signal_decisions',
  'CREATE TABLE signal_decisions (\n'
  '    decision_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),\n'
  '    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    threshold REAL NOT NULL,\n'
  '    decided_at TEXT NOT NULL,\n'
  '    best_ask REAL,\n'
  '    entry_vwap REAL,\n'
  '    entry_shares REAL,\n'
  '    entry_cost REAL,\n'
  '    prior_entry_vwap REAL,\n'
  '    entry_provenance TEXT,\n'
  '    decision_status TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    episode_id TEXT,\n'
  '    UNIQUE (run_id, token_id, threshold)\n'
  ')'),
 ('table',
  'stop_execution_attempts',
  'stop_execution_attempts',
  'CREATE TABLE stop_execution_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    policy_id TEXT NOT NULL REFERENCES counterfactual_exit_policies(policy_id),\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    stop_price REAL NOT NULL,\n'
  '    prior_best_bid REAL,\n'
  '    trigger_best_bid REAL,\n'
  '    requested_shares REAL NOT NULL,\n'
  '    filled_shares REAL NOT NULL,\n'
  '    remaining_shares REAL NOT NULL,\n'
  '    exit_vwap REAL,\n'
  '    gross_proceeds REAL NOT NULL,\n'
  '    fee_rate REAL NOT NULL,\n'
  '    estimated_fee REAL NOT NULL,\n'
  '    net_proceeds REAL NOT NULL,\n'
  '    levels_used INTEGER NOT NULL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('FULL_EXIT','PARTIAL_FILL','NO_BID_DEPTH')),\n"
  '    gap_from_stop REAL,\n'
  '    drop_from_prior REAL,\n'
  '    UNIQUE (policy_id, run_id)\n'
  ')'),
 ('table',
  'storage_metrics',
  'storage_metrics',
  'CREATE TABLE storage_metrics (\n'
  '    metric_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL,\n'
  '    free_bytes INTEGER NOT NULL,\n'
  '    total_bytes INTEGER NOT NULL,\n'
  '    used_ratio REAL NOT NULL\n'
  ')'),
 ('trigger',
  'api_requests_forbid_delete',
  'api_requests',
  'CREATE TRIGGER api_requests_forbid_delete BEFORE DELETE ON api_requests BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'api_requests_forbid_update',
  'api_requests',
  'CREATE TRIGGER api_requests_forbid_update BEFORE UPDATE ON api_requests BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'counterfactual_exit_policies_forbid_delete',
  'counterfactual_exit_policies',
  'CREATE TRIGGER counterfactual_exit_policies_forbid_delete BEFORE DELETE ON counterfactual_exit_policies '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'counterfactual_exit_policies_forbid_update',
  'counterfactual_exit_policies',
  'CREATE TRIGGER counterfactual_exit_policies_forbid_update BEFORE UPDATE ON counterfactual_exit_policies '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'counterfactual_stop_exits_forbid_delete',
  'counterfactual_stop_exits',
  'CREATE TRIGGER counterfactual_stop_exits_forbid_delete BEFORE DELETE ON counterfactual_stop_exits BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'counterfactual_stop_exits_forbid_update',
  'counterfactual_stop_exits',
  'CREATE TRIGGER counterfactual_stop_exits_forbid_update BEFORE UPDATE ON counterfactual_stop_exits BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'data_quality_issues_forbid_delete',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_forbid_delete BEFORE DELETE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'data_quality_issues_forbid_update',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_forbid_update BEFORE UPDATE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'database_checks_forbid_delete',
  'database_checks',
  'CREATE TRIGGER database_checks_forbid_delete BEFORE DELETE ON database_checks BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'database_checks_forbid_update',
  'database_checks',
  'CREATE TRIGGER database_checks_forbid_update BEFORE UPDATE ON database_checks BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'episode_path_observations_forbid_delete',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_forbid_delete BEFORE DELETE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_path_observations_forbid_update',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_forbid_update BEFORE UPDATE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_observations_forbid_delete',
  'event_observations',
  'CREATE TRIGGER event_observations_forbid_delete BEFORE DELETE ON event_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_observations_forbid_update',
  'event_observations',
  'CREATE TRIGGER event_observations_forbid_update BEFORE UPDATE ON event_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'hypothetical_episodes_forbid_delete',
  'hypothetical_episodes',
  'CREATE TRIGGER hypothetical_episodes_forbid_delete BEFORE DELETE ON hypothetical_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'hypothetical_episodes_forbid_update',
  'hypothetical_episodes',
  'CREATE TRIGGER hypothetical_episodes_forbid_update BEFORE UPDATE ON hypothetical_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'league_registry_versions_forbid_delete',
  'league_registry_versions',
  'CREATE TRIGGER league_registry_versions_forbid_delete BEFORE DELETE ON league_registry_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'league_registry_versions_forbid_update',
  'league_registry_versions',
  'CREATE TRIGGER league_registry_versions_forbid_update BEFORE UPDATE ON league_registry_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_observations_forbid_delete',
  'market_observations',
  'CREATE TRIGGER market_observations_forbid_delete BEFORE DELETE ON market_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_observations_forbid_update',
  'market_observations',
  'CREATE TRIGGER market_observations_forbid_update BEFORE UPDATE ON market_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_sweeps_forbid_delete',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_forbid_delete BEFORE DELETE ON market_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'market_sweeps_forbid_update',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_forbid_update BEFORE UPDATE ON market_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'orderbook_levels_forbid_delete',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_forbid_delete BEFORE DELETE ON orderbook_levels BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'orderbook_levels_forbid_update',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_forbid_update BEFORE UPDATE ON orderbook_levels BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'orderbook_snapshots_forbid_delete',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_forbid_delete BEFORE DELETE ON orderbook_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_snapshots_forbid_update',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_forbid_update BEFORE UPDATE ON orderbook_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_token_attempts_forbid_delete',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_forbid_delete BEFORE DELETE ON orderbook_token_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_token_attempts_forbid_update',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_forbid_update BEFORE UPDATE ON orderbook_token_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'outcome_observations_forbid_delete',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_forbid_delete BEFORE DELETE ON outcome_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'outcome_observations_forbid_update',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_forbid_update BEFORE UPDATE ON outcome_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'raw_payloads_forbid_delete',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_forbid_delete BEFORE DELETE ON raw_payloads BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'raw_payloads_forbid_update',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_forbid_update BEFORE UPDATE ON raw_payloads BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_forbid_delete',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_forbid_delete BEFORE DELETE ON research_config_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_forbid_update',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_forbid_update BEFORE UPDATE ON research_config_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_run_events_forbid_delete',
  'research_run_events',
  'CREATE TRIGGER research_run_events_forbid_delete BEFORE DELETE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_run_events_forbid_update',
  'research_run_events',
  'CREATE TRIGGER research_run_events_forbid_update BEFORE UPDATE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_attempts_forbid_delete',
  'resolution_attempts',
  'CREATE TRIGGER resolution_attempts_forbid_delete BEFORE DELETE ON resolution_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_attempts_forbid_update',
  'resolution_attempts',
  'CREATE TRIGGER resolution_attempts_forbid_update BEFORE UPDATE ON resolution_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_observations_forbid_delete',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_forbid_delete BEFORE DELETE ON resolution_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_observations_forbid_update',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_forbid_update BEFORE UPDATE ON resolution_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'schema_metadata_forbid_delete',
  'schema_metadata',
  'CREATE TRIGGER schema_metadata_forbid_delete BEFORE DELETE ON schema_metadata BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'schema_metadata_forbid_update',
  'schema_metadata',
  'CREATE TRIGGER schema_metadata_forbid_update BEFORE UPDATE ON schema_metadata BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'signal_decisions_forbid_delete',
  'signal_decisions',
  'CREATE TRIGGER signal_decisions_forbid_delete BEFORE DELETE ON signal_decisions BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'signal_decisions_forbid_update',
  'signal_decisions',
  'CREATE TRIGGER signal_decisions_forbid_update BEFORE UPDATE ON signal_decisions BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'stop_execution_attempts_forbid_delete',
  'stop_execution_attempts',
  'CREATE TRIGGER stop_execution_attempts_forbid_delete BEFORE DELETE ON stop_execution_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'stop_execution_attempts_forbid_update',
  'stop_execution_attempts',
  'CREATE TRIGGER stop_execution_attempts_forbid_update BEFORE UPDATE ON stop_execution_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'storage_metrics_forbid_delete',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_forbid_delete BEFORE DELETE ON storage_metrics BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'storage_metrics_forbid_update',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_forbid_update BEFORE UPDATE ON storage_metrics BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"))

WATERMELON_RAW_TABLES = {'event_observations': ('watermelon-event-source-v1',
                        'CREATE TABLE event_observations (\n'
                        '    event_observation_id TEXT PRIMARY KEY,\n'
                        '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        '    source_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
                        '    page_number INTEGER NOT NULL,\n'
                        '    request_id TEXT NOT NULL,\n'
                        '    observed_at TEXT NOT NULL,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    event_title TEXT,\n'
                        '    event_slug TEXT,\n'
                        '    canonical_event_sha256 TEXT NOT NULL,\n'
                        '    sport_id TEXT,\n'
                        '    sport_code TEXT,\n'
                        '    sport_name TEXT,\n'
                        '    sport_primary_tag_id TEXT,\n'
                        '    sport_series_id TEXT,\n'
                        '    series_slug TEXT,\n'
                        '    tag_ids_json TEXT NOT NULL,\n'
                        '    tag_slugs_json TEXT NOT NULL,\n'
                        '    series_ids_json TEXT NOT NULL,\n'
                        '    series_slugs_json TEXT NOT NULL,\n'
                        '    team_leagues_json TEXT NOT NULL,\n'
                        '    sport_json TEXT NOT NULL,\n'
                        '    tags_json TEXT NOT NULL,\n'
                        '    series_json TEXT NOT NULL,\n'
                        '    teams_json TEXT NOT NULL,\n'
                        '    classifier_version TEXT NOT NULL,\n'
                        '    league_mapping_sha256 TEXT NOT NULL,\n'
                        '    league_code TEXT,\n'
                        '    league_name TEXT,\n'
                        '    classification_status TEXT NOT NULL CHECK (classification_status IN '
                        "('ACCEPTED','REJECTED','DRIFT')),\n"
                        '    rejection_reason TEXT NOT NULL,\n'
                        '    classification_evidence_json TEXT NOT NULL,\n'
                        '    UNIQUE (sweep_id, event_id)\n'
                        ')',
                        ('event_title',
                         'event_slug',
                         'canonical_event_sha256',
                         'sport_id',
                         'sport_code',
                         'sport_name',
                         'sport_primary_tag_id',
                         'sport_series_id',
                         'series_slug',
                         'tag_ids_json',
                         'tag_slugs_json',
                         'series_ids_json',
                         'series_slugs_json',
                         'team_leagues_json'),
                        (),
                        'CREATE TABLE event_observations (\n'
                        '    event_observation_id TEXT PRIMARY KEY,\n'
                        '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        '    source_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
                        '    page_number INTEGER NOT NULL,\n'
                        '    request_id TEXT NOT NULL,\n'
                        '    observed_at TEXT NOT NULL,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    sport_json TEXT NOT NULL,\n'
                        '    tags_json TEXT NOT NULL,\n'
                        '    series_json TEXT NOT NULL,\n'
                        '    teams_json TEXT NOT NULL,\n'
                        '    classifier_version TEXT NOT NULL,\n'
                        '    league_mapping_sha256 TEXT NOT NULL,\n'
                        '    league_code TEXT,\n'
                        '    league_name TEXT,\n'
                        '    classification_status TEXT NOT NULL CHECK (classification_status IN '
                        "('ACCEPTED','REJECTED','DRIFT')),\n"
                        '    rejection_reason TEXT NOT NULL,\n'
                        '    classification_evidence_json TEXT NOT NULL,\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                        '    UNIQUE (sweep_id, event_id)\n'
                        ')'),
 'market_observations': ('watermelon-market-source-v1',
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    event_observation_id TEXT NOT NULL REFERENCES '
                         'event_observations(event_observation_id),\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    event_id TEXT NOT NULL,\n'
                         '    event_title TEXT,\n'
                         '    condition_id TEXT,\n'
                         '    market_id TEXT,\n'
                         '    question TEXT,\n'
                         '    group_item_title TEXT,\n'
                         '    sports_market_type TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    end_date TEXT,\n'
                         '    game_start_time TEXT,\n'
                         '    hours_until_end REAL,\n'
                         '    sports_phase TEXT NOT NULL,\n'
                         '    event_live INTEGER,\n'
                         '    event_ended INTEGER,\n'
                         '    event_game_status TEXT,\n'
                         '    liquidity REAL,\n'
                         '    volume_total REAL,\n'
                         '    active INTEGER,\n'
                         '    closed INTEGER,\n'
                         '    accepting_orders INTEGER,\n'
                         '    enable_order_book INTEGER,\n'
                         '    neg_risk INTEGER,\n'
                         '    match_winner_class TEXT NOT NULL,\n'
                         '    eligible_outcome_indices_json TEXT NOT NULL,\n'
                         '    classification_evidence_json TEXT NOT NULL,\n'
                         '    cadence_arm TEXT NOT NULL,\n'
                         '    fee_rate REAL,\n'
                         '    fee_schedule_json TEXT NOT NULL,\n'
                         '    outcome_labels_json TEXT NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
                         '    exclusion_reason TEXT NOT NULL,\n'
                         '    normalized_json TEXT NOT NULL,\n'
                         '    UNIQUE (sweep_id, event_id, condition_id)\n'
                         ')',
                         ('event_title',
                          'condition_id',
                          'market_id',
                          'question',
                          'group_item_title',
                          'sports_market_type',
                          'end_date',
                          'game_start_time',
                          'event_live',
                          'event_ended',
                          'event_game_status',
                          'liquidity',
                          'volume_total',
                          'active',
                          'closed',
                          'accepting_orders',
                          'enable_order_book',
                          'neg_risk',
                          'fee_schedule_json'),
                         ('condition_id',),
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    event_observation_id TEXT NOT NULL REFERENCES '
                         'event_observations(event_observation_id),\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    event_id TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    hours_until_end REAL,\n'
                         '    sports_phase TEXT NOT NULL,\n'
                         '    match_winner_class TEXT NOT NULL,\n'
                         '    eligible_outcome_indices_json TEXT NOT NULL,\n'
                         '    classification_evidence_json TEXT NOT NULL,\n'
                         '    cadence_arm TEXT NOT NULL,\n'
                         '    fee_rate REAL,\n'
                         '    outcome_labels_json TEXT NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
                         '    exclusion_reason TEXT NOT NULL,\n'
                         '    normalized_json TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (sweep_id, event_id, condition_id)\n'
                         ')'),
 'outcome_observations': ('black-outcome-source-v1',
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    market_observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(observation_id),\n'
                          '    sweep_id TEXT NOT NULL,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    event_id TEXT NOT NULL,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    outcome_index INTEGER NOT NULL,\n'
                          '    outcome_label TEXT NOT NULL,\n'
                          '    entry_eligible INTEGER NOT NULL CHECK (entry_eligible IN (0,1)),\n'
                          '    gamma_probability REAL,\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    UNIQUE (sweep_id, token_id)\n'
                          ')',
                          ('condition_id',
                           'event_id',
                           'token_id',
                           'outcome_index',
                           'outcome_label',
                           'gamma_probability'),
                          ('condition_id', 'event_id', 'token_id'),
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    market_observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(observation_id),\n'
                          '    sweep_id TEXT NOT NULL,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    event_id TEXT NOT NULL,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    entry_eligible INTEGER NOT NULL CHECK (entry_eligible IN (0,1)),\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                          '    UNIQUE (sweep_id, token_id)\n'
                          ')'),
 'orderbook_snapshots': ('black-book-source-v1',
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    request_id TEXT NOT NULL,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    raw_book_sha256 TEXT NOT NULL,\n'
                         '    best_bid REAL,\n'
                         '    best_ask REAL,\n'
                         '    bid_level_count INTEGER NOT NULL,\n'
                         '    ask_level_count INTEGER NOT NULL,\n'
                         '    source_timestamp TEXT,\n'
                         '    tick_size REAL,\n'
                         '    min_order_size REAL,\n'
                         '    UNIQUE (run_id, token_id)\n'
                         ')',
                         ('token_id',
                          'best_bid',
                          'best_ask',
                          'bid_level_count',
                          'ask_level_count',
                          'source_timestamp',
                          'tick_size',
                          'min_order_size'),
                         ('token_id',),
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    request_id TEXT NOT NULL,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    raw_book_sha256 TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (run_id, token_id)\n'
                         ')')}

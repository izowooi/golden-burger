"""Reviewed Cherry shadow v2: original 18 tables, 4 indexes, 36 guards.

Public source fields and selected copies are shared. Notional computations,
policy choices, experiment provenance and hypothetical economics stay private.
"""

CHERRY_LOGICAL_SCHEMA_SHA256 = '4a1855a155f77ff9a1f838005af50a62daa0377554638caf41a7608e4c04facf'

CHERRY_SCHEMA_OBJECTS = (('index',
  'shadow_episodes_cluster_idx',
  'shadow_episodes',
  'CREATE INDEX shadow_episodes_cluster_idx\n    ON shadow_episodes(event_cluster_id, band_id, entered_at)'),
 ('index',
  'shadow_market_event_idx',
  'shadow_market_observations',
  'CREATE INDEX shadow_market_event_idx\n    ON shadow_market_observations(event_cluster_id, condition_id)'),
 ('index',
  'shadow_memberships_condition_idx',
  'shadow_sweep_memberships',
  'CREATE INDEX shadow_memberships_condition_idx\n    ON shadow_sweep_memberships(condition_id, sweep_id)'),
 ('index',
  'shadow_run_events_run_idx',
  'shadow_run_events',
  'CREATE INDEX shadow_run_events_run_idx\n    ON shadow_run_events(run_id, observed_at)'),
 ('table',
  'shadow_api_attempts',
  'shadow_api_attempts',
  'CREATE TABLE shadow_api_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method = 'GET'),\n"
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL NOT NULL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED')),\n"
  '    http_status INTEGER,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'shadow_book_attempts',
  'shadow_book_attempts',
  'CREATE TABLE shadow_book_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    purpose TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    source_received_at TEXT,\n'
  '    error_type TEXT,\n'
  '    UNIQUE(run_id, token_id)\n'
  ')'),
 ('table',
  'shadow_book_levels',
  'shadow_book_levels',
  'CREATE TABLE shadow_book_levels (\n'
  '    level_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES shadow_book_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),\n"
  '    level_index INTEGER NOT NULL,\n'
  '    price REAL NOT NULL,\n'
  '    size REAL NOT NULL,\n'
  '    UNIQUE(snapshot_id, side, level_index)\n'
  ')'),
 ('table',
  'shadow_book_snapshots',
  'shadow_book_snapshots',
  'CREATE TABLE shadow_book_snapshots (\n'
  '    snapshot_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    raw_book_sha256 TEXT NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    market_hash TEXT,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    UNIQUE(run_id, token_id)\n'
  ')'),
 ('table',
  'shadow_cell_decisions',
  'shadow_cell_decisions',
  'CREATE TABLE shadow_cell_decisions (\n'
  '    decision_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    market_observation_id TEXT NOT NULL REFERENCES shadow_market_observations(observation_id),\n'
  '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    band_id TEXT NOT NULL,\n'
  '    band_role TEXT NOT NULL,\n'
  '    band_low REAL NOT NULL,\n'
  '    band_high REAL NOT NULL,\n'
  '    decided_at TEXT NOT NULL,\n'
  '    entry_best_ask REAL,\n'
  '    entry_vwap REAL,\n'
  '    entry_shares REAL,\n'
  '    entry_cost REAL,\n'
  '    decision_status TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    episode_id TEXT,\n'
  '    UNIQUE(run_id, condition_id, token_id, band_id)\n'
  ')'),
 ('table',
  'shadow_config_versions',
  'shadow_config_versions',
  'CREATE TABLE shadow_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  '    data_contract TEXT NOT NULL,\n'
  '    runtime_job TEXT NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode = 'shadow'),\n"
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_id TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    config_json TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'shadow_data_quality_issues',
  'shadow_data_quality_issues',
  'CREATE TABLE shadow_data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    severity TEXT NOT NULL,\n'
  '    issue_type TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    token_id TEXT,\n'
  '    detail_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'shadow_episode_policies',
  'shadow_episode_policies',
  'CREATE TABLE shadow_episode_policies (\n'
  '    episode_policy_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),\n'
  '    policy_id TEXT NOT NULL,\n'
  '    policy_role TEXT NOT NULL,\n'
  '    take_profit REAL,\n'
  '    stop_loss REAL,\n'
  '    trailing REAL,\n'
  '    created_at TEXT NOT NULL,\n'
  '    UNIQUE(episode_id, policy_id)\n'
  ')'),
 ('table',
  'shadow_episodes',
  'shadow_episodes',
  'CREATE TABLE shadow_episodes (\n'
  '    episode_id TEXT PRIMARY KEY,\n'
  '    opened_run_id TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    question TEXT,\n'
  '    category TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    band_id TEXT NOT NULL,\n'
  '    band_role TEXT NOT NULL,\n'
  '    entered_at TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    time_stratum TEXT NOT NULL,\n'
  '    end_date TEXT,\n'
  '    game_start_time TEXT,\n'
  '    liquidity REAL NOT NULL,\n'
  '    volume_total REAL NOT NULL,\n'
  '    entry_best_ask REAL NOT NULL,\n'
  '    entry_vwap REAL NOT NULL,\n'
  '    entry_shares REAL NOT NULL,\n'
  '    entry_cost REAL NOT NULL,\n'
  '    UNIQUE(condition_id, token_id, band_id)\n'
  ')'),
 ('table',
  'shadow_market_observations',
  'shadow_market_observations',
  'CREATE TABLE shadow_market_observations (\n'
  '    observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    deterministic_ordinal INTEGER NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_slug TEXT,\n'
  '    event_title TEXT,\n'
  '    category TEXT,\n'
  '    event_tags_json TEXT NOT NULL,\n'
  '    market_tags_json TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    market_slug TEXT,\n'
  '    question TEXT,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    end_date TEXT,\n'
  '    game_start_time TEXT,\n'
  '    entry_reference TEXT,\n'
  '    hours_until_entry_reference REAL,\n'
  '    sports_market_type TEXT,\n'
  '    time_stratum TEXT NOT NULL,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    accepting_orders INTEGER,\n'
  '    enable_order_book INTEGER,\n'
  '    outcomes_json TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    gamma_probabilities_json TEXT NOT NULL,\n'
  '    primary_outcome_index INTEGER,\n'
  '    primary_outcome_label TEXT,\n'
  '    primary_token_id TEXT,\n'
  '    primary_gamma_probability REAL,\n'
  '    identity_aligned INTEGER NOT NULL CHECK (identity_aligned IN (0,1)),\n'
  '    eligibility_status TEXT NOT NULL,\n'
  '    exclusion_reasons_json TEXT NOT NULL,\n'
  '    book_selection_status TEXT NOT NULL,\n'
  '    UNIQUE(sweep_id, condition_id)\n'
  ')'),
 ('table',
  'shadow_market_sweeps',
  'shadow_market_sweeps',
  'CREATE TABLE shadow_market_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    page_count INTEGER NOT NULL,\n'
  '    raw_market_count INTEGER NOT NULL,\n'
  '    unique_condition_count INTEGER NOT NULL,\n'
  '    eligible_candidate_count INTEGER NOT NULL,\n'
  '    selected_book_count INTEGER NOT NULL,\n'
  '    capped_candidate_count INTEGER NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),\n'
  '    membership_sha256 TEXT NOT NULL,\n'
  '    request_envelope_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'shadow_path_observations',
  'shadow_path_observations',
  'CREATE TABLE shadow_path_observations (\n'
  '    path_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    source_received_at TEXT,\n'
  '    best_bid REAL,\n'
  '    executable_bid_vwap REAL,\n'
  '    executable_proceeds REAL,\n'
  '    filled_shares REAL NOT NULL,\n'
  '    remaining_shares REAL NOT NULL,\n'
  '    depth_complete INTEGER NOT NULL CHECK (depth_complete IN (0,1)),\n'
  '    peak_executable_bid_vwap REAL,\n'
  '    path_status TEXT NOT NULL,\n'
  '    UNIQUE(episode_id, run_id)\n'
  ')'),
 ('table',
  'shadow_policy_exits',
  'shadow_policy_exits',
  'CREATE TABLE shadow_policy_exits (\n'
  '    exit_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),\n'
  '    policy_id TEXT NOT NULL,\n'
  '    run_id TEXT NOT NULL,\n'
  '    exited_at TEXT NOT NULL,\n'
  '    source_received_at TEXT,\n'
  '    exit_kind TEXT NOT NULL,\n'
  '    trigger_value REAL,\n'
  '    exit_price_vwap REAL,\n'
  '    exit_proceeds REAL NOT NULL,\n'
  '    pnl_usdc REAL NOT NULL,\n'
  '    roi REAL NOT NULL,\n'
  '    resolution_id TEXT REFERENCES shadow_resolution_observations(resolution_id),\n'
  '    evidence_basis TEXT NOT NULL,\n'
  '    UNIQUE(episode_id, policy_id)\n'
  ')'),
 ('table',
  'shadow_raw_payloads',
  'shadow_raw_payloads',
  'CREATE TABLE shadow_raw_payloads (\n'
  '    payload_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    payload_kind TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    sha256 TEXT NOT NULL,\n'
  '    raw_bytes INTEGER NOT NULL,\n'
  '    gzip_bytes INTEGER NOT NULL,\n'
  '    payload_gzip BLOB NOT NULL,\n'
  '    UNIQUE(run_id, payload_kind, request_id, sha256)\n'
  ')'),
 ('table',
  'shadow_resolution_observations',
  'shadow_resolution_observations',
  'CREATE TABLE shadow_resolution_observations (\n'
  '    resolution_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    resolution_status TEXT NOT NULL,\n'
  '    outcomes_json TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    final_prices_json TEXT NOT NULL,\n'
  '    winner_index INTEGER,\n'
  '    token_payout REAL,\n'
  '    evidence_basis TEXT NOT NULL,\n'
  '    UNIQUE(run_id, condition_id, token_id)\n'
  ')'),
 ('table',
  'shadow_run_events',
  'shadow_run_events',
  'CREATE TABLE shadow_run_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),\n"
  '    observed_at TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    detail_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'shadow_schema_metadata',
  'shadow_schema_metadata',
  'CREATE TABLE shadow_schema_metadata (\n'
  '    data_contract TEXT PRIMARY KEY,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'shadow_sweep_memberships',
  'shadow_sweep_memberships',
  'CREATE TABLE shadow_sweep_memberships (\n'
  '    membership_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    page_number INTEGER NOT NULL,\n'
  '    page_ordinal INTEGER NOT NULL,\n'
  '    deterministic_ordinal INTEGER NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    qualification_status TEXT NOT NULL,\n'
  '    exclusion_reasons_json TEXT NOT NULL,\n'
  '    UNIQUE(sweep_id, page_number, page_ordinal)\n'
  ')'),
 ('trigger',
  'shadow_api_attempts_deny_delete',
  'shadow_api_attempts',
  'CREATE TRIGGER shadow_api_attempts_deny_delete BEFORE DELETE ON shadow_api_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_api_attempts_deny_update',
  'shadow_api_attempts',
  'CREATE TRIGGER shadow_api_attempts_deny_update BEFORE UPDATE ON shadow_api_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_attempts_deny_delete',
  'shadow_book_attempts',
  'CREATE TRIGGER shadow_book_attempts_deny_delete BEFORE DELETE ON shadow_book_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_attempts_deny_update',
  'shadow_book_attempts',
  'CREATE TRIGGER shadow_book_attempts_deny_update BEFORE UPDATE ON shadow_book_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_levels_deny_delete',
  'shadow_book_levels',
  'CREATE TRIGGER shadow_book_levels_deny_delete BEFORE DELETE ON shadow_book_levels BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_levels_deny_update',
  'shadow_book_levels',
  'CREATE TRIGGER shadow_book_levels_deny_update BEFORE UPDATE ON shadow_book_levels BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_snapshots_deny_delete',
  'shadow_book_snapshots',
  'CREATE TRIGGER shadow_book_snapshots_deny_delete BEFORE DELETE ON shadow_book_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_book_snapshots_deny_update',
  'shadow_book_snapshots',
  'CREATE TRIGGER shadow_book_snapshots_deny_update BEFORE UPDATE ON shadow_book_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_cell_decisions_deny_delete',
  'shadow_cell_decisions',
  'CREATE TRIGGER shadow_cell_decisions_deny_delete BEFORE DELETE ON shadow_cell_decisions BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_cell_decisions_deny_update',
  'shadow_cell_decisions',
  'CREATE TRIGGER shadow_cell_decisions_deny_update BEFORE UPDATE ON shadow_cell_decisions BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_config_versions_deny_delete',
  'shadow_config_versions',
  'CREATE TRIGGER shadow_config_versions_deny_delete BEFORE DELETE ON shadow_config_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_config_versions_deny_update',
  'shadow_config_versions',
  'CREATE TRIGGER shadow_config_versions_deny_update BEFORE UPDATE ON shadow_config_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_data_quality_issues_deny_delete',
  'shadow_data_quality_issues',
  'CREATE TRIGGER shadow_data_quality_issues_deny_delete BEFORE DELETE ON shadow_data_quality_issues BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_data_quality_issues_deny_update',
  'shadow_data_quality_issues',
  'CREATE TRIGGER shadow_data_quality_issues_deny_update BEFORE UPDATE ON shadow_data_quality_issues BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_episode_policies_deny_delete',
  'shadow_episode_policies',
  'CREATE TRIGGER shadow_episode_policies_deny_delete BEFORE DELETE ON shadow_episode_policies BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_episode_policies_deny_update',
  'shadow_episode_policies',
  'CREATE TRIGGER shadow_episode_policies_deny_update BEFORE UPDATE ON shadow_episode_policies BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_episodes_deny_delete',
  'shadow_episodes',
  'CREATE TRIGGER shadow_episodes_deny_delete BEFORE DELETE ON shadow_episodes BEGIN SELECT RAISE(ABORT, '
  "'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_episodes_deny_update',
  'shadow_episodes',
  'CREATE TRIGGER shadow_episodes_deny_update BEFORE UPDATE ON shadow_episodes BEGIN SELECT RAISE(ABORT, '
  "'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_market_observations_deny_delete',
  'shadow_market_observations',
  'CREATE TRIGGER shadow_market_observations_deny_delete BEFORE DELETE ON shadow_market_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_market_observations_deny_update',
  'shadow_market_observations',
  'CREATE TRIGGER shadow_market_observations_deny_update BEFORE UPDATE ON shadow_market_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_market_sweeps_deny_delete',
  'shadow_market_sweeps',
  'CREATE TRIGGER shadow_market_sweeps_deny_delete BEFORE DELETE ON shadow_market_sweeps BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_market_sweeps_deny_update',
  'shadow_market_sweeps',
  'CREATE TRIGGER shadow_market_sweeps_deny_update BEFORE UPDATE ON shadow_market_sweeps BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_path_observations_deny_delete',
  'shadow_path_observations',
  'CREATE TRIGGER shadow_path_observations_deny_delete BEFORE DELETE ON shadow_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_path_observations_deny_update',
  'shadow_path_observations',
  'CREATE TRIGGER shadow_path_observations_deny_update BEFORE UPDATE ON shadow_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_policy_exits_deny_delete',
  'shadow_policy_exits',
  'CREATE TRIGGER shadow_policy_exits_deny_delete BEFORE DELETE ON shadow_policy_exits BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_policy_exits_deny_update',
  'shadow_policy_exits',
  'CREATE TRIGGER shadow_policy_exits_deny_update BEFORE UPDATE ON shadow_policy_exits BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_raw_payloads_deny_delete',
  'shadow_raw_payloads',
  'CREATE TRIGGER shadow_raw_payloads_deny_delete BEFORE DELETE ON shadow_raw_payloads BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_raw_payloads_deny_update',
  'shadow_raw_payloads',
  'CREATE TRIGGER shadow_raw_payloads_deny_update BEFORE UPDATE ON shadow_raw_payloads BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_resolution_observations_deny_delete',
  'shadow_resolution_observations',
  'CREATE TRIGGER shadow_resolution_observations_deny_delete BEFORE DELETE ON shadow_resolution_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_resolution_observations_deny_update',
  'shadow_resolution_observations',
  'CREATE TRIGGER shadow_resolution_observations_deny_update BEFORE UPDATE ON shadow_resolution_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_run_events_deny_delete',
  'shadow_run_events',
  'CREATE TRIGGER shadow_run_events_deny_delete BEFORE DELETE ON shadow_run_events BEGIN SELECT RAISE(ABORT, '
  "'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_run_events_deny_update',
  'shadow_run_events',
  'CREATE TRIGGER shadow_run_events_deny_update BEFORE UPDATE ON shadow_run_events BEGIN SELECT RAISE(ABORT, '
  "'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_schema_metadata_deny_delete',
  'shadow_schema_metadata',
  'CREATE TRIGGER shadow_schema_metadata_deny_delete BEFORE DELETE ON shadow_schema_metadata BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_schema_metadata_deny_update',
  'shadow_schema_metadata',
  'CREATE TRIGGER shadow_schema_metadata_deny_update BEFORE UPDATE ON shadow_schema_metadata BEGIN SELECT '
  "RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_sweep_memberships_deny_delete',
  'shadow_sweep_memberships',
  'CREATE TRIGGER shadow_sweep_memberships_deny_delete BEFORE DELETE ON shadow_sweep_memberships BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"),
 ('trigger',
  'shadow_sweep_memberships_deny_update',
  'shadow_sweep_memberships',
  'CREATE TRIGGER shadow_sweep_memberships_deny_update BEFORE UPDATE ON shadow_sweep_memberships BEGIN '
  "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"))

CHERRY_RAW_TABLES = {'shadow_market_observations': ('cherry-shadow-market-source-v2',
                                'CREATE TABLE shadow_market_observations (\n'
                                '    observation_id TEXT PRIMARY KEY,\n'
                                '    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),\n'
                                '    run_id TEXT NOT NULL,\n'
                                '    deterministic_ordinal INTEGER NOT NULL,\n'
                                '    event_cluster_id TEXT NOT NULL,\n'
                                '    event_id TEXT NOT NULL,\n'
                                '    event_slug TEXT,\n'
                                '    event_title TEXT,\n'
                                '    category TEXT,\n'
                                '    event_tags_json TEXT NOT NULL,\n'
                                '    market_tags_json TEXT NOT NULL,\n'
                                '    condition_id TEXT NOT NULL,\n'
                                '    market_id TEXT,\n'
                                '    market_slug TEXT,\n'
                                '    question TEXT,\n'
                                '    source_received_at TEXT NOT NULL,\n'
                                '    end_date TEXT,\n'
                                '    game_start_time TEXT,\n'
                                '    entry_reference TEXT,\n'
                                '    hours_until_entry_reference REAL,\n'
                                '    sports_market_type TEXT,\n'
                                '    time_stratum TEXT NOT NULL,\n'
                                '    liquidity REAL,\n'
                                '    volume_total REAL,\n'
                                '    active INTEGER,\n'
                                '    closed INTEGER,\n'
                                '    accepting_orders INTEGER,\n'
                                '    enable_order_book INTEGER,\n'
                                '    outcomes_json TEXT NOT NULL,\n'
                                '    token_ids_json TEXT NOT NULL,\n'
                                '    gamma_probabilities_json TEXT NOT NULL,\n'
                                '    primary_outcome_index INTEGER,\n'
                                '    primary_outcome_label TEXT,\n'
                                '    primary_token_id TEXT,\n'
                                '    primary_gamma_probability REAL,\n'
                                '    identity_aligned INTEGER NOT NULL CHECK (identity_aligned IN (0,1)),\n'
                                '    eligibility_status TEXT NOT NULL,\n'
                                '    exclusion_reasons_json TEXT NOT NULL,\n'
                                '    book_selection_status TEXT NOT NULL,\n'
                                '    UNIQUE(sweep_id, condition_id)\n'
                                ')',
                                ('event_id',
                                 'event_slug',
                                 'event_title',
                                 'category',
                                 'condition_id',
                                 'market_id',
                                 'market_slug',
                                 'question',
                                 'end_date',
                                 'game_start_time',
                                 'sports_market_type',
                                 'liquidity',
                                 'volume_total',
                                 'active',
                                 'closed',
                                 'accepting_orders',
                                 'enable_order_book',
                                 'primary_outcome_label',
                                 'primary_token_id',
                                 'primary_gamma_probability'),
                                ('event_id', 'condition_id', 'primary_token_id'),
                                'CREATE TABLE shadow_market_observations (\n'
                                '    observation_id TEXT PRIMARY KEY,\n'
                                '    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),\n'
                                '    run_id TEXT NOT NULL,\n'
                                '    deterministic_ordinal INTEGER NOT NULL,\n'
                                '    event_cluster_id TEXT NOT NULL,\n'
                                '    event_id TEXT NOT NULL,\n'
                                '    event_tags_json TEXT NOT NULL,\n'
                                '    market_tags_json TEXT NOT NULL,\n'
                                '    condition_id TEXT NOT NULL,\n'
                                '    source_received_at TEXT NOT NULL,\n'
                                '    entry_reference TEXT,\n'
                                '    hours_until_entry_reference REAL,\n'
                                '    time_stratum TEXT NOT NULL,\n'
                                '    outcomes_json TEXT NOT NULL,\n'
                                '    token_ids_json TEXT NOT NULL,\n'
                                '    gamma_probabilities_json TEXT NOT NULL,\n'
                                '    primary_outcome_index INTEGER,\n'
                                '    primary_token_id TEXT,\n'
                                '    identity_aligned INTEGER NOT NULL CHECK (identity_aligned IN (0,1)),\n'
                                '    eligibility_status TEXT NOT NULL,\n'
                                '    exclusion_reasons_json TEXT NOT NULL,\n'
                                '    book_selection_status TEXT NOT NULL,\n'
                                '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                                '    UNIQUE(sweep_id, condition_id)\n'
                                ')',
                                None,
                                'primary_token_id'),
 'shadow_book_snapshots': ('cherry-shadow-book-source-v2',
                           'CREATE TABLE shadow_book_snapshots (\n'
                           '    snapshot_id TEXT PRIMARY KEY,\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    request_id TEXT NOT NULL,\n'
                           '    source_received_at TEXT NOT NULL,\n'
                           '    raw_book_sha256 TEXT NOT NULL,\n'
                           '    source_timestamp TEXT,\n'
                           '    market_hash TEXT,\n'
                           '    best_bid REAL,\n'
                           '    best_ask REAL,\n'
                           '    bid_level_count INTEGER NOT NULL,\n'
                           '    ask_level_count INTEGER NOT NULL,\n'
                           '    UNIQUE(run_id, token_id)\n'
                           ')',
                           ('token_id',
                            'source_timestamp',
                            'market_hash',
                            'best_bid',
                            'best_ask',
                            'bid_level_count',
                            'ask_level_count'),
                           ('token_id',),
                           'CREATE TABLE shadow_book_snapshots (\n'
                           '    snapshot_id TEXT PRIMARY KEY,\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    request_id TEXT NOT NULL,\n'
                           '    source_received_at TEXT NOT NULL,\n'
                           '    raw_book_sha256 TEXT NOT NULL,\n'
                           '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                           '    UNIQUE(run_id, token_id)\n'
                           ')',
                           None,
                           None),
 'shadow_resolution_observations': ('cherry-shadow-resolution-source-v2',
                                    'CREATE TABLE shadow_resolution_observations (\n'
                                    '    resolution_id TEXT PRIMARY KEY,\n'
                                    '    run_id TEXT NOT NULL,\n'
                                    '    condition_id TEXT NOT NULL,\n'
                                    '    token_id TEXT NOT NULL,\n'
                                    '    request_id TEXT NOT NULL,\n'
                                    '    source_received_at TEXT NOT NULL,\n'
                                    '    resolution_status TEXT NOT NULL,\n'
                                    '    outcomes_json TEXT NOT NULL,\n'
                                    '    token_ids_json TEXT NOT NULL,\n'
                                    '    final_prices_json TEXT NOT NULL,\n'
                                    '    winner_index INTEGER,\n'
                                    '    token_payout REAL,\n'
                                    '    evidence_basis TEXT NOT NULL,\n'
                                    '    UNIQUE(run_id, condition_id, token_id)\n'
                                    ')',
                                    ('condition_id', 'token_id', 'winner_index', 'token_payout'),
                                    ('condition_id', 'token_id'),
                                    'CREATE TABLE shadow_resolution_observations (\n'
                                    '    resolution_id TEXT PRIMARY KEY,\n'
                                    '    run_id TEXT NOT NULL,\n'
                                    '    condition_id TEXT NOT NULL,\n'
                                    '    token_id TEXT NOT NULL,\n'
                                    '    request_id TEXT NOT NULL,\n'
                                    '    source_received_at TEXT NOT NULL,\n'
                                    '    resolution_status TEXT NOT NULL,\n'
                                    '    outcomes_json TEXT NOT NULL,\n'
                                    '    token_ids_json TEXT NOT NULL,\n'
                                    '    final_prices_json TEXT NOT NULL,\n'
                                    '    evidence_basis TEXT NOT NULL,\n'
                                    '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                                    '    UNIQUE(run_id, condition_id, token_id)\n'
                                    ')',
                                    None,
                                    None),
 'shadow_episodes': ('cherry-shadow-episode-source-v2',
                     'CREATE TABLE shadow_episodes (\n'
                     '    episode_id TEXT PRIMARY KEY,\n'
                     '    opened_run_id TEXT NOT NULL,\n'
                     '    config_hash TEXT NOT NULL,\n'
                     '    strategy_source_digest TEXT NOT NULL,\n'
                     '    preregistration_sha256 TEXT NOT NULL,\n'
                     '    event_cluster_id TEXT NOT NULL,\n'
                     '    event_id TEXT NOT NULL,\n'
                     '    condition_id TEXT NOT NULL,\n'
                     '    question TEXT,\n'
                     '    category TEXT,\n'
                     '    token_id TEXT NOT NULL,\n'
                     '    outcome_index INTEGER NOT NULL,\n'
                     '    outcome_label TEXT NOT NULL,\n'
                     '    band_id TEXT NOT NULL,\n'
                     '    band_role TEXT NOT NULL,\n'
                     '    entered_at TEXT NOT NULL,\n'
                     '    source_received_at TEXT NOT NULL,\n'
                     '    time_stratum TEXT NOT NULL,\n'
                     '    end_date TEXT,\n'
                     '    game_start_time TEXT,\n'
                     '    liquidity REAL NOT NULL,\n'
                     '    volume_total REAL NOT NULL,\n'
                     '    entry_best_ask REAL NOT NULL,\n'
                     '    entry_vwap REAL NOT NULL,\n'
                     '    entry_shares REAL NOT NULL,\n'
                     '    entry_cost REAL NOT NULL,\n'
                     '    UNIQUE(condition_id, token_id, band_id)\n'
                     ')',
                     ('event_id',
                      'condition_id',
                      'question',
                      'category',
                      'token_id',
                      'outcome_label',
                      'end_date',
                      'game_start_time',
                      'liquidity',
                      'volume_total',
                      'entry_best_ask'),
                     ('event_id', 'condition_id', 'token_id'),
                     'CREATE TABLE shadow_episodes (\n'
                     '    episode_id TEXT PRIMARY KEY,\n'
                     '    opened_run_id TEXT NOT NULL,\n'
                     '    config_hash TEXT NOT NULL,\n'
                     '    strategy_source_digest TEXT NOT NULL,\n'
                     '    preregistration_sha256 TEXT NOT NULL,\n'
                     '    event_cluster_id TEXT NOT NULL,\n'
                     '    event_id TEXT NOT NULL,\n'
                     '    condition_id TEXT NOT NULL,\n'
                     '    token_id TEXT NOT NULL,\n'
                     '    outcome_index INTEGER NOT NULL,\n'
                     '    band_id TEXT NOT NULL,\n'
                     '    band_role TEXT NOT NULL,\n'
                     '    entered_at TEXT NOT NULL,\n'
                     '    source_received_at TEXT NOT NULL,\n'
                     '    time_stratum TEXT NOT NULL,\n'
                     '    entry_vwap REAL NOT NULL,\n'
                     '    entry_shares REAL NOT NULL,\n'
                     '    entry_cost REAL NOT NULL,\n'
                     '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                     '    UNIQUE(condition_id, token_id, band_id)\n'
                     ')',
                     None,
                     None),
 'shadow_cell_decisions': ('cherry-shadow-decision-source-v2',
                           'CREATE TABLE shadow_cell_decisions (\n'
                           '    decision_id TEXT PRIMARY KEY,\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    market_observation_id TEXT NOT NULL REFERENCES '
                           'shadow_market_observations(observation_id),\n'
                           '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
                           '    event_cluster_id TEXT NOT NULL,\n'
                           '    condition_id TEXT NOT NULL,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    band_id TEXT NOT NULL,\n'
                           '    band_role TEXT NOT NULL,\n'
                           '    band_low REAL NOT NULL,\n'
                           '    band_high REAL NOT NULL,\n'
                           '    decided_at TEXT NOT NULL,\n'
                           '    entry_best_ask REAL,\n'
                           '    entry_vwap REAL,\n'
                           '    entry_shares REAL,\n'
                           '    entry_cost REAL,\n'
                           '    decision_status TEXT NOT NULL,\n'
                           '    details_json TEXT NOT NULL,\n'
                           '    episode_id TEXT,\n'
                           '    UNIQUE(run_id, condition_id, token_id, band_id)\n'
                           ')',
                           ('condition_id', 'token_id', 'entry_best_ask'),
                           ('condition_id', 'token_id'),
                           'CREATE TABLE shadow_cell_decisions (\n'
                           '    decision_id TEXT PRIMARY KEY,\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    market_observation_id TEXT NOT NULL REFERENCES '
                           'shadow_market_observations(observation_id),\n'
                           '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
                           '    event_cluster_id TEXT NOT NULL,\n'
                           '    condition_id TEXT NOT NULL,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    band_id TEXT NOT NULL,\n'
                           '    band_role TEXT NOT NULL,\n'
                           '    band_low REAL NOT NULL,\n'
                           '    band_high REAL NOT NULL,\n'
                           '    decided_at TEXT NOT NULL,\n'
                           '    entry_vwap REAL,\n'
                           '    entry_shares REAL,\n'
                           '    entry_cost REAL,\n'
                           '    decision_status TEXT NOT NULL,\n'
                           '    details_json TEXT NOT NULL,\n'
                           '    episode_id TEXT,\n'
                           '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                           '    UNIQUE(run_id, condition_id, token_id, band_id)\n'
                           ')',
                           None,
                           None),
 'shadow_path_observations': ('cherry-shadow-path-source-v2',
                              'CREATE TABLE shadow_path_observations (\n'
                              '    path_id TEXT PRIMARY KEY,\n'
                              '    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
                              '    observed_at TEXT NOT NULL,\n'
                              '    source_received_at TEXT,\n'
                              '    best_bid REAL,\n'
                              '    executable_bid_vwap REAL,\n'
                              '    executable_proceeds REAL,\n'
                              '    filled_shares REAL NOT NULL,\n'
                              '    remaining_shares REAL NOT NULL,\n'
                              '    depth_complete INTEGER NOT NULL CHECK (depth_complete IN (0,1)),\n'
                              '    peak_executable_bid_vwap REAL,\n'
                              '    path_status TEXT NOT NULL,\n'
                              '    UNIQUE(episode_id, run_id)\n'
                              ')',
                              ('best_bid',),
                              (),
                              'CREATE TABLE shadow_path_observations (\n'
                              '    path_id TEXT PRIMARY KEY,\n'
                              '    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),\n'
                              '    observed_at TEXT NOT NULL,\n'
                              '    source_received_at TEXT,\n'
                              '    executable_bid_vwap REAL,\n'
                              '    executable_proceeds REAL,\n'
                              '    filled_shares REAL NOT NULL,\n'
                              '    remaining_shares REAL NOT NULL,\n'
                              '    depth_complete INTEGER NOT NULL CHECK (depth_complete IN (0,1)),\n'
                              '    peak_executable_bid_vwap REAL,\n'
                              '    path_status TEXT NOT NULL,\n'
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              '    UNIQUE(episode_id, run_id)\n'
                              ')',
                              None,
                              None)}

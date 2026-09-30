"""Reviewed Strawberry frozen v1 and follow-up v2a (physical v4) schemas.

Each profile freezes its native tables, constraints, indexes and original guards.
Normalized outcome_type/neg_risk and event_cluster_id retain the producer's
normalization/first-source-event meaning, not presence of absent API fields.
Displayed source summaries and source copies never represent confirmed fills.
"""

STRAWBERRY_V1_LOGICAL_SCHEMA_SHA256 = '7fb1919251a6b6170d36411471855dcdbdfa4eeecc2a5f2c054174a3fc370f99'

STRAWBERRY_V1_SCHEMA_OBJECTS = (('index',
  'api_requests_hash_idx',
  'api_requests',
  'CREATE INDEX api_requests_hash_idx ON api_requests(request_hash)'),
 ('index',
  'api_requests_run_idx',
  'api_requests',
  'CREATE INDEX api_requests_run_idx\n    ON api_requests(run_id, request_kind, page_number)'),
 ('index',
  'candidate_metadata_condition_idx',
  'candidate_metadata_observations',
  'CREATE INDEX candidate_metadata_condition_idx\n'
  '    ON candidate_metadata_observations(condition_id, observed_at)'),
 ('index',
  'clob_attempt_status_idx',
  'clob_token_attempts',
  'CREATE INDEX clob_attempt_status_idx\n    ON clob_token_attempts(status, source_received_at)'),
 ('index',
  'clob_snapshot_token_idx',
  'clob_snapshots',
  'CREATE INDEX clob_snapshot_token_idx\n    ON clob_snapshots(token_id, source_received_at)'),
 ('index',
  'crossing_decision_status_idx',
  'crossing_decisions',
  'CREATE INDEX crossing_decision_status_idx\n'
  '    ON crossing_decisions(decision_status, decided_at, entry_threshold)'),
 ('index',
  'episode_cluster_idx',
  'hypothetical_episodes',
  'CREATE INDEX episode_cluster_idx\n    ON hypothetical_episodes(event_cluster_id, entry_observed_at)'),
 ('index',
  'episode_entry_idx',
  'hypothetical_episodes',
  'CREATE INDEX episode_entry_idx\n'
  '    ON hypothetical_episodes(entry_status, entry_observed_at, entry_threshold)'),
 ('index',
  'episode_path_time_idx',
  'episode_path_observations',
  'CREATE INDEX episode_path_time_idx\n    ON episode_path_observations(episode_id, observed_at)'),
 ('index',
  'market_catalog_cluster_idx',
  'market_catalog_versions',
  'CREATE INDEX market_catalog_cluster_idx\n'
  '    ON market_catalog_versions(event_cluster_id, source_received_at)'),
 ('index',
  'market_catalog_condition_idx',
  'market_catalog_versions',
  'CREATE INDEX market_catalog_condition_idx\n'
  '    ON market_catalog_versions(condition_id, source_received_at)'),
 ('index',
  'market_sweeps_time_idx',
  'market_sweeps',
  'CREATE INDEX market_sweeps_time_idx ON market_sweeps(completed_at)'),
 ('index',
  'outcome_condition_idx',
  'outcome_observations',
  'CREATE INDEX outcome_condition_idx\n    ON outcome_observations(condition_id, observed_at)'),
 ('index',
  'outcome_token_history_idx',
  'outcome_observations',
  'CREATE INDEX outcome_token_history_idx\n    ON outcome_observations(token_id, observed_at)'),
 ('index',
  'quality_issue_idx',
  'data_quality_issues',
  'CREATE INDEX quality_issue_idx\n    ON data_quality_issues(severity, recorded_at, issue_code)'),
 ('index',
  'raw_payloads_hash_idx',
  'raw_payloads',
  'CREATE INDEX raw_payloads_hash_idx ON raw_payloads(payload_sha256)'),
 ('index',
  'research_run_events_run_idx',
  'research_run_events',
  'CREATE INDEX research_run_events_run_idx\n    ON research_run_events(run_id, event_at)'),
 ('index',
  'research_run_events_time_idx',
  'research_run_events',
  'CREATE INDEX research_run_events_time_idx\n    ON research_run_events(event_at, event_type)'),
 ('index',
  'resolution_condition_idx',
  'resolution_observations',
  'CREATE INDEX resolution_condition_idx\n'
  '    ON resolution_observations(condition_id, observed_at, resolution_status)'),
 ('index',
  'storage_metric_time_idx',
  'storage_metrics',
  'CREATE INDEX storage_metric_time_idx ON storage_metrics(recorded_at)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    request_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method IN ('GET','POST')),\n"
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    body_sha256 TEXT,\n'
  '    request_hash TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('SUCCESS','ERROR')),\n"
  '    http_status INTEGER,\n'
  '    retryable INTEGER NOT NULL CHECK (retryable IN (0,1)),\n'
  '    retry_after_seconds REAL,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'candidate_metadata_observations',
  'candidate_metadata_observations',
  'CREATE TABLE candidate_metadata_observations (\n'
  '    metadata_observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    lookup_status TEXT NOT NULL,\n'
  '    requested_at TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    request_id TEXT REFERENCES api_requests(request_id),\n'
  '    raw_market_sha256 TEXT,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_ids_json TEXT NOT NULL,\n'
  '    event_cluster_id TEXT,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    volume_24h REAL,\n'
  '    end_date TEXT,\n'
  '    category TEXT,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    enrichment_lag_seconds REAL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (sweep_id, condition_id)\n'
  ')'),
 ('table',
  'clob_levels',
  'clob_levels',
  'CREATE TABLE clob_levels (\n'
  '    level_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES clob_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),\n"
  '    level_index INTEGER NOT NULL,\n'
  '    price REAL NOT NULL CHECK (price > 0 AND price <= 1),\n'
  '    size REAL NOT NULL CHECK (size > 0),\n'
  '    UNIQUE (snapshot_id, side, level_index)\n'
  ')'),
 ('table',
  'clob_snapshots',
  'clob_snapshots',
  'CREATE TABLE clob_snapshots (\n'
  '    snapshot_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    raw_book_sha256 TEXT NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    tick_size REAL,\n'
  '    min_order_size REAL,\n'
  '    fee_rate_bps REAL,\n'
  '    source_metadata_json TEXT NOT NULL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    spread REAL,\n'
  '    bid_depth_notional REAL NOT NULL,\n'
  '    ask_depth_notional REAL NOT NULL,\n'
  '    UNIQUE (sweep_id, token_id)\n'
  ')'),
 ('table',
  'clob_token_attempts',
  'clob_token_attempts',
  'CREATE TABLE clob_token_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  "    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('CROSSING','EPISODE','BOTH')),\n"
  "    status TEXT NOT NULL CHECK (status IN ('OBSERVED','EMPTY_BOOK','MISSING','MALFORMED','ERROR')),\n"
  '    request_id TEXT REFERENCES api_requests(request_id),\n'
  '    request_started_at TEXT,\n'
  '    source_received_at TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (sweep_id, token_id)\n'
  ')'),
 ('table',
  'crossing_decisions',
  'crossing_decisions',
  'CREATE TABLE crossing_decisions (\n'
  '    decision_id TEXT PRIMARY KEY,\n'
  '    observation_id TEXT NOT NULL REFERENCES outcome_observations(observation_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    entry_threshold REAL NOT NULL,\n'
  '    decided_at TEXT NOT NULL,\n'
  '    prior_condition_id TEXT,\n'
  '    prior_probability REAL,\n'
  '    prior_observed_at TEXT,\n'
  '    prior_gap_minutes REAL,\n'
  '    current_probability REAL NOT NULL,\n'
  '    decision_status TEXT NOT NULL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
  '    jump_size REAL,\n'
  '    crossed_threshold_count INTEGER NOT NULL,\n'
  '    episode_id TEXT,\n'
  '    details_json TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, token_id, entry_threshold)\n'
  ')'),
 ('table',
  'cycle_stats',
  'cycle_stats',
  'CREATE TABLE cycle_stats (\n'
  '    cycle_stat_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    sweep_id TEXT NOT NULL UNIQUE REFERENCES market_sweeps(sweep_id),\n'
  '    cycle_number INTEGER NOT NULL UNIQUE,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    runtime_seconds REAL NOT NULL,\n'
  '    page_count INTEGER NOT NULL,\n'
  '    membership_count INTEGER NOT NULL,\n'
  '    crossing_count INTEGER NOT NULL,\n'
  '    executable_episode_count INTEGER NOT NULL,\n'
  '    clob_requested_count INTEGER NOT NULL,\n'
  '    path_observation_count INTEGER NOT NULL,\n'
  '    resolution_observation_count INTEGER NOT NULL,\n'
  '    stats_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    sweep_id TEXT,\n'
  "    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),\n"
  '    issue_code TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    recorded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'episode_path_observations',
  'episode_path_observations',
  'CREATE TABLE episode_path_observations (\n'
  '    path_observation_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    path_status TEXT NOT NULL,\n'
  '    censor_reason TEXT,\n'
  '    fixed_shares REAL NOT NULL,\n'
  '    best_bid REAL,\n'
  '    exit_bid_vwap REAL,\n'
  '    exit_proceeds_usdc REAL,\n'
  '    bid_depth_notional REAL,\n'
  '    prior_executable_bid_vwap REAL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
  '    entry_cycle_baseline INTEGER NOT NULL CHECK (entry_cycle_baseline IN (0,1)),\n'
  '    details_json TEXT NOT NULL,\n'
  '    UNIQUE (episode_id, sweep_id)\n'
  ')'),
 ('table',
  'episode_threshold_events',
  'episode_threshold_events',
  'CREATE TABLE episode_threshold_events (\n'
  '    threshold_event_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
  '    path_observation_id TEXT NOT NULL REFERENCES episode_path_observations(path_observation_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  "    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),\n"
  '    threshold REAL NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    executable_bid_vwap REAL NOT NULL,\n'
  '    prior_executable_bid_vwap REAL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
  '    conservative_priority INTEGER NOT NULL,\n'
  '    UNIQUE (episode_id, event_kind, threshold)\n'
  ')'),
 ('table',
  'experiment_contracts',
  'experiment_contracts',
  'CREATE TABLE experiment_contracts (\n'
  "    job_name TEXT PRIMARY KEY CHECK (job_name = 'strawberry-shadow-one'),\n"
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  '    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 10),\n'
  '    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute = 7),\n'
  '    entry_start TEXT NOT NULL,\n'
  '    entry_end TEXT NOT NULL,\n'
  '    followup_end TEXT NOT NULL,\n'
  '    entry_thresholds_json TEXT NOT NULL,\n'
  '    stop_thresholds_json TEXT NOT NULL,\n'
  '    target_thresholds_json TEXT NOT NULL,\n'
  '    primary_entry_threshold REAL NOT NULL CHECK (primary_entry_threshold = 0.95),\n'
  '    primary_stop_threshold REAL NOT NULL CHECK (primary_stop_threshold = 0.85),\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    contract_json TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'hypothetical_episodes',
  'hypothetical_episodes',
  'CREATE TABLE hypothetical_episodes (\n'
  '    episode_id TEXT PRIMARY KEY,\n'
  '    decision_id TEXT NOT NULL UNIQUE REFERENCES crossing_decisions(decision_id),\n'
  '    originating_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_cluster_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  "    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),\n"
  '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
  '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
  "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
  '    metadata_observation_id TEXT REFERENCES candidate_metadata_observations(metadata_observation_id),\n'
  '    metadata_status TEXT NOT NULL,\n'
  '    entry_threshold REAL NOT NULL,\n'
  '    crossing_prior_probability REAL NOT NULL,\n'
  '    crossing_probability REAL NOT NULL,\n'
  '    crossing_gap_minutes REAL NOT NULL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
  '    entry_observed_at TEXT NOT NULL,\n'
  '    entry_status TEXT NOT NULL,\n'
  '    entry_censor_reason TEXT,\n'
  '    entry_snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
  '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
  '    entry_ask_vwap REAL,\n'
  '    fixed_shares REAL,\n'
  '    best_ask REAL,\n'
  '    spread REAL,\n'
  '    ask_depth_notional REAL,\n'
  '    source_tick_size REAL,\n'
  '    source_min_order_size REAL,\n'
  '    source_fee_rate_bps REAL,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    volume_24h REAL,\n'
  '    end_date TEXT,\n'
  '    category TEXT,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL,\n'
  '    UNIQUE (token_id, entry_threshold)\n'
  ')'),
 ('table',
  'latest_outcome_state',
  'latest_outcome_state',
  'CREATE TABLE latest_outcome_state (\n'
  '    token_id TEXT PRIMARY KEY,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    source_page_number INTEGER NOT NULL,\n'
  '    source_item_number INTEGER NOT NULL,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    updated_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_catalog_versions',
  'market_catalog_versions',
  'CREATE TABLE market_catalog_versions (\n'
  '    catalog_version_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    page_number INTEGER NOT NULL,\n'
  '    item_number INTEGER NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_ids_json TEXT NOT NULL,\n'
  '    event_cluster_id TEXT,\n'
  '    market_slug TEXT,\n'
  '    question TEXT,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    orderbook_enabled INTEGER,\n'
  '    accepting_orders INTEGER,\n'
  '    tradable INTEGER NOT NULL CHECK (tradable IN (0,1)),\n'
  '    exclusion_reason TEXT NOT NULL,\n'
  '    outcome_type TEXT,\n'
  '    neg_risk INTEGER,\n'
  '    sports_classification TEXT NOT NULL,\n'
  '    sports_classifier_version TEXT NOT NULL,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    volume_24h REAL,\n'
  '    end_date TEXT,\n'
  '    category TEXT,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    outcome_labels_json TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    outcome_prices_json TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    normalized_market_json TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, page_number, item_number)\n'
  ')'),
 ('table',
  'market_membership_blobs',
  'market_membership_blobs',
  'CREATE TABLE market_membership_blobs (\n'
  '    membership_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL UNIQUE REFERENCES market_sweeps(sweep_id),\n'
  "    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),\n"
  '    membership_sha256 TEXT NOT NULL,\n'
  '    uncompressed_bytes INTEGER NOT NULL,\n'
  '    compressed_bytes INTEGER NOT NULL,\n'
  '    membership_blob BLOB NOT NULL,\n'
  '    recorded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_page_lineage',
  'market_page_lineage',
  'CREATE TABLE market_page_lineage (\n'
  '    page_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    page_number INTEGER NOT NULL,\n'
  '    cursor_in TEXT,\n'
  '    cursor_out TEXT,\n'
  '    market_count INTEGER NOT NULL,\n'
  '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
  '    request_hash TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    response_sha256 TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, page_number)\n'
  ')'),
 ('table',
  'market_sweeps',
  'market_sweeps',
  'CREATE TABLE market_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    cycle_number INTEGER NOT NULL UNIQUE,\n'
  '    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),\n"
  "    source_name TEXT NOT NULL CHECK (source_name = 'clob_sampling_markets'),\n"
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    published_at TEXT NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),\n'
  '    page_count INTEGER NOT NULL,\n'
  '    membership_count INTEGER NOT NULL,\n'
  '    unique_condition_count INTEGER NOT NULL,\n'
  '    aligned_outcome_count INTEGER NOT NULL,\n'
  '    tradable_market_count INTEGER NOT NULL,\n'
  '    evidence_catalog_count INTEGER NOT NULL,\n'
  '    evidence_outcome_count INTEGER NOT NULL,\n'
  '    membership_sha256 TEXT NOT NULL,\n'
  '    request_lineage_sha256 TEXT NOT NULL\n'
  ')'),
 ('table',
  'outcome_observations',
  'outcome_observations',
  'CREATE TABLE outcome_observations (\n'
  '    observation_id TEXT PRIMARY KEY,\n'
  '    catalog_version_id TEXT NOT NULL REFERENCES market_catalog_versions(catalog_version_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_cluster_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),\n'
  '    observed_at TEXT NOT NULL,\n'
  "    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),\n"
  '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
  '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
  "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
  '    sports_classifier_version TEXT NOT NULL,\n'
  '    liquidity REAL,\n'
  '    volume_total REAL,\n'
  '    volume_24h REAL,\n'
  '    end_date TEXT,\n'
  '    category TEXT,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, token_id)\n'
  ')'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads (\n'
  '    payload_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL UNIQUE REFERENCES api_requests(request_id),\n'
  '    payload_kind TEXT NOT NULL,\n'
  '    source_received_at TEXT NOT NULL,\n'
  "    content_encoding TEXT NOT NULL CHECK (content_encoding = 'gzip'),\n"
  '    payload_sha256 TEXT NOT NULL,\n'
  '    uncompressed_bytes INTEGER NOT NULL,\n'
  '    compressed_bytes INTEGER NOT NULL,\n'
  '    payload_blob BLOB NOT NULL,\n'
  '    recorded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one'),\n"
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),\n"
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    config_json TEXT NOT NULL,\n'
  '    git_commit TEXT,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_run_events',
  'research_run_events',
  'CREATE TABLE research_run_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),\n'
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one'),\n"
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),\n"
  '    event_at TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'resolution_observations',
  'resolution_observations',
  'CREATE TABLE resolution_observations (\n'
  '    resolution_observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    requested_at TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    lookup_status TEXT NOT NULL,\n'
  '    resolution_status TEXT NOT NULL CHECK (resolution_status IN '
  "('RESOLVED','UNRESOLVED','MISSING','MALFORMED','ERROR')),\n"
  '    request_id TEXT REFERENCES api_requests(request_id),\n'
  '    raw_market_sha256 TEXT,\n'
  '    winning_outcome_index INTEGER,\n'
  '    winning_outcome_label TEXT,\n'
  '    winning_token_id TEXT,\n'
  '    token_payouts_json TEXT NOT NULL,\n'
  '    resolution_jump_without_target_json TEXT NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (sweep_id, condition_id)\n'
  ')'),
 ('table',
  'schema_metadata',
  'schema_metadata',
  'CREATE TABLE schema_metadata (\n    key TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n)'),
 ('table',
  'storage_metrics',
  'storage_metrics',
  'CREATE TABLE storage_metrics (\n'
  '    metric_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT,\n'
  '    phase TEXT NOT NULL,\n'
  '    recorded_at TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL,\n'
  '    journal_bytes INTEGER NOT NULL,\n'
  '    filesystem_total_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_bytes INTEGER NOT NULL,\n'
  '    filesystem_free_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_ratio REAL NOT NULL,\n'
  "    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK','WARN','STOP'))\n"
  ')'),
 ('trigger',
  'api_requests_no_delete',
  'api_requests',
  'CREATE TRIGGER api_requests_no_delete BEFORE DELETE ON api_requests BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'api_requests_no_update',
  'api_requests',
  'CREATE TRIGGER api_requests_no_update BEFORE UPDATE ON api_requests BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'candidate_metadata_observations_no_delete',
  'candidate_metadata_observations',
  'CREATE TRIGGER candidate_metadata_observations_no_delete BEFORE DELETE ON candidate_metadata_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'candidate_metadata_observations_no_update',
  'candidate_metadata_observations',
  'CREATE TRIGGER candidate_metadata_observations_no_update BEFORE UPDATE ON candidate_metadata_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'clob_levels_no_delete',
  'clob_levels',
  "CREATE TRIGGER clob_levels_no_delete BEFORE DELETE ON clob_levels BEGIN SELECT RAISE(ABORT, 'append-only "
  "evidence'); END"),
 ('trigger',
  'clob_levels_no_update',
  'clob_levels',
  "CREATE TRIGGER clob_levels_no_update BEFORE UPDATE ON clob_levels BEGIN SELECT RAISE(ABORT, 'append-only "
  "evidence'); END"),
 ('trigger',
  'clob_snapshots_no_delete',
  'clob_snapshots',
  'CREATE TRIGGER clob_snapshots_no_delete BEFORE DELETE ON clob_snapshots BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'clob_snapshots_no_update',
  'clob_snapshots',
  'CREATE TRIGGER clob_snapshots_no_update BEFORE UPDATE ON clob_snapshots BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'clob_token_attempts_no_delete',
  'clob_token_attempts',
  'CREATE TRIGGER clob_token_attempts_no_delete BEFORE DELETE ON clob_token_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'clob_token_attempts_no_update',
  'clob_token_attempts',
  'CREATE TRIGGER clob_token_attempts_no_update BEFORE UPDATE ON clob_token_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'crossing_decisions_no_delete',
  'crossing_decisions',
  'CREATE TRIGGER crossing_decisions_no_delete BEFORE DELETE ON crossing_decisions BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'crossing_decisions_no_update',
  'crossing_decisions',
  'CREATE TRIGGER crossing_decisions_no_update BEFORE UPDATE ON crossing_decisions BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'cycle_stats_no_delete',
  'cycle_stats',
  "CREATE TRIGGER cycle_stats_no_delete BEFORE DELETE ON cycle_stats BEGIN SELECT RAISE(ABORT, 'append-only "
  "evidence'); END"),
 ('trigger',
  'cycle_stats_no_update',
  'cycle_stats',
  "CREATE TRIGGER cycle_stats_no_update BEFORE UPDATE ON cycle_stats BEGIN SELECT RAISE(ABORT, 'append-only "
  "evidence'); END"),
 ('trigger',
  'data_quality_issues_no_delete',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_no_delete BEFORE DELETE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'data_quality_issues_no_update',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_no_update BEFORE UPDATE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_path_observations_no_delete',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_no_delete BEFORE DELETE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_path_observations_no_update',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_no_update BEFORE UPDATE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_threshold_events_no_delete',
  'episode_threshold_events',
  'CREATE TRIGGER episode_threshold_events_no_delete BEFORE DELETE ON episode_threshold_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_threshold_events_no_update',
  'episode_threshold_events',
  'CREATE TRIGGER episode_threshold_events_no_update BEFORE UPDATE ON episode_threshold_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'experiment_contracts_no_delete',
  'experiment_contracts',
  'CREATE TRIGGER experiment_contracts_no_delete BEFORE DELETE ON experiment_contracts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'experiment_contracts_no_update',
  'experiment_contracts',
  'CREATE TRIGGER experiment_contracts_no_update BEFORE UPDATE ON experiment_contracts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'hypothetical_episodes_no_delete',
  'hypothetical_episodes',
  'CREATE TRIGGER hypothetical_episodes_no_delete BEFORE DELETE ON hypothetical_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'hypothetical_episodes_no_update',
  'hypothetical_episodes',
  'CREATE TRIGGER hypothetical_episodes_no_update BEFORE UPDATE ON hypothetical_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_catalog_versions_no_delete',
  'market_catalog_versions',
  'CREATE TRIGGER market_catalog_versions_no_delete BEFORE DELETE ON market_catalog_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_catalog_versions_no_update',
  'market_catalog_versions',
  'CREATE TRIGGER market_catalog_versions_no_update BEFORE UPDATE ON market_catalog_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_membership_blobs_no_delete',
  'market_membership_blobs',
  'CREATE TRIGGER market_membership_blobs_no_delete BEFORE DELETE ON market_membership_blobs BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_membership_blobs_no_update',
  'market_membership_blobs',
  'CREATE TRIGGER market_membership_blobs_no_update BEFORE UPDATE ON market_membership_blobs BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_page_lineage_no_delete',
  'market_page_lineage',
  'CREATE TRIGGER market_page_lineage_no_delete BEFORE DELETE ON market_page_lineage BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_page_lineage_no_update',
  'market_page_lineage',
  'CREATE TRIGGER market_page_lineage_no_update BEFORE UPDATE ON market_page_lineage BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_sweeps_no_delete',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_no_delete BEFORE DELETE ON market_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'market_sweeps_no_update',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_no_update BEFORE UPDATE ON market_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'outcome_observations_no_delete',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_no_delete BEFORE DELETE ON outcome_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'outcome_observations_no_update',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_no_update BEFORE UPDATE ON outcome_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'raw_payloads_no_delete',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_no_delete BEFORE DELETE ON raw_payloads BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'raw_payloads_no_update',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_no_update BEFORE UPDATE ON raw_payloads BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_no_delete',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_delete BEFORE DELETE ON research_config_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_no_update',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_update BEFORE UPDATE ON research_config_versions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_run_events_no_delete',
  'research_run_events',
  'CREATE TRIGGER research_run_events_no_delete BEFORE DELETE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_run_events_no_update',
  'research_run_events',
  'CREATE TRIGGER research_run_events_no_update BEFORE UPDATE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_observations_no_delete',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_no_delete BEFORE DELETE ON resolution_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'resolution_observations_no_update',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_no_update BEFORE UPDATE ON resolution_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'schema_metadata_no_delete',
  'schema_metadata',
  'CREATE TRIGGER schema_metadata_no_delete BEFORE DELETE ON schema_metadata BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'schema_metadata_no_update',
  'schema_metadata',
  'CREATE TRIGGER schema_metadata_no_update BEFORE UPDATE ON schema_metadata BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'storage_metrics_no_delete',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_no_delete BEFORE DELETE ON storage_metrics BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'storage_metrics_no_update',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_no_update BEFORE UPDATE ON storage_metrics BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"))

STRAWBERRY_V1_RAW_TABLES = {'market_catalog_versions': ('strawberry-v1-market-catalog-versions',
                             'CREATE TABLE market_catalog_versions (\n'
                             '    catalog_version_id TEXT PRIMARY KEY,\n'
                             '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    page_number INTEGER NOT NULL,\n'
                             '    item_number INTEGER NOT NULL,\n'
                             '    source_received_at TEXT NOT NULL,\n'
                             '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                             '    condition_id TEXT,\n'
                             '    market_id TEXT,\n'
                             '    event_id TEXT,\n'
                             '    event_ids_json TEXT NOT NULL,\n'
                             '    event_cluster_id TEXT,\n'
                             '    market_slug TEXT,\n'
                             '    question TEXT,\n'
                             '    active INTEGER,\n'
                             '    closed INTEGER,\n'
                             '    orderbook_enabled INTEGER,\n'
                             '    accepting_orders INTEGER,\n'
                             '    tradable INTEGER NOT NULL CHECK (tradable IN (0,1)),\n'
                             '    exclusion_reason TEXT NOT NULL,\n'
                             '    outcome_type TEXT,\n'
                             '    neg_risk INTEGER,\n'
                             '    sports_classification TEXT NOT NULL,\n'
                             '    sports_classifier_version TEXT NOT NULL,\n'
                             '    liquidity REAL,\n'
                             '    volume_total REAL,\n'
                             '    volume_24h REAL,\n'
                             '    end_date TEXT,\n'
                             '    category TEXT,\n'
                             '    tags_json TEXT NOT NULL,\n'
                             '    outcome_labels_json TEXT NOT NULL,\n'
                             '    token_ids_json TEXT NOT NULL,\n'
                             '    outcome_prices_json TEXT NOT NULL,\n'
                             '    raw_market_sha256 TEXT NOT NULL,\n'
                             '    normalized_market_json TEXT NOT NULL,\n'
                             '    UNIQUE (sweep_id, page_number, item_number)\n'
                             ')',
                             ('condition_id',
                              'market_id',
                              'event_id',
                              'event_cluster_id',
                              'market_slug',
                              'question',
                              'active',
                              'closed',
                              'orderbook_enabled',
                              'accepting_orders',
                              'outcome_type',
                              'neg_risk',
                              'liquidity',
                              'volume_total',
                              'volume_24h',
                              'end_date',
                              'category'),
                             ('condition_id', 'event_cluster_id'),
                             'CREATE TABLE market_catalog_versions (\n'
                             '    catalog_version_id TEXT PRIMARY KEY,\n'
                             '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    page_number INTEGER NOT NULL,\n'
                             '    item_number INTEGER NOT NULL,\n'
                             '    source_received_at TEXT NOT NULL,\n'
                             '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                             '    condition_id TEXT,\n'
                             '    event_ids_json TEXT NOT NULL,\n'
                             '    event_cluster_id TEXT,\n'
                             '    tradable INTEGER NOT NULL CHECK (tradable IN (0,1)),\n'
                             '    exclusion_reason TEXT NOT NULL,\n'
                             '    sports_classification TEXT NOT NULL,\n'
                             '    sports_classifier_version TEXT NOT NULL,\n'
                             '    tags_json TEXT NOT NULL,\n'
                             '    outcome_labels_json TEXT NOT NULL,\n'
                             '    token_ids_json TEXT NOT NULL,\n'
                             '    outcome_prices_json TEXT NOT NULL,\n'
                             '    raw_market_sha256 TEXT NOT NULL,\n'
                             '    normalized_market_json TEXT NOT NULL,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                             '    UNIQUE (sweep_id, page_number, item_number)\n'
                             ')'),
 'outcome_observations': ('strawberry-v1-outcome-observations',
                          'CREATE TABLE outcome_observations (\n'
                          '    observation_id TEXT PRIMARY KEY,\n'
                          '    catalog_version_id TEXT NOT NULL REFERENCES '
                          'market_catalog_versions(catalog_version_id),\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    market_id TEXT,\n'
                          '    event_id TEXT,\n'
                          '    event_cluster_id TEXT,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    outcome_index INTEGER NOT NULL,\n'
                          '    outcome_label TEXT NOT NULL,\n'
                          '    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),\n'
                          '    observed_at TEXT NOT NULL,\n'
                          "    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),\n"
                          '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
                          '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
                          "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
                          '    sports_classifier_version TEXT NOT NULL,\n'
                          '    liquidity REAL,\n'
                          '    volume_total REAL,\n'
                          '    volume_24h REAL,\n'
                          '    end_date TEXT,\n'
                          '    category TEXT,\n'
                          '    tags_json TEXT NOT NULL,\n'
                          '    raw_market_sha256 TEXT NOT NULL,\n'
                          '    UNIQUE (sweep_id, token_id)\n'
                          ')',
                          ('condition_id',
                           'market_id',
                           'event_id',
                           'event_cluster_id',
                           'token_id',
                           'outcome_index',
                           'outcome_label',
                           'probability',
                           'outcome_type',
                           'neg_risk',
                           'liquidity',
                           'volume_total',
                           'volume_24h',
                           'end_date',
                           'category'),
                          ('condition_id', 'token_id'),
                          'CREATE TABLE outcome_observations (\n'
                          '    observation_id TEXT PRIMARY KEY,\n'
                          '    catalog_version_id TEXT NOT NULL REFERENCES '
                          'market_catalog_versions(catalog_version_id),\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
                          "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
                          '    sports_classifier_version TEXT NOT NULL,\n'
                          '    tags_json TEXT NOT NULL,\n'
                          '    raw_market_sha256 TEXT NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                          '    UNIQUE (sweep_id, token_id)\n'
                          ')'),
 'crossing_decisions': ('strawberry-v1-crossing-decisions',
                        'CREATE TABLE crossing_decisions (\n'
                        '    decision_id TEXT PRIMARY KEY,\n'
                        '    observation_id TEXT NOT NULL REFERENCES outcome_observations(observation_id),\n'
                        '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        '    condition_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    entry_threshold REAL NOT NULL,\n'
                        '    decided_at TEXT NOT NULL,\n'
                        '    prior_condition_id TEXT,\n'
                        '    prior_probability REAL,\n'
                        '    prior_observed_at TEXT,\n'
                        '    prior_gap_minutes REAL,\n'
                        '    current_probability REAL NOT NULL,\n'
                        '    decision_status TEXT NOT NULL,\n'
                        '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
                        '    jump_size REAL,\n'
                        '    crossed_threshold_count INTEGER NOT NULL,\n'
                        '    episode_id TEXT,\n'
                        '    details_json TEXT NOT NULL,\n'
                        '    UNIQUE (sweep_id, token_id, entry_threshold)\n'
                        ')',
                        ('condition_id',
                         'token_id',
                         'prior_condition_id',
                         'prior_probability',
                         'current_probability'),
                        ('token_id',),
                        'CREATE TABLE crossing_decisions (\n'
                        '    decision_id TEXT PRIMARY KEY,\n'
                        '    observation_id TEXT NOT NULL REFERENCES outcome_observations(observation_id),\n'
                        '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    entry_threshold REAL NOT NULL,\n'
                        '    decided_at TEXT NOT NULL,\n'
                        '    prior_observed_at TEXT,\n'
                        '    prior_gap_minutes REAL,\n'
                        '    decision_status TEXT NOT NULL,\n'
                        '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
                        '    jump_size REAL,\n'
                        '    crossed_threshold_count INTEGER NOT NULL,\n'
                        '    episode_id TEXT,\n'
                        '    details_json TEXT NOT NULL,\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                        '    UNIQUE (sweep_id, token_id, entry_threshold)\n'
                        ')'),
 'candidate_metadata_observations': ('strawberry-v1-candidate-metadata-observations',
                                     'CREATE TABLE candidate_metadata_observations (\n'
                                     '    metadata_observation_id TEXT PRIMARY KEY,\n'
                                     '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                                     '    run_id TEXT NOT NULL,\n'
                                     '    condition_id TEXT NOT NULL,\n'
                                     '    lookup_status TEXT NOT NULL,\n'
                                     '    requested_at TEXT NOT NULL,\n'
                                     '    observed_at TEXT NOT NULL,\n'
                                     '    request_id TEXT REFERENCES api_requests(request_id),\n'
                                     '    raw_market_sha256 TEXT,\n'
                                     '    market_id TEXT,\n'
                                     '    event_id TEXT,\n'
                                     '    event_ids_json TEXT NOT NULL,\n'
                                     '    event_cluster_id TEXT,\n'
                                     '    liquidity REAL,\n'
                                     '    volume_total REAL,\n'
                                     '    volume_24h REAL,\n'
                                     '    end_date TEXT,\n'
                                     '    category TEXT,\n'
                                     '    tags_json TEXT NOT NULL,\n'
                                     '    enrichment_lag_seconds REAL,\n'
                                     '    error_type TEXT,\n'
                                     '    error_message TEXT,\n'
                                     '    UNIQUE (sweep_id, condition_id)\n'
                                     ')',
                                     ('condition_id',
                                      'market_id',
                                      'event_id',
                                      'event_cluster_id',
                                      'liquidity',
                                      'volume_total',
                                      'volume_24h',
                                      'end_date',
                                      'category'),
                                     ('condition_id',),
                                     'CREATE TABLE candidate_metadata_observations (\n'
                                     '    metadata_observation_id TEXT PRIMARY KEY,\n'
                                     '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                                     '    run_id TEXT NOT NULL,\n'
                                     '    condition_id TEXT NOT NULL,\n'
                                     '    lookup_status TEXT NOT NULL,\n'
                                     '    requested_at TEXT NOT NULL,\n'
                                     '    observed_at TEXT NOT NULL,\n'
                                     '    request_id TEXT REFERENCES api_requests(request_id),\n'
                                     '    raw_market_sha256 TEXT,\n'
                                     '    event_ids_json TEXT NOT NULL,\n'
                                     '    tags_json TEXT NOT NULL,\n'
                                     '    enrichment_lag_seconds REAL,\n'
                                     '    error_type TEXT,\n'
                                     '    error_message TEXT,\n'
                                     '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                                     '    UNIQUE (sweep_id, condition_id)\n'
                                     ')'),
 'clob_token_attempts': ('strawberry-v1-clob-token-attempts',
                         'CREATE TABLE clob_token_attempts (\n'
                         '    attempt_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    attempt_role TEXT NOT NULL CHECK (attempt_role IN '
                         "('CROSSING','EPISODE','BOTH')),\n"
                         '    status TEXT NOT NULL CHECK (status IN '
                         "('OBSERVED','EMPTY_BOOK','MISSING','MALFORMED','ERROR')),\n"
                         '    request_id TEXT REFERENCES api_requests(request_id),\n'
                         '    request_started_at TEXT,\n'
                         '    source_received_at TEXT,\n'
                         '    error_type TEXT,\n'
                         '    error_message TEXT,\n'
                         '    UNIQUE (sweep_id, token_id)\n'
                         ')',
                         ('token_id',),
                         ('token_id',),
                         'CREATE TABLE clob_token_attempts (\n'
                         '    attempt_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    attempt_role TEXT NOT NULL CHECK (attempt_role IN '
                         "('CROSSING','EPISODE','BOTH')),\n"
                         '    status TEXT NOT NULL CHECK (status IN '
                         "('OBSERVED','EMPTY_BOOK','MISSING','MALFORMED','ERROR')),\n"
                         '    request_id TEXT REFERENCES api_requests(request_id),\n'
                         '    request_started_at TEXT,\n'
                         '    source_received_at TEXT,\n'
                         '    error_type TEXT,\n'
                         '    error_message TEXT,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (sweep_id, token_id)\n'
                         ')'),
 'clob_snapshots': ('strawberry-v1-clob-snapshots',
                    'CREATE TABLE clob_snapshots (\n'
                    '    snapshot_id TEXT PRIMARY KEY,\n'
                    '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                    '    run_id TEXT NOT NULL,\n'
                    '    token_id TEXT NOT NULL,\n'
                    '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                    '    source_received_at TEXT NOT NULL,\n'
                    '    raw_book_sha256 TEXT NOT NULL,\n'
                    '    source_timestamp TEXT,\n'
                    '    tick_size REAL,\n'
                    '    min_order_size REAL,\n'
                    '    fee_rate_bps REAL,\n'
                    '    source_metadata_json TEXT NOT NULL,\n'
                    '    bid_level_count INTEGER NOT NULL,\n'
                    '    ask_level_count INTEGER NOT NULL,\n'
                    '    best_bid REAL,\n'
                    '    best_ask REAL,\n'
                    '    spread REAL,\n'
                    '    bid_depth_notional REAL NOT NULL,\n'
                    '    ask_depth_notional REAL NOT NULL,\n'
                    '    UNIQUE (sweep_id, token_id)\n'
                    ')',
                    ('token_id',
                     'source_timestamp',
                     'tick_size',
                     'min_order_size',
                     'fee_rate_bps',
                     'bid_level_count',
                     'ask_level_count',
                     'best_bid',
                     'best_ask',
                     'spread',
                     'bid_depth_notional',
                     'ask_depth_notional'),
                    ('token_id',),
                    'CREATE TABLE clob_snapshots (\n'
                    '    snapshot_id TEXT PRIMARY KEY,\n'
                    '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                    '    run_id TEXT NOT NULL,\n'
                    '    token_id TEXT NOT NULL,\n'
                    '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                    '    source_received_at TEXT NOT NULL,\n'
                    '    raw_book_sha256 TEXT NOT NULL,\n'
                    '    source_metadata_json TEXT NOT NULL,\n'
                    '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                    '    UNIQUE (sweep_id, token_id)\n'
                    ')'),
 'hypothetical_episodes': ('strawberry-v1-hypothetical-episodes',
                           'CREATE TABLE hypothetical_episodes (\n'
                           '    episode_id TEXT PRIMARY KEY,\n'
                           '    decision_id TEXT NOT NULL UNIQUE REFERENCES '
                           'crossing_decisions(decision_id),\n'
                           '    originating_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    condition_id TEXT NOT NULL,\n'
                           '    market_id TEXT,\n'
                           '    event_id TEXT,\n'
                           '    event_cluster_id TEXT,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    outcome_index INTEGER NOT NULL,\n'
                           '    outcome_label TEXT NOT NULL,\n'
                           "    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),\n"
                           '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
                           '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
                           "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
                           '    metadata_observation_id TEXT REFERENCES '
                           'candidate_metadata_observations(metadata_observation_id),\n'
                           '    metadata_status TEXT NOT NULL,\n'
                           '    entry_threshold REAL NOT NULL,\n'
                           '    crossing_prior_probability REAL NOT NULL,\n'
                           '    crossing_probability REAL NOT NULL,\n'
                           '    crossing_gap_minutes REAL NOT NULL,\n'
                           '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
                           '    entry_observed_at TEXT NOT NULL,\n'
                           '    entry_status TEXT NOT NULL,\n'
                           '    entry_censor_reason TEXT,\n'
                           '    entry_snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
                           '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
                           '    entry_ask_vwap REAL,\n'
                           '    fixed_shares REAL,\n'
                           '    best_ask REAL,\n'
                           '    spread REAL,\n'
                           '    ask_depth_notional REAL,\n'
                           '    source_tick_size REAL,\n'
                           '    source_min_order_size REAL,\n'
                           '    source_fee_rate_bps REAL,\n'
                           '    liquidity REAL,\n'
                           '    volume_total REAL,\n'
                           '    volume_24h REAL,\n'
                           '    end_date TEXT,\n'
                           '    category TEXT,\n'
                           '    tags_json TEXT NOT NULL,\n'
                           '    created_at TEXT NOT NULL,\n'
                           '    UNIQUE (token_id, entry_threshold)\n'
                           ')',
                           ('condition_id',
                            'market_id',
                            'event_id',
                            'event_cluster_id',
                            'token_id',
                            'outcome_index',
                            'outcome_label',
                            'outcome_type',
                            'neg_risk',
                            'crossing_prior_probability',
                            'crossing_probability',
                            'best_ask',
                            'spread',
                            'ask_depth_notional',
                            'source_tick_size',
                            'source_min_order_size',
                            'source_fee_rate_bps',
                            'liquidity',
                            'volume_total',
                            'volume_24h',
                            'end_date',
                            'category'),
                           ('event_cluster_id', 'token_id'),
                           'CREATE TABLE hypothetical_episodes (\n'
                           '    episode_id TEXT PRIMARY KEY,\n'
                           '    decision_id TEXT NOT NULL UNIQUE REFERENCES '
                           'crossing_decisions(decision_id),\n'
                           '    originating_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                           '    run_id TEXT NOT NULL,\n'
                           '    event_cluster_id TEXT,\n'
                           '    token_id TEXT NOT NULL,\n'
                           '    sports_classification TEXT NOT NULL CHECK (sports_classification IN '
                           "('SPORTS','NON_SPORTS','UNKNOWN')),\n"
                           '    metadata_observation_id TEXT REFERENCES '
                           'candidate_metadata_observations(metadata_observation_id),\n'
                           '    metadata_status TEXT NOT NULL,\n'
                           '    entry_threshold REAL NOT NULL,\n'
                           '    crossing_gap_minutes REAL NOT NULL,\n'
                           '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
                           '    entry_observed_at TEXT NOT NULL,\n'
                           '    entry_status TEXT NOT NULL,\n'
                           '    entry_censor_reason TEXT,\n'
                           '    entry_snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
                           '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
                           '    entry_ask_vwap REAL,\n'
                           '    fixed_shares REAL,\n'
                           '    tags_json TEXT NOT NULL,\n'
                           '    created_at TEXT NOT NULL,\n'
                           '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                           '    UNIQUE (token_id, entry_threshold)\n'
                           ')'),
 'episode_path_observations': ('strawberry-v1-episode-path-observations',
                               'CREATE TABLE episode_path_observations (\n'
                               '    path_observation_id TEXT PRIMARY KEY,\n'
                               '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
                               '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL,\n'
                               '    censor_reason TEXT,\n'
                               '    fixed_shares REAL NOT NULL,\n'
                               '    best_bid REAL,\n'
                               '    exit_bid_vwap REAL,\n'
                               '    exit_proceeds_usdc REAL,\n'
                               '    bid_depth_notional REAL,\n'
                               '    prior_executable_bid_vwap REAL,\n'
                               '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
                               '    entry_cycle_baseline INTEGER NOT NULL CHECK (entry_cycle_baseline IN '
                               '(0,1)),\n'
                               '    details_json TEXT NOT NULL,\n'
                               '    UNIQUE (episode_id, sweep_id)\n'
                               ')',
                               ('best_bid', 'bid_depth_notional'),
                               (),
                               'CREATE TABLE episode_path_observations (\n'
                               '    path_observation_id TEXT PRIMARY KEY,\n'
                               '    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),\n'
                               '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL,\n'
                               '    censor_reason TEXT,\n'
                               '    fixed_shares REAL NOT NULL,\n'
                               '    exit_bid_vwap REAL,\n'
                               '    exit_proceeds_usdc REAL,\n'
                               '    prior_executable_bid_vwap REAL,\n'
                               '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
                               '    entry_cycle_baseline INTEGER NOT NULL CHECK (entry_cycle_baseline IN '
                               '(0,1)),\n'
                               '    details_json TEXT NOT NULL,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                               '    UNIQUE (episode_id, sweep_id)\n'
                               ')'),
 'resolution_observations': ('strawberry-v1-resolution-observations',
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    resolution_status TEXT NOT NULL CHECK (resolution_status IN '
                             "('RESOLVED','UNRESOLVED','MISSING','MALFORMED','ERROR')),\n"
                             '    request_id TEXT REFERENCES api_requests(request_id),\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    winning_outcome_index INTEGER,\n'
                             '    winning_outcome_label TEXT,\n'
                             '    winning_token_id TEXT,\n'
                             '    token_payouts_json TEXT NOT NULL,\n'
                             '    resolution_jump_without_target_json TEXT NOT NULL,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT,\n'
                             '    UNIQUE (sweep_id, condition_id)\n'
                             ')',
                             ('condition_id', 'winning_outcome_label', 'winning_token_id'),
                             ('condition_id',),
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    resolution_status TEXT NOT NULL CHECK (resolution_status IN '
                             "('RESOLVED','UNRESOLVED','MISSING','MALFORMED','ERROR')),\n"
                             '    request_id TEXT REFERENCES api_requests(request_id),\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    winning_outcome_index INTEGER,\n'
                             '    token_payouts_json TEXT NOT NULL,\n'
                             '    resolution_jump_without_target_json TEXT NOT NULL,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                             '    UNIQUE (sweep_id, condition_id)\n'
                             ')'),
 'latest_outcome_state': ('strawberry-v1-latest-outcome-state',
                          'CREATE TABLE latest_outcome_state (\n'
                          '    token_id TEXT PRIMARY KEY,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                          '    source_page_number INTEGER NOT NULL,\n'
                          '    source_item_number INTEGER NOT NULL,\n'
                          '    raw_market_sha256 TEXT NOT NULL,\n'
                          '    updated_at TEXT NOT NULL\n'
                          ')',
                          ('token_id', 'condition_id', 'probability'),
                          ('token_id',),
                          'CREATE TABLE latest_outcome_state (\n'
                          '    token_id TEXT PRIMARY KEY,\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                          '    source_page_number INTEGER NOT NULL,\n'
                          '    source_item_number INTEGER NOT NULL,\n'
                          '    raw_market_sha256 TEXT NOT NULL,\n'
                          '    updated_at TEXT NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                          ')')}

STRAWBERRY_FOLLOWUP_LOGICAL_SCHEMA_SHA256 = '245c0908c329460e963c3350b8d3afb8ba876d0f23689d9d84bf1847d9e55d69'

STRAWBERRY_FOLLOWUP_SCHEMA_OBJECTS = (('index',
  'compact_books_token_idx',
  'compact_books',
  'CREATE INDEX compact_books_token_idx\n    ON compact_books(token_id,source_received_at)'),
 ('index',
  'followup_api_run_idx',
  'api_requests',
  'CREATE INDEX followup_api_run_idx\n    ON api_requests(run_id,request_kind,page_number)'),
 ('index',
  'followup_cycles_time_idx',
  'followup_cycles',
  'CREATE INDEX followup_cycles_time_idx\n    ON followup_cycles(completed_at)'),
 ('index',
  'followup_path_episode_idx',
  'episode_path_observations',
  'CREATE INDEX followup_path_episode_idx\n    ON episode_path_observations(episode_id,observed_at)'),
 ('index',
  'followup_path_latest_executable_idx',
  'episode_path_observations',
  'CREATE INDEX followup_path_latest_executable_idx ON episode_path_observations(episode_id,observed_at '
  "DESC,path_observation_id DESC,exit_bid_vwap) WHERE path_status='EXECUTABLE' AND exit_bid_vwap IS NOT "
  'NULL'),
 ('index',
  'followup_resolution_condition_idx',
  'resolution_observations',
  'CREATE INDEX followup_resolution_condition_idx\n    ON resolution_observations(condition_id,observed_at)'),
 ('index',
  'followup_resolution_resolved_condition_idx',
  'resolution_observations',
  'CREATE INDEX followup_resolution_resolved_condition_idx ON resolution_observations(condition_id) WHERE '
  "resolution_status='RESOLVED'"),
 ('index',
  'followup_run_events_idx',
  'research_run_events',
  'CREATE INDEX followup_run_events_idx\n    ON research_run_events(run_id,event_at)'),
 ('index',
  'imported_episode_condition_idx',
  'imported_episodes',
  'CREATE INDEX imported_episode_condition_idx\n    ON imported_episodes(condition_id,episode_id)'),
 ('index',
  'imported_episode_token_idx',
  'imported_episodes',
  'CREATE INDEX imported_episode_token_idx\n    ON imported_episodes(token_id,episode_id)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    request_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method IN ('GET','POST')),\n"
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    body_sha256 TEXT,\n'
  '    request_hash TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('SUCCESS','ERROR')),\n"
  '    http_status INTEGER,\n'
  '    retryable INTEGER NOT NULL CHECK (retryable IN (0,1)),\n'
  '    retry_after_seconds REAL,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'book_token_attempts',
  'book_token_attempts',
  'CREATE TABLE book_token_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('OBSERVED','MISSING','EMPTY_BOOK','MALFORMED','ERROR')),\n"
  '    request_id TEXT REFERENCES api_requests(request_id),\n'
  '    request_started_at TEXT,\n'
  '    received_at TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (cycle_id,token_id)\n'
  ')'),
 ('table',
  'compact_books',
  'compact_books',
  'CREATE TABLE compact_books (\n'
  '    book_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    source_received_at TEXT NOT NULL,\n'
  '    source_response_sha256 TEXT NOT NULL,\n'
  "    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),\n"
  '    book_sha256 TEXT NOT NULL,\n'
  '    uncompressed_bytes INTEGER NOT NULL,\n'
  '    compressed_bytes INTEGER NOT NULL,\n'
  '    book_blob BLOB NOT NULL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    bid_depth_notional REAL NOT NULL,\n'
  '    ask_depth_notional REAL NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    tick_size REAL,\n'
  '    min_order_size REAL,\n'
  '    fee_rate_bps REAL,\n'
  '    UNIQUE (cycle_id,token_id)\n'
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  "    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),\n"
  '    issue_code TEXT NOT NULL,\n'
  '    recorded_at TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'episode_path_observations',
  'episode_path_observations',
  'CREATE TABLE episode_path_observations (\n'
  '    path_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),\n'
  '    book_id TEXT REFERENCES compact_books(book_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    path_status TEXT NOT NULL,\n'
  '    censor_reason TEXT,\n'
  '    fixed_shares REAL NOT NULL,\n'
  '    best_bid REAL,\n'
  '    exit_bid_vwap REAL,\n'
  '    exit_proceeds_usdc REAL,\n'
  '    covered_shares REAL,\n'
  '    bid_depth_notional REAL,\n'
  '    prior_executable_bid_vwap REAL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
  '    details_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id,episode_id)\n'
  ')'),
 ('table',
  'episode_threshold_events',
  'episode_threshold_events',
  'CREATE TABLE episode_threshold_events (\n'
  '    threshold_event_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),\n'
  '    path_observation_id TEXT NOT NULL REFERENCES episode_path_observations(path_observation_id),\n'
  "    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),\n"
  '    threshold REAL NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    executable_bid_vwap REAL NOT NULL,\n'
  '    prior_executable_bid_vwap REAL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
  '    conservative_priority INTEGER NOT NULL CHECK (conservative_priority IN (0,1)),\n'
  '    UNIQUE (episode_id,event_kind,threshold)\n'
  ')'),
 ('table',
  'followup_contracts',
  'followup_contracts',
  'CREATE TABLE followup_contracts (\n'
  "    job_name TEXT PRIMARY KEY CHECK (job_name = 'strawberry-shadow-one-followup-v2a'),\n"
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-followup-v2a'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  '    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 10),\n'
  '    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute = 7),\n'
  '    entry_start TEXT NOT NULL,\n'
  '    entry_end TEXT NOT NULL,\n'
  '    followup_end TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    contract_json TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'followup_cycles',
  'followup_cycles',
  'CREATE TABLE followup_cycles (\n'
  '    cycle_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    cycle_number INTEGER NOT NULL UNIQUE,\n'
  '    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
  '    anchor_sha256 TEXT NOT NULL,\n'
  "    validation_mode TEXT NOT NULL CHECK (validation_mode IN ('FULL_SEED','PINNED_FAST')),\n"
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    published_at TEXT NOT NULL,\n'
  '    unresolved_episode_count INTEGER NOT NULL,\n'
  '    distinct_token_count INTEGER NOT NULL,\n'
  '    distinct_condition_count INTEGER NOT NULL,\n'
  '    book_observed_count INTEGER NOT NULL,\n'
  '    path_observation_count INTEGER NOT NULL,\n'
  '    resolution_observation_count INTEGER NOT NULL,\n'
  '    newly_resolved_condition_count INTEGER NOT NULL,\n'
  '    prepublication_seconds REAL NOT NULL,\n'
  '    summary_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'imported_condition_status',
  'imported_condition_status',
  'CREATE TABLE imported_condition_status (\n'
  '    condition_id TEXT PRIMARY KEY,\n'
  '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
  '    terminal_at_handoff INTEGER NOT NULL CHECK (terminal_at_handoff IN (0,1)),\n'
  '    source_resolution_observation_id TEXT,\n'
  '    source_sweep_id TEXT,\n'
  '    source_run_id TEXT,\n'
  '    observed_at TEXT,\n'
  '    winning_outcome_index INTEGER,\n'
  '    winning_outcome_label TEXT,\n'
  '    winning_token_id TEXT,\n'
  '    token_payouts_json TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT,\n'
  '    seed_row_sha256 TEXT NOT NULL UNIQUE\n'
  ')'),
 ('table',
  'imported_episodes',
  'imported_episodes',
  'CREATE TABLE imported_episodes (\n'
  '    episode_id TEXT PRIMARY KEY,\n'
  '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
  '    source_decision_id TEXT NOT NULL,\n'
  '    source_originating_sweep_id TEXT NOT NULL,\n'
  '    source_run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_cluster_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    outcome_type TEXT NOT NULL,\n'
  '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
  '    sports_classification TEXT NOT NULL,\n'
  '    entry_threshold REAL NOT NULL,\n'
  '    entry_observed_at TEXT NOT NULL,\n'
  '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
  '    entry_ask_vwap REAL NOT NULL,\n'
  '    fixed_shares REAL NOT NULL CHECK (fixed_shares > 0),\n'
  '    source_last_path_observation_id TEXT,\n'
  '    source_last_path_observed_at TEXT,\n'
  '    source_last_executable_bid_vwap REAL,\n'
  '    episode_json TEXT NOT NULL,\n'
  '    seed_row_sha256 TEXT NOT NULL UNIQUE,\n'
  '    UNIQUE (token_id,entry_threshold)\n'
  ')'),
 ('table',
  'imported_threshold_events',
  'imported_threshold_events',
  'CREATE TABLE imported_threshold_events (\n'
  '    source_threshold_event_id TEXT PRIMARY KEY,\n'
  '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
  '    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),\n'
  '    path_observation_id TEXT NOT NULL,\n'
  '    sweep_id TEXT NOT NULL,\n'
  "    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),\n"
  '    threshold REAL NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    executable_bid_vwap REAL NOT NULL,\n'
  '    prior_executable_bid_vwap REAL,\n'
  '    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),\n'
  '    conservative_priority INTEGER NOT NULL,\n'
  '    seed_row_sha256 TEXT NOT NULL UNIQUE,\n'
  '    UNIQUE (episode_id,event_kind,threshold)\n'
  ')'),
 ('table',
  'phase_timings',
  'phase_timings',
  'CREATE TABLE phase_timings (\n'
  '    phase_timing_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    phase_name TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_seconds REAL NOT NULL CHECK (elapsed_seconds >= 0),\n'
  '    details_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id,phase_name)\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one-followup-v2a'),\n"
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-followup-v2a'),\n"
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    config_json TEXT NOT NULL,\n'
  '    git_commit TEXT,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_run_events',
  'research_run_events',
  'CREATE TABLE research_run_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),\n'
  "    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),\n"
  "    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one-followup-v2a'),\n"
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),\n"
  '    event_at TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'resolution_observations',
  'resolution_observations',
  'CREATE TABLE resolution_observations (\n'
  '    resolution_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    requested_at TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    lookup_status TEXT NOT NULL,\n'
  '    resolution_status TEXT NOT NULL,\n'
  '    request_id TEXT REFERENCES api_requests(request_id),\n'
  '    raw_market_sha256 TEXT,\n'
  '    encoding TEXT,\n'
  '    uncompressed_bytes INTEGER,\n'
  '    compressed_bytes INTEGER,\n'
  '    market_blob BLOB,\n'
  '    winning_outcome_index INTEGER,\n'
  '    winning_outcome_label TEXT,\n'
  '    winning_token_id TEXT,\n'
  '    token_payouts_json TEXT NOT NULL,\n'
  '    resolution_jump_without_target_json TEXT NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (cycle_id,condition_id)\n'
  ')'),
 ('table',
  'schema_metadata',
  'schema_metadata',
  'CREATE TABLE schema_metadata (\n    key TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n)'),
 ('table',
  'source_anchors',
  'source_anchors',
  'CREATE TABLE source_anchors (\n'
  "    anchor_id TEXT PRIMARY KEY CHECK (anchor_id = 'v1-seed'),\n"
  '    source_path TEXT NOT NULL,\n'
  '    source_file_fingerprint_sha256 TEXT NOT NULL,\n'
  '    source_db_size_bytes INTEGER NOT NULL,\n'
  '    source_db_mtime_ns INTEGER NOT NULL,\n'
  '    source_schema_version INTEGER NOT NULL CHECK (source_schema_version = 1),\n'
  '    source_schema_sha256 TEXT NOT NULL,\n'
  "    source_data_contract TEXT NOT NULL CHECK (source_data_contract = 'last-mile-clob-v1'),\n"
  "    source_job_name TEXT NOT NULL CHECK (source_job_name = 'strawberry-shadow-one'),\n"
  '    source_entry_start TEXT NOT NULL,\n'
  '    source_entry_end TEXT NOT NULL,\n'
  '    source_followup_end TEXT NOT NULL,\n'
  '    source_sweep_id TEXT NOT NULL,\n'
  '    source_cycle_number INTEGER NOT NULL,\n'
  '    source_sweep_completed_at TEXT NOT NULL,\n'
  '    source_successful_at TEXT NOT NULL,\n'
  '    source_config_hash TEXT NOT NULL,\n'
  '    source_strategy_digest TEXT NOT NULL,\n'
  '    source_counts_json TEXT NOT NULL,\n'
  '    episode_seed_sha256 TEXT NOT NULL,\n'
  '    condition_seed_sha256 TEXT NOT NULL,\n'
  '    threshold_seed_sha256 TEXT NOT NULL,\n'
  '    executable_episode_count INTEGER NOT NULL,\n'
  '    condition_count INTEGER NOT NULL,\n'
  '    terminal_condition_count INTEGER NOT NULL,\n'
  '    threshold_event_count INTEGER NOT NULL,\n'
  '    anchor_sha256 TEXT NOT NULL UNIQUE,\n'
  '    captured_at TEXT NOT NULL,\n'
  '    seeded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'storage_metrics',
  'storage_metrics',
  'CREATE TABLE storage_metrics (\n'
  '    metric_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT,\n'
  '    phase TEXT NOT NULL,\n'
  '    recorded_at TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL,\n'
  '    journal_bytes INTEGER NOT NULL,\n'
  '    filesystem_total_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_bytes INTEGER NOT NULL,\n'
  '    filesystem_free_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_ratio REAL NOT NULL,\n'
  "    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK','WARN','STOP'))\n"
  ')'),
 ('trigger',
  'api_requests_no_delete',
  'api_requests',
  "CREATE TRIGGER api_requests_no_delete BEFORE DELETE ON api_requests BEGIN SELECT RAISE(ABORT,'append-only "
  "follow-up evidence'); END"),
 ('trigger',
  'api_requests_no_update',
  'api_requests',
  "CREATE TRIGGER api_requests_no_update BEFORE UPDATE ON api_requests BEGIN SELECT RAISE(ABORT,'append-only "
  "follow-up evidence'); END"),
 ('trigger',
  'book_token_attempts_no_delete',
  'book_token_attempts',
  'CREATE TRIGGER book_token_attempts_no_delete BEFORE DELETE ON book_token_attempts BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'book_token_attempts_no_update',
  'book_token_attempts',
  'CREATE TRIGGER book_token_attempts_no_update BEFORE UPDATE ON book_token_attempts BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'compact_books_no_delete',
  'compact_books',
  'CREATE TRIGGER compact_books_no_delete BEFORE DELETE ON compact_books BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'compact_books_no_update',
  'compact_books',
  'CREATE TRIGGER compact_books_no_update BEFORE UPDATE ON compact_books BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'data_quality_issues_no_delete',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_no_delete BEFORE DELETE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'data_quality_issues_no_update',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_no_update BEFORE UPDATE ON data_quality_issues BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'episode_path_observations_no_delete',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_no_delete BEFORE DELETE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'episode_path_observations_no_update',
  'episode_path_observations',
  'CREATE TRIGGER episode_path_observations_no_update BEFORE UPDATE ON episode_path_observations BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'episode_threshold_events_no_delete',
  'episode_threshold_events',
  'CREATE TRIGGER episode_threshold_events_no_delete BEFORE DELETE ON episode_threshold_events BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'episode_threshold_events_no_update',
  'episode_threshold_events',
  'CREATE TRIGGER episode_threshold_events_no_update BEFORE UPDATE ON episode_threshold_events BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'followup_contracts_no_delete',
  'followup_contracts',
  'CREATE TRIGGER followup_contracts_no_delete BEFORE DELETE ON followup_contracts BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'followup_contracts_no_update',
  'followup_contracts',
  'CREATE TRIGGER followup_contracts_no_update BEFORE UPDATE ON followup_contracts BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'followup_cycles_no_delete',
  'followup_cycles',
  'CREATE TRIGGER followup_cycles_no_delete BEFORE DELETE ON followup_cycles BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'followup_cycles_no_update',
  'followup_cycles',
  'CREATE TRIGGER followup_cycles_no_update BEFORE UPDATE ON followup_cycles BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_condition_status_no_delete',
  'imported_condition_status',
  'CREATE TRIGGER imported_condition_status_no_delete BEFORE DELETE ON imported_condition_status BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_condition_status_no_update',
  'imported_condition_status',
  'CREATE TRIGGER imported_condition_status_no_update BEFORE UPDATE ON imported_condition_status BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_episodes_no_delete',
  'imported_episodes',
  'CREATE TRIGGER imported_episodes_no_delete BEFORE DELETE ON imported_episodes BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_episodes_no_update',
  'imported_episodes',
  'CREATE TRIGGER imported_episodes_no_update BEFORE UPDATE ON imported_episodes BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_threshold_events_no_delete',
  'imported_threshold_events',
  'CREATE TRIGGER imported_threshold_events_no_delete BEFORE DELETE ON imported_threshold_events BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'imported_threshold_events_no_update',
  'imported_threshold_events',
  'CREATE TRIGGER imported_threshold_events_no_update BEFORE UPDATE ON imported_threshold_events BEGIN '
  "SELECT RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'phase_timings_no_delete',
  'phase_timings',
  'CREATE TRIGGER phase_timings_no_delete BEFORE DELETE ON phase_timings BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'phase_timings_no_update',
  'phase_timings',
  'CREATE TRIGGER phase_timings_no_update BEFORE UPDATE ON phase_timings BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'research_config_versions_no_delete',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_delete BEFORE DELETE ON research_config_versions BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'research_config_versions_no_update',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_update BEFORE UPDATE ON research_config_versions BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'research_run_events_no_delete',
  'research_run_events',
  'CREATE TRIGGER research_run_events_no_delete BEFORE DELETE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'research_run_events_no_update',
  'research_run_events',
  'CREATE TRIGGER research_run_events_no_update BEFORE UPDATE ON research_run_events BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'resolution_observations_no_delete',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_no_delete BEFORE DELETE ON resolution_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'resolution_observations_no_update',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_no_update BEFORE UPDATE ON resolution_observations BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'source_anchors_no_delete',
  'source_anchors',
  'CREATE TRIGGER source_anchors_no_delete BEFORE DELETE ON source_anchors BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'source_anchors_no_update',
  'source_anchors',
  'CREATE TRIGGER source_anchors_no_update BEFORE UPDATE ON source_anchors BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'storage_metrics_no_delete',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_no_delete BEFORE DELETE ON storage_metrics BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"),
 ('trigger',
  'storage_metrics_no_update',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_no_update BEFORE UPDATE ON storage_metrics BEGIN SELECT '
  "RAISE(ABORT,'append-only follow-up evidence'); END"))

STRAWBERRY_FOLLOWUP_RAW_TABLES = {'imported_episodes': ('strawberry-v2a-imported-episodes',
                       'CREATE TABLE imported_episodes (\n'
                       '    episode_id TEXT PRIMARY KEY,\n'
                       '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
                       '    source_decision_id TEXT NOT NULL,\n'
                       '    source_originating_sweep_id TEXT NOT NULL,\n'
                       '    source_run_id TEXT NOT NULL,\n'
                       '    condition_id TEXT NOT NULL,\n'
                       '    market_id TEXT,\n'
                       '    event_id TEXT,\n'
                       '    event_cluster_id TEXT,\n'
                       '    token_id TEXT NOT NULL,\n'
                       '    outcome_index INTEGER NOT NULL,\n'
                       '    outcome_label TEXT NOT NULL,\n'
                       '    outcome_type TEXT NOT NULL,\n'
                       '    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),\n'
                       '    sports_classification TEXT NOT NULL,\n'
                       '    entry_threshold REAL NOT NULL,\n'
                       '    entry_observed_at TEXT NOT NULL,\n'
                       '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
                       '    entry_ask_vwap REAL NOT NULL,\n'
                       '    fixed_shares REAL NOT NULL CHECK (fixed_shares > 0),\n'
                       '    source_last_path_observation_id TEXT,\n'
                       '    source_last_path_observed_at TEXT,\n'
                       '    source_last_executable_bid_vwap REAL,\n'
                       '    episode_json TEXT NOT NULL,\n'
                       '    seed_row_sha256 TEXT NOT NULL UNIQUE,\n'
                       '    UNIQUE (token_id,entry_threshold)\n'
                       ')',
                       ('condition_id',
                        'market_id',
                        'event_id',
                        'event_cluster_id',
                        'token_id',
                        'outcome_index',
                        'outcome_label',
                        'outcome_type',
                        'neg_risk'),
                       ('condition_id', 'token_id'),
                       'CREATE TABLE imported_episodes (\n'
                       '    episode_id TEXT PRIMARY KEY,\n'
                       '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
                       '    source_decision_id TEXT NOT NULL,\n'
                       '    source_originating_sweep_id TEXT NOT NULL,\n'
                       '    source_run_id TEXT NOT NULL,\n'
                       '    condition_id TEXT NOT NULL,\n'
                       '    token_id TEXT NOT NULL,\n'
                       '    sports_classification TEXT NOT NULL,\n'
                       '    entry_threshold REAL NOT NULL,\n'
                       '    entry_observed_at TEXT NOT NULL,\n'
                       '    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),\n'
                       '    entry_ask_vwap REAL NOT NULL,\n'
                       '    fixed_shares REAL NOT NULL CHECK (fixed_shares > 0),\n'
                       '    source_last_path_observation_id TEXT,\n'
                       '    source_last_path_observed_at TEXT,\n'
                       '    source_last_executable_bid_vwap REAL,\n'
                       '    episode_json TEXT NOT NULL,\n'
                       '    seed_row_sha256 TEXT NOT NULL UNIQUE,\n'
                       '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                       '    UNIQUE (token_id,entry_threshold)\n'
                       ')'),
 'imported_condition_status': ('strawberry-v2a-imported-condition-status',
                               'CREATE TABLE imported_condition_status (\n'
                               '    condition_id TEXT PRIMARY KEY,\n'
                               '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
                               '    terminal_at_handoff INTEGER NOT NULL CHECK (terminal_at_handoff IN '
                               '(0,1)),\n'
                               '    source_resolution_observation_id TEXT,\n'
                               '    source_sweep_id TEXT,\n'
                               '    source_run_id TEXT,\n'
                               '    observed_at TEXT,\n'
                               '    winning_outcome_index INTEGER,\n'
                               '    winning_outcome_label TEXT,\n'
                               '    winning_token_id TEXT,\n'
                               '    token_payouts_json TEXT NOT NULL,\n'
                               '    raw_market_sha256 TEXT,\n'
                               '    seed_row_sha256 TEXT NOT NULL UNIQUE\n'
                               ')',
                               ('condition_id', 'winning_outcome_label', 'winning_token_id'),
                               ('condition_id',),
                               'CREATE TABLE imported_condition_status (\n'
                               '    condition_id TEXT PRIMARY KEY,\n'
                               '    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),\n'
                               '    terminal_at_handoff INTEGER NOT NULL CHECK (terminal_at_handoff IN '
                               '(0,1)),\n'
                               '    source_resolution_observation_id TEXT,\n'
                               '    source_sweep_id TEXT,\n'
                               '    source_run_id TEXT,\n'
                               '    observed_at TEXT,\n'
                               '    winning_outcome_index INTEGER,\n'
                               '    token_payouts_json TEXT NOT NULL,\n'
                               '    raw_market_sha256 TEXT,\n'
                               '    seed_row_sha256 TEXT NOT NULL UNIQUE,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                               ')'),
 'book_token_attempts': ('strawberry-v2a-book-token-attempts',
                         'CREATE TABLE book_token_attempts (\n'
                         '    attempt_id TEXT PRIMARY KEY,\n'
                         '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    status TEXT NOT NULL CHECK (status IN '
                         "('OBSERVED','MISSING','EMPTY_BOOK','MALFORMED','ERROR')),\n"
                         '    request_id TEXT REFERENCES api_requests(request_id),\n'
                         '    request_started_at TEXT,\n'
                         '    received_at TEXT,\n'
                         '    error_type TEXT,\n'
                         '    error_message TEXT,\n'
                         '    UNIQUE (cycle_id,token_id)\n'
                         ')',
                         ('token_id',),
                         ('token_id',),
                         'CREATE TABLE book_token_attempts (\n'
                         '    attempt_id TEXT PRIMARY KEY,\n'
                         '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    status TEXT NOT NULL CHECK (status IN '
                         "('OBSERVED','MISSING','EMPTY_BOOK','MALFORMED','ERROR')),\n"
                         '    request_id TEXT REFERENCES api_requests(request_id),\n'
                         '    request_started_at TEXT,\n'
                         '    received_at TEXT,\n'
                         '    error_type TEXT,\n'
                         '    error_message TEXT,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (cycle_id,token_id)\n'
                         ')'),
 'compact_books': ('strawberry-v2a-compact-books',
                   'CREATE TABLE compact_books (\n'
                   '    book_id TEXT PRIMARY KEY,\n'
                   '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                   '    run_id TEXT NOT NULL,\n'
                   '    token_id TEXT NOT NULL,\n'
                   '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                   '    source_received_at TEXT NOT NULL,\n'
                   '    source_response_sha256 TEXT NOT NULL,\n'
                   "    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),\n"
                   '    book_sha256 TEXT NOT NULL,\n'
                   '    uncompressed_bytes INTEGER NOT NULL,\n'
                   '    compressed_bytes INTEGER NOT NULL,\n'
                   '    book_blob BLOB NOT NULL,\n'
                   '    bid_level_count INTEGER NOT NULL,\n'
                   '    ask_level_count INTEGER NOT NULL,\n'
                   '    best_bid REAL,\n'
                   '    best_ask REAL,\n'
                   '    bid_depth_notional REAL NOT NULL,\n'
                   '    ask_depth_notional REAL NOT NULL,\n'
                   '    source_timestamp TEXT,\n'
                   '    tick_size REAL,\n'
                   '    min_order_size REAL,\n'
                   '    fee_rate_bps REAL,\n'
                   '    UNIQUE (cycle_id,token_id)\n'
                   ')',
                   ('token_id',
                    'bid_level_count',
                    'ask_level_count',
                    'best_bid',
                    'best_ask',
                    'bid_depth_notional',
                    'ask_depth_notional',
                    'source_timestamp',
                    'tick_size',
                    'min_order_size',
                    'fee_rate_bps'),
                   ('token_id',),
                   'CREATE TABLE compact_books (\n'
                   '    book_id TEXT PRIMARY KEY,\n'
                   '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                   '    run_id TEXT NOT NULL,\n'
                   '    token_id TEXT NOT NULL,\n'
                   '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
                   '    source_received_at TEXT NOT NULL,\n'
                   '    source_response_sha256 TEXT NOT NULL,\n'
                   "    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),\n"
                   '    book_sha256 TEXT NOT NULL,\n'
                   '    uncompressed_bytes INTEGER NOT NULL,\n'
                   '    compressed_bytes INTEGER NOT NULL,\n'
                   '    book_blob BLOB NOT NULL,\n'
                   '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                   '    UNIQUE (cycle_id,token_id)\n'
                   ')'),
 'episode_path_observations': ('strawberry-v2a-episode-path-observations',
                               'CREATE TABLE episode_path_observations (\n'
                               '    path_observation_id TEXT PRIMARY KEY,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),\n'
                               '    book_id TEXT REFERENCES compact_books(book_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL,\n'
                               '    censor_reason TEXT,\n'
                               '    fixed_shares REAL NOT NULL,\n'
                               '    best_bid REAL,\n'
                               '    exit_bid_vwap REAL,\n'
                               '    exit_proceeds_usdc REAL,\n'
                               '    covered_shares REAL,\n'
                               '    bid_depth_notional REAL,\n'
                               '    prior_executable_bid_vwap REAL,\n'
                               '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
                               '    details_json TEXT NOT NULL,\n'
                               '    UNIQUE (cycle_id,episode_id)\n'
                               ')',
                               ('best_bid', 'bid_depth_notional'),
                               (),
                               'CREATE TABLE episode_path_observations (\n'
                               '    path_observation_id TEXT PRIMARY KEY,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),\n'
                               '    book_id TEXT REFERENCES compact_books(book_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL,\n'
                               '    censor_reason TEXT,\n'
                               '    fixed_shares REAL NOT NULL,\n'
                               '    exit_bid_vwap REAL,\n'
                               '    exit_proceeds_usdc REAL,\n'
                               '    covered_shares REAL,\n'
                               '    prior_executable_bid_vwap REAL,\n'
                               '    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),\n'
                               '    details_json TEXT NOT NULL,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                               '    UNIQUE (cycle_id,episode_id)\n'
                               ')'),
 'resolution_observations': ('strawberry-v2a-resolution-observations',
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    resolution_status TEXT NOT NULL,\n'
                             '    request_id TEXT REFERENCES api_requests(request_id),\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    encoding TEXT,\n'
                             '    uncompressed_bytes INTEGER,\n'
                             '    compressed_bytes INTEGER,\n'
                             '    market_blob BLOB,\n'
                             '    winning_outcome_index INTEGER,\n'
                             '    winning_outcome_label TEXT,\n'
                             '    winning_token_id TEXT,\n'
                             '    token_payouts_json TEXT NOT NULL,\n'
                             '    resolution_jump_without_target_json TEXT NOT NULL,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT,\n'
                             '    UNIQUE (cycle_id,condition_id)\n'
                             ')',
                             ('condition_id', 'winning_outcome_label', 'winning_token_id'),
                             ('condition_id',),
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    resolution_status TEXT NOT NULL,\n'
                             '    request_id TEXT REFERENCES api_requests(request_id),\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    encoding TEXT,\n'
                             '    uncompressed_bytes INTEGER,\n'
                             '    compressed_bytes INTEGER,\n'
                             '    market_blob BLOB,\n'
                             '    winning_outcome_index INTEGER,\n'
                             '    token_payouts_json TEXT NOT NULL,\n'
                             '    resolution_jump_without_target_json TEXT NOT NULL,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                             '    UNIQUE (cycle_id,condition_id)\n'
                             ')')}

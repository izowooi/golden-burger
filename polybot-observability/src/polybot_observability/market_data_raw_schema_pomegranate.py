"""Reviewed Pomegranate research-full-v1 schema version 4 and source ownership.

Frozen from the native producer SCHEMA and its 46 original append-only guards.
Public Data API observations are taker TAPE, never account-confirmed fills.
No inference of historical schema versions 1-3 is permitted.
"""

POMEGRANATE_LOGICAL_SCHEMA_SHA256 = '5927374cf39df089b55a04bd832f053153e17eeddd1ab6769e6d5342e7104437'

POMEGRANATE_SCHEMA_OBJECTS = (('index',
  'api_requests_attempt_idx',
  'api_requests',
  'CREATE INDEX api_requests_attempt_idx\n'
  '    ON api_requests(sweep_attempt_id, request_kind, page_number, attempt_number)'),
 ('index',
  'api_requests_hash_idx',
  'api_requests',
  'CREATE INDEX api_requests_hash_idx ON api_requests(request_hash)'),
 ('index',
  'market_obs_condition_time_idx',
  'market_observations',
  'CREATE INDEX market_obs_condition_time_idx\n    ON market_observations(condition_id, page_received_at)'),
 ('index',
  'market_obs_run_idx',
  'market_observations',
  'CREATE INDEX market_obs_run_idx ON market_observations(run_id)'),
 ('index',
  'market_sweeps_cycle_idx',
  'market_sweeps',
  'CREATE UNIQUE INDEX market_sweeps_cycle_idx\n    ON market_sweeps(cycle_number)'),
 ('index',
  'orderbook_snapshot_token_idx',
  'orderbook_snapshots',
  'CREATE INDEX orderbook_snapshot_token_idx\n    ON orderbook_snapshots(token_id, received_at)'),
 ('index',
  'orderbook_token_attempt_status_idx',
  'orderbook_token_attempts',
  'CREATE INDEX orderbook_token_attempt_status_idx\n    ON orderbook_token_attempts(status, received_at)'),
 ('index',
  'research_run_events_time_idx',
  'research_run_events',
  'CREATE INDEX research_run_events_time_idx\n    ON research_run_events(event_at, run_id)'),
 ('index',
  'resolution_condition_time_idx',
  'resolution_observations',
  'CREATE INDEX resolution_condition_time_idx\n    ON resolution_observations(condition_id, observed_at)'),
 ('index',
  'source_component_run_idx',
  'source_component_runs',
  'CREATE INDEX source_component_run_idx\n    ON source_component_runs(run_id, component)'),
 ('index',
  'trade_membership_hash_idx',
  'trade_tape_memberships',
  'CREATE INDEX trade_membership_hash_idx\n    ON trade_tape_memberships(trade_id, received_at)'),
 ('index',
  'trade_observation_economic_idx',
  'trade_observations',
  'CREATE INDEX trade_observation_economic_idx\n'
  '    ON trade_observations(economic_row_hash, occurrence_index)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    request_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT,\n'
  '    sweep_attempt_id TEXT,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method IN ('GET', 'POST')),\n"
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    body_sha256 TEXT,\n'
  '    request_hash TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL,\n'
  '    status TEXT NOT NULL,\n'
  '    http_status INTEGER,\n'
  '    retryable INTEGER NOT NULL,\n'
  '    retry_after_seconds REAL,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'collection_contracts',
  'collection_contracts',
  'CREATE TABLE collection_contracts (\n'
  '    contract_name TEXT PRIMARY KEY,\n'
  '    schema_version INTEGER NOT NULL,\n'
  '    database_utc_date TEXT NOT NULL,\n'
  '    prior_trade_watermark_epoch INTEGER,\n'
  '    prior_trade_bootstrap_start_epoch INTEGER,\n'
  '    prior_census_condition_count INTEGER NOT NULL DEFAULT 0,\n'
  '    prior_census_digest_sha256 TEXT,\n'
  '    metadata_json TEXT NOT NULL,\n'
  '    content_sha256 TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL,\n'
  "    CHECK (contract_name = 'research-full-v1')\n"
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT,\n'
  '    cycle_number INTEGER,\n'
  '    component TEXT NOT NULL,\n'
  '    severity TEXT NOT NULL,\n'
  '    issue_code TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_metadata_versions',
  'market_metadata_versions',
  'CREATE TABLE market_metadata_versions (\n'
  '    metadata_version_id TEXT PRIMARY KEY,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    content_sha256 TEXT NOT NULL,\n'
  '    metadata_json TEXT NOT NULL,\n'
  '    first_observed_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    first_observed_at TEXT NOT NULL,\n'
  '    UNIQUE (source_market_key, content_sha256)\n'
  ')'),
 ('table',
  'market_observations',
  'market_observations',
  'CREATE TABLE market_observations (\n'
  '    observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    page_number INTEGER NOT NULL,\n'
  '    item_number INTEGER NOT NULL,\n'
  '    page_received_at TEXT NOT NULL,\n'
  '    page_request_id TEXT NOT NULL,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    event_slug TEXT,\n'
  '    market_slug TEXT,\n'
  '    question TEXT,\n'
  '    volume_total_raw TEXT,\n'
  '    volume_total REAL,\n'
  '    volume_24h_raw TEXT,\n'
  '    volume_24h REAL,\n'
  '    volume_1h_raw TEXT,\n'
  '    volume_1h REAL,\n'
  '    volume_week_raw TEXT,\n'
  '    volume_week REAL,\n'
  '    volume_month_raw TEXT,\n'
  '    volume_month REAL,\n'
  '    volume_year_raw TEXT,\n'
  '    volume_year REAL,\n'
  '    liquidity_raw TEXT,\n'
  '    liquidity REAL,\n'
  '    liquidity_variants_json TEXT NOT NULL,\n'
  '    outcome_prices_json TEXT NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    spread REAL,\n'
  '    last_trade_price REAL,\n'
  '    price_changes_json TEXT NOT NULL,\n'
  '    start_date TEXT,\n'
  '    end_date TEXT,\n'
  '    game_start_time TEXT,\n'
  '    created_at_source TEXT,\n'
  '    updated_at_source TEXT,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    sports_json TEXT NOT NULL,\n'
  '    category TEXT,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    enable_order_book INTEGER,\n'
  '    accepting_orders INTEGER,\n'
  '    neg_risk INTEGER,\n'
  '    fees_enabled INTEGER,\n'
  '    fee_metadata_json TEXT NOT NULL,\n'
  '    tick_size_raw TEXT,\n'
  '    min_order_size_raw TEXT,\n'
  '    source_clocks_json TEXT NOT NULL,\n'
  '    parse_quality_json TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_sweep_memberships',
  'market_sweep_memberships',
  'CREATE TABLE market_sweep_memberships (\n'
  '    membership_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),\n'
  '    membership_ordinal INTEGER NOT NULL,\n'
  '    page_number INTEGER NOT NULL,\n'
  '    item_number INTEGER NOT NULL,\n'
  '    page_received_at TEXT NOT NULL,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    duplicate_ordinal INTEGER NOT NULL,\n'
  '    UNIQUE (sweep_id, membership_ordinal)\n'
  ')'),
 ('table',
  'market_sweeps',
  'market_sweeps',
  'CREATE TABLE market_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),\n'
  '    page_count INTEGER NOT NULL,\n'
  '    raw_market_count INTEGER NOT NULL,\n'
  '    unique_condition_count INTEGER NOT NULL,\n'
  '    missing_condition_id_count INTEGER NOT NULL,\n'
  '    duplicate_condition_count INTEGER NOT NULL,\n'
  '    request_attestation_json TEXT NOT NULL,\n'
  '    request_attestation_sha256 TEXT NOT NULL,\n'
  '    membership_digest_sha256 TEXT NOT NULL,\n'
  '    raw_payload_page_count INTEGER NOT NULL,\n'
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'research-full-v1')\n"
  ')'),
 ('table',
  'orderbook_depth_metrics',
  'orderbook_depth_metrics',
  'CREATE TABLE orderbook_depth_metrics (\n'
  '    metric_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),\n"
  '    target_notional REAL NOT NULL,\n'
  '    filled_notional REAL NOT NULL,\n'
  '    base_quantity REAL NOT NULL,\n'
  '    vwap_price REAL,\n'
  '    worst_price REAL,\n'
  '    complete INTEGER NOT NULL,\n'
  '    levels_consumed INTEGER NOT NULL,\n'
  '    UNIQUE (snapshot_id, side, target_notional)\n'
  ')'),
 ('table',
  'orderbook_levels',
  'orderbook_levels',
  'CREATE TABLE orderbook_levels (\n'
  '    level_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BID', 'ASK')),\n"
  '    level_index INTEGER NOT NULL,\n'
  '    price_raw TEXT NOT NULL,\n'
  '    price REAL NOT NULL,\n'
  '    size_raw TEXT NOT NULL,\n'
  '    size REAL NOT NULL,\n'
  '    UNIQUE (snapshot_id, side, level_index)\n'
  ')'),
 ('table',
  'orderbook_selections',
  'orderbook_selections',
  'CREATE TABLE orderbook_selections (\n'
  '    selection_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    collection_id TEXT NOT NULL,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    selection_reason TEXT NOT NULL,\n'
  '    sampler_version TEXT NOT NULL,\n'
  '    frame_market_count INTEGER NOT NULL,\n'
  '    bucket_candidate_count INTEGER NOT NULL,\n'
  '    bucket_visit_index INTEGER NOT NULL,\n'
  '    sampler_slot INTEGER NOT NULL,\n'
  '    rotation_offset INTEGER NOT NULL,\n'
  '    wrap_around INTEGER NOT NULL,\n'
  '    sample_max INTEGER NOT NULL,\n'
  '    sampled_market_count INTEGER NOT NULL,\n'
  '    truncated_count INTEGER NOT NULL,\n'
  '    truncation_applied INTEGER NOT NULL,\n'
  '    inclusion_probability_basis TEXT NOT NULL,\n'
  '    long_run_coverage_basis TEXT NOT NULL,\n'
  '    bucket_number INTEGER NOT NULL,\n'
  '    bucket_count INTEGER NOT NULL,\n'
  '    selection_rank TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    outcome_labels_json TEXT NOT NULL,\n'
  '    expected_token_count INTEGER NOT NULL,\n'
  '    observed_token_count INTEGER NOT NULL,\n'
  '    coverage_ratio REAL NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    error_message TEXT,\n'
  '    selected_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'orderbook_snapshots',
  'orderbook_snapshots',
  'CREATE TABLE orderbook_snapshots (\n'
  '    snapshot_id TEXT PRIMARY KEY,\n'
  '    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    received_at TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL,\n'
  '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
  '    source_timestamp TEXT,\n'
  '    source_hash TEXT,\n'
  '    market TEXT,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    spread REAL,\n'
  '    last_trade_price REAL,\n'
  '    tick_size_raw TEXT,\n'
  '    min_order_size_raw TEXT,\n'
  '    neg_risk INTEGER,\n'
  '    raw_book_sha256 TEXT NOT NULL\n'
  ')'),
 ('table',
  'orderbook_token_attempts',
  'orderbook_token_attempts',
  'CREATE TABLE orderbook_token_attempts (\n'
  '    token_attempt_id TEXT PRIMARY KEY,\n'
  '    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    collection_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER,\n'
  '    outcome_label TEXT,\n'
  "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', 'MISSING', 'ERROR')),\n"
  '    request_id TEXT,\n'
  '    raw_payload_id TEXT REFERENCES raw_payloads(payload_id),\n'
  '    received_at TEXT NOT NULL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (selection_id, token_id)\n'
  ')'),
 ('table',
  'outcome_observations',
  'outcome_observations',
  'CREATE TABLE outcome_observations (\n'
  '    outcome_observation_id TEXT PRIMARY KEY,\n'
  '    observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT,\n'
  '    token_id TEXT,\n'
  '    price_raw TEXT,\n'
  '    price REAL,\n'
  '    label_present INTEGER NOT NULL,\n'
  '    token_present INTEGER NOT NULL,\n'
  '    price_present INTEGER NOT NULL,\n'
  '    UNIQUE (observation_id, outcome_index)\n'
  ')'),
 ('table',
  'prior_census_conditions',
  'prior_census_conditions',
  'CREATE TABLE prior_census_conditions (\n'
  '    condition_id TEXT PRIMARY KEY,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    prior_sweep_id TEXT NOT NULL,\n'
  '    carried_from_utc_date TEXT NOT NULL,\n'
  '    carried_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads (\n'
  '    payload_id TEXT PRIMARY KEY,\n'
  '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    payload_kind TEXT NOT NULL,\n'
  '    content_encoding TEXT NOT NULL,\n'
  '    payload_sha256 TEXT NOT NULL,\n'
  '    uncompressed_bytes INTEGER NOT NULL,\n'
  '    compressed_bytes INTEGER,\n'
  '    blob_stored INTEGER NOT NULL,\n'
  '    payload_blob BLOB,\n'
  '    recorded_at TEXT NOT NULL,\n'
  '    CHECK ((blob_stored = 1 AND payload_blob IS NOT NULL) OR\n'
  '           (blob_stored = 0 AND payload_blob IS NULL))\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  '    schema_version INTEGER NOT NULL,\n'
  '    strategy_name TEXT NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  '    config_json TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    git_commit TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_run_events',
  'research_run_events',
  'CREATE TABLE research_run_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED', 'SUCCEEDED', 'FAILED')),\n"
  '    event_at TEXT NOT NULL,\n'
  '    strategy_name TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  '    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    git_commit TEXT NOT NULL,\n'
  '    cycle_stats_json TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (run_id, event_type)\n'
  ')'),
 ('table',
  'resolution_observations',
  'resolution_observations',
  'CREATE TABLE resolution_observations (\n'
  '    resolution_observation_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    requested_at TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    lookup_status TEXT NOT NULL,\n'
  '    request_id TEXT,\n'
  '    market_id TEXT,\n'
  '    resolved INTEGER,\n'
  '    closed INTEGER,\n'
  '    one_hot INTEGER,\n'
  '    one_hot_outcome_index INTEGER,\n'
  '    one_hot_outcome_label TEXT,\n'
  '    resolution_value_raw TEXT,\n'
  '    resolution_source_raw TEXT,\n'
  '    redeemable INTEGER,\n'
  '    source_updated_at TEXT,\n'
  '    source_end_date TEXT,\n'
  '    outcome_prices_json TEXT NOT NULL,\n'
  '    raw_market_sha256 TEXT,\n'
  '    raw_market_json TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'resolution_watchlist',
  'resolution_watchlist',
  'CREATE TABLE resolution_watchlist (\n'
  '    condition_id TEXT PRIMARY KEY,\n'
  '    market_id TEXT,\n'
  '    source_market_key TEXT NOT NULL,\n'
  '    first_seen_sweep_id TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL,\n'
  '    selection_reason TEXT NOT NULL\n'
  '    ,carried_from_utc_date TEXT\n'
  '    ,prior_state_json TEXT\n'
  '    ,terminal INTEGER NOT NULL DEFAULT 0\n'
  ')'),
 ('table',
  'source_component_runs',
  'source_component_runs',
  'CREATE TABLE source_component_runs (\n'
  '    component_run_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    component TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    requested_count INTEGER,\n'
  '    observed_count INTEGER,\n'
  '    error_count INTEGER NOT NULL DEFAULT 0,\n'
  '    possible_gap INTEGER NOT NULL DEFAULT 0,\n'
  '    details_json TEXT NOT NULL,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'storage_metrics',
  'storage_metrics',
  'CREATE TABLE storage_metrics (\n'
  '    storage_metric_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT,\n'
  '    cycle_number INTEGER,\n'
  '    phase TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL,\n'
  '    wal_bytes INTEGER NOT NULL,\n'
  '    shm_bytes INTEGER NOT NULL,\n'
  '    logical_bytes INTEGER NOT NULL,\n'
  '    filesystem_total_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_bytes INTEGER NOT NULL,\n'
  '    filesystem_free_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_ratio REAL NOT NULL,\n'
  '    recent_growth_bytes_per_cycle REAL NOT NULL,\n'
  '    forecast_next_day_bytes REAL NOT NULL,\n'
  '    forecast_days_to_stop REAL,\n'
  '    guard_state TEXT NOT NULL\n'
  ')'),
 ('table',
  'trade_observations',
  'trade_observations',
  'CREATE TABLE trade_observations (\n'
  '    trade_id TEXT PRIMARY KEY,\n'
  '    economic_row_hash TEXT NOT NULL,\n'
  '    occurrence_index INTEGER NOT NULL,\n'
  '    side TEXT,\n'
  '    asset TEXT,\n'
  '    condition_id TEXT,\n'
  '    size_raw TEXT,\n'
  '    size REAL,\n'
  '    price_raw TEXT,\n'
  '    price REAL,\n'
  '    timestamp_raw TEXT,\n'
  '    timestamp_epoch REAL,\n'
  '    transaction_hash TEXT,\n'
  '    proxy_wallet TEXT,\n'
  '    outcome TEXT,\n'
  '    outcome_index_raw TEXT,\n'
  '    outcome_index INTEGER,\n'
  '    sanitized_trade_json TEXT NOT NULL,\n'
  '    first_received_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'trade_tape_memberships',
  'trade_tape_memberships',
  'CREATE TABLE trade_tape_memberships (\n'
  '    membership_id TEXT PRIMARY KEY,\n'
  '    trade_sweep_id TEXT NOT NULL REFERENCES trade_tape_sweeps(trade_sweep_id),\n'
  '    window_id TEXT NOT NULL REFERENCES trade_tape_windows(window_id),\n'
  '    trade_id TEXT NOT NULL REFERENCES trade_observations(trade_id),\n'
  '    item_number INTEGER NOT NULL,\n'
  '    received_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'trade_tape_sweeps',
  'trade_tape_sweeps',
  'CREATE TABLE trade_tape_sweeps (\n'
  '    trade_sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    target_start_epoch INTEGER NOT NULL,\n'
  '    source_target_end_epoch INTEGER NOT NULL,\n'
  '    bounded_target_end_epoch INTEGER NOT NULL,\n'
  '    watermark_before_epoch INTEGER,\n'
  '    watermark_advance_to_epoch INTEGER,\n'
  '    status TEXT NOT NULL,\n'
  '    possible_gap INTEGER NOT NULL,\n'
  '    window_count INTEGER NOT NULL,\n'
  '    membership_count INTEGER NOT NULL,\n'
  '    unique_trade_count INTEGER NOT NULL,\n'
  '    head_timestamp_raw TEXT,\n'
  '    tail_timestamp_raw TEXT,\n'
  '    membership_digest_sha256 TEXT NOT NULL,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'trade_tape_windows',
  'trade_tape_windows',
  'CREATE TABLE trade_tape_windows (\n'
  '    window_id TEXT PRIMARY KEY,\n'
  '    trade_sweep_id TEXT NOT NULL REFERENCES trade_tape_sweeps(trade_sweep_id),\n'
  '    parent_window_id TEXT,\n'
  '    start_epoch INTEGER NOT NULL,\n'
  '    end_epoch INTEGER NOT NULL,\n'
  '    split_depth INTEGER NOT NULL,\n'
  '    offset INTEGER NOT NULL,\n'
  '    request_id TEXT,\n'
  '    raw_payload_id TEXT,\n'
  '    received_at TEXT NOT NULL,\n'
  '    row_count INTEGER NOT NULL,\n'
  '    membership_count INTEGER NOT NULL,\n'
  '    economic_unique_count INTEGER NOT NULL,\n'
  '    duplicate_economic_row_count INTEGER NOT NULL,\n'
  '    membership_digest_sha256 TEXT NOT NULL,\n'
  '    hit_cap INTEGER NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    possible_gap INTEGER NOT NULL,\n'
  '    error_message TEXT\n'
  ')'),
 ('trigger',
  'api_requests_append_only_delete',
  'api_requests',
  'CREATE TRIGGER api_requests_append_only_delete\n'
  '                    BEFORE DELETE ON api_requests\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'api_requests_append_only_update',
  'api_requests',
  'CREATE TRIGGER api_requests_append_only_update\n'
  '                    BEFORE UPDATE ON api_requests\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'collection_contracts_append_only_delete',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_append_only_delete\n'
  '                    BEFORE DELETE ON collection_contracts\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'collection_contracts_append_only_update',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_append_only_update\n'
  '                    BEFORE UPDATE ON collection_contracts\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'data_quality_issues_append_only_delete',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_append_only_delete\n'
  '                    BEFORE DELETE ON data_quality_issues\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'data_quality_issues_append_only_update',
  'data_quality_issues',
  'CREATE TRIGGER data_quality_issues_append_only_update\n'
  '                    BEFORE UPDATE ON data_quality_issues\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'market_metadata_versions_append_only_delete',
  'market_metadata_versions',
  'CREATE TRIGGER market_metadata_versions_append_only_delete\n'
  '                    BEFORE DELETE ON market_metadata_versions\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'market_metadata_versions_append_only_update',
  'market_metadata_versions',
  'CREATE TRIGGER market_metadata_versions_append_only_update\n'
  '                    BEFORE UPDATE ON market_metadata_versions\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'market_observations_append_only_delete',
  'market_observations',
  'CREATE TRIGGER market_observations_append_only_delete\n'
  '                    BEFORE DELETE ON market_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'market_observations_append_only_update',
  'market_observations',
  'CREATE TRIGGER market_observations_append_only_update\n'
  '                    BEFORE UPDATE ON market_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'market_sweep_memberships_append_only_delete',
  'market_sweep_memberships',
  'CREATE TRIGGER market_sweep_memberships_append_only_delete\n'
  '                    BEFORE DELETE ON market_sweep_memberships\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'market_sweep_memberships_append_only_update',
  'market_sweep_memberships',
  'CREATE TRIGGER market_sweep_memberships_append_only_update\n'
  '                    BEFORE UPDATE ON market_sweep_memberships\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'market_sweeps_append_only_delete',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_append_only_delete\n'
  '                    BEFORE DELETE ON market_sweeps\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'market_sweeps_append_only_update',
  'market_sweeps',
  'CREATE TRIGGER market_sweeps_append_only_update\n'
  '                    BEFORE UPDATE ON market_sweeps\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'orderbook_depth_metrics_append_only_delete',
  'orderbook_depth_metrics',
  'CREATE TRIGGER orderbook_depth_metrics_append_only_delete\n'
  '                    BEFORE DELETE ON orderbook_depth_metrics\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'orderbook_depth_metrics_append_only_update',
  'orderbook_depth_metrics',
  'CREATE TRIGGER orderbook_depth_metrics_append_only_update\n'
  '                    BEFORE UPDATE ON orderbook_depth_metrics\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'orderbook_levels_append_only_delete',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_append_only_delete\n'
  '                    BEFORE DELETE ON orderbook_levels\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'orderbook_levels_append_only_update',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_append_only_update\n'
  '                    BEFORE UPDATE ON orderbook_levels\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'orderbook_selections_append_only_delete',
  'orderbook_selections',
  'CREATE TRIGGER orderbook_selections_append_only_delete\n'
  '                    BEFORE DELETE ON orderbook_selections\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'orderbook_selections_append_only_update',
  'orderbook_selections',
  'CREATE TRIGGER orderbook_selections_append_only_update\n'
  '                    BEFORE UPDATE ON orderbook_selections\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'orderbook_snapshots_append_only_delete',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_append_only_delete\n'
  '                    BEFORE DELETE ON orderbook_snapshots\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'orderbook_snapshots_append_only_update',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_append_only_update\n'
  '                    BEFORE UPDATE ON orderbook_snapshots\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'orderbook_token_attempts_append_only_delete',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_append_only_delete\n'
  '                    BEFORE DELETE ON orderbook_token_attempts\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'orderbook_token_attempts_append_only_update',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_append_only_update\n'
  '                    BEFORE UPDATE ON orderbook_token_attempts\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'outcome_observations_append_only_delete',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_append_only_delete\n'
  '                    BEFORE DELETE ON outcome_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'outcome_observations_append_only_update',
  'outcome_observations',
  'CREATE TRIGGER outcome_observations_append_only_update\n'
  '                    BEFORE UPDATE ON outcome_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'raw_payloads_append_only_delete',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_append_only_delete\n'
  '                    BEFORE DELETE ON raw_payloads\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'raw_payloads_append_only_update',
  'raw_payloads',
  'CREATE TRIGGER raw_payloads_append_only_update\n'
  '                    BEFORE UPDATE ON raw_payloads\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'research_config_versions_append_only_delete',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_append_only_delete\n'
  '                    BEFORE DELETE ON research_config_versions\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'research_config_versions_append_only_update',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_append_only_update\n'
  '                    BEFORE UPDATE ON research_config_versions\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'research_run_events_append_only_delete',
  'research_run_events',
  'CREATE TRIGGER research_run_events_append_only_delete\n'
  '                    BEFORE DELETE ON research_run_events\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'research_run_events_append_only_update',
  'research_run_events',
  'CREATE TRIGGER research_run_events_append_only_update\n'
  '                    BEFORE UPDATE ON research_run_events\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'resolution_observations_append_only_delete',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_append_only_delete\n'
  '                    BEFORE DELETE ON resolution_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'resolution_observations_append_only_update',
  'resolution_observations',
  'CREATE TRIGGER resolution_observations_append_only_update\n'
  '                    BEFORE UPDATE ON resolution_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'source_component_runs_append_only_delete',
  'source_component_runs',
  'CREATE TRIGGER source_component_runs_append_only_delete\n'
  '                    BEFORE DELETE ON source_component_runs\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'source_component_runs_append_only_update',
  'source_component_runs',
  'CREATE TRIGGER source_component_runs_append_only_update\n'
  '                    BEFORE UPDATE ON source_component_runs\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'storage_metrics_append_only_delete',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_append_only_delete\n'
  '                    BEFORE DELETE ON storage_metrics\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'storage_metrics_append_only_update',
  'storage_metrics',
  'CREATE TRIGGER storage_metrics_append_only_update\n'
  '                    BEFORE UPDATE ON storage_metrics\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'trade_observations_append_only_delete',
  'trade_observations',
  'CREATE TRIGGER trade_observations_append_only_delete\n'
  '                    BEFORE DELETE ON trade_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'trade_observations_append_only_update',
  'trade_observations',
  'CREATE TRIGGER trade_observations_append_only_update\n'
  '                    BEFORE UPDATE ON trade_observations\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'trade_tape_memberships_append_only_delete',
  'trade_tape_memberships',
  'CREATE TRIGGER trade_tape_memberships_append_only_delete\n'
  '                    BEFORE DELETE ON trade_tape_memberships\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'trade_tape_memberships_append_only_update',
  'trade_tape_memberships',
  'CREATE TRIGGER trade_tape_memberships_append_only_update\n'
  '                    BEFORE UPDATE ON trade_tape_memberships\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'trade_tape_sweeps_append_only_delete',
  'trade_tape_sweeps',
  'CREATE TRIGGER trade_tape_sweeps_append_only_delete\n'
  '                    BEFORE DELETE ON trade_tape_sweeps\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'trade_tape_sweeps_append_only_update',
  'trade_tape_sweeps',
  'CREATE TRIGGER trade_tape_sweeps_append_only_update\n'
  '                    BEFORE UPDATE ON trade_tape_sweeps\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'),
 ('trigger',
  'trade_tape_windows_append_only_delete',
  'trade_tape_windows',
  'CREATE TRIGGER trade_tape_windows_append_only_delete\n'
  '                    BEFORE DELETE ON trade_tape_windows\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');\n"
  '                    END'),
 ('trigger',
  'trade_tape_windows_append_only_update',
  'trade_tape_windows',
  'CREATE TRIGGER trade_tape_windows_append_only_update\n'
  '                    BEFORE UPDATE ON trade_tape_windows\n'
  '                    BEGIN\n'
  "                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');\n"
  '                    END'))

POMEGRANATE_RAW_TABLES = {'market_observations': ('pomegranate-market-observations-v1',
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    cycle_number INTEGER NOT NULL,\n'
                         '    page_number INTEGER NOT NULL,\n'
                         '    item_number INTEGER NOT NULL,\n'
                         '    page_received_at TEXT NOT NULL,\n'
                         '    page_request_id TEXT NOT NULL,\n'
                         '    source_market_key TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    market_id TEXT,\n'
                         '    event_id TEXT,\n'
                         '    event_slug TEXT,\n'
                         '    market_slug TEXT,\n'
                         '    question TEXT,\n'
                         '    volume_total_raw TEXT,\n'
                         '    volume_total REAL,\n'
                         '    volume_24h_raw TEXT,\n'
                         '    volume_24h REAL,\n'
                         '    volume_1h_raw TEXT,\n'
                         '    volume_1h REAL,\n'
                         '    volume_week_raw TEXT,\n'
                         '    volume_week REAL,\n'
                         '    volume_month_raw TEXT,\n'
                         '    volume_month REAL,\n'
                         '    volume_year_raw TEXT,\n'
                         '    volume_year REAL,\n'
                         '    liquidity_raw TEXT,\n'
                         '    liquidity REAL,\n'
                         '    liquidity_variants_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    best_bid REAL,\n'
                         '    best_ask REAL,\n'
                         '    spread REAL,\n'
                         '    last_trade_price REAL,\n'
                         '    price_changes_json TEXT NOT NULL,\n'
                         '    start_date TEXT,\n'
                         '    end_date TEXT,\n'
                         '    game_start_time TEXT,\n'
                         '    created_at_source TEXT,\n'
                         '    updated_at_source TEXT,\n'
                         '    tags_json TEXT NOT NULL,\n'
                         '    sports_json TEXT NOT NULL,\n'
                         '    category TEXT,\n'
                         '    active INTEGER,\n'
                         '    closed INTEGER,\n'
                         '    enable_order_book INTEGER,\n'
                         '    accepting_orders INTEGER,\n'
                         '    neg_risk INTEGER,\n'
                         '    fees_enabled INTEGER,\n'
                         '    fee_metadata_json TEXT NOT NULL,\n'
                         '    tick_size_raw TEXT,\n'
                         '    min_order_size_raw TEXT,\n'
                         '    source_clocks_json TEXT NOT NULL,\n'
                         '    parse_quality_json TEXT NOT NULL,\n'
                         '    raw_market_sha256 TEXT NOT NULL\n'
                         ')',
                         ('condition_id',
                          'market_id',
                          'event_id',
                          'event_slug',
                          'market_slug',
                          'question',
                          'volume_total_raw',
                          'volume_total',
                          'volume_24h_raw',
                          'volume_24h',
                          'volume_1h_raw',
                          'volume_1h',
                          'volume_week_raw',
                          'volume_week',
                          'volume_month_raw',
                          'volume_month',
                          'volume_year_raw',
                          'volume_year',
                          'liquidity_raw',
                          'liquidity',
                          'best_bid',
                          'best_ask',
                          'spread',
                          'last_trade_price',
                          'start_date',
                          'end_date',
                          'game_start_time',
                          'created_at_source',
                          'updated_at_source',
                          'category',
                          'active',
                          'closed',
                          'enable_order_book',
                          'accepting_orders',
                          'neg_risk',
                          'fees_enabled',
                          'tick_size_raw',
                          'min_order_size_raw'),
                         ('condition_id',),
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    cycle_number INTEGER NOT NULL,\n'
                         '    page_number INTEGER NOT NULL,\n'
                         '    item_number INTEGER NOT NULL,\n'
                         '    page_received_at TEXT NOT NULL,\n'
                         '    page_request_id TEXT NOT NULL,\n'
                         '    source_market_key TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    liquidity_variants_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    price_changes_json TEXT NOT NULL,\n'
                         '    tags_json TEXT NOT NULL,\n'
                         '    sports_json TEXT NOT NULL,\n'
                         '    fee_metadata_json TEXT NOT NULL,\n'
                         '    source_clocks_json TEXT NOT NULL,\n'
                         '    parse_quality_json TEXT NOT NULL,\n'
                         '    raw_market_sha256 TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                         ')'),
 'market_sweep_memberships': ('pomegranate-market-sweep-memberships-v1',
                              'CREATE TABLE market_sweep_memberships (\n'
                              '    membership_id TEXT PRIMARY KEY,\n'
                              '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                              '    observation_id TEXT NOT NULL REFERENCES '
                              'market_observations(observation_id),\n'
                              '    membership_ordinal INTEGER NOT NULL,\n'
                              '    page_number INTEGER NOT NULL,\n'
                              '    item_number INTEGER NOT NULL,\n'
                              '    page_received_at TEXT NOT NULL,\n'
                              '    source_market_key TEXT NOT NULL,\n'
                              '    condition_id TEXT,\n'
                              '    market_id TEXT,\n'
                              '    event_id TEXT,\n'
                              '    raw_market_sha256 TEXT NOT NULL,\n'
                              '    duplicate_ordinal INTEGER NOT NULL,\n'
                              '    UNIQUE (sweep_id, membership_ordinal)\n'
                              ')',
                              ('condition_id', 'market_id', 'event_id'),
                              (),
                              'CREATE TABLE market_sweep_memberships (\n'
                              '    membership_id TEXT PRIMARY KEY,\n'
                              '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                              '    observation_id TEXT NOT NULL REFERENCES '
                              'market_observations(observation_id),\n'
                              '    membership_ordinal INTEGER NOT NULL,\n'
                              '    page_number INTEGER NOT NULL,\n'
                              '    item_number INTEGER NOT NULL,\n'
                              '    page_received_at TEXT NOT NULL,\n'
                              '    source_market_key TEXT NOT NULL,\n'
                              '    raw_market_sha256 TEXT NOT NULL,\n'
                              '    duplicate_ordinal INTEGER NOT NULL,\n'
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              '    UNIQUE (sweep_id, membership_ordinal)\n'
                              ')'),
 'outcome_observations': ('pomegranate-outcome-observations-v1',
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(observation_id),\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    outcome_index INTEGER NOT NULL,\n'
                          '    outcome_label TEXT,\n'
                          '    token_id TEXT,\n'
                          '    price_raw TEXT,\n'
                          '    price REAL,\n'
                          '    label_present INTEGER NOT NULL,\n'
                          '    token_present INTEGER NOT NULL,\n'
                          '    price_present INTEGER NOT NULL,\n'
                          '    UNIQUE (observation_id, outcome_index)\n'
                          ')',
                          ('outcome_index', 'outcome_label', 'token_id', 'price_raw', 'price'),
                          ('outcome_index',),
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(observation_id),\n'
                          '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                          '    outcome_index INTEGER NOT NULL,\n'
                          '    label_present INTEGER NOT NULL,\n'
                          '    token_present INTEGER NOT NULL,\n'
                          '    price_present INTEGER NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                          '    UNIQUE (observation_id, outcome_index)\n'
                          ')'),
 'market_metadata_versions': ('pomegranate-market-metadata-versions-v1',
                              'CREATE TABLE market_metadata_versions (\n'
                              '    metadata_version_id TEXT PRIMARY KEY,\n'
                              '    source_market_key TEXT NOT NULL,\n'
                              '    condition_id TEXT,\n'
                              '    market_id TEXT,\n'
                              '    content_sha256 TEXT NOT NULL,\n'
                              '    metadata_json TEXT NOT NULL,\n'
                              '    first_observed_sweep_id TEXT NOT NULL REFERENCES '
                              'market_sweeps(sweep_id),\n'
                              '    first_observed_at TEXT NOT NULL,\n'
                              '    UNIQUE (source_market_key, content_sha256)\n'
                              ')',
                              ('condition_id', 'market_id'),
                              (),
                              'CREATE TABLE market_metadata_versions (\n'
                              '    metadata_version_id TEXT PRIMARY KEY,\n'
                              '    source_market_key TEXT NOT NULL,\n'
                              '    content_sha256 TEXT NOT NULL,\n'
                              '    metadata_json TEXT NOT NULL,\n'
                              '    first_observed_sweep_id TEXT NOT NULL REFERENCES '
                              'market_sweeps(sweep_id),\n'
                              '    first_observed_at TEXT NOT NULL,\n'
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              '    UNIQUE (source_market_key, content_sha256)\n'
                              ')'),
 'orderbook_selections': ('pomegranate-orderbook-selections-v1',
                          'CREATE TABLE orderbook_selections (\n'
                          '    selection_id TEXT PRIMARY KEY,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    cycle_number INTEGER NOT NULL,\n'
                          '    collection_id TEXT NOT NULL,\n'
                          '    source_market_key TEXT NOT NULL,\n'
                          '    condition_id TEXT,\n'
                          '    market_id TEXT,\n'
                          '    selection_reason TEXT NOT NULL,\n'
                          '    sampler_version TEXT NOT NULL,\n'
                          '    frame_market_count INTEGER NOT NULL,\n'
                          '    bucket_candidate_count INTEGER NOT NULL,\n'
                          '    bucket_visit_index INTEGER NOT NULL,\n'
                          '    sampler_slot INTEGER NOT NULL,\n'
                          '    rotation_offset INTEGER NOT NULL,\n'
                          '    wrap_around INTEGER NOT NULL,\n'
                          '    sample_max INTEGER NOT NULL,\n'
                          '    sampled_market_count INTEGER NOT NULL,\n'
                          '    truncated_count INTEGER NOT NULL,\n'
                          '    truncation_applied INTEGER NOT NULL,\n'
                          '    inclusion_probability_basis TEXT NOT NULL,\n'
                          '    long_run_coverage_basis TEXT NOT NULL,\n'
                          '    bucket_number INTEGER NOT NULL,\n'
                          '    bucket_count INTEGER NOT NULL,\n'
                          '    selection_rank TEXT NOT NULL,\n'
                          '    token_ids_json TEXT NOT NULL,\n'
                          '    outcome_labels_json TEXT NOT NULL,\n'
                          '    expected_token_count INTEGER NOT NULL,\n'
                          '    observed_token_count INTEGER NOT NULL,\n'
                          '    coverage_ratio REAL NOT NULL,\n'
                          '    status TEXT NOT NULL,\n'
                          '    error_message TEXT,\n'
                          '    selected_at TEXT NOT NULL\n'
                          ')',
                          ('condition_id', 'market_id'),
                          (),
                          'CREATE TABLE orderbook_selections (\n'
                          '    selection_id TEXT PRIMARY KEY,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    cycle_number INTEGER NOT NULL,\n'
                          '    collection_id TEXT NOT NULL,\n'
                          '    source_market_key TEXT NOT NULL,\n'
                          '    selection_reason TEXT NOT NULL,\n'
                          '    sampler_version TEXT NOT NULL,\n'
                          '    frame_market_count INTEGER NOT NULL,\n'
                          '    bucket_candidate_count INTEGER NOT NULL,\n'
                          '    bucket_visit_index INTEGER NOT NULL,\n'
                          '    sampler_slot INTEGER NOT NULL,\n'
                          '    rotation_offset INTEGER NOT NULL,\n'
                          '    wrap_around INTEGER NOT NULL,\n'
                          '    sample_max INTEGER NOT NULL,\n'
                          '    sampled_market_count INTEGER NOT NULL,\n'
                          '    truncated_count INTEGER NOT NULL,\n'
                          '    truncation_applied INTEGER NOT NULL,\n'
                          '    inclusion_probability_basis TEXT NOT NULL,\n'
                          '    long_run_coverage_basis TEXT NOT NULL,\n'
                          '    bucket_number INTEGER NOT NULL,\n'
                          '    bucket_count INTEGER NOT NULL,\n'
                          '    selection_rank TEXT NOT NULL,\n'
                          '    token_ids_json TEXT NOT NULL,\n'
                          '    outcome_labels_json TEXT NOT NULL,\n'
                          '    expected_token_count INTEGER NOT NULL,\n'
                          '    observed_token_count INTEGER NOT NULL,\n'
                          '    coverage_ratio REAL NOT NULL,\n'
                          '    status TEXT NOT NULL,\n'
                          '    error_message TEXT,\n'
                          '    selected_at TEXT NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                          ')'),
 'orderbook_token_attempts': ('pomegranate-orderbook-token-attempts-v1',
                              'CREATE TABLE orderbook_token_attempts (\n'
                              '    token_attempt_id TEXT PRIMARY KEY,\n'
                              '    selection_id TEXT NOT NULL REFERENCES '
                              'orderbook_selections(selection_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    cycle_number INTEGER NOT NULL,\n'
                              '    collection_id TEXT NOT NULL,\n'
                              '    token_id TEXT NOT NULL,\n'
                              '    outcome_index INTEGER,\n'
                              '    outcome_label TEXT,\n'
                              "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', "
                              "'MISSING', 'ERROR')),\n"
                              '    request_id TEXT,\n'
                              '    raw_payload_id TEXT REFERENCES raw_payloads(payload_id),\n'
                              '    received_at TEXT NOT NULL,\n'
                              '    bid_level_count INTEGER NOT NULL,\n'
                              '    ask_level_count INTEGER NOT NULL,\n'
                              '    error_type TEXT,\n'
                              '    error_message TEXT,\n'
                              '    UNIQUE (selection_id, token_id)\n'
                              ')',
                              ('token_id', 'outcome_index', 'outcome_label'),
                              ('token_id',),
                              'CREATE TABLE orderbook_token_attempts (\n'
                              '    token_attempt_id TEXT PRIMARY KEY,\n'
                              '    selection_id TEXT NOT NULL REFERENCES '
                              'orderbook_selections(selection_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    cycle_number INTEGER NOT NULL,\n'
                              '    collection_id TEXT NOT NULL,\n'
                              '    token_id TEXT NOT NULL,\n'
                              "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', "
                              "'MISSING', 'ERROR')),\n"
                              '    request_id TEXT,\n'
                              '    raw_payload_id TEXT REFERENCES raw_payloads(payload_id),\n'
                              '    received_at TEXT NOT NULL,\n'
                              '    bid_level_count INTEGER NOT NULL,\n'
                              '    ask_level_count INTEGER NOT NULL,\n'
                              '    error_type TEXT,\n'
                              '    error_message TEXT,\n'
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              '    UNIQUE (selection_id, token_id)\n'
                              ')'),
 'orderbook_snapshots': ('pomegranate-orderbook-snapshots-v1',
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    cycle_number INTEGER NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    received_at TEXT NOT NULL,\n'
                         '    request_id TEXT NOT NULL,\n'
                         '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
                         '    source_timestamp TEXT,\n'
                         '    source_hash TEXT,\n'
                         '    market TEXT,\n'
                         '    best_bid REAL,\n'
                         '    best_ask REAL,\n'
                         '    spread REAL,\n'
                         '    last_trade_price REAL,\n'
                         '    tick_size_raw TEXT,\n'
                         '    min_order_size_raw TEXT,\n'
                         '    neg_risk INTEGER,\n'
                         '    raw_book_sha256 TEXT NOT NULL\n'
                         ')',
                         ('token_id',
                          'source_timestamp',
                          'source_hash',
                          'market',
                          'best_bid',
                          'best_ask',
                          'spread',
                          'last_trade_price',
                          'tick_size_raw',
                          'min_order_size_raw',
                          'neg_risk'),
                         ('token_id',),
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    cycle_number INTEGER NOT NULL,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    received_at TEXT NOT NULL,\n'
                         '    request_id TEXT NOT NULL,\n'
                         '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),\n'
                         '    raw_book_sha256 TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                         ')'),
 'resolution_observations': ('pomegranate-resolution-observations-v1',
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    cycle_number INTEGER NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    request_id TEXT,\n'
                             '    market_id TEXT,\n'
                             '    resolved INTEGER,\n'
                             '    closed INTEGER,\n'
                             '    one_hot INTEGER,\n'
                             '    one_hot_outcome_index INTEGER,\n'
                             '    one_hot_outcome_label TEXT,\n'
                             '    resolution_value_raw TEXT,\n'
                             '    resolution_source_raw TEXT,\n'
                             '    redeemable INTEGER,\n'
                             '    source_updated_at TEXT,\n'
                             '    source_end_date TEXT,\n'
                             '    outcome_prices_json TEXT NOT NULL,\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    raw_market_json TEXT,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT\n'
                             ')',
                             ('condition_id',
                              'market_id',
                              'resolved',
                              'closed',
                              'resolution_value_raw',
                              'resolution_source_raw',
                              'redeemable',
                              'source_updated_at',
                              'source_end_date',
                              'one_hot_outcome_label'),
                             ('condition_id',),
                             'CREATE TABLE resolution_observations (\n'
                             '    resolution_observation_id TEXT PRIMARY KEY,\n'
                             '    run_id TEXT NOT NULL,\n'
                             '    cycle_number INTEGER NOT NULL,\n'
                             '    condition_id TEXT NOT NULL,\n'
                             '    requested_at TEXT NOT NULL,\n'
                             '    observed_at TEXT NOT NULL,\n'
                             '    lookup_status TEXT NOT NULL,\n'
                             '    request_id TEXT,\n'
                             '    one_hot INTEGER,\n'
                             '    one_hot_outcome_index INTEGER,\n'
                             '    outcome_prices_json TEXT NOT NULL,\n'
                             '    raw_market_sha256 TEXT,\n'
                             '    raw_market_json TEXT,\n'
                             '    error_type TEXT,\n'
                             '    error_message TEXT,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                             ')'),
 'trade_observations': ('pomegranate-trade-observations-v1',
                        'CREATE TABLE trade_observations (\n'
                        '    trade_id TEXT PRIMARY KEY,\n'
                        '    economic_row_hash TEXT NOT NULL,\n'
                        '    occurrence_index INTEGER NOT NULL,\n'
                        '    side TEXT,\n'
                        '    asset TEXT,\n'
                        '    condition_id TEXT,\n'
                        '    size_raw TEXT,\n'
                        '    size REAL,\n'
                        '    price_raw TEXT,\n'
                        '    price REAL,\n'
                        '    timestamp_raw TEXT,\n'
                        '    timestamp_epoch REAL,\n'
                        '    transaction_hash TEXT,\n'
                        '    proxy_wallet TEXT,\n'
                        '    outcome TEXT,\n'
                        '    outcome_index_raw TEXT,\n'
                        '    outcome_index INTEGER,\n'
                        '    sanitized_trade_json TEXT NOT NULL,\n'
                        '    first_received_at TEXT NOT NULL\n'
                        ')',
                        ('side',
                         'asset',
                         'condition_id',
                         'size_raw',
                         'size',
                         'price_raw',
                         'price',
                         'timestamp_raw',
                         'timestamp_epoch',
                         'transaction_hash',
                         'proxy_wallet',
                         'outcome',
                         'outcome_index_raw',
                         'outcome_index'),
                        (),
                        'CREATE TABLE trade_observations (\n'
                        '    trade_id TEXT PRIMARY KEY,\n'
                        '    economic_row_hash TEXT NOT NULL,\n'
                        '    occurrence_index INTEGER NOT NULL,\n'
                        '    sanitized_trade_json TEXT NOT NULL,\n'
                        '    first_received_at TEXT NOT NULL,\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                        ')'),
 'trade_tape_sweeps': ('pomegranate-trade-tape-sweeps-v1',
                       'CREATE TABLE trade_tape_sweeps (\n'
                       '    trade_sweep_id TEXT PRIMARY KEY,\n'
                       '    run_id TEXT NOT NULL,\n'
                       '    cycle_number INTEGER NOT NULL,\n'
                       '    started_at TEXT NOT NULL,\n'
                       '    completed_at TEXT NOT NULL,\n'
                       '    target_start_epoch INTEGER NOT NULL,\n'
                       '    source_target_end_epoch INTEGER NOT NULL,\n'
                       '    bounded_target_end_epoch INTEGER NOT NULL,\n'
                       '    watermark_before_epoch INTEGER,\n'
                       '    watermark_advance_to_epoch INTEGER,\n'
                       '    status TEXT NOT NULL,\n'
                       '    possible_gap INTEGER NOT NULL,\n'
                       '    window_count INTEGER NOT NULL,\n'
                       '    membership_count INTEGER NOT NULL,\n'
                       '    unique_trade_count INTEGER NOT NULL,\n'
                       '    head_timestamp_raw TEXT,\n'
                       '    tail_timestamp_raw TEXT,\n'
                       '    membership_digest_sha256 TEXT NOT NULL,\n'
                       '    error_message TEXT\n'
                       ')',
                       ('head_timestamp_raw', 'tail_timestamp_raw'),
                       (),
                       'CREATE TABLE trade_tape_sweeps (\n'
                       '    trade_sweep_id TEXT PRIMARY KEY,\n'
                       '    run_id TEXT NOT NULL,\n'
                       '    cycle_number INTEGER NOT NULL,\n'
                       '    started_at TEXT NOT NULL,\n'
                       '    completed_at TEXT NOT NULL,\n'
                       '    target_start_epoch INTEGER NOT NULL,\n'
                       '    source_target_end_epoch INTEGER NOT NULL,\n'
                       '    bounded_target_end_epoch INTEGER NOT NULL,\n'
                       '    watermark_before_epoch INTEGER,\n'
                       '    watermark_advance_to_epoch INTEGER,\n'
                       '    status TEXT NOT NULL,\n'
                       '    possible_gap INTEGER NOT NULL,\n'
                       '    window_count INTEGER NOT NULL,\n'
                       '    membership_count INTEGER NOT NULL,\n'
                       '    unique_trade_count INTEGER NOT NULL,\n'
                       '    membership_digest_sha256 TEXT NOT NULL,\n'
                       '    error_message TEXT,\n'
                       '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                       ')'),
 'resolution_watchlist': ('pomegranate-resolution-watchlist-v1',
                          'CREATE TABLE resolution_watchlist (\n'
                          '    condition_id TEXT PRIMARY KEY,\n'
                          '    market_id TEXT,\n'
                          '    source_market_key TEXT NOT NULL,\n'
                          '    first_seen_sweep_id TEXT NOT NULL,\n'
                          '    first_seen_at TEXT NOT NULL,\n'
                          '    selection_reason TEXT NOT NULL\n'
                          '    ,carried_from_utc_date TEXT\n'
                          '    ,prior_state_json TEXT\n'
                          '    ,terminal INTEGER NOT NULL DEFAULT 0\n'
                          ')',
                          ('condition_id', 'market_id'),
                          ('condition_id',),
                          'CREATE TABLE resolution_watchlist (\n'
                          '    condition_id TEXT PRIMARY KEY,\n'
                          '    source_market_key TEXT NOT NULL,\n'
                          '    first_seen_sweep_id TEXT NOT NULL,\n'
                          '    first_seen_at TEXT NOT NULL,\n'
                          '    selection_reason TEXT NOT NULL\n'
                          '    ,carried_from_utc_date TEXT\n'
                          '    ,prior_state_json TEXT\n'
                          '    ,terminal INTEGER NOT NULL DEFAULT 0,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                          ')'),
 'prior_census_conditions': ('pomegranate-prior-census-conditions-v1',
                             'CREATE TABLE prior_census_conditions (\n'
                             '    condition_id TEXT PRIMARY KEY,\n'
                             '    source_market_key TEXT NOT NULL,\n'
                             '    market_id TEXT,\n'
                             '    prior_sweep_id TEXT NOT NULL,\n'
                             '    carried_from_utc_date TEXT NOT NULL,\n'
                             '    carried_at TEXT NOT NULL\n'
                             ')',
                             ('condition_id', 'market_id'),
                             ('condition_id',),
                             'CREATE TABLE prior_census_conditions (\n'
                             '    condition_id TEXT PRIMARY KEY,\n'
                             '    source_market_key TEXT NOT NULL,\n'
                             '    prior_sweep_id TEXT NOT NULL,\n'
                             '    carried_from_utc_date TEXT NOT NULL,\n'
                             '    carried_at TEXT NOT NULL,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                             ')')}

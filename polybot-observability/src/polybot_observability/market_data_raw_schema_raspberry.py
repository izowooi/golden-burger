"""Reviewed Raspberry queue-echo-v3 SQLite v3 source ownership.

Frozen native tables, explicit indexes and original no_update/no_delete guards.
Receipt clocks, experimental calculations and durable lease/request markers stay
private. Public source-copy cells do not assert actual trading fills or P&L.
Legacy queue-echo-v1/v2 layouts are deliberately outside this profile.
"""

RASPBERRY_LOGICAL_SCHEMA_SHA256 = 'd75d65dd206fe20ef4a466a42305205dcf633c901eb8d31088cae14fef23cf92'

RASPBERRY_SCHEMA_OBJECTS = (('index',
  'api_requests_run_idx',
  'api_requests',
  'CREATE INDEX api_requests_run_idx ON api_requests(run_id, request_kind)'),
 ('index',
  'book_snapshot_history_idx',
  'orderbook_snapshots',
  'CREATE INDEX book_snapshot_history_idx\n'
  '    ON orderbook_snapshots(condition_id, token_id, observed_at)'),
 ('index',
  'cycle_slot_events_slot_idx',
  'cycle_slot_events',
  'CREATE INDEX cycle_slot_events_slot_idx\n    ON cycle_slot_events(job_name, slot_at, event_type)'),
 ('index',
  'followup_case_idx',
  'followup_attempts',
  'CREATE INDEX followup_case_idx ON followup_attempts(case_id, attempted_at)'),
 ('index',
  'followup_claim_leases_latest_idx',
  'followup_claim_leases',
  'CREATE INDEX followup_claim_leases_latest_idx\n'
  '    ON followup_claim_leases(claim_id, generation DESC)'),
 ('index',
  'market_obs_condition_idx',
  'market_observations',
  'CREATE INDEX market_obs_condition_idx\n    ON market_observations(condition_id, observed_at)'),
 ('index',
  'research_cases_due_idx',
  'research_cases',
  'CREATE INDEX research_cases_due_idx\n    ON research_cases(target_at, window_end)'),
 ('index',
  'research_run_events_run_idx',
  'research_run_events',
  'CREATE INDEX research_run_events_run_idx\n    ON research_run_events(run_id, event_at)'),
 ('index',
  'signal_decisions_qualified_idx',
  'signal_decisions',
  'CREATE INDEX signal_decisions_qualified_idx\n'
  '    ON signal_decisions(qualified, evaluated_at, condition_id)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    request_id TEXT PRIMARY KEY,\n'
  '    logical_request_id TEXT NOT NULL,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method IN ('GET', 'POST')),\n"
  '    url TEXT NOT NULL,\n'
  '    params_json TEXT NOT NULL,\n'
  '    body_sha256 TEXT,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL,\n'
  '    timeout_connect_seconds REAL NOT NULL,\n'
  '    timeout_read_seconds REAL NOT NULL,\n'
  '    budget_remaining_before_seconds REAL,\n'
  "    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'ERROR')),\n"
  '    http_status INTEGER,\n'
  '    retryable INTEGER NOT NULL,\n'
  '    retry_after_seconds REAL,\n'
  '    response_sha256 TEXT,\n'
  '    response_bytes INTEGER,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'cycle_slot_claims',
  'cycle_slot_claims',
  'CREATE TABLE cycle_slot_claims (\n'
  '    claim_id TEXT PRIMARY KEY,\n'
  '    slot_id TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    invocation_id TEXT NOT NULL UNIQUE,\n'
  '    owner_run_id TEXT,\n'
  '    slot_at TEXT NOT NULL,\n'
  '    claimed_at TEXT NOT NULL,\n'
  '    lateness_seconds REAL NOT NULL CHECK (lateness_seconds >= 0),\n'
  "    disposition TEXT NOT NULL CHECK (disposition IN ('CLAIMED', 'SKIPPED_LATE')),\n"
  '    UNIQUE (job_name, slot_id),\n'
  '    UNIQUE (job_name, slot_at)\n'
  ')'),
 ('table',
  'cycle_slot_events',
  'cycle_slot_events',
  'CREATE TABLE cycle_slot_events (\n'
  '    event_id TEXT PRIMARY KEY,\n'
  '    invocation_id TEXT NOT NULL UNIQUE,\n'
  '    claim_id TEXT REFERENCES cycle_slot_claims(claim_id),\n'
  '    slot_id TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  '    slot_at TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    lateness_seconds REAL NOT NULL CHECK (lateness_seconds >= 0),\n'
  '    event_type TEXT NOT NULL CHECK (\n'
  "        event_type IN ('CLAIMED', 'SKIPPED_LATE', 'SKIPPED_DUPLICATE')\n"
  '    ),\n'
  '    details_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'cycle_stats',
  'cycle_stats',
  'CREATE TABLE cycle_stats (\n'
  '    cycle_stat_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    cycle_number INTEGER NOT NULL UNIQUE,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    shard_index INTEGER NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    runtime_seconds REAL NOT NULL,\n'
  '    stats_json TEXT NOT NULL,\n'
  '    db_bytes INTEGER NOT NULL,\n'
  '    wal_bytes INTEGER NOT NULL\n'
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    issue_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    sweep_id TEXT,\n'
  "    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'HIGH', 'CRITICAL')),\n"
  '    issue_code TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    recorded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'experiment_contracts',
  'experiment_contracts',
  'CREATE TABLE experiment_contracts (\n'
  '    job_name TEXT PRIMARY KEY,\n'
  '    strategy_name TEXT NOT NULL,\n'
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3'),\n"
  '    schema_version INTEGER NOT NULL CHECK (schema_version = 3),\n'
  "    schema_profile TEXT NOT NULL CHECK (schema_profile = 'queue-echo-v3-sqlite-v3'),\n"
  '    shard_index INTEGER NOT NULL CHECK (shard_index BETWEEN 0 AND 2),\n'
  '    shard_count INTEGER NOT NULL CHECK (shard_count = 3),\n'
  '    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 5),\n'
  '    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute BETWEEN 0 AND 2),\n'
  '    window_start TEXT NOT NULL,\n'
  '    window_end TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    data_contract_sha256 TEXT NOT NULL,\n'
  '    contract_json TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'followup_attempts',
  'followup_attempts',
  'CREATE TABLE followup_attempts (\n'
  '    followup_id TEXT PRIMARY KEY,\n'
  '    case_id TEXT NOT NULL REFERENCES research_cases(case_id),\n'
  '    claim_id TEXT NOT NULL UNIQUE REFERENCES followup_claims(claim_id),\n'
  '    observing_run_id TEXT NOT NULL,\n'
  '    attempted_at TEXT NOT NULL,\n'
  '    status TEXT NOT NULL CHECK (status IN (\n'
  "        'QUOTE_COMPLETE', 'SOURCE_MISSING', 'EMPTY_BOOK', 'INVALID_QUOTE',\n"
  "        'WINDOW_EXPIRED', 'STALE_REQUEST_UNKNOWN'\n"
  '    )),\n'
  '    source_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    observed_at TEXT,\n'
  '    exit_bid REAL,\n'
  '    exit_vwap REAL,\n'
  '    exit_proceeds_usdc REAL,\n'
  '    executable_return_bps REAL,\n'
  '    base_stressed_return_bps REAL,\n'
  '    severe_stressed_return_bps REAL,\n'
  '    details_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'followup_claim_leases',
  'followup_claim_leases',
  'CREATE TABLE followup_claim_leases (\n'
  '    lease_id TEXT PRIMARY KEY,\n'
  '    claim_id TEXT NOT NULL REFERENCES followup_claims(claim_id),\n'
  '    generation INTEGER NOT NULL CHECK (generation >= 1),\n'
  '    owner_run_id TEXT NOT NULL,\n'
  '    claimed_at TEXT NOT NULL,\n'
  '    lease_expires_at TEXT NOT NULL,\n'
  '    recovery_reason TEXT NOT NULL,\n'
  '    UNIQUE (claim_id, generation)\n'
  ')'),
 ('table',
  'followup_claims',
  'followup_claims',
  'CREATE TABLE followup_claims (\n'
  '    claim_id TEXT PRIMARY KEY,\n'
  '    case_id TEXT NOT NULL UNIQUE REFERENCES research_cases(case_id),\n'
  '    first_claimed_by_run_id TEXT NOT NULL,\n'
  '    claimed_at TEXT NOT NULL,\n'
  '    target_at TEXT NOT NULL,\n'
  '    window_end TEXT NOT NULL\n'
  ')'),
 ('table',
  'followup_request_starts',
  'followup_request_starts',
  'CREATE TABLE followup_request_starts (\n'
  '    claim_id TEXT PRIMARY KEY REFERENCES followup_claims(claim_id),\n'
  '    lease_id TEXT NOT NULL REFERENCES followup_claim_leases(lease_id),\n'
  '    observing_run_id TEXT NOT NULL,\n'
  '    logical_request_id TEXT NOT NULL,\n'
  '    request_started_at TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_observations',
  'market_observations',
  'CREATE TABLE market_observations (\n'
  '    observation_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    market_slug TEXT,\n'
  '    question TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    end_date TEXT NOT NULL,\n'
  '    hours_to_end REAL NOT NULL,\n'
  '    liquidity REAL NOT NULL,\n'
  '    volume_total REAL NOT NULL,\n'
  '    volume_24h REAL NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    outcome_labels_json TEXT NOT NULL,\n'
  '    outcome_prices_json TEXT NOT NULL,\n'
  '    gamma_best_bid REAL,\n'
  '    gamma_best_ask REAL,\n'
  '    gamma_spread REAL,\n'
  '    raw_market_sha256 TEXT NOT NULL,\n'
  '    event_selection_hash TEXT NOT NULL,\n'
  '    panel_selected INTEGER NOT NULL,\n'
  '    shard_index INTEGER NOT NULL,\n'
  '    shard_selected INTEGER NOT NULL,\n'
  '    tags_json TEXT NOT NULL,\n'
  '    UNIQUE (sweep_id, condition_id)\n'
  ')'),
 ('table',
  'market_sweeps',
  'market_sweeps',
  'CREATE TABLE market_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    cycle_number INTEGER NOT NULL UNIQUE,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),\n'
  '    page_count INTEGER NOT NULL,\n'
  '    source_envelope_count INTEGER NOT NULL,\n'
  '    parsed_market_count INTEGER NOT NULL,\n'
  '    eligible_market_count INTEGER NOT NULL,\n'
  '    membership_sha256 TEXT NOT NULL,\n'
  "    membership_encoding TEXT NOT NULL CHECK (membership_encoding = 'gzip-json-v1'),\n"
  '    membership_blob BLOB NOT NULL,\n'
  '    funnel_json TEXT NOT NULL,\n'
  '    source_filter_json TEXT NOT NULL,\n'
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3')\n"
  ')'),
 ('table',
  'orderbook_levels',
  'orderbook_levels',
  'CREATE TABLE orderbook_levels (\n'
  '    level_id TEXT PRIMARY KEY,\n'
  '    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
  "    side TEXT NOT NULL CHECK (side IN ('BID', 'ASK')),\n"
  '    level_index INTEGER NOT NULL,\n'
  '    price REAL NOT NULL,\n'
  '    size REAL NOT NULL,\n'
  '    in_near_touch_window INTEGER NOT NULL,\n'
  '    used_for_entry INTEGER NOT NULL,\n'
  '    UNIQUE (snapshot_id, side, level_index)\n'
  ')'),
 ('table',
  'orderbook_snapshots',
  'orderbook_snapshots',
  'CREATE TABLE orderbook_snapshots (\n'
  '    snapshot_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER,\n'
  '    outcome_label TEXT,\n'
  "    snapshot_role TEXT NOT NULL CHECK (snapshot_role IN ('UNIVERSE', 'FOLLOWUP_ONLY')),\n"
  '    request_started_at TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    source_hash TEXT,\n'
  '    raw_book_sha256 TEXT NOT NULL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    spread REAL,\n'
  '    tick_size REAL,\n'
  '    min_order_size REAL,\n'
  '    best_bid_notional REAL,\n'
  '    best_ask_notional REAL,\n'
  '    one_tick_spread INTEGER NOT NULL,\n'
  '    near_bid_notional REAL,\n'
  '    near_ask_notional REAL,\n'
  '    weighted_imbalance REAL,\n'
  '    pair_score REAL,\n'
  '    entry_notional_usdc REAL,\n'
  '    entry_vwap REAL,\n'
  '    entry_shares REAL,\n'
  '    entry_complete INTEGER NOT NULL,\n'
  '    quote_eligible INTEGER NOT NULL,\n'
  '    candidate_up INTEGER NOT NULL,\n'
  "    CHECK (snapshot_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
  '    UNIQUE (sweep_id, token_id, snapshot_role)\n'
  ')'),
 ('table',
  'orderbook_token_attempts',
  'orderbook_token_attempts',
  'CREATE TABLE orderbook_token_attempts (\n'
  '    attempt_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER,\n'
  '    outcome_label TEXT,\n'
  "    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('UNIVERSE', 'FOLLOWUP_ONLY')),\n"
  "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', 'MISSING', 'MALFORMED', "
  "'ERROR')),\n"
  '    request_id TEXT,\n'
  '    logical_request_id TEXT,\n'
  '    request_started_at TEXT,\n'
  '    received_at TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  "    CHECK (attempt_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
  '    UNIQUE (sweep_id, token_id, attempt_role)\n'
  ')'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads (\n'
  '    payload_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_id TEXT NOT NULL REFERENCES api_requests(request_id),\n'
  '    logical_request_id TEXT NOT NULL,\n'
  '    payload_kind TEXT NOT NULL CHECK (\n'
  "        payload_kind IN ('clob_universe_books', 'clob_followup_books')\n"
  '    ),\n'
  "    content_encoding TEXT NOT NULL CHECK (content_encoding = 'gzip'),\n"
  '    payload_sha256 TEXT NOT NULL,\n'
  '    uncompressed_bytes INTEGER NOT NULL,\n'
  '    compressed_bytes INTEGER NOT NULL,\n'
  '    payload_blob BLOB NOT NULL,\n'
  '    recorded_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_cases',
  'research_cases',
  'CREATE TABLE research_cases (\n'
  '    case_id TEXT PRIMARY KEY,\n'
  '    decision_id TEXT NOT NULL REFERENCES signal_decisions(decision_id),\n'
  "    case_kind TEXT NOT NULL CHECK (case_kind IN ('SIGNAL', 'CONTROL', 'OPPOSITE')),\n"
  '    matched_pair_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_id TEXT,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_label TEXT,\n'
  '    entry_snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    entry_at TEXT NOT NULL,\n'
  '    entry_cost_usdc REAL NOT NULL,\n'
  '    entry_shares REAL NOT NULL,\n'
  '    entry_vwap REAL NOT NULL,\n'
  '    target_at TEXT NOT NULL,\n'
  '    window_end TEXT NOT NULL,\n'
  '    control_match_distance REAL,\n'
  '    UNIQUE (decision_id, case_kind)\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  '    strategy_name TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  '    shard_index INTEGER NOT NULL,\n'
  '    shard_count INTEGER NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode = 'sim'),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),\n"
  "    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3'),\n"
  '    schema_version INTEGER NOT NULL CHECK (schema_version = 3),\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    data_contract_sha256 TEXT NOT NULL,\n'
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
  "    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED', 'SUCCEEDED', 'FAILED')),\n"
  '    event_at TEXT NOT NULL,\n'
  '    details_json TEXT NOT NULL,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT\n'
  ')'),
 ('table',
  'schema_metadata',
  'schema_metadata',
  'CREATE TABLE schema_metadata (\n    key TEXT PRIMARY KEY,\n    value TEXT NOT NULL\n)'),
 ('table',
  'signal_decisions',
  'signal_decisions',
  'CREATE TABLE signal_decisions (\n'
  '    decision_id TEXT PRIMARY KEY,\n'
  '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    config_hash TEXT NOT NULL,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    market_id TEXT,\n'
  '    event_id TEXT,\n'
  '    arm TEXT NOT NULL,\n'
  '    confirmation_steps INTEGER NOT NULL,\n'
  '    evaluated_at TEXT NOT NULL,\n'
  '    pair_snapshot_ids_json TEXT NOT NULL,\n'
  '    pair_score REAL,\n'
  '    pair_received_skew_seconds REAL,\n'
  '    neutral_candidate INTEGER NOT NULL,\n'
  '    selected_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    selected_token_id TEXT,\n'
  '    selected_outcome_label TEXT,\n'
  '    history_snapshot_ids_json TEXT NOT NULL,\n'
  '    history_timestamps_json TEXT NOT NULL,\n'
  '    history_scores_json TEXT NOT NULL,\n'
  '    history_gaps_minutes_json TEXT NOT NULL,\n'
  '    one_sided_candidate INTEGER NOT NULL,\n'
  '    persistence_passed INTEGER NOT NULL,\n'
  '    cooldown_allowed INTEGER NOT NULL,\n'
  '    experiment_window_eligible INTEGER NOT NULL,\n'
  '    qualified INTEGER NOT NULL,\n'
  '    rejection_reason TEXT NOT NULL,\n'
  '    target_at TEXT,\n'
  '    window_end TEXT,\n'
  '    prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    prior_15m_move REAL,\n'
  "    prior_move_bin TEXT NOT NULL CHECK (prior_move_bin IN ('DOWN','FLAT','UP','MISSING')),\n"
  '    matched_control_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    matched_control_prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
  '    matched_control_prior_15m_move REAL,\n'
  '    matched_control_prior_move_bin TEXT CHECK (matched_control_prior_move_bin IN '
  "('DOWN','FLAT','UP','MISSING')),\n"
  '    control_match_distance REAL,\n'
  '    UNIQUE (sweep_id, condition_id, arm)\n'
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
  '    wal_bytes INTEGER NOT NULL,\n'
  '    filesystem_total_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_bytes INTEGER NOT NULL,\n'
  '    filesystem_free_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_ratio REAL NOT NULL,\n'
  "    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK', 'WARN', 'STOP'))\n"
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
  'cycle_slot_claims_no_delete',
  'cycle_slot_claims',
  'CREATE TRIGGER cycle_slot_claims_no_delete BEFORE DELETE ON cycle_slot_claims BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'cycle_slot_claims_no_update',
  'cycle_slot_claims',
  'CREATE TRIGGER cycle_slot_claims_no_update BEFORE UPDATE ON cycle_slot_claims BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'cycle_slot_events_no_delete',
  'cycle_slot_events',
  'CREATE TRIGGER cycle_slot_events_no_delete BEFORE DELETE ON cycle_slot_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'cycle_slot_events_no_update',
  'cycle_slot_events',
  'CREATE TRIGGER cycle_slot_events_no_update BEFORE UPDATE ON cycle_slot_events BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'cycle_stats_no_delete',
  'cycle_stats',
  'CREATE TRIGGER cycle_stats_no_delete BEFORE DELETE ON cycle_stats BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'cycle_stats_no_update',
  'cycle_stats',
  'CREATE TRIGGER cycle_stats_no_update BEFORE UPDATE ON cycle_stats BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
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
  'followup_attempts_no_delete',
  'followup_attempts',
  'CREATE TRIGGER followup_attempts_no_delete BEFORE DELETE ON followup_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'followup_attempts_no_update',
  'followup_attempts',
  'CREATE TRIGGER followup_attempts_no_update BEFORE UPDATE ON followup_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'followup_claim_leases_no_delete',
  'followup_claim_leases',
  'CREATE TRIGGER followup_claim_leases_no_delete BEFORE DELETE ON followup_claim_leases BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'followup_claim_leases_no_update',
  'followup_claim_leases',
  'CREATE TRIGGER followup_claim_leases_no_update BEFORE UPDATE ON followup_claim_leases BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'followup_claims_no_delete',
  'followup_claims',
  'CREATE TRIGGER followup_claims_no_delete BEFORE DELETE ON followup_claims BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'followup_claims_no_update',
  'followup_claims',
  'CREATE TRIGGER followup_claims_no_update BEFORE UPDATE ON followup_claims BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'followup_request_starts_no_delete',
  'followup_request_starts',
  'CREATE TRIGGER followup_request_starts_no_delete BEFORE DELETE ON followup_request_starts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'followup_request_starts_no_update',
  'followup_request_starts',
  'CREATE TRIGGER followup_request_starts_no_update BEFORE UPDATE ON followup_request_starts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_observations_no_delete',
  'market_observations',
  'CREATE TRIGGER market_observations_no_delete BEFORE DELETE ON market_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'market_observations_no_update',
  'market_observations',
  'CREATE TRIGGER market_observations_no_update BEFORE UPDATE ON market_observations BEGIN SELECT '
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
  'orderbook_levels_no_delete',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_no_delete BEFORE DELETE ON orderbook_levels BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_levels_no_update',
  'orderbook_levels',
  'CREATE TRIGGER orderbook_levels_no_update BEFORE UPDATE ON orderbook_levels BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_snapshots_no_delete',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_no_delete BEFORE DELETE ON orderbook_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_snapshots_no_update',
  'orderbook_snapshots',
  'CREATE TRIGGER orderbook_snapshots_no_update BEFORE UPDATE ON orderbook_snapshots BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_token_attempts_no_delete',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_no_delete BEFORE DELETE ON orderbook_token_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'orderbook_token_attempts_no_update',
  'orderbook_token_attempts',
  'CREATE TRIGGER orderbook_token_attempts_no_update BEFORE UPDATE ON orderbook_token_attempts BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
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
  'research_cases_no_delete',
  'research_cases',
  'CREATE TRIGGER research_cases_no_delete BEFORE DELETE ON research_cases BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'research_cases_no_update',
  'research_cases',
  'CREATE TRIGGER research_cases_no_update BEFORE UPDATE ON research_cases BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_no_delete',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_delete BEFORE DELETE ON research_config_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'research_config_versions_no_update',
  'research_config_versions',
  'CREATE TRIGGER research_config_versions_no_update BEFORE UPDATE ON research_config_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
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
  'signal_decisions_no_delete',
  'signal_decisions',
  'CREATE TRIGGER signal_decisions_no_delete BEFORE DELETE ON signal_decisions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'signal_decisions_no_update',
  'signal_decisions',
  'CREATE TRIGGER signal_decisions_no_update BEFORE UPDATE ON signal_decisions BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
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

RASPBERRY_RAW_TABLES = {'market_observations': ('raspberry-market-observations-v3',
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    condition_id TEXT NOT NULL,\n'
                         '    market_id TEXT,\n'
                         '    event_id TEXT,\n'
                         '    market_slug TEXT,\n'
                         '    question TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    end_date TEXT NOT NULL,\n'
                         '    hours_to_end REAL NOT NULL,\n'
                         '    liquidity REAL NOT NULL,\n'
                         '    volume_total REAL NOT NULL,\n'
                         '    volume_24h REAL NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    outcome_labels_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    gamma_best_bid REAL,\n'
                         '    gamma_best_ask REAL,\n'
                         '    gamma_spread REAL,\n'
                         '    raw_market_sha256 TEXT NOT NULL,\n'
                         '    event_selection_hash TEXT NOT NULL,\n'
                         '    panel_selected INTEGER NOT NULL,\n'
                         '    shard_index INTEGER NOT NULL,\n'
                         '    shard_selected INTEGER NOT NULL,\n'
                         '    tags_json TEXT NOT NULL,\n'
                         '    UNIQUE (sweep_id, condition_id)\n'
                         ')',
                         ('condition_id',
                          'market_id',
                          'event_id',
                          'market_slug',
                          'question',
                          'end_date',
                          'liquidity',
                          'volume_total',
                          'volume_24h',
                          'gamma_best_bid',
                          'gamma_best_ask',
                          'gamma_spread'),
                         ('condition_id',),
                         'CREATE TABLE market_observations (\n'
                         '    observation_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    condition_id TEXT NOT NULL,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    hours_to_end REAL NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    outcome_labels_json TEXT NOT NULL,\n'
                         '    outcome_prices_json TEXT NOT NULL,\n'
                         '    raw_market_sha256 TEXT NOT NULL,\n'
                         '    event_selection_hash TEXT NOT NULL,\n'
                         '    panel_selected INTEGER NOT NULL,\n'
                         '    shard_index INTEGER NOT NULL,\n'
                         '    shard_selected INTEGER NOT NULL,\n'
                         '    tags_json TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (sweep_id, condition_id)\n'
                         ')'),
 'orderbook_token_attempts': ('raspberry-orderbook-token-attempts-v3',
                              'CREATE TABLE orderbook_token_attempts (\n'
                              '    attempt_id TEXT PRIMARY KEY,\n'
                              '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    condition_id TEXT,\n'
                              '    token_id TEXT NOT NULL,\n'
                              '    outcome_index INTEGER,\n'
                              '    outcome_label TEXT,\n'
                              "    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('UNIVERSE', "
                              "'FOLLOWUP_ONLY')),\n"
                              "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', "
                              "'MISSING', 'MALFORMED', 'ERROR')),\n"
                              '    request_id TEXT,\n'
                              '    logical_request_id TEXT,\n'
                              '    request_started_at TEXT,\n'
                              '    received_at TEXT,\n'
                              '    error_type TEXT,\n'
                              '    error_message TEXT,\n'
                              "    CHECK (attempt_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
                              '    UNIQUE (sweep_id, token_id, attempt_role)\n'
                              ')',
                              ('condition_id', 'token_id', 'outcome_index', 'outcome_label'),
                              ('token_id',),
                              'CREATE TABLE orderbook_token_attempts (\n'
                              '    attempt_id TEXT PRIMARY KEY,\n'
                              '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    token_id TEXT NOT NULL,\n'
                              "    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('UNIVERSE', "
                              "'FOLLOWUP_ONLY')),\n"
                              "    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', "
                              "'MISSING', 'MALFORMED', 'ERROR')),\n"
                              '    request_id TEXT,\n'
                              '    logical_request_id TEXT,\n'
                              '    request_started_at TEXT,\n'
                              '    received_at TEXT,\n'
                              '    error_type TEXT,\n'
                              '    error_message TEXT,\n'
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              "    CHECK (attempt_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
                              '    UNIQUE (sweep_id, token_id, attempt_role)\n'
                              ')'),
 'orderbook_snapshots': ('raspberry-orderbook-snapshots-v3',
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    config_hash TEXT NOT NULL,\n'
                         '    strategy_source_digest TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    market_id TEXT,\n'
                         '    event_id TEXT,\n'
                         '    token_id TEXT NOT NULL,\n'
                         '    outcome_index INTEGER,\n'
                         '    outcome_label TEXT,\n'
                         "    snapshot_role TEXT NOT NULL CHECK (snapshot_role IN ('UNIVERSE', "
                         "'FOLLOWUP_ONLY')),\n"
                         '    request_started_at TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    source_timestamp TEXT,\n'
                         '    source_hash TEXT,\n'
                         '    raw_book_sha256 TEXT NOT NULL,\n'
                         '    bid_level_count INTEGER NOT NULL,\n'
                         '    ask_level_count INTEGER NOT NULL,\n'
                         '    best_bid REAL,\n'
                         '    best_ask REAL,\n'
                         '    spread REAL,\n'
                         '    tick_size REAL,\n'
                         '    min_order_size REAL,\n'
                         '    best_bid_notional REAL,\n'
                         '    best_ask_notional REAL,\n'
                         '    one_tick_spread INTEGER NOT NULL,\n'
                         '    near_bid_notional REAL,\n'
                         '    near_ask_notional REAL,\n'
                         '    weighted_imbalance REAL,\n'
                         '    pair_score REAL,\n'
                         '    entry_notional_usdc REAL,\n'
                         '    entry_vwap REAL,\n'
                         '    entry_shares REAL,\n'
                         '    entry_complete INTEGER NOT NULL,\n'
                         '    quote_eligible INTEGER NOT NULL,\n'
                         '    candidate_up INTEGER NOT NULL,\n'
                         "    CHECK (snapshot_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
                         '    UNIQUE (sweep_id, token_id, snapshot_role)\n'
                         ')',
                         ('condition_id',
                          'market_id',
                          'event_id',
                          'token_id',
                          'outcome_index',
                          'outcome_label',
                          'source_timestamp',
                          'source_hash',
                          'bid_level_count',
                          'ask_level_count',
                          'best_bid',
                          'best_ask',
                          'spread',
                          'tick_size',
                          'min_order_size',
                          'best_bid_notional',
                          'best_ask_notional'),
                         ('condition_id', 'token_id'),
                         'CREATE TABLE orderbook_snapshots (\n'
                         '    snapshot_id TEXT PRIMARY KEY,\n'
                         '    sweep_id TEXT REFERENCES market_sweeps(sweep_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    config_hash TEXT NOT NULL,\n'
                         '    strategy_source_digest TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    token_id TEXT NOT NULL,\n'
                         "    snapshot_role TEXT NOT NULL CHECK (snapshot_role IN ('UNIVERSE', "
                         "'FOLLOWUP_ONLY')),\n"
                         '    request_started_at TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    raw_book_sha256 TEXT NOT NULL,\n'
                         '    one_tick_spread INTEGER NOT NULL,\n'
                         '    near_bid_notional REAL,\n'
                         '    near_ask_notional REAL,\n'
                         '    weighted_imbalance REAL,\n'
                         '    pair_score REAL,\n'
                         '    entry_notional_usdc REAL,\n'
                         '    entry_vwap REAL,\n'
                         '    entry_shares REAL,\n'
                         '    entry_complete INTEGER NOT NULL,\n'
                         '    quote_eligible INTEGER NOT NULL,\n'
                         '    candidate_up INTEGER NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         "    CHECK (snapshot_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),\n"
                         '    UNIQUE (sweep_id, token_id, snapshot_role)\n'
                         ')'),
 'signal_decisions': ('raspberry-signal-decisions-v3',
                      'CREATE TABLE signal_decisions (\n'
                      '    decision_id TEXT PRIMARY KEY,\n'
                      '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                      '    run_id TEXT NOT NULL,\n'
                      '    config_hash TEXT NOT NULL,\n'
                      '    strategy_source_digest TEXT NOT NULL,\n'
                      '    condition_id TEXT NOT NULL,\n'
                      '    market_id TEXT,\n'
                      '    event_id TEXT,\n'
                      '    arm TEXT NOT NULL,\n'
                      '    confirmation_steps INTEGER NOT NULL,\n'
                      '    evaluated_at TEXT NOT NULL,\n'
                      '    pair_snapshot_ids_json TEXT NOT NULL,\n'
                      '    pair_score REAL,\n'
                      '    pair_received_skew_seconds REAL,\n'
                      '    neutral_candidate INTEGER NOT NULL,\n'
                      '    selected_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                      '    selected_token_id TEXT,\n'
                      '    selected_outcome_label TEXT,\n'
                      '    history_snapshot_ids_json TEXT NOT NULL,\n'
                      '    history_timestamps_json TEXT NOT NULL,\n'
                      '    history_scores_json TEXT NOT NULL,\n'
                      '    history_gaps_minutes_json TEXT NOT NULL,\n'
                      '    one_sided_candidate INTEGER NOT NULL,\n'
                      '    persistence_passed INTEGER NOT NULL,\n'
                      '    cooldown_allowed INTEGER NOT NULL,\n'
                      '    experiment_window_eligible INTEGER NOT NULL,\n'
                      '    qualified INTEGER NOT NULL,\n'
                      '    rejection_reason TEXT NOT NULL,\n'
                      '    target_at TEXT,\n'
                      '    window_end TEXT,\n'
                      '    prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                      '    prior_15m_move REAL,\n'
                      '    prior_move_bin TEXT NOT NULL CHECK (prior_move_bin IN '
                      "('DOWN','FLAT','UP','MISSING')),\n"
                      '    matched_control_snapshot_id TEXT REFERENCES '
                      'orderbook_snapshots(snapshot_id),\n'
                      '    matched_control_prior_price_snapshot_id TEXT REFERENCES '
                      'orderbook_snapshots(snapshot_id),\n'
                      '    matched_control_prior_15m_move REAL,\n'
                      '    matched_control_prior_move_bin TEXT CHECK (matched_control_prior_move_bin IN '
                      "('DOWN','FLAT','UP','MISSING')),\n"
                      '    control_match_distance REAL,\n'
                      '    UNIQUE (sweep_id, condition_id, arm)\n'
                      ')',
                      ('condition_id',
                       'market_id',
                       'event_id',
                       'selected_token_id',
                       'selected_outcome_label'),
                      ('condition_id',),
                      'CREATE TABLE signal_decisions (\n'
                      '    decision_id TEXT PRIMARY KEY,\n'
                      '    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),\n'
                      '    run_id TEXT NOT NULL,\n'
                      '    config_hash TEXT NOT NULL,\n'
                      '    strategy_source_digest TEXT NOT NULL,\n'
                      '    condition_id TEXT NOT NULL,\n'
                      '    arm TEXT NOT NULL,\n'
                      '    confirmation_steps INTEGER NOT NULL,\n'
                      '    evaluated_at TEXT NOT NULL,\n'
                      '    pair_snapshot_ids_json TEXT NOT NULL,\n'
                      '    pair_score REAL,\n'
                      '    pair_received_skew_seconds REAL,\n'
                      '    neutral_candidate INTEGER NOT NULL,\n'
                      '    selected_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                      '    history_snapshot_ids_json TEXT NOT NULL,\n'
                      '    history_timestamps_json TEXT NOT NULL,\n'
                      '    history_scores_json TEXT NOT NULL,\n'
                      '    history_gaps_minutes_json TEXT NOT NULL,\n'
                      '    one_sided_candidate INTEGER NOT NULL,\n'
                      '    persistence_passed INTEGER NOT NULL,\n'
                      '    cooldown_allowed INTEGER NOT NULL,\n'
                      '    experiment_window_eligible INTEGER NOT NULL,\n'
                      '    qualified INTEGER NOT NULL,\n'
                      '    rejection_reason TEXT NOT NULL,\n'
                      '    target_at TEXT,\n'
                      '    window_end TEXT,\n'
                      '    prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                      '    prior_15m_move REAL,\n'
                      '    prior_move_bin TEXT NOT NULL CHECK (prior_move_bin IN '
                      "('DOWN','FLAT','UP','MISSING')),\n"
                      '    matched_control_snapshot_id TEXT REFERENCES '
                      'orderbook_snapshots(snapshot_id),\n'
                      '    matched_control_prior_price_snapshot_id TEXT REFERENCES '
                      'orderbook_snapshots(snapshot_id),\n'
                      '    matched_control_prior_15m_move REAL,\n'
                      '    matched_control_prior_move_bin TEXT CHECK (matched_control_prior_move_bin IN '
                      "('DOWN','FLAT','UP','MISSING')),\n"
                      '    control_match_distance REAL,\n'
                      '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                      '    UNIQUE (sweep_id, condition_id, arm)\n'
                      ')'),
 'research_cases': ('raspberry-research-cases-v3',
                    'CREATE TABLE research_cases (\n'
                    '    case_id TEXT PRIMARY KEY,\n'
                    '    decision_id TEXT NOT NULL REFERENCES signal_decisions(decision_id),\n'
                    "    case_kind TEXT NOT NULL CHECK (case_kind IN ('SIGNAL', 'CONTROL', "
                    "'OPPOSITE')),\n"
                    '    matched_pair_id TEXT NOT NULL,\n'
                    '    condition_id TEXT NOT NULL,\n'
                    '    event_id TEXT,\n'
                    '    token_id TEXT NOT NULL,\n'
                    '    outcome_label TEXT,\n'
                    '    entry_snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
                    '    entry_at TEXT NOT NULL,\n'
                    '    entry_cost_usdc REAL NOT NULL,\n'
                    '    entry_shares REAL NOT NULL,\n'
                    '    entry_vwap REAL NOT NULL,\n'
                    '    target_at TEXT NOT NULL,\n'
                    '    window_end TEXT NOT NULL,\n'
                    '    control_match_distance REAL,\n'
                    '    UNIQUE (decision_id, case_kind)\n'
                    ')',
                    ('condition_id', 'event_id', 'token_id', 'outcome_label'),
                    (),
                    'CREATE TABLE research_cases (\n'
                    '    case_id TEXT PRIMARY KEY,\n'
                    '    decision_id TEXT NOT NULL REFERENCES signal_decisions(decision_id),\n'
                    "    case_kind TEXT NOT NULL CHECK (case_kind IN ('SIGNAL', 'CONTROL', "
                    "'OPPOSITE')),\n"
                    '    matched_pair_id TEXT NOT NULL,\n'
                    '    entry_snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),\n'
                    '    entry_at TEXT NOT NULL,\n'
                    '    entry_cost_usdc REAL NOT NULL,\n'
                    '    entry_shares REAL NOT NULL,\n'
                    '    entry_vwap REAL NOT NULL,\n'
                    '    target_at TEXT NOT NULL,\n'
                    '    window_end TEXT NOT NULL,\n'
                    '    control_match_distance REAL,\n'
                    '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                    '    UNIQUE (decision_id, case_kind)\n'
                    ')'),
 'followup_request_starts': ('raspberry-followup-request-starts-v3',
                             'CREATE TABLE followup_request_starts (\n'
                             '    claim_id TEXT PRIMARY KEY REFERENCES followup_claims(claim_id),\n'
                             '    lease_id TEXT NOT NULL REFERENCES followup_claim_leases(lease_id),\n'
                             '    observing_run_id TEXT NOT NULL,\n'
                             '    logical_request_id TEXT NOT NULL,\n'
                             '    request_started_at TEXT NOT NULL,\n'
                             '    token_id TEXT NOT NULL\n'
                             ')',
                             ('token_id',),
                             (),
                             'CREATE TABLE followup_request_starts (\n'
                             '    claim_id TEXT PRIMARY KEY REFERENCES followup_claims(claim_id),\n'
                             '    lease_id TEXT NOT NULL REFERENCES followup_claim_leases(lease_id),\n'
                             '    observing_run_id TEXT NOT NULL,\n'
                             '    logical_request_id TEXT NOT NULL,\n'
                             '    request_started_at TEXT NOT NULL,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                             ')'),
 'followup_attempts': ('raspberry-followup-attempts-v3',
                       'CREATE TABLE followup_attempts (\n'
                       '    followup_id TEXT PRIMARY KEY,\n'
                       '    case_id TEXT NOT NULL REFERENCES research_cases(case_id),\n'
                       '    claim_id TEXT NOT NULL UNIQUE REFERENCES followup_claims(claim_id),\n'
                       '    observing_run_id TEXT NOT NULL,\n'
                       '    attempted_at TEXT NOT NULL,\n'
                       '    status TEXT NOT NULL CHECK (status IN (\n'
                       "        'QUOTE_COMPLETE', 'SOURCE_MISSING', 'EMPTY_BOOK', 'INVALID_QUOTE',\n"
                       "        'WINDOW_EXPIRED', 'STALE_REQUEST_UNKNOWN'\n"
                       '    )),\n'
                       '    source_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                       '    observed_at TEXT,\n'
                       '    exit_bid REAL,\n'
                       '    exit_vwap REAL,\n'
                       '    exit_proceeds_usdc REAL,\n'
                       '    executable_return_bps REAL,\n'
                       '    base_stressed_return_bps REAL,\n'
                       '    severe_stressed_return_bps REAL,\n'
                       '    details_json TEXT NOT NULL\n'
                       ')',
                       ('exit_bid',),
                       (),
                       'CREATE TABLE followup_attempts (\n'
                       '    followup_id TEXT PRIMARY KEY,\n'
                       '    case_id TEXT NOT NULL REFERENCES research_cases(case_id),\n'
                       '    claim_id TEXT NOT NULL UNIQUE REFERENCES followup_claims(claim_id),\n'
                       '    observing_run_id TEXT NOT NULL,\n'
                       '    attempted_at TEXT NOT NULL,\n'
                       '    status TEXT NOT NULL CHECK (status IN (\n'
                       "        'QUOTE_COMPLETE', 'SOURCE_MISSING', 'EMPTY_BOOK', 'INVALID_QUOTE',\n"
                       "        'WINDOW_EXPIRED', 'STALE_REQUEST_UNKNOWN'\n"
                       '    )),\n'
                       '    source_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),\n'
                       '    observed_at TEXT,\n'
                       '    exit_vwap REAL,\n'
                       '    exit_proceeds_usdc REAL,\n'
                       '    executable_return_bps REAL,\n'
                       '    base_stressed_return_bps REAL,\n'
                       '    severe_stressed_return_bps REAL,\n'
                       '    details_json TEXT NOT NULL,\n'
                       '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                       ')')}

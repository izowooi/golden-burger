"""Reviewed historical Coconut physical v6 schema; never the White recorder.

Authority: migration0006 plus original append-only guards. Public source mapping
preserves generated MISSING event IDs locally and original CHECKs on full rows.
"""

COCONUT_LOGICAL_SCHEMA_SHA256 = '987533ad780c48d5ec3e293e19f0083c221a9ebe7471f88d094d5a6cf8d11c67'

COCONUT_SCHEMA_OBJECTS = (('index',
  'event_family_cluster_time_idx',
  'event_observations',
  'CREATE INDEX event_family_cluster_time_idx\n'
  'ON event_observations(sport_family, event_cluster_id, observed_at)'),
 ('index',
  'game_lifecycle_latest_idx',
  'game_lifecycle_observations',
  'CREATE INDEX game_lifecycle_latest_idx\nON game_lifecycle_observations(event_cluster_id, observed_at)'),
 ('index',
  'research_run_event_run_time_idx',
  'research_run_events',
  'CREATE INDEX research_run_event_run_time_idx\nON research_run_events(run_id, observed_at)'),
 ('table',
  'api_requests',
  'api_requests',
  'CREATE TABLE api_requests (\n'
  '    api_attempt_id TEXT PRIMARY KEY,\n'
  '    logical_request_id TEXT NOT NULL,\n'
  '    run_id TEXT NOT NULL,\n'
  '    request_kind TEXT NOT NULL,\n'
  '    sport_family TEXT,\n'
  '    page_number INTEGER,\n'
  '    attempt_number INTEGER NOT NULL,\n'
  "    method TEXT NOT NULL CHECK (method IN ('GET','POST','WSS')),\n"
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
  '    error_message TEXT,\n'
  '    UNIQUE (logical_request_id, attempt_number)\n'
  ')'),
 ('table',
  'book_ladder_observations',
  'book_ladder_observations',
  'CREATE TABLE book_ladder_observations (\n'
  '    ladder_observation_id TEXT PRIMARY KEY,\n'
  '    book_snapshot_id TEXT NOT NULL REFERENCES book_snapshots(book_snapshot_id),\n'
  '    cycle_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  "    ask_status TEXT NOT NULL CHECK (ask_status IN ('FULL','PARTIAL','EMPTY')),\n"
  '    ask_filled_usdc REAL NOT NULL,\n'
  '    ask_remaining_usdc REAL NOT NULL,\n'
  '    ask_shares REAL NOT NULL,\n'
  '    ask_vwap REAL,\n'
  '    ask_worst_price REAL,\n'
  '    ask_levels_used INTEGER NOT NULL,\n'
  '    immediate_bid_status TEXT NOT NULL CHECK (immediate_bid_status IN '
  "('FULL','PARTIAL','EMPTY','NOT_APPLICABLE')),\n"
  '    immediate_bid_filled_shares REAL NOT NULL,\n'
  '    immediate_bid_remaining_shares REAL NOT NULL,\n'
  '    immediate_bid_vwap REAL,\n'
  '    immediate_bid_worst_price REAL,\n'
  '    immediate_bid_levels_used INTEGER NOT NULL,\n'
  '    UNIQUE (book_snapshot_id, notional_usdc)\n'
  ')'),
 ('table',
  'book_snapshots',
  'book_snapshots',
  'CREATE TABLE book_snapshots (\n'
  '    book_snapshot_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    logical_request_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    source_timestamp TEXT,\n'
  '    canonical_sha256 TEXT NOT NULL,\n'
  '    canonical_bytes INTEGER NOT NULL,\n'
  '    gzip_bytes INTEGER NOT NULL,\n'
  '    book_gzip BLOB NOT NULL,\n'
  '    best_bid REAL,\n'
  '    best_ask REAL,\n'
  '    bid_level_count INTEGER NOT NULL,\n'
  '    ask_level_count INTEGER NOT NULL,\n'
  '    tick_size REAL,\n'
  '    min_size REAL,\n'
  '    fee_status TEXT NOT NULL,\n'
  '    public_fee_rate_bps REAL,\n'
  '    UNIQUE (cycle_id, token_id)\n'
  ')'),
 ('table',
  'book_token_attempts',
  'book_token_attempts',
  'CREATE TABLE book_token_attempts (\n'
  '    book_attempt_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    logical_request_id TEXT,\n'
  '    observed_at TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (cycle_id, token_id)\n'
  ')'),
 ('table',
  'collection_contracts',
  'collection_contracts',
  'CREATE TABLE collection_contracts (\n'
  '    singleton INTEGER PRIMARY KEY CHECK (singleton=1),\n'
  "    contract_name TEXT NOT NULL CHECK (contract_name='research-full-v1'),\n"
  '    database_utc_date TEXT NOT NULL\n'
  ')'),
 ('table',
  'collection_cycles',
  'collection_cycles',
  'CREATE TABLE collection_cycles (\n'
  '    cycle_id TEXT PRIMARY KEY,\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    slot_start_utc TEXT NOT NULL UNIQUE,\n'
  '    job_name TEXT NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode IN ('sim','shadow')),\n"
  '    started_at TEXT NOT NULL,\n'
  '    cooperative_deadline_at TEXT NOT NULL,\n'
  '    request_stop_at TEXT NOT NULL,\n'
  '    hard_deadline_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_seconds REAL NOT NULL,\n'
  '    receipt_skew_seconds REAL NOT NULL,\n'
  '    all_families_cursor_complete INTEGER NOT NULL CHECK (all_families_cursor_complete IN (0,1)),\n'
  '    followup_complete INTEGER NOT NULL CHECK (followup_complete IN (0,1)),\n'
  '    request_envelope_json TEXT NOT NULL,\n'
  '    summary_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'data_quality_issues',
  'data_quality_issues',
  'CREATE TABLE data_quality_issues (\n'
  '    data_quality_issue_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT,\n'
  '    run_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  "    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),\n"
  '    issue_type TEXT NOT NULL,\n'
  '    sport_family TEXT,\n'
  '    detail_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'database_checks',
  'database_checks',
  'CREATE TABLE database_checks (\n'
  '    database_check_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT,\n'
  '    run_id TEXT,\n'
  "    check_type TEXT NOT NULL CHECK (check_type IN ('QUICK_CHECK','SCHEMA_FINGERPRINT')),\n"
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    elapsed_ms REAL NOT NULL,\n'
  '    result TEXT NOT NULL,\n'
  '    database_bytes INTEGER NOT NULL\n'
  ')'),
 ('table',
  'episode_carryovers',
  'episode_carryovers',
  'CREATE TABLE episode_carryovers (\n'
  '    episode_carryover_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL UNIQUE,\n'
  '    origin_utc_date TEXT NOT NULL,\n'
  '    carried_from_utc_date TEXT NOT NULL,\n'
  '    created_run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    competition_code TEXT,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  '    threshold REAL NOT NULL,\n'
  '    crossed_at TEXT NOT NULL,\n'
  '    entry_ask_vwap REAL NOT NULL,\n'
  '    entry_shares REAL NOT NULL,\n'
  '    liquidity REAL,\n'
  '    volume_num REAL,\n'
  '    volume_24hr REAL,\n'
  '    carried_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'episode_path_observations',
  'episode_path_observations',
  'CREATE TABLE episode_path_observations (\n'
  '    episode_path_observation_id TEXT PRIMARY KEY,\n'
  '    episode_id TEXT NOT NULL,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  '    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  "    path_status TEXT NOT NULL CHECK (path_status IN ('FULL','PARTIAL','EMPTY','BOOK_UNAVAILABLE')),\n"
  '    best_bid REAL,\n'
  '    requested_shares REAL NOT NULL,\n'
  '    filled_shares REAL NOT NULL,\n'
  '    remaining_shares REAL NOT NULL,\n'
  '    executable_bid_vwap REAL,\n'
  '    worst_bid REAL,\n'
  '    levels_used INTEGER NOT NULL,\n'
  '    UNIQUE (episode_id, cycle_id)\n'
  ')'),
 ('table',
  'event_observations',
  'event_observations',
  'CREATE TABLE event_observations (\n'
  '    event_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    sweep_id TEXT REFERENCES sport_sweeps(sweep_id),\n'
  '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(raw_payload_id),\n'
  '    run_id TEXT NOT NULL,\n'
  "    source_kind TEXT NOT NULL CHECK (source_kind IN ('DISCOVERY','FOLLOWUP')),\n"
  '    sport_family TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    canonical_game_slug TEXT NOT NULL,\n'
  '    game_id_alias TEXT,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    title TEXT,\n'
  '    slug TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    competition_code TEXT,\n'
  '    competition_name TEXT,\n'
  '    season_phase TEXT NOT NULL CHECK (season_phase IN '
  "('PRESEASON','REGULAR','POSTSEASON','UNKNOWN','NOT_APPLICABLE')),\n"
  '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
  "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
  '    lifecycle_reason TEXT NOT NULL,\n'
  '    scheduled_start_field TEXT,\n'
  '    scheduled_start_raw TEXT,\n'
  '    scheduled_start_utc TEXT,\n'
  '    classification_status TEXT NOT NULL CHECK (classification_status IN '
  "('ACCEPTED','REJECTED','DRIFT')),\n"
  '    classification_reason TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    sports_registry_sha256 TEXT NOT NULL,\n'
  '    volume_num REAL,\n'
  '    volume_24hr REAL,\n'
  '    liquidity REAL,\n'
  '    liquidity_num REAL,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    live INTEGER,\n'
  '    ended INTEGER,\n'
  '    end_date TEXT,\n'
  '    raw_lifecycle_json TEXT NOT NULL,\n'
  '    sport_json TEXT NOT NULL,\n'
  '    classification_evidence_json TEXT NOT NULL,\n'
  '    normalized_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, sport_family, event_id)\n'
  ')'),
 ('table',
  'event_series_observations',
  'event_series_observations',
  'CREATE TABLE event_series_observations (\n'
  '    event_series_observation_id TEXT PRIMARY KEY,\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    series_index INTEGER NOT NULL,\n'
  '    series_id TEXT,\n'
  '    series_slug TEXT,\n'
  '    series_title TEXT,\n'
  '    series_json TEXT NOT NULL,\n'
  '    UNIQUE (event_observation_id, series_index)\n'
  ')'),
 ('table',
  'event_tag_observations',
  'event_tag_observations',
  'CREATE TABLE event_tag_observations (\n'
  '    event_tag_observation_id TEXT PRIMARY KEY,\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    tag_index INTEGER NOT NULL,\n'
  '    tag_id TEXT,\n'
  '    tag_slug TEXT,\n'
  '    tag_label TEXT,\n'
  '    tag_json TEXT NOT NULL,\n'
  '    UNIQUE (event_observation_id, tag_index)\n'
  ')'),
 ('table',
  'event_team_observations',
  'event_team_observations',
  'CREATE TABLE event_team_observations (\n'
  '    event_team_observation_id TEXT PRIMARY KEY,\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    team_index INTEGER NOT NULL,\n'
  '    team_id TEXT,\n'
  '    team_name TEXT,\n'
  '    team_alias TEXT,\n'
  '    team_abbreviation TEXT,\n'
  '    team_league TEXT,\n'
  '    team_json TEXT NOT NULL,\n'
  '    UNIQUE (event_observation_id, team_index)\n'
  ')'),
 ('table',
  'game_anchor_observations',
  'game_anchor_observations',
  'CREATE TABLE game_anchor_observations (\n'
  '    game_anchor_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    book_snapshot_id TEXT NOT NULL REFERENCES book_snapshots(book_snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    scheduled_start_field TEXT NOT NULL,\n'
  '    scheduled_start_raw TEXT NOT NULL,\n'
  '    scheduled_start_utc TEXT NOT NULL,\n'
  '    minutes_to_scheduled_start REAL NOT NULL,\n'
  "    anchor_role TEXT NOT NULL CHECK (anchor_role='PRESTART_CANDIDATE'),\n"
  '    UNIQUE (cycle_id, token_id)\n'
  ')'),
 ('table',
  'game_lifecycle_observations',
  'game_lifecycle_observations',
  'CREATE TABLE game_lifecycle_observations (\n'
  '    game_lifecycle_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    event_observation_id TEXT REFERENCES event_observations(event_observation_id),\n'
  '    sport_family TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    canonical_game_slug TEXT NOT NULL,\n'
  '    game_id_alias TEXT,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    source_kind TEXT NOT NULL CHECK (source_kind IN '
  "('GAMMA_DISCOVERY','GAMMA_FOLLOWUP','SPORTS_WSS','GAMMA_CLOCK_FALLBACK','CLOB_DERIVED')),\n"
  '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
  "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
  '    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0,1)),\n'
  '    phase_source TEXT NOT NULL,\n'
  '    scheduled_start_field TEXT,\n'
  '    scheduled_start_raw TEXT,\n'
  '    scheduled_start_utc TEXT,\n'
  '    logical_request_id TEXT,\n'
  '    raw_lifecycle_json TEXT NOT NULL,\n'
  '    evidence_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'market_observations',
  'market_observations',
  'CREATE TABLE market_observations (\n'
  '    market_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    event_observation_id TEXT NOT NULL REFERENCES event_observations(event_observation_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT,\n'
  '    market_id TEXT,\n'
  '    question TEXT,\n'
  '    group_item_title TEXT,\n'
  '    sports_market_type TEXT,\n'
  '    structure_kind TEXT NOT NULL,\n'
  '    result_kind TEXT,\n'
  '    neg_risk INTEGER,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    active INTEGER,\n'
  '    closed INTEGER,\n'
  '    accepting_source_activity INTEGER,\n'
  '    public_book_enabled INTEGER,\n'
  '    volume_num REAL,\n'
  '    volume_24hr REAL,\n'
  '    liquidity REAL,\n'
  '    liquidity_num REAL,\n'
  '    event_volume_num REAL,\n'
  '    event_volume_24hr REAL,\n'
  '    event_liquidity REAL,\n'
  '    event_liquidity_num REAL,\n'
  '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
  '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
  '    exclusion_reason TEXT NOT NULL,\n'
  '    labels_json TEXT NOT NULL,\n'
  '    token_ids_json TEXT NOT NULL,\n'
  '    probabilities_json TEXT NOT NULL,\n'
  '    classification_evidence_json TEXT NOT NULL,\n'
  '    normalized_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, sport_family, event_id, condition_id)\n'
  ')'),
 ('table',
  'outcome_observations',
  'outcome_observations',
  'CREATE TABLE outcome_observations (\n'
  '    outcome_observation_id TEXT PRIMARY KEY,\n'
  '    market_observation_id TEXT NOT NULL REFERENCES market_observations(market_observation_id),\n'
  '    cycle_id TEXT NOT NULL,\n'
  '    run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL CHECK (outcome_index IN (0,1)),\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    gamma_probability REAL,\n'
  '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
  '    threshold_eligible INTEGER NOT NULL CHECK (threshold_eligible IN (0,1)),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, token_id)\n'
  ')'),
 ('table',
  'raw_payloads',
  'raw_payloads',
  'CREATE TABLE raw_payloads (\n'
  '    raw_payload_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    payload_kind TEXT NOT NULL,\n'
  '    sport_family TEXT,\n'
  '    logical_request_id TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    sha256 TEXT NOT NULL,\n'
  '    raw_bytes INTEGER NOT NULL,\n'
  '    gzip_bytes INTEGER NOT NULL,\n'
  '    payload_gzip BLOB NOT NULL,\n'
  '    UNIQUE (cycle_id, payload_kind, logical_request_id, sha256)\n'
  ')'),
 ('table',
  'research_config_versions',
  'research_config_versions',
  'CREATE TABLE research_config_versions (\n'
  '    config_hash TEXT PRIMARY KEY,\n'
  '    strategy_source_digest TEXT NOT NULL,\n'
  '    preregistration_sha256 TEXT NOT NULL,\n'
  '    sports_registry_sha256 TEXT NOT NULL,\n'
  '    job_name TEXT NOT NULL,\n'
  "    mode TEXT NOT NULL CHECK (mode IN ('sim','shadow')),\n"
  "    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode='archive_only'),\n"
  '    config_json TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'research_run_events',
  'research_run_events',
  'CREATE TABLE research_run_events (\n'
  '    run_event_id TEXT PRIMARY KEY,\n'
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
  '    resolution_attempt_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    attempted_at TEXT NOT NULL,\n'
  '    status TEXT NOT NULL,\n'
  '    logical_request_id TEXT,\n'
  '    error_type TEXT,\n'
  '    error_message TEXT,\n'
  '    UNIQUE (cycle_id, condition_id)\n'
  ')'),
 ('table',
  'resolution_observations',
  'resolution_observations',
  'CREATE TABLE resolution_observations (\n'
  '    resolution_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    resolution_status TEXT NOT NULL CHECK (resolution_status IN '
  "('RESOLVED','VOID','TIE','OPEN','CLOSED_UNRESOLVED','MALFORMED')),\n"
  '    winner_indices_json TEXT NOT NULL,\n'
  '    logical_request_id TEXT,\n'
  '    raw_sha256 TEXT,\n'
  '    evidence_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, condition_id)\n'
  ')'),
 ('table',
  'schedule_revision_observations',
  'schedule_revision_observations',
  'CREATE TABLE schedule_revision_observations (\n'
  '    schedule_revision_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    prior_scheduled_start_field TEXT,\n'
  '    prior_scheduled_start_raw TEXT,\n'
  '    prior_scheduled_start_utc TEXT,\n'
  '    new_scheduled_start_field TEXT,\n'
  '    new_scheduled_start_raw TEXT,\n'
  '    new_scheduled_start_utc TEXT,\n'
  '    source_kind TEXT NOT NULL,\n'
  '    evidence_json TEXT NOT NULL\n'
  ')'),
 ('table',
  'schema_metadata',
  'schema_metadata',
  'CREATE TABLE schema_metadata (\n'
  '    singleton INTEGER PRIMARY KEY CHECK (singleton=1),\n'
  '    database_utc_date TEXT NOT NULL,\n'
  '    data_contract TEXT NOT NULL,\n'
  "    collection_contract TEXT NOT NULL CHECK (collection_contract='research-full-v1'),\n"
  '    schema_profile TEXT NOT NULL,\n'
  '    universe_profile TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    sports_registry_sha256 TEXT NOT NULL,\n'
  '    migration_sha256 TEXT NOT NULL,\n'
  '    schema_sha256 TEXT NOT NULL,\n'
  '    created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'slot_claims',
  'slot_claims',
  'CREATE TABLE slot_claims (\n'
  '    slot_claim_id TEXT PRIMARY KEY,\n'
  '    slot_start_utc TEXT NOT NULL UNIQUE,\n'
  '    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes=5),\n'
  '    run_id TEXT NOT NULL UNIQUE,\n'
  '    job_name TEXT NOT NULL,\n'
  '    claimed_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'sport_sweeps',
  'sport_sweeps',
  'CREATE TABLE sport_sweeps (\n'
  '    sweep_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  "    sport_family TEXT NOT NULL CHECK (sport_family IN ('soccer','mlb','nba','nfl','nhl')),\n"
  '    tag_id INTEGER NOT NULL,\n'
  '    started_at TEXT NOT NULL,\n'
  '    completed_at TEXT NOT NULL,\n'
  '    page_count INTEGER NOT NULL,\n'
  '    source_event_count INTEGER NOT NULL,\n'
  '    accepted_event_count INTEGER NOT NULL,\n'
  '    rejected_event_count INTEGER NOT NULL,\n'
  '    drift_event_count INTEGER NOT NULL,\n'
  '    cursor_complete INTEGER NOT NULL CHECK (cursor_complete IN (0,1)),\n'
  '    terminal_cursor TEXT,\n'
  '    start_time_min TEXT NOT NULL,\n'
  '    start_time_max TEXT NOT NULL,\n'
  '    request_envelope_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, sport_family)\n'
  ')'),
 ('table',
  'sports_clock_observations',
  'sports_clock_observations',
  'CREATE TABLE sports_clock_observations (\n'
  '    sports_clock_observation_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    canonical_game_slug TEXT NOT NULL,\n'
  '    game_id_alias TEXT,\n'
  '    observed_at TEXT NOT NULL,\n'
  "    source_kind TEXT NOT NULL CHECK (source_kind IN ('SPORTS_WSS','GAMMA_FALLBACK')),\n"
  '    matched_by TEXT NOT NULL CHECK (matched_by IN '
  "('CANONICAL_SLUG','GAME_ID_ALIAS','SAME_CYCLE_GAMMA')),\n"
  '    source_identity TEXT NOT NULL,\n'
  '    period_raw TEXT,\n'
  '    elapsed_raw TEXT,\n'
  '    score_raw TEXT,\n'
  '    live INTEGER,\n'
  '    ended INTEGER,\n'
  '    logical_request_id TEXT,\n'
  '    raw_sha256 TEXT NOT NULL,\n'
  '    clock_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, event_cluster_id, source_kind)\n'
  ')'),
 ('table',
  'sports_registry_versions',
  'sports_registry_versions',
  'CREATE TABLE sports_registry_versions (\n'
  '    sports_registry_sha256 TEXT PRIMARY KEY,\n'
  '    universe_profile TEXT NOT NULL,\n'
  '    classifier_version TEXT NOT NULL,\n'
  '    registry_json TEXT NOT NULL,\n'
  '    first_seen_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'storage_metrics',
  'storage_metrics',
  'CREATE TABLE storage_metrics (\n'
  '    storage_metric_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT,\n'
  '    run_id TEXT,\n'
  '    phase TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    database_bytes INTEGER NOT NULL,\n'
  '    wal_bytes INTEGER NOT NULL,\n'
  '    filesystem_total_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_bytes INTEGER NOT NULL,\n'
  '    filesystem_free_bytes INTEGER NOT NULL,\n'
  '    filesystem_used_ratio REAL NOT NULL,\n'
  "    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK','WARN','STOP'))\n"
  ')'),
 ('table',
  'threshold_episodes',
  'threshold_episodes',
  'CREATE TABLE threshold_episodes (\n'
  '    episode_id TEXT PRIMARY KEY,\n'
  '    threshold_vector_id TEXT NOT NULL REFERENCES threshold_vectors(threshold_vector_id),\n'
  '    origin_utc_date TEXT NOT NULL,\n'
  '    created_run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    competition_code TEXT,\n'
  '    event_id TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    outcome_index INTEGER NOT NULL,\n'
  '    outcome_label TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  '    threshold REAL NOT NULL,\n'
  '    crossed_at TEXT NOT NULL,\n'
  '    entry_ask_vwap REAL NOT NULL,\n'
  '    entry_shares REAL NOT NULL,\n'
  '    entry_book_snapshot_id TEXT NOT NULL REFERENCES book_snapshots(book_snapshot_id),\n'
  '    liquidity REAL,\n'
  '    volume_num REAL,\n'
  '    volume_24hr REAL,\n'
  '    UNIQUE (condition_id, token_id, notional_usdc, threshold)\n'
  ')'),
 ('table',
  'threshold_state_carryovers',
  'threshold_state_carryovers',
  'CREATE TABLE threshold_state_carryovers (\n'
  '    threshold_state_carryover_id TEXT PRIMARY KEY,\n'
  '    carried_from_utc_date TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    observed_at TEXT NOT NULL,\n'
  '    observation_status TEXT NOT NULL,\n'
  '    executable_ask_vwap REAL,\n'
  '    executable_ask_shares REAL,\n'
  '    prior_vector_sha256 TEXT NOT NULL,\n'
  '    carried_at TEXT NOT NULL,\n'
  '    UNIQUE (token_id, notional_usdc)\n'
  ')'),
 ('table',
  'threshold_vectors',
  'threshold_vectors',
  'CREATE TABLE threshold_vectors (\n'
  '    threshold_vector_id TEXT PRIMARY KEY,\n'
  '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
  '    run_id TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    season_phase TEXT NOT NULL,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    event_cluster_id TEXT NOT NULL,\n'
  '    condition_id TEXT NOT NULL,\n'
  '    token_id TEXT NOT NULL,\n'
  '    notional_usdc REAL NOT NULL,\n'
  '    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),\n'
  '    observed_at TEXT NOT NULL,\n'
  '    observation_status TEXT NOT NULL,\n'
  '    executable_ask_vwap REAL,\n'
  '    executable_ask_shares REAL,\n'
  '    prior_observed_at TEXT,\n'
  '    prior_observation_status TEXT,\n'
  '    prior_executable_ask_vwap REAL,\n'
  '    observation_gap_seconds REAL,\n'
  '    states_json TEXT NOT NULL,\n'
  '    upward_crossings_json TEXT NOT NULL,\n'
  '    left_censored_json TEXT NOT NULL,\n'
  '    gap_censored_json TEXT NOT NULL,\n'
  '    UNIQUE (cycle_id, token_id, notional_usdc)\n'
  ')'),
 ('table',
  'tracked_game_carryovers',
  'tracked_game_carryovers',
  'CREATE TABLE tracked_game_carryovers (\n'
  '    tracked_game_carryover_id TEXT PRIMARY KEY,\n'
  '    carried_from_utc_date TEXT NOT NULL,\n'
  '    sport_family TEXT NOT NULL,\n'
  '    event_id TEXT NOT NULL,\n'
  '    canonical_game_slug TEXT NOT NULL,\n'
  '    game_id_alias TEXT,\n'
  '    event_cluster_id TEXT NOT NULL UNIQUE,\n'
  '    lifecycle_state TEXT NOT NULL,\n'
  '    scheduled_start_field TEXT,\n'
  '    scheduled_start_raw TEXT,\n'
  '    scheduled_start_utc TEXT,\n'
  '    prior_lifecycle_sha256 TEXT NOT NULL,\n'
  '    carried_at TEXT NOT NULL\n'
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
  'book_ladder_observations_forbid_delete',
  'book_ladder_observations',
  'CREATE TRIGGER book_ladder_observations_forbid_delete BEFORE DELETE ON book_ladder_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'book_ladder_observations_forbid_update',
  'book_ladder_observations',
  'CREATE TRIGGER book_ladder_observations_forbid_update BEFORE UPDATE ON book_ladder_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'book_snapshots_forbid_delete',
  'book_snapshots',
  'CREATE TRIGGER book_snapshots_forbid_delete BEFORE DELETE ON book_snapshots BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'book_snapshots_forbid_update',
  'book_snapshots',
  'CREATE TRIGGER book_snapshots_forbid_update BEFORE UPDATE ON book_snapshots BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'book_token_attempts_forbid_delete',
  'book_token_attempts',
  'CREATE TRIGGER book_token_attempts_forbid_delete BEFORE DELETE ON book_token_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'book_token_attempts_forbid_update',
  'book_token_attempts',
  'CREATE TRIGGER book_token_attempts_forbid_update BEFORE UPDATE ON book_token_attempts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'collection_contracts_forbid_delete',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_forbid_delete BEFORE DELETE ON collection_contracts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'collection_contracts_forbid_update',
  'collection_contracts',
  'CREATE TRIGGER collection_contracts_forbid_update BEFORE UPDATE ON collection_contracts BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'collection_cycles_forbid_delete',
  'collection_cycles',
  'CREATE TRIGGER collection_cycles_forbid_delete BEFORE DELETE ON collection_cycles BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'collection_cycles_forbid_update',
  'collection_cycles',
  'CREATE TRIGGER collection_cycles_forbid_update BEFORE UPDATE ON collection_cycles BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
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
  'episode_carryovers_forbid_delete',
  'episode_carryovers',
  'CREATE TRIGGER episode_carryovers_forbid_delete BEFORE DELETE ON episode_carryovers BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'episode_carryovers_forbid_update',
  'episode_carryovers',
  'CREATE TRIGGER episode_carryovers_forbid_update BEFORE UPDATE ON episode_carryovers BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
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
  'event_series_observations_forbid_delete',
  'event_series_observations',
  'CREATE TRIGGER event_series_observations_forbid_delete BEFORE DELETE ON event_series_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_series_observations_forbid_update',
  'event_series_observations',
  'CREATE TRIGGER event_series_observations_forbid_update BEFORE UPDATE ON event_series_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_tag_observations_forbid_delete',
  'event_tag_observations',
  'CREATE TRIGGER event_tag_observations_forbid_delete BEFORE DELETE ON event_tag_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_tag_observations_forbid_update',
  'event_tag_observations',
  'CREATE TRIGGER event_tag_observations_forbid_update BEFORE UPDATE ON event_tag_observations BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_team_observations_forbid_delete',
  'event_team_observations',
  'CREATE TRIGGER event_team_observations_forbid_delete BEFORE DELETE ON event_team_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'event_team_observations_forbid_update',
  'event_team_observations',
  'CREATE TRIGGER event_team_observations_forbid_update BEFORE UPDATE ON event_team_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'game_anchor_observations_forbid_delete',
  'game_anchor_observations',
  'CREATE TRIGGER game_anchor_observations_forbid_delete BEFORE DELETE ON game_anchor_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'game_anchor_observations_forbid_update',
  'game_anchor_observations',
  'CREATE TRIGGER game_anchor_observations_forbid_update BEFORE UPDATE ON game_anchor_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'game_lifecycle_observations_forbid_delete',
  'game_lifecycle_observations',
  'CREATE TRIGGER game_lifecycle_observations_forbid_delete BEFORE DELETE ON game_lifecycle_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'game_lifecycle_observations_forbid_update',
  'game_lifecycle_observations',
  'CREATE TRIGGER game_lifecycle_observations_forbid_update BEFORE UPDATE ON game_lifecycle_observations '
  "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
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
  'schedule_revision_observations_forbid_delete',
  'schedule_revision_observations',
  'CREATE TRIGGER schedule_revision_observations_forbid_delete BEFORE DELETE ON '
  "schedule_revision_observations BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'schedule_revision_observations_forbid_update',
  'schedule_revision_observations',
  'CREATE TRIGGER schedule_revision_observations_forbid_update BEFORE UPDATE ON '
  "schedule_revision_observations BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"),
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
  'slot_claims_forbid_delete',
  'slot_claims',
  'CREATE TRIGGER slot_claims_forbid_delete BEFORE DELETE ON slot_claims BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'slot_claims_forbid_update',
  'slot_claims',
  'CREATE TRIGGER slot_claims_forbid_update BEFORE UPDATE ON slot_claims BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'sport_sweeps_forbid_delete',
  'sport_sweeps',
  'CREATE TRIGGER sport_sweeps_forbid_delete BEFORE DELETE ON sport_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'sport_sweeps_forbid_update',
  'sport_sweeps',
  'CREATE TRIGGER sport_sweeps_forbid_update BEFORE UPDATE ON sport_sweeps BEGIN SELECT RAISE(ABORT, '
  "'append-only evidence'); END"),
 ('trigger',
  'sports_clock_observations_forbid_delete',
  'sports_clock_observations',
  'CREATE TRIGGER sports_clock_observations_forbid_delete BEFORE DELETE ON sports_clock_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'sports_clock_observations_forbid_update',
  'sports_clock_observations',
  'CREATE TRIGGER sports_clock_observations_forbid_update BEFORE UPDATE ON sports_clock_observations BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'sports_registry_versions_forbid_delete',
  'sports_registry_versions',
  'CREATE TRIGGER sports_registry_versions_forbid_delete BEFORE DELETE ON sports_registry_versions BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'sports_registry_versions_forbid_update',
  'sports_registry_versions',
  'CREATE TRIGGER sports_registry_versions_forbid_update BEFORE UPDATE ON sports_registry_versions BEGIN '
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
  "'append-only evidence'); END"),
 ('trigger',
  'threshold_episodes_forbid_delete',
  'threshold_episodes',
  'CREATE TRIGGER threshold_episodes_forbid_delete BEFORE DELETE ON threshold_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'threshold_episodes_forbid_update',
  'threshold_episodes',
  'CREATE TRIGGER threshold_episodes_forbid_update BEFORE UPDATE ON threshold_episodes BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'threshold_state_carryovers_forbid_delete',
  'threshold_state_carryovers',
  'CREATE TRIGGER threshold_state_carryovers_forbid_delete BEFORE DELETE ON threshold_state_carryovers BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'threshold_state_carryovers_forbid_update',
  'threshold_state_carryovers',
  'CREATE TRIGGER threshold_state_carryovers_forbid_update BEFORE UPDATE ON threshold_state_carryovers BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'threshold_vectors_forbid_delete',
  'threshold_vectors',
  'CREATE TRIGGER threshold_vectors_forbid_delete BEFORE DELETE ON threshold_vectors BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'threshold_vectors_forbid_update',
  'threshold_vectors',
  'CREATE TRIGGER threshold_vectors_forbid_update BEFORE UPDATE ON threshold_vectors BEGIN SELECT '
  "RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'tracked_game_carryovers_forbid_delete',
  'tracked_game_carryovers',
  'CREATE TRIGGER tracked_game_carryovers_forbid_delete BEFORE DELETE ON tracked_game_carryovers BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"),
 ('trigger',
  'tracked_game_carryovers_forbid_update',
  'tracked_game_carryovers',
  'CREATE TRIGGER tracked_game_carryovers_forbid_update BEFORE UPDATE ON tracked_game_carryovers BEGIN '
  "SELECT RAISE(ABORT, 'append-only evidence'); END"))

COCONUT_RAW_TABLES = {'event_observations': ('coconut-event-source-v1',
                        'CREATE TABLE event_observations (\n'
                        '    event_observation_id TEXT PRIMARY KEY,\n'
                        '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                        '    sweep_id TEXT REFERENCES sport_sweeps(sweep_id),\n'
                        '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(raw_payload_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        "    source_kind TEXT NOT NULL CHECK (source_kind IN ('DISCOVERY','FOLLOWUP')),\n"
                        '    sport_family TEXT NOT NULL,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    canonical_game_slug TEXT NOT NULL,\n'
                        '    game_id_alias TEXT,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    title TEXT,\n'
                        '    slug TEXT,\n'
                        '    observed_at TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    competition_name TEXT,\n'
                        '    season_phase TEXT NOT NULL CHECK (season_phase IN '
                        "('PRESEASON','REGULAR','POSTSEASON','UNKNOWN','NOT_APPLICABLE')),\n"
                        '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
                        "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
                        '    lifecycle_reason TEXT NOT NULL,\n'
                        '    scheduled_start_field TEXT,\n'
                        '    scheduled_start_raw TEXT,\n'
                        '    scheduled_start_utc TEXT,\n'
                        '    classification_status TEXT NOT NULL CHECK (classification_status IN '
                        "('ACCEPTED','REJECTED','DRIFT')),\n"
                        '    classification_reason TEXT NOT NULL,\n'
                        '    classifier_version TEXT NOT NULL,\n'
                        '    sports_registry_sha256 TEXT NOT NULL,\n'
                        '    volume_num REAL,\n'
                        '    volume_24hr REAL,\n'
                        '    liquidity REAL,\n'
                        '    liquidity_num REAL,\n'
                        '    active INTEGER,\n'
                        '    closed INTEGER,\n'
                        '    live INTEGER,\n'
                        '    ended INTEGER,\n'
                        '    end_date TEXT,\n'
                        '    raw_lifecycle_json TEXT NOT NULL,\n'
                        '    sport_json TEXT NOT NULL,\n'
                        '    classification_evidence_json TEXT NOT NULL,\n'
                        '    normalized_json TEXT NOT NULL,\n'
                        '    UNIQUE (cycle_id, sport_family, event_id)\n'
                        ')',
                        ('game_id_alias',
                         'title',
                         'slug',
                         'volume_num',
                         'volume_24hr',
                         'liquidity',
                         'liquidity_num',
                         'active',
                         'closed',
                         'live',
                         'ended',
                         'end_date',
                         'scheduled_start_raw'),
                        (),
                        'CREATE TABLE event_observations (\n'
                        '    event_observation_id TEXT PRIMARY KEY,\n'
                        '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                        '    sweep_id TEXT REFERENCES sport_sweeps(sweep_id),\n'
                        '    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(raw_payload_id),\n'
                        '    run_id TEXT NOT NULL,\n'
                        "    source_kind TEXT NOT NULL CHECK (source_kind IN ('DISCOVERY','FOLLOWUP')),\n"
                        '    sport_family TEXT NOT NULL,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    canonical_game_slug TEXT NOT NULL,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    observed_at TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    competition_name TEXT,\n'
                        '    season_phase TEXT NOT NULL CHECK (season_phase IN '
                        "('PRESEASON','REGULAR','POSTSEASON','UNKNOWN','NOT_APPLICABLE')),\n"
                        '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
                        "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
                        '    lifecycle_reason TEXT NOT NULL,\n'
                        '    scheduled_start_field TEXT,\n'
                        '    scheduled_start_utc TEXT,\n'
                        '    classification_status TEXT NOT NULL CHECK (classification_status IN '
                        "('ACCEPTED','REJECTED','DRIFT')),\n"
                        '    classification_reason TEXT NOT NULL,\n'
                        '    classifier_version TEXT NOT NULL,\n'
                        '    sports_registry_sha256 TEXT NOT NULL,\n'
                        '    raw_lifecycle_json TEXT NOT NULL,\n'
                        '    sport_json TEXT NOT NULL,\n'
                        '    classification_evidence_json TEXT NOT NULL,\n'
                        '    normalized_json TEXT NOT NULL,\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                        '    UNIQUE (cycle_id, sport_family, event_id)\n'
                        ')'),
 'event_tag_observations': ('coconut-tag-source-v1',
                            'CREATE TABLE event_tag_observations (\n'
                            '    event_tag_observation_id TEXT PRIMARY KEY,\n'
                            '    event_observation_id TEXT NOT NULL REFERENCES '
                            'event_observations(event_observation_id),\n'
                            '    tag_index INTEGER NOT NULL,\n'
                            '    tag_id TEXT,\n'
                            '    tag_slug TEXT,\n'
                            '    tag_label TEXT,\n'
                            '    tag_json TEXT NOT NULL,\n'
                            '    UNIQUE (event_observation_id, tag_index)\n'
                            ')',
                            ('tag_id', 'tag_slug', 'tag_label'),
                            (),
                            'CREATE TABLE event_tag_observations (\n'
                            '    event_tag_observation_id TEXT PRIMARY KEY,\n'
                            '    event_observation_id TEXT NOT NULL REFERENCES '
                            'event_observations(event_observation_id),\n'
                            '    tag_index INTEGER NOT NULL,\n'
                            '    tag_json TEXT NOT NULL,\n'
                            '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                            '    UNIQUE (event_observation_id, tag_index)\n'
                            ')'),
 'event_series_observations': ('coconut-series-source-v1',
                               'CREATE TABLE event_series_observations (\n'
                               '    event_series_observation_id TEXT PRIMARY KEY,\n'
                               '    event_observation_id TEXT NOT NULL REFERENCES '
                               'event_observations(event_observation_id),\n'
                               '    series_index INTEGER NOT NULL,\n'
                               '    series_id TEXT,\n'
                               '    series_slug TEXT,\n'
                               '    series_title TEXT,\n'
                               '    series_json TEXT NOT NULL,\n'
                               '    UNIQUE (event_observation_id, series_index)\n'
                               ')',
                               ('series_id', 'series_slug', 'series_title'),
                               (),
                               'CREATE TABLE event_series_observations (\n'
                               '    event_series_observation_id TEXT PRIMARY KEY,\n'
                               '    event_observation_id TEXT NOT NULL REFERENCES '
                               'event_observations(event_observation_id),\n'
                               '    series_index INTEGER NOT NULL,\n'
                               '    series_json TEXT NOT NULL,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                               '    UNIQUE (event_observation_id, series_index)\n'
                               ')'),
 'event_team_observations': ('coconut-team-source-v1',
                             'CREATE TABLE event_team_observations (\n'
                             '    event_team_observation_id TEXT PRIMARY KEY,\n'
                             '    event_observation_id TEXT NOT NULL REFERENCES '
                             'event_observations(event_observation_id),\n'
                             '    team_index INTEGER NOT NULL,\n'
                             '    team_id TEXT,\n'
                             '    team_name TEXT,\n'
                             '    team_alias TEXT,\n'
                             '    team_abbreviation TEXT,\n'
                             '    team_league TEXT,\n'
                             '    team_json TEXT NOT NULL,\n'
                             '    UNIQUE (event_observation_id, team_index)\n'
                             ')',
                             ('team_id', 'team_name', 'team_alias', 'team_abbreviation', 'team_league'),
                             (),
                             'CREATE TABLE event_team_observations (\n'
                             '    event_team_observation_id TEXT PRIMARY KEY,\n'
                             '    event_observation_id TEXT NOT NULL REFERENCES '
                             'event_observations(event_observation_id),\n'
                             '    team_index INTEGER NOT NULL,\n'
                             '    team_json TEXT NOT NULL,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                             '    UNIQUE (event_observation_id, team_index)\n'
                             ')'),
 'market_observations': ('coconut-market-source-v1',
                         'CREATE TABLE market_observations (\n'
                         '    market_observation_id TEXT PRIMARY KEY,\n'
                         '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                         '    event_observation_id TEXT NOT NULL REFERENCES '
                         'event_observations(event_observation_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    sport_family TEXT NOT NULL,\n'
                         '    season_phase TEXT NOT NULL,\n'
                         '    lifecycle_state TEXT NOT NULL,\n'
                         '    event_id TEXT NOT NULL,\n'
                         '    event_cluster_id TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    market_id TEXT,\n'
                         '    question TEXT,\n'
                         '    group_item_title TEXT,\n'
                         '    sports_market_type TEXT,\n'
                         '    structure_kind TEXT NOT NULL,\n'
                         '    result_kind TEXT,\n'
                         '    neg_risk INTEGER,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    active INTEGER,\n'
                         '    closed INTEGER,\n'
                         '    accepting_source_activity INTEGER,\n'
                         '    public_book_enabled INTEGER,\n'
                         '    volume_num REAL,\n'
                         '    volume_24hr REAL,\n'
                         '    liquidity REAL,\n'
                         '    liquidity_num REAL,\n'
                         '    event_volume_num REAL,\n'
                         '    event_volume_24hr REAL,\n'
                         '    event_liquidity REAL,\n'
                         '    event_liquidity_num REAL,\n'
                         '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
                         '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
                         '    exclusion_reason TEXT NOT NULL,\n'
                         '    labels_json TEXT NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    probabilities_json TEXT NOT NULL,\n'
                         '    classification_evidence_json TEXT NOT NULL,\n'
                         '    normalized_json TEXT NOT NULL,\n'
                         '    UNIQUE (cycle_id, sport_family, event_id, condition_id)\n'
                         ')',
                         ('condition_id',
                          'market_id',
                          'question',
                          'group_item_title',
                          'sports_market_type',
                          'neg_risk',
                          'active',
                          'closed',
                          'accepting_source_activity',
                          'public_book_enabled',
                          'volume_num',
                          'volume_24hr',
                          'liquidity',
                          'liquidity_num',
                          'event_volume_num',
                          'event_volume_24hr',
                          'event_liquidity',
                          'event_liquidity_num'),
                         ('condition_id',),
                         'CREATE TABLE market_observations (\n'
                         '    market_observation_id TEXT PRIMARY KEY,\n'
                         '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                         '    event_observation_id TEXT NOT NULL REFERENCES '
                         'event_observations(event_observation_id),\n'
                         '    run_id TEXT NOT NULL,\n'
                         '    sport_family TEXT NOT NULL,\n'
                         '    season_phase TEXT NOT NULL,\n'
                         '    lifecycle_state TEXT NOT NULL,\n'
                         '    event_id TEXT NOT NULL,\n'
                         '    event_cluster_id TEXT NOT NULL,\n'
                         '    condition_id TEXT,\n'
                         '    structure_kind TEXT NOT NULL,\n'
                         '    result_kind TEXT,\n'
                         '    observed_at TEXT NOT NULL,\n'
                         '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
                         '    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),\n'
                         '    exclusion_reason TEXT NOT NULL,\n'
                         '    labels_json TEXT NOT NULL,\n'
                         '    token_ids_json TEXT NOT NULL,\n'
                         '    probabilities_json TEXT NOT NULL,\n'
                         '    classification_evidence_json TEXT NOT NULL,\n'
                         '    normalized_json TEXT NOT NULL,\n'
                         '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                         '    UNIQUE (cycle_id, sport_family, event_id, condition_id)\n'
                         ')'),
 'outcome_observations': ('coconut-outcome-source-v1',
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    market_observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(market_observation_id),\n'
                          '    cycle_id TEXT NOT NULL,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    sport_family TEXT NOT NULL,\n'
                          '    season_phase TEXT NOT NULL,\n'
                          '    lifecycle_state TEXT NOT NULL,\n'
                          '    event_cluster_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    outcome_index INTEGER NOT NULL CHECK (outcome_index IN (0,1)),\n'
                          '    outcome_label TEXT NOT NULL,\n'
                          '    gamma_probability REAL,\n'
                          '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
                          '    threshold_eligible INTEGER NOT NULL CHECK (threshold_eligible IN (0,1)),\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    UNIQUE (cycle_id, token_id)\n'
                          ')',
                          ('condition_id', 'token_id', 'outcome_index', 'outcome_label', 'gamma_probability'),
                          ('condition_id', 'token_id'),
                          'CREATE TABLE outcome_observations (\n'
                          '    outcome_observation_id TEXT PRIMARY KEY,\n'
                          '    market_observation_id TEXT NOT NULL REFERENCES '
                          'market_observations(market_observation_id),\n'
                          '    cycle_id TEXT NOT NULL,\n'
                          '    run_id TEXT NOT NULL,\n'
                          '    sport_family TEXT NOT NULL,\n'
                          '    season_phase TEXT NOT NULL,\n'
                          '    lifecycle_state TEXT NOT NULL,\n'
                          '    event_cluster_id TEXT NOT NULL,\n'
                          '    condition_id TEXT NOT NULL,\n'
                          '    token_id TEXT NOT NULL,\n'
                          '    structure_eligible INTEGER NOT NULL CHECK (structure_eligible IN (0,1)),\n'
                          '    threshold_eligible INTEGER NOT NULL CHECK (threshold_eligible IN (0,1)),\n'
                          '    observed_at TEXT NOT NULL,\n'
                          '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                          '    UNIQUE (cycle_id, token_id)\n'
                          ')'),
 'book_snapshots': ('coconut-book-source-v1',
                    'CREATE TABLE book_snapshots (\n'
                    '    book_snapshot_id TEXT PRIMARY KEY,\n'
                    '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                    '    run_id TEXT NOT NULL,\n'
                    '    token_id TEXT NOT NULL,\n'
                    '    logical_request_id TEXT NOT NULL,\n'
                    '    observed_at TEXT NOT NULL,\n'
                    '    source_timestamp TEXT,\n'
                    '    canonical_sha256 TEXT NOT NULL,\n'
                    '    canonical_bytes INTEGER NOT NULL,\n'
                    '    gzip_bytes INTEGER NOT NULL,\n'
                    '    book_gzip BLOB NOT NULL,\n'
                    '    best_bid REAL,\n'
                    '    best_ask REAL,\n'
                    '    bid_level_count INTEGER NOT NULL,\n'
                    '    ask_level_count INTEGER NOT NULL,\n'
                    '    tick_size REAL,\n'
                    '    min_size REAL,\n'
                    '    fee_status TEXT NOT NULL,\n'
                    '    public_fee_rate_bps REAL,\n'
                    '    UNIQUE (cycle_id, token_id)\n'
                    ')',
                    ('token_id',
                     'source_timestamp',
                     'best_bid',
                     'best_ask',
                     'bid_level_count',
                     'ask_level_count',
                     'tick_size',
                     'min_size',
                     'public_fee_rate_bps'),
                    ('token_id',),
                    'CREATE TABLE book_snapshots (\n'
                    '    book_snapshot_id TEXT PRIMARY KEY,\n'
                    '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                    '    run_id TEXT NOT NULL,\n'
                    '    token_id TEXT NOT NULL,\n'
                    '    logical_request_id TEXT NOT NULL,\n'
                    '    observed_at TEXT NOT NULL,\n'
                    '    canonical_sha256 TEXT NOT NULL,\n'
                    '    canonical_bytes INTEGER NOT NULL,\n'
                    '    gzip_bytes INTEGER NOT NULL,\n'
                    '    book_gzip BLOB NOT NULL,\n'
                    '    fee_status TEXT NOT NULL,\n'
                    '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                    '    UNIQUE (cycle_id, token_id)\n'
                    ')'),
 'sports_clock_observations': ('coconut-clock-source-v1',
                               'CREATE TABLE sports_clock_observations (\n'
                               '    sports_clock_observation_id TEXT PRIMARY KEY,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    event_cluster_id TEXT NOT NULL,\n'
                               '    canonical_game_slug TEXT NOT NULL,\n'
                               '    game_id_alias TEXT,\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    source_kind TEXT NOT NULL CHECK (source_kind IN '
                               "('SPORTS_WSS','GAMMA_FALLBACK')),\n"
                               '    matched_by TEXT NOT NULL CHECK (matched_by IN '
                               "('CANONICAL_SLUG','GAME_ID_ALIAS','SAME_CYCLE_GAMMA')),\n"
                               '    source_identity TEXT NOT NULL,\n'
                               '    period_raw TEXT,\n'
                               '    elapsed_raw TEXT,\n'
                               '    score_raw TEXT,\n'
                               '    live INTEGER,\n'
                               '    ended INTEGER,\n'
                               '    logical_request_id TEXT,\n'
                               '    raw_sha256 TEXT NOT NULL,\n'
                               '    clock_json TEXT NOT NULL,\n'
                               '    UNIQUE (cycle_id, event_cluster_id, source_kind)\n'
                               ')',
                               ('period_raw', 'elapsed_raw', 'score_raw', 'live', 'ended'),
                               (),
                               'CREATE TABLE sports_clock_observations (\n'
                               '    sports_clock_observation_id TEXT PRIMARY KEY,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    event_cluster_id TEXT NOT NULL,\n'
                               '    canonical_game_slug TEXT NOT NULL,\n'
                               '    game_id_alias TEXT,\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    source_kind TEXT NOT NULL CHECK (source_kind IN '
                               "('SPORTS_WSS','GAMMA_FALLBACK')),\n"
                               '    matched_by TEXT NOT NULL CHECK (matched_by IN '
                               "('CANONICAL_SLUG','GAME_ID_ALIAS','SAME_CYCLE_GAMMA')),\n"
                               '    source_identity TEXT NOT NULL,\n'
                               '    logical_request_id TEXT,\n'
                               '    raw_sha256 TEXT NOT NULL,\n'
                               '    clock_json TEXT NOT NULL,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                               '    UNIQUE (cycle_id, event_cluster_id, source_kind)\n'
                               ')'),
 'game_lifecycle_observations': ('coconut-lifecycle-source-v1',
                                 'CREATE TABLE game_lifecycle_observations (\n'
                                 '    game_lifecycle_observation_id TEXT PRIMARY KEY,\n'
                                 '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                                 '    run_id TEXT NOT NULL,\n'
                                 '    event_observation_id TEXT REFERENCES '
                                 'event_observations(event_observation_id),\n'
                                 '    sport_family TEXT NOT NULL,\n'
                                 '    event_id TEXT NOT NULL,\n'
                                 '    canonical_game_slug TEXT NOT NULL,\n'
                                 '    game_id_alias TEXT,\n'
                                 '    event_cluster_id TEXT NOT NULL,\n'
                                 '    observed_at TEXT NOT NULL,\n'
                                 '    source_kind TEXT NOT NULL CHECK (source_kind IN '
                                 "('GAMMA_DISCOVERY','GAMMA_FOLLOWUP','SPORTS_WSS','GAMMA_CLOCK_FALLBACK','CLOB_DERIVED')),\n"
                                 '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
                                 "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
                                 '    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0,1)),\n'
                                 '    phase_source TEXT NOT NULL,\n'
                                 '    scheduled_start_field TEXT,\n'
                                 '    scheduled_start_raw TEXT,\n'
                                 '    scheduled_start_utc TEXT,\n'
                                 '    logical_request_id TEXT,\n'
                                 '    raw_lifecycle_json TEXT NOT NULL,\n'
                                 '    evidence_json TEXT NOT NULL\n'
                                 ')',
                                 ('game_id_alias', 'scheduled_start_raw'),
                                 (),
                                 'CREATE TABLE game_lifecycle_observations (\n'
                                 '    game_lifecycle_observation_id TEXT PRIMARY KEY,\n'
                                 '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                                 '    run_id TEXT NOT NULL,\n'
                                 '    event_observation_id TEXT REFERENCES '
                                 'event_observations(event_observation_id),\n'
                                 '    sport_family TEXT NOT NULL,\n'
                                 '    event_id TEXT NOT NULL,\n'
                                 '    canonical_game_slug TEXT NOT NULL,\n'
                                 '    event_cluster_id TEXT NOT NULL,\n'
                                 '    observed_at TEXT NOT NULL,\n'
                                 '    source_kind TEXT NOT NULL CHECK (source_kind IN '
                                 "('GAMMA_DISCOVERY','GAMMA_FOLLOWUP','SPORTS_WSS','GAMMA_CLOCK_FALLBACK','CLOB_DERIVED')),\n"
                                 '    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN '
                                 "('DISCOVERED_OPEN','PREGAME','IN_PLAY','ENDED','POSTPONED','CANCELLED','RESOLVED','VOID','TIE')),\n"
                                 '    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0,1)),\n'
                                 '    phase_source TEXT NOT NULL,\n'
                                 '    scheduled_start_field TEXT,\n'
                                 '    scheduled_start_utc TEXT,\n'
                                 '    logical_request_id TEXT,\n'
                                 '    raw_lifecycle_json TEXT NOT NULL,\n'
                                 '    evidence_json TEXT NOT NULL,\n'
                                 '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                                 ')'),
 'tracked_game_carryovers': ('coconut-lifecycle-source-v1',
                             'CREATE TABLE tracked_game_carryovers (\n'
                             '    tracked_game_carryover_id TEXT PRIMARY KEY,\n'
                             '    carried_from_utc_date TEXT NOT NULL,\n'
                             '    sport_family TEXT NOT NULL,\n'
                             '    event_id TEXT NOT NULL,\n'
                             '    canonical_game_slug TEXT NOT NULL,\n'
                             '    game_id_alias TEXT,\n'
                             '    event_cluster_id TEXT NOT NULL UNIQUE,\n'
                             '    lifecycle_state TEXT NOT NULL,\n'
                             '    scheduled_start_field TEXT,\n'
                             '    scheduled_start_raw TEXT,\n'
                             '    scheduled_start_utc TEXT,\n'
                             '    prior_lifecycle_sha256 TEXT NOT NULL,\n'
                             '    carried_at TEXT NOT NULL\n'
                             ')',
                             ('game_id_alias', 'scheduled_start_raw'),
                             (),
                             'CREATE TABLE tracked_game_carryovers (\n'
                             '    tracked_game_carryover_id TEXT PRIMARY KEY,\n'
                             '    carried_from_utc_date TEXT NOT NULL,\n'
                             '    sport_family TEXT NOT NULL,\n'
                             '    event_id TEXT NOT NULL,\n'
                             '    canonical_game_slug TEXT NOT NULL,\n'
                             '    event_cluster_id TEXT NOT NULL UNIQUE,\n'
                             '    lifecycle_state TEXT NOT NULL,\n'
                             '    scheduled_start_field TEXT,\n'
                             '    scheduled_start_utc TEXT,\n'
                             '    prior_lifecycle_sha256 TEXT NOT NULL,\n'
                             '    carried_at TEXT NOT NULL,\n'
                             '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                             ')'),
 'schedule_revision_observations': ('coconut-schedule-revision-source-v1',
                                    'CREATE TABLE schedule_revision_observations (\n'
                                    '    schedule_revision_observation_id TEXT PRIMARY KEY,\n'
                                    '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                                    '    run_id TEXT NOT NULL,\n'
                                    '    sport_family TEXT NOT NULL,\n'
                                    '    event_id TEXT NOT NULL,\n'
                                    '    event_cluster_id TEXT NOT NULL,\n'
                                    '    observed_at TEXT NOT NULL,\n'
                                    '    prior_scheduled_start_field TEXT,\n'
                                    '    prior_scheduled_start_raw TEXT,\n'
                                    '    prior_scheduled_start_utc TEXT,\n'
                                    '    new_scheduled_start_field TEXT,\n'
                                    '    new_scheduled_start_raw TEXT,\n'
                                    '    new_scheduled_start_utc TEXT,\n'
                                    '    source_kind TEXT NOT NULL,\n'
                                    '    evidence_json TEXT NOT NULL\n'
                                    ')',
                                    ('prior_scheduled_start_raw', 'new_scheduled_start_raw'),
                                    (),
                                    'CREATE TABLE schedule_revision_observations (\n'
                                    '    schedule_revision_observation_id TEXT PRIMARY KEY,\n'
                                    '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                                    '    run_id TEXT NOT NULL,\n'
                                    '    sport_family TEXT NOT NULL,\n'
                                    '    event_id TEXT NOT NULL,\n'
                                    '    event_cluster_id TEXT NOT NULL,\n'
                                    '    observed_at TEXT NOT NULL,\n'
                                    '    prior_scheduled_start_field TEXT,\n'
                                    '    prior_scheduled_start_utc TEXT,\n'
                                    '    new_scheduled_start_field TEXT,\n'
                                    '    new_scheduled_start_utc TEXT,\n'
                                    '    source_kind TEXT NOT NULL,\n'
                                    '    evidence_json TEXT NOT NULL,\n'
                                    '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                                    ')'),
 'threshold_episodes': ('coconut-episode-source-v1',
                        'CREATE TABLE threshold_episodes (\n'
                        '    episode_id TEXT PRIMARY KEY,\n'
                        '    threshold_vector_id TEXT NOT NULL REFERENCES '
                        'threshold_vectors(threshold_vector_id),\n'
                        '    origin_utc_date TEXT NOT NULL,\n'
                        '    created_run_id TEXT NOT NULL,\n'
                        '    sport_family TEXT NOT NULL,\n'
                        '    season_phase TEXT NOT NULL,\n'
                        '    lifecycle_state TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    condition_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    outcome_index INTEGER NOT NULL,\n'
                        '    outcome_label TEXT NOT NULL,\n'
                        '    notional_usdc REAL NOT NULL,\n'
                        '    threshold REAL NOT NULL,\n'
                        '    crossed_at TEXT NOT NULL,\n'
                        '    entry_ask_vwap REAL NOT NULL,\n'
                        '    entry_shares REAL NOT NULL,\n'
                        '    entry_book_snapshot_id TEXT NOT NULL REFERENCES '
                        'book_snapshots(book_snapshot_id),\n'
                        '    liquidity REAL,\n'
                        '    volume_num REAL,\n'
                        '    volume_24hr REAL,\n'
                        '    UNIQUE (condition_id, token_id, notional_usdc, threshold)\n'
                        ')',
                        ('condition_id',
                         'token_id',
                         'outcome_index',
                         'outcome_label',
                         'liquidity',
                         'volume_num',
                         'volume_24hr'),
                        ('condition_id', 'token_id'),
                        'CREATE TABLE threshold_episodes (\n'
                        '    episode_id TEXT PRIMARY KEY,\n'
                        '    threshold_vector_id TEXT NOT NULL REFERENCES '
                        'threshold_vectors(threshold_vector_id),\n'
                        '    origin_utc_date TEXT NOT NULL,\n'
                        '    created_run_id TEXT NOT NULL,\n'
                        '    sport_family TEXT NOT NULL,\n'
                        '    season_phase TEXT NOT NULL,\n'
                        '    lifecycle_state TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    condition_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    notional_usdc REAL NOT NULL,\n'
                        '    threshold REAL NOT NULL,\n'
                        '    crossed_at TEXT NOT NULL,\n'
                        '    entry_ask_vwap REAL NOT NULL,\n'
                        '    entry_shares REAL NOT NULL,\n'
                        '    entry_book_snapshot_id TEXT NOT NULL REFERENCES '
                        'book_snapshots(book_snapshot_id),\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                        '    UNIQUE (condition_id, token_id, notional_usdc, threshold)\n'
                        ')'),
 'episode_carryovers': ('coconut-episode-source-v1',
                        'CREATE TABLE episode_carryovers (\n'
                        '    episode_carryover_id TEXT PRIMARY KEY,\n'
                        '    episode_id TEXT NOT NULL UNIQUE,\n'
                        '    origin_utc_date TEXT NOT NULL,\n'
                        '    carried_from_utc_date TEXT NOT NULL,\n'
                        '    created_run_id TEXT NOT NULL,\n'
                        '    sport_family TEXT NOT NULL,\n'
                        '    season_phase TEXT NOT NULL,\n'
                        '    lifecycle_state TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    condition_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    outcome_index INTEGER NOT NULL,\n'
                        '    outcome_label TEXT NOT NULL,\n'
                        '    notional_usdc REAL NOT NULL,\n'
                        '    threshold REAL NOT NULL,\n'
                        '    crossed_at TEXT NOT NULL,\n'
                        '    entry_ask_vwap REAL NOT NULL,\n'
                        '    entry_shares REAL NOT NULL,\n'
                        '    liquidity REAL,\n'
                        '    volume_num REAL,\n'
                        '    volume_24hr REAL,\n'
                        '    carried_at TEXT NOT NULL\n'
                        ')',
                        ('condition_id',
                         'token_id',
                         'outcome_index',
                         'outcome_label',
                         'liquidity',
                         'volume_num',
                         'volume_24hr'),
                        ('condition_id', 'token_id'),
                        'CREATE TABLE episode_carryovers (\n'
                        '    episode_carryover_id TEXT PRIMARY KEY,\n'
                        '    episode_id TEXT NOT NULL UNIQUE,\n'
                        '    origin_utc_date TEXT NOT NULL,\n'
                        '    carried_from_utc_date TEXT NOT NULL,\n'
                        '    created_run_id TEXT NOT NULL,\n'
                        '    sport_family TEXT NOT NULL,\n'
                        '    season_phase TEXT NOT NULL,\n'
                        '    lifecycle_state TEXT NOT NULL,\n'
                        '    competition_code TEXT,\n'
                        '    event_id TEXT NOT NULL,\n'
                        '    event_cluster_id TEXT NOT NULL,\n'
                        '    condition_id TEXT NOT NULL,\n'
                        '    token_id TEXT NOT NULL,\n'
                        '    notional_usdc REAL NOT NULL,\n'
                        '    threshold REAL NOT NULL,\n'
                        '    crossed_at TEXT NOT NULL,\n'
                        '    entry_ask_vwap REAL NOT NULL,\n'
                        '    entry_shares REAL NOT NULL,\n'
                        '    carried_at TEXT NOT NULL,\n'
                        '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0)\n'
                        ')'),
 'game_anchor_observations': ('coconut-anchor-source-v1',
                              'CREATE TABLE game_anchor_observations (\n'
                              '    game_anchor_observation_id TEXT PRIMARY KEY,\n'
                              '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    sport_family TEXT NOT NULL,\n'
                              '    season_phase TEXT NOT NULL,\n'
                              '    event_cluster_id TEXT NOT NULL,\n'
                              '    condition_id TEXT NOT NULL,\n'
                              '    token_id TEXT NOT NULL,\n'
                              '    book_snapshot_id TEXT NOT NULL REFERENCES '
                              'book_snapshots(book_snapshot_id),\n'
                              '    observed_at TEXT NOT NULL,\n'
                              '    scheduled_start_field TEXT NOT NULL,\n'
                              '    scheduled_start_raw TEXT NOT NULL,\n'
                              '    scheduled_start_utc TEXT NOT NULL,\n'
                              '    minutes_to_scheduled_start REAL NOT NULL,\n'
                              "    anchor_role TEXT NOT NULL CHECK (anchor_role='PRESTART_CANDIDATE'),\n"
                              '    UNIQUE (cycle_id, token_id)\n'
                              ')',
                              ('scheduled_start_raw',),
                              (),
                              'CREATE TABLE game_anchor_observations (\n'
                              '    game_anchor_observation_id TEXT PRIMARY KEY,\n'
                              '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                              '    run_id TEXT NOT NULL,\n'
                              '    sport_family TEXT NOT NULL,\n'
                              '    season_phase TEXT NOT NULL,\n'
                              '    event_cluster_id TEXT NOT NULL,\n'
                              '    condition_id TEXT NOT NULL,\n'
                              '    token_id TEXT NOT NULL,\n'
                              '    book_snapshot_id TEXT NOT NULL REFERENCES '
                              'book_snapshots(book_snapshot_id),\n'
                              '    observed_at TEXT NOT NULL,\n'
                              '    scheduled_start_field TEXT NOT NULL,\n'
                              '    scheduled_start_utc TEXT NOT NULL,\n'
                              '    minutes_to_scheduled_start REAL NOT NULL,\n'
                              "    anchor_role TEXT NOT NULL CHECK (anchor_role='PRESTART_CANDIDATE'),\n"
                              '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                              '    UNIQUE (cycle_id, token_id)\n'
                              ')'),
 'episode_path_observations': ('coconut-path-source-v1',
                               'CREATE TABLE episode_path_observations (\n'
                               '    episode_path_observation_id TEXT PRIMARY KEY,\n'
                               '    episode_id TEXT NOT NULL,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    token_id TEXT NOT NULL,\n'
                               '    notional_usdc REAL NOT NULL,\n'
                               '    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL CHECK (path_status IN '
                               "('FULL','PARTIAL','EMPTY','BOOK_UNAVAILABLE')),\n"
                               '    best_bid REAL,\n'
                               '    requested_shares REAL NOT NULL,\n'
                               '    filled_shares REAL NOT NULL,\n'
                               '    remaining_shares REAL NOT NULL,\n'
                               '    executable_bid_vwap REAL,\n'
                               '    worst_bid REAL,\n'
                               '    levels_used INTEGER NOT NULL,\n'
                               '    UNIQUE (episode_id, cycle_id)\n'
                               ')',
                               ('token_id', 'best_bid'),
                               ('token_id',),
                               'CREATE TABLE episode_path_observations (\n'
                               '    episode_path_observation_id TEXT PRIMARY KEY,\n'
                               '    episode_id TEXT NOT NULL,\n'
                               '    cycle_id TEXT NOT NULL REFERENCES collection_cycles(cycle_id),\n'
                               '    run_id TEXT NOT NULL,\n'
                               '    token_id TEXT NOT NULL,\n'
                               '    notional_usdc REAL NOT NULL,\n'
                               '    book_snapshot_id TEXT REFERENCES book_snapshots(book_snapshot_id),\n'
                               '    observed_at TEXT NOT NULL,\n'
                               '    path_status TEXT NOT NULL CHECK (path_status IN '
                               "('FULL','PARTIAL','EMPTY','BOOK_UNAVAILABLE')),\n"
                               '    requested_shares REAL NOT NULL,\n'
                               '    filled_shares REAL NOT NULL,\n'
                               '    remaining_shares REAL NOT NULL,\n'
                               '    executable_bid_vwap REAL,\n'
                               '    worst_bid REAL,\n'
                               '    levels_used INTEGER NOT NULL,\n'
                               '    _public_record_id INTEGER NOT NULL CHECK(_public_record_id>0),\n'
                               '    UNIQUE (episode_id, cycle_id)\n'
                               ')')}

"""SQLite schemas.

core.db      — shared Polymarket sports data, one writer at a time (collector jobs).
books/*.db   — monthly order-book snapshot shards (largest table, kept out of core).
strategy db  — one ledger per strategy variant (orders, fills, positions, params).

All timestamps are integer unix seconds UTC unless the column name says otherwise.
Schema changes are additive: add a new statement to the tuple, never edit a shipped one.
"""

CORE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS games (
        game_key TEXT PRIMARY KEY,              -- Gamma event id
        sport TEXT NOT NULL,                    -- soccer | mlb | nba | nfl | nhl
        league TEXT,
        event_slug TEXT,
        title TEXT,
        home_team TEXT,
        away_team TEXT,
        start_time INTEGER,                     -- scheduled game start (UTC)
        polymarket_game_id TEXT,                -- sports feed gameId
        sportradar_game_id TEXT,
        status TEXT,                            -- scheduled | live | ended | cancelled
        home_score INTEGER,
        away_score INTEGER,
        ended_at INTEGER,
        first_seen INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        source TEXT NOT NULL,                   -- discover | history_backfill
        meta TEXT                               -- raw-ish json of useful extra fields
    )
    """,
    "CREATE INDEX IF NOT EXISTS games_sport_start ON games(sport, start_time)",
    "CREATE INDEX IF NOT EXISTS games_status ON games(status)",
    """
    CREATE TABLE IF NOT EXISTS markets (
        condition_id TEXT PRIMARY KEY,
        market_id TEXT,                         -- Gamma market id
        event_id TEXT,
        game_key TEXT REFERENCES games(game_key),
        market_type TEXT NOT NULL,              -- moneyline | draw | total | spread | other
        sports_market_type TEXT,                -- raw Gamma sportsMarketType
        line REAL,
        question TEXT,
        slug TEXT,
        group_item_title TEXT,
        volume REAL,
        liquidity REAL,
        fee_schedule TEXT,                      -- json
        neg_risk INTEGER,
        created_at INTEGER,
        end_date INTEGER,
        closed INTEGER NOT NULL DEFAULT 0,
        resolved_outcome_index INTEGER,         -- winning outcome index once resolved
        resolved_at INTEGER,
        resolution_source TEXT,                 -- gamma_outcome_prices | uma | data_api ...
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS markets_game ON markets(game_key)",
    "CREATE INDEX IF NOT EXISTS markets_type ON markets(market_type, closed)",
    """
    CREATE TABLE IF NOT EXISTS tokens (
        token_id TEXT PRIMARY KEY,
        condition_id TEXT NOT NULL REFERENCES markets(condition_id),
        outcome_index INTEGER NOT NULL,
        outcome_label TEXT,
        side TEXT                               -- home | away | draw | yes | no | over | under
    )
    """,
    "CREATE INDEX IF NOT EXISTS tokens_condition ON tokens(condition_id)",
    """
    CREATE TABLE IF NOT EXISTS price_bars (
        token_id TEXT NOT NULL,
        ts INTEGER NOT NULL,                    -- minute bucket start (UTC, multiple of 60)
        source TEXT NOT NULL,                   -- poll_mid | ws_last | history
        price REAL NOT NULL,
        received_at INTEGER,
        PRIMARY KEY (token_id, ts, source)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX IF NOT EXISTS price_bars_ts ON price_bars(ts)",
    """
    CREATE TABLE IF NOT EXISTS game_states (
        game_key TEXT NOT NULL,
        ts INTEGER NOT NULL,                    -- source timestamp if present else received
        received_at INTEGER NOT NULL,
        source TEXT NOT NULL,                   -- ws_sports | gamma | backfill
        status TEXT,
        live INTEGER,
        ended INTEGER,
        period TEXT,
        elapsed TEXT,                           -- raw clock string from feed
        game_minute REAL,                       -- normalised minute of play, if derivable
        home_score INTEGER,
        away_score INTEGER,
        raw TEXT,
        PRIMARY KEY (game_key, ts, source)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS market_metrics (
        condition_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        volume REAL,
        liquidity REAL,
        open_interest REAL,
        live_volume REAL,
        PRIMARY KEY (condition_id, ts)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS public_trades (
        uid TEXT PRIMARY KEY,                   -- tx hash + asset + side + size + price + ts digest
        condition_id TEXT NOT NULL,
        token_id TEXT,
        ts INTEGER NOT NULL,
        side TEXT,
        price REAL,
        size REAL,
        usd REAL,
        wallet TEXT,
        tx_hash TEXT,
        outcome_index INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS public_trades_cond_ts ON public_trades(condition_id, ts)",
    """
    CREATE TABLE IF NOT EXISTS backfill_status (
        condition_id TEXT NOT NULL,
        kind TEXT NOT NULL,                     -- prices | trades | resolution | holders
        status TEXT NOT NULL,                   -- done | partial | failed | empty
        rows INTEGER,
        detail TEXT,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (condition_id, kind)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        name TEXT PRIMARY KEY,
        value TEXT,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT,
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS quality_events_ts ON quality_events(ts)",
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job TEXT NOT NULL,
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        ok INTEGER,
        summary TEXT,
        git_commit TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS job_runs_job ON job_runs(job, started_at)",
)

BOOKS_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS book_snapshots (
        token_id TEXT NOT NULL,
        ts INTEGER NOT NULL,                    -- received time (UTC seconds)
        source TEXT NOT NULL,                   -- poll | ws
        best_bid REAL,
        best_ask REAL,
        mid REAL,
        spread REAL,
        bid_depth_usd REAL,                     -- all visible levels
        ask_depth_usd REAL,
        imb_l1 REAL,                            -- (bid-ask)/(bid+ask) size imbalance
        imb_l5 REAL,
        imb_l10 REAL,
        levels_z BLOB,                          -- zlib(json {"b":[[p,s]..10],"a":[[p,s]..10]})
        exchange_ts INTEGER,                    -- book timestamp from API if present (ms->s)
        PRIMARY KEY (token_id, ts, source)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX IF NOT EXISTS book_snapshots_ts ON book_snapshots(ts)",
)

STRATEGY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS param_versions (
        version INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        params TEXT NOT NULL,                   -- json of the full params used
        stake_usdc REAL NOT NULL,
        mode TEXT NOT NULL,                     -- live | paper
        git_commit TEXT,
        author TEXT NOT NULL,                   -- init | autopilot | human
        rationale TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cycles (
        cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        param_version INTEGER,
        ok INTEGER,
        candidates INTEGER,
        orders INTEGER,
        error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER,
        ts INTEGER NOT NULL,
        token_id TEXT,
        condition_id TEXT,
        game_key TEXT,
        action TEXT NOT NULL,                   -- enter | exit | skip | hold
        reason TEXT,
        features TEXT                           -- json snapshot of inputs used
    )
    """,
    "CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts)",
    """
    CREATE TABLE IF NOT EXISTS orders (
        intent_id TEXT PRIMARY KEY,             -- uuid written BEFORE posting
        position_id TEXT,
        created_at INTEGER NOT NULL,
        mode TEXT NOT NULL,                     -- live | paper
        side TEXT NOT NULL,                     -- BUY | SELL
        token_id TEXT NOT NULL,
        condition_id TEXT,
        order_type TEXT NOT NULL,               -- FOK | GTC | FAK
        usdc_amount REAL,                       -- BUY notional requested
        shares REAL,                            -- SELL shares requested
        limit_price REAL,
        expected_avg_price REAL,                -- full-book walk estimate at decision time
        exchange_order_id TEXT,
        status TEXT NOT NULL,                   -- intent | posted | matched | confirmed | failed | cancelled | unknown
        response TEXT,
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS orders_status ON orders(status)",
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,               -- exchange trade id (paper: intent_id)
        intent_id TEXT NOT NULL REFERENCES orders(intent_id),
        ts INTEGER NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        shares REAL NOT NULL,
        fee_usdc REAL,                          -- NULL = unknown (never assume 0)
        status TEXT NOT NULL,                   -- CONFIRMED | MATCHED | MINED | FAILED | PAPER
        raw TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,                     -- live | paper
        param_version INTEGER NOT NULL,
        stake_usdc REAL NOT NULL,               -- stake tier at entry
        sport TEXT,
        league TEXT,
        game_key TEXT,
        condition_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        outcome_label TEXT,
        opened_at INTEGER NOT NULL,
        game_minute_at_entry REAL,
        entry_price REAL,                       -- confirmed VWAP
        shares REAL,                            -- confirmed shares still held
        cost_usdc REAL,                         -- confirmed spend incl. fee
        entry_fee_usdc REAL,
        exit_rules TEXT,                        -- json of TP/SL/time rules frozen at entry
        status TEXT NOT NULL,                   -- pending | open | closing | closed | resolved | quarantined
        closed_at INTEGER,
        exit_reason TEXT,                       -- take_profit | stop_loss | time_exit | resolution_win | resolution_loss | manual
        exit_price REAL,
        proceeds_usdc REAL,                     -- sell proceeds net of fee, or resolution payout
        exit_fee_usdc REAL,
        realized_pnl REAL,                      -- proceeds - cost (only when fully settled)
        settlement TEXT,                        -- confirmed_sell | resolution | paper
        redeemed INTEGER NOT NULL DEFAULT 0,
        notes TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS positions_status ON positions(status)",
    "CREATE INDEX IF NOT EXISTS positions_closed ON positions(closed_at)",
    """
    CREATE TABLE IF NOT EXISTS stake_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        from_usdc REAL,
        to_usdc REAL NOT NULL,
        from_mode TEXT,
        to_mode TEXT,
        reason TEXT NOT NULL,
        evidence TEXT,                          -- json of the gate statistics
        sport TEXT                              -- per-sport stake/mode move; NULL = whole variant
    )
    """,
)

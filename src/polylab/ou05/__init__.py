"""Soccer "O/U 0.5 goals" market-life study (docs/research/ou05-overround-study.md).

A dedicated, self-contained collector for every open soccer Total 0.5 market (all leagues), from market
creation to resolution, at 1-minute resolution with BOTH sides' level-1 quotes:

  store.py     data/ou05/registry.db (market registry, metrics, poll state, checkpoints, quality, job runs)
               + data/ou05/YYYY-MM.db monthly shards (ou05_quotes)
  discover.py  `polylab ou05 discover` (hourly): Gamma open totals -> registry; closing/resolution
  poll.py      `polylab ou05 poll` (every minute): POST /books for both tokens -> change-or-heartbeat rows
  backfill.py  `polylab ou05 backfill`: CLOB prices-history (mid only) for closed and open markets

It never writes core.db: registering ~900 extra games there would pull them into tick's poll and strategy
universes (common.active_tokens / pregame_tokens). core.db is only read (game status, goal markers).
"""

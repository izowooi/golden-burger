"""Near-resolution "general" markets (every Gamma category) for cherry back-to-basics (docs/strategies/cherry.md).

One central, budgeted store next to core.db (which keeps only the 5-sport game universe):

  store.py     data/general/registry.db (market registry incl. raw Gamma inputs, metrics, poll state, checkpoints,
               quality, job runs) + data/general/YYYY-MM.db monthly shards (gen_quotes, YES token only)
  discover.py  `polylab general discover` (10 min): open markets whose end_ref is within the next ~4 days
  poll.py      `polylab general poll` (1 min, or every N min as a budget fallback): YES-token L1 quotes,
               change-or-heartbeat rows; markets already tracked by core.db are not polled again
  backfill.py  `polylab general backfill`: closed markets of the last months, CLOB prices-history of the YES token
               over the final ~4.5 days before end_ref (fidelity 5 by default), resumable and budgeted

Binary books mirror (NO ask = 1 - YES bid), so only the YES (outcome index 0) token is stored; readers derive NO.
"""

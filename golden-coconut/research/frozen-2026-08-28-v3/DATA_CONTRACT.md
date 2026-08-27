# Golden Coconut v3 Data Contract

Data contract: `major-sports-lifecycle-census-v3`. Daily-rsync collection contract:
`research-full-v1`.

- Accountless `archive_only`; simulation/shadow modes only.
- Canonical artifact: `data/coconut-major-sports-lifecycle-5m-v3/trades_sim.db`.
- UTC archives are immutable whole files named `trades_sim_YYYYMMDD.db`.
- SQLite is standalone create-only schema v3 and append-only. It never opens, migrates, merges,
  copies, or backfills a v1/v2 database.
- Discovery is five independent Gamma `/events/keyset` terminal-cursor sweeps with
  `closed=false`, `include_children=false`, `related_tags=false`,
  `start_time_min=slot-24h`, and `start_time_max=slot+48h`; no `live` request parameter is used.
- New discovery membership is independently revalidated against the UTC half-open scheduled-start
  interval. Raw out-of-window responses are preserved and rejected rather than silently discarded.
- Accepted event IDs and canonical slugs are followed after leaving the window. A tracked event is
  not dropped merely because it reappears outside a later discovery interval.
- `DISCOVERED_OPEN` is an explicit unknown-phase measurement stratum. Eligible books and vectors are
  collected in that stratum but never pooled with `PREGAME` or `IN_PLAY`.
- Future scheduled-start evidence may produce a `PRESTART_CANDIDATE` anchor without inferring
  lifecycle or match minute. Known in-play/terminal evidence cannot produce a prestart anchor.
- Canonical full-book gzip is unique per token/cycle. Each frozen notional
  (`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`) receives an independent
  `0.75..0.99` threshold vector and censoring state.
- Public fee absence is not replaced with zero or a fallback value.
- Cohort: config hash × strategy source digest × mode × runtime job. Git is provenance only.
- No wallet, order, fill, position, trade, or realized P&L table exists.

Analyzer input is restricted to daily-rsync-verified v3 shards from one cohort and to atomically
published cycles with one unique `SUCCEEDED` terminal event and all five cursor-complete sweeps.
Schedule-window violations, lifecycle strata, season phase, sport family, and notional are reported
separately. Health success alone does not authorize a profitability or trading conclusion.

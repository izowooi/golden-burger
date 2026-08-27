# Golden Coconut v5 Data Contract

Data contract: `major-sports-lifecycle-census-v5`. Daily-rsync collection contract:
`research-full-v1`.

- Accountless `archive_only`; simulation/shadow modes only.
- Canonical artifact: `data/coconut-major-sports-lifecycle-5m-v5/trades_sim.db`.
- UTC archives are immutable whole files named `trades_sim_YYYYMMDD.db`.
- SQLite is standalone create-only schema v5 and append-only. It never opens, migrates, merges,
  copies, or backfills a v1/v2/v3/v4 database.
- Discovery is five logical family sweeps. Soccer uses eight frozen competition query tags; each
  other family uses one frozen major-league query tag. Every physical `/events/keyset` request uses
  the same half-open `start_time_min/max` envelope and every query-tag cursor must terminate.
- Raw pages preserve query-tag provenance through `api_requests.params_json`. Duplicate event IDs
  may exist in raw pages but normalized event evidence is unique per family cycle.
- US event identity requires the frozen major sport object/tags, exactly two same-league teams, and
  exactly one semantic root or season series. A season series must use exact
  `<family>-<year>` slug/ticker and `<league name> <year>` title with a year no later than the
  scheduled calendar year and no more than one year earlier; its numeric ID remains recorded but is
  not incorrectly equated with the sport root ID.
- Soccer draw is accepted only as bare `Draw`/`Tie` or exact
  `Draw (<canonical event title>)`/`Tie (<canonical event title>)`. Substring matches are forbidden.
- New discovery membership is independently revalidated against the UTC half-open scheduled-start
  interval. Raw out-of-window responses are preserved and rejected rather than silently discarded.
- Accepted event IDs and canonical slugs are followed after leaving the window.
- `DISCOVERED_OPEN` is an explicit unknown-phase stratum. Its books and vectors are collected but
  never pooled with `PREGAME` or `IN_PLAY`.
- Future scheduled-start evidence may produce a `PRESTART_CANDIDATE` anchor without inferring
  lifecycle or match minute.
- Canonical full-book gzip is unique per token/cycle. Each frozen notional
  (`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`) receives an independent
  `0.75..0.99` threshold vector and censoring state.
- Public fee absence is not replaced with zero or a fallback value.
- Cohort: config hash × strategy source digest × mode × runtime job. Git is provenance only.
- No wallet, order, fill, position, trade, or realized P&L table exists.

Analyzer input is restricted to daily-rsync-verified v5 shards from one cohort and to atomically
published cycles with one unique `SUCCEEDED` terminal event and all five logical sweeps complete.
Schedule-window violations, lifecycle strata, season phase, sport family, query-tag coverage, and
notional are reported separately. Health success does not authorize a profitability conclusion.

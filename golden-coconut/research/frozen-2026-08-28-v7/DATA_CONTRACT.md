# Golden Coconut v7 Data Contract

Contract: `major-sports-lifecycle-census-v7`.

V7 creates a fresh `data/coconut-major-sports-lifecycle-5m-v7/trades_sim.db`. It reuses the exact
create-only v6 SQLite schema (`PRAGMA user_version=6`) and immutable v6 sports registry because no
column, classification, universe, or estimand changed. V1 through v6 databases are not migrated,
copied, or pooled into this cohort.

The new contract field is acquisition provenance: `trading.gamma.parallel_family_workers` must be
exactly five. Soccer, MLB, NBA, NFL, and NHL each use one isolated public HTTP client concurrently,
while normalization and atomic publication retain deterministic frozen family order. Every API
attempt remains an append-only `api_requests` row. A family exception, incomplete cursor, exhausted
retry, global receipt skew above 90 seconds, or downstream CRITICAL/HIGH gate invalidates the cycle.

Only unique `research_run_events.event_type='SUCCEEDED'` cycles with five terminal family sweeps are
eligible for collection-health analysis. Displayed books and ladder walks are counterfactual
observations, not fills, orders, transactions, positions, or realized P&L.

# Golden Coconut v1 Data Contract

Data contract: `major-sports-inplay-moneyline-census-v1`.

- Accountless `archive_only`; sim/shadow modes only.
- Canonical active artifact: `data/coconut-major-sports-5m-v1/trades_sim.db`.
- UTC archives: whole files named `trades_sim_YYYYMMDD.db`.
- SQLite is create-only and append-only. Existing epochs are never altered or merged.
- Required evidence domains: schema/registry/config/run; slots/cycles/family sweeps/API/raw; event/tag/
  series/team; market/outcome; book attempt/snapshot/ladder; threshold vector/episode/path; resolution;
  source sports clock; data quality/storage/database checks.
- No transactional account tables and no realized performance fields.
- Canonical book gzip is deterministic (`mtime=0`) and unique per token/cycle.
- Optional public fee observations have no fallback value.
- Cooperative/request-stop/hard budgets: 225/195/240 seconds. Receipt skew max: 90 seconds.
- Storage: 150 GiB free floor, 70% warning, 80% stop, no automatic row deletion.
- Cohort: config hash × strategy source digest × mode × runtime job, with registry hash as frozen
  provenance.

Analyzer estimands are health and coverage only. Every sport and US season phase is reported separately.
Missing sports make the five-family macro null. Profitability remains null during health-only collection.

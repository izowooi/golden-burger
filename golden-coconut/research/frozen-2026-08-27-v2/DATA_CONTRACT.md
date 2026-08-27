# Golden Coconut v2 Data Contract

Data contract: `major-sports-lifecycle-census-v2`. Daily-rsync collection contract:
`research-full-v1`.

- Accountless `archive_only`; sim/shadow modes only.
- Canonical runtime/artifact:
  `data/coconut-major-sports-lifecycle-5m-v2/trades_sim.db`.
- UTC archives are immutable whole files named `trades_sim_YYYYMMDD.db`.
- Every shard contains exactly one `collection_contracts` row with
  `contract_name=research-full-v1` and the shard's canonical UTC date.
- SQLite is standalone create-only schema v2 and append-only. It does not open, migrate, copy, merge,
  or backfill a v1 database.
- Discovery is five independent Gamma `/events/keyset` terminal-cursor sweeps using exactly
  `closed=false`, `include_children=false`, `related_tags=false`, and
  `start_date_min=slot-24h`, `start_date_max=slot+48h`. There is no `live` request parameter.
- Each discovered accepted game is followed through an explicit Gamma event-by-id request until a
  terminal `RESOLVED`, `VOID`, `TIE`, or `CANCELLED` observation. `POSTPONED` remains follow-up
  eligible. Schedule changes are append-only revisions.
- Gamma lifecycle fields and same-cycle source clocks are preserved verbatim in compressed raw
  payloads and normalized lifecycle JSON. Wall time never creates a match minute.
- The canonical game identity is the Gamma event slug. `gameId` and WSS identifiers are aliases.
  A WSS no-message result is a coverage fact, never proof that a game is absent.
- Pregame and in-play are separate strata. Pregame canonical books retain enough evidence to select
  T-24h, T-60m, and last-prestart anchors where observations exist.
- Canonical full-book gzip remains unique per token/cycle. Every frozen notional
  (`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`) receives its own
  `0.75..0.99` threshold vector, censoring state,
  crossing episode, and path replay.
- Cohort: config hash × strategy source digest × mode × runtime job. Git is provenance only.
- No transactional account tables and no realized performance fields.

Analyzer input is restricted to one cohort and to cycles with a unique `SUCCEEDED` terminal event,
five cursor-complete sweeps, and an atomically published cycle. A unique canonical game is the sample
unit. Selection and profitability remain `null` until every preregistered health gate passes; passing a
health gate does not itself authorize a trading conclusion.

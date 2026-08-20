# Golden Tangerine frozen live A/B protocol — 2026-08-20

- Companion evidence: Golden Black `sports-resolution-paired-v1`
- Entry window: `[2026-08-20T14:08:00Z, 2026-09-19T14:08:00Z)`
- Follow-up cutoff: `2026-10-19T14:08:00Z`
- First collection-health checkpoint: `2026-08-22T10:00:00Z` (`2026-08-22 19:00 KST`)
- Cadence: 5 minutes, non-concurrent
- Arm A: `polybot-orange`, exact `$5` ask VWAP `[0.94,0.95]`
- Arm B: `polybot-fox`, exact `$5` ask VWAP `[0.92,0.93]`
- Population: sports, Gamma endDate `(0h,6h]`, liquidity `>=10,000`, cumulative volume
  `>=5,000`, exactly two aligned labels/prices/tokens, explicit boolean `negRisk`, both outcomes,
  accepting CLOB only. Named moneyline (`negRisk=false`) and Yes/No proposition
  (`negRisk=true`) are retained as separate strata.
- Pagination: `/events/keyset`, limit 500, max 4 pages, terminal cursor required
- Signal unit: token's first exact displayed `$5` VWAP observation in its job arm
- Order: fresh full-depth recheck, exact `$5` shares, venue-tick FOK BUY
- Exposure: account 3, event 1, new/cycle 1; no account-wide wallet adoption
- Exit: hold to proven resolution; no TP, stop, trailing, time exit or pre-resolution SELL
- Evidence: `config_hash × strategy_source_digest × mode × job_name` cohort, entry
  episode/snapshot, order intent/status/fill/fee, final payout. Git commit is provenance only.
- Manual wallet positions are out of scope and must remain untouched
- 24h/7d checks are health only; no threshold tuning before entry/follow-up completion
- This is an explicitly approved low-notional live pilot, not final scale-up evidence

Pre-entry-window correction (2026-08-20): manual build #56081 showed the prior literal
`[Yes,No] AND negRisk=false` wording selected zero markets while Golden Black's intended paired
population contained 47 named moneylines and 13 Yes/No negRisk propositions. No entry episode or
order existed. Before the originally scheduled start, the contract was corrected to the aligned two-outcome
population above; thresholds, clocks, notional, cadence and exit policy did not change.

Pre-entry evidence-identity correction (2026-08-20): unrelated monorepo commits were observed to
split `git_commit` lineage despite identical Tangerine code. Before the entry window, the frozen
cohort identity was made source-scoped as stated above. No trading parameter or decision rule changed.

Operator-requested immediate-start correction (2026-08-20): at `2026-08-20T14:08:00Z`, before
the originally scheduled start and with no entry episode or order in either live arm, the entry
window was moved forward to start immediately. Its exact 30-day duration and the following exact
30-day resolution follow-up were preserved. Thresholds, universe, notional, cadence, exposure and
exit policy did not change. The first operational health review is fixed at
`2026-08-22T10:00:00Z`; it is not a parameter-selection checkpoint.

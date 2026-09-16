# Golden Plum Soccer $10 scale cohort v16

- King and Queen Soccer keep the direct complete six-book leader entry `[.70,.73]`, one-observation signal, source minute `<60`, common SL `entry-.12`, minute-65 full-holding FOK exit and absolute TP `.85/.90`. Change only the target BUY notional `$5 → $10`.
- The baseline signal remains exact `$5`. A fresh `$10` full-depth FOK is submitted when fully executable; otherwise use the largest fully executable configured rung down to `$5`, or no order. Confirmed fill size/VWAP/fee determines cost and every exit. Intermediate/partial outcomes are reported, never rewritten as `$10`.
- Existing positions retain entry-time TP/SL/time-exit values and actual cost. DBs are append-only and are not reset or migrated. The new `config_hash × strategy_source_digest × mode × runtime` begins after successful deployment.
- Account/event/cycle caps, full-holding stop/time exits, partial TP ledger, `-$300` confirmed economic-loss guard, pending/quarantine and reconciliation fail-closed behavior remain unchanged. MLB children remain close-only/$5. NFL is a separate `$10` cohort.
- This is operator-approved scale testing, not a profitability conclusion. Review actual `$10/$5` coverage, FOK confirmations, exit depth and confirmed drawdown before any later increase.

# Golden Plum Soccer continuous corrected live v13

- King/Queen keep entry `[.70,.73]`, one current quote, TP `.90/.95`, SL `-.15`, minute-75 full FOK exit and exact `$5` until an explicit operator stop or retune.
- One-day, three-day and seven-day reviews are checkpoints only. They never close entry or revert to `.75-.78`/three-observation legacy logic.
- `9999-12-31T23:59:59Z` is the explicit open-ended sentinel used by the existing finite-date validation machinery.
- Existing positions, confirmed P&L, economic loss history and close-only MLB reconciliation remain continuous.

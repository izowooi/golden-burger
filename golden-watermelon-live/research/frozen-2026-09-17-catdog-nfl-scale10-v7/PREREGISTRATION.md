# Golden Watermelon NFL `$10` scale cohort v7

## Prospective contract

- `polybot-cat / watermelon-live-cat-nfl-91-1m-v6`: NFL entry lower bound `.91`.
- `polybot-dog / watermelon-live-dog-nfl-94-1m-v6`: NFL entry lower bound `.94`.
- Common NFL policy: exact top-level whole-game two-team moneyline, upper
  `.999`, exact baseline `$5` signal book, target `$10`, no TP, resolution
  hold, effective stop `max(.70, confirmed entry VWAP-.30)` and existing
  event/account guards.
- A fresh full-depth book selects one fully executable ladder amount. It first
  attempts a full `$10` FOK BUY and may fall back to a full `$5` FOK BUY;
  `$6` through `$9` and partial fills are not valid entries.
- The only A/B treatment remains the NFL entry lower bound. Soccer remains a
  separate `.91/.92`, `$10`, stop `.65` cohort and MLB remains close-only `$5`.
- Soccer, MLB and NFL keep separate runtime jobs, databases, parameter records,
  notional values and cohort identities. Existing v6 NFL positions retain the
  parameters recorded when they were bought.
- NFL entry remains enabled until an explicit operator stop or retune.

## Scale decision

The operator authorized Cat and Dog to move from `$5` to `$10` on 2026-09-17.
The earlier 15-game NFL replay remains exploratory evidence. This v7 cohort is
prospective execution evidence and must be reviewed separately from v6 `$5`
entries using confirmed fills, exact fees, stop execution gaps and exact
resolution.

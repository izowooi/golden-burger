# Golden Watermelon NFL `.91/.94` live A/B v6

## Prospective contract

- `polybot-cat / watermelon-live-cat-nfl-91-1m-v6`: NFL entry lower bound `.91`.
- `polybot-dog / watermelon-live-dog-nfl-94-1m-v6`: NFL entry lower bound `.94`.
- Common NFL policy: exact top-level whole-game two-team moneyline, upper
  `.999`, exact baseline `$5`, target `$5`, no TP, resolution hold, effective
  stop `max(.70, confirmed entry VWAP-.30)` and existing event/account guards.
- The only A/B treatment is the NFL entry lower bound. Soccer remains `.91/.92`
  and MLB remains close-only in these two accounts.
- Soccer, MLB and NFL use separate runtime jobs, databases, parameter records,
  notional values and cohort identities. A change in one family cannot alter
  another family.
- NFL entry remains enabled until an explicit operator stop or retune. Existing
  v5 NFL databases, soccer databases and MLB databases are not migrated.

## Exploratory evidence

The verified `polybot-gold / plum-shadow-gold-nfl-1m-v1` pin
`20260914T105722Z` contains 15 resolved NFL games and 4,915 snapshots.
Displayed full-depth `$5` replay with recorded fee formula produced:

| NFL entry / stop | signals | positive | P&L | latest cohort halves | historical cohort |
|---|---:|---:|---:|---:|---:|
| `.91 / .70` | 14 | 12 | `+$1.43380` | `+$0.33408 / +$0.65277` | `+$0.44694` |
| `.94 / .70` | 14 | 13 | `+$1.31326` | `-$0.22681 / +$1.09313` | `+$0.44694` |

The small opening-week cluster is exploratory and cannot establish a winner or
authorize scaling. Both arms start at `$5`; confirmed FOK fill, fee, stop gap
and exact resolution are the prospective evidence.

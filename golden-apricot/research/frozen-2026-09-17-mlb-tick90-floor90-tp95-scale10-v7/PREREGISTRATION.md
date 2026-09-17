# Golden Apricot MLB Tick90 / floor .90 / TP .95 / `$10` v7

## Prospective live contract

- Eco: `apricot-live-eco-mlb-tick90-tp95-v2`.
- Fruit: `apricot-live-fruit-mlb-tick90-tp95-v2`.
- MLB exact whole-game direct HOME/AWAY moneyline only.
- Durable first complete common HOME/AWAY tick is tick0; entry uses the first
  valid tick in `[90,92]` minutes.
- The unique midpoint favorite must have baseline exact `$5` ask VWAP in
  `[.90,.999]`, leader margin at least `.005`, and spread at most `.05`.
- Target is `$10`. The fresh full-depth book selects one atomic `$10` FOK BUY.
  If `$10` is not fully executable, it may select one atomic `$5` FOK BUY.
  `$6-$9`, partial fill, and request-price assumptions are invalid.
- Both accounts use full-holding bid VWAP `.95` FOK TP; otherwise verified
  one-hot resolution.
- Eco and Fruit are independent replication accounts. No parameter differs.
- Existing v2 positions, if any, retain the parameters recorded at BUY.

## Size-effect audit

The verified Gold MLB pin `20260917T064111Z` contains 210 resolved games. With
the selected Tick90/floor `.90`/TP `.95` policy:

| target | trades | principal | net | ROI | max drawdown | full target coverage |
|---|---:|---:|---:|---:|---:|---:|
| `$5` | 57 | `$285.72610` | `+$3.33194` | `+1.1661%` | `$0.18851` | 57/57 |
| `$10` | 57 | `$571.45085` | `+$6.84726` | `+1.1982%` | `$0.36735` | 57/57 |

The `$10` replay remained positive in all five chronological 42-game blocks:
`+$0.12343 / +$1.13077 / +$3.42573 / +$0.99339 / +$1.17394`.
No replay trade fell back to `$5` because all 57 recorded books supported a
complete `$10` order.

Historical live target cohorts do not isolate stake size. The old Tick50 `$5`
and `$10` entries occurred on different dates and multiple config/source
cohorts. Confirmed actual totals were positive for the pooled `$5` rows and
negative for the pooled `$10` rows, but the same-book replay shows near-linear
P&L and drawdown scaling rather than a direction reversal caused by the sizing
code. This v7 scale decision applies only to the new conservative policy.

Displayed depth does not guarantee live FOK fill. Review target/selected/max
executable notional, fallback reason, confirmed fill, fee, TP and resolution
for every prospective entry.

# Golden Apricot MLB Tick90 / floor .90 / TP .95 v6

## Prospective live contract

- Eco: `apricot-live-eco-mlb-tick90-tp95-v2`.
- Fruit: `apricot-live-fruit-mlb-tick90-tp95-v2`.
- MLB exact whole-game direct HOME/AWAY moneyline only.
- The first complete common HOME/AWAY tick is durable tick0. Entry uses the
  first valid tick in `[90,92]` minutes.
- The unique midpoint favorite must have baseline exact `$5` ask VWAP in
  `[.90,.999]`, leader margin at least `.005`, and spread at most `.05`.
- Live target remains `$5`. A fresh full-depth book must support one complete
  `$5` FOK BUY. Partial fills and intermediate notionals are invalid.
- Both accounts use full-holding bid VWAP `.95` FOK TP; if it is not reached,
  the exact token is held to verified one-hot resolution.
- Eco and Fruit are independent replication accounts. This cohort has no
  treatment difference; a new A/B axis requires a later preregistration.
- Old Tick50 entries keep the TP and resolution rules stored when they were
  bought. Old databases are not copied, merged, or backfilled into v2.

## Selection evidence

The verified Gold MLB pin at `20260917T064111Z` contains 210 resolved games.
The `$10` displayed-depth replay tested 847 combinations: tick 20–120 by ten
minutes, TP `.94-.99` or resolution hold, and baseline entry floor
`0/.50/.55/.60/.65/.70/.75/.80/.85/.90/.95`.

The only combination with at least 50 trades and positive net P&L in all five
chronological 42-game blocks was tick90, floor `.90`, TP `.95`:

- 57 trades, displayed principal `$571.45085`.
- net `+$6.84726`, ROI `+1.1982%`.
- five block nets `+$0.12343 / +$1.13077 / +$3.42573 / +$0.99339 / +$1.17394`.
- maximum drawdown `$0.36735`; worst trade `-$0.21781`.
- 52 TP exits and 5 resolution exits.

The previous Tick50 TP `.98/.99` replay remained slightly positive in aggregate
but the fifth chronological block lost `-$38.16220 / -$36.03845`, with maximum
drawdown above `$85`. Aggregate positivity therefore did not justify retaining
the previous parameters.

This is a displayed-book simulation, not a live fill guarantee. White and Gold
showed minute-scale tick0 differences on the newest slate, and one live Seattle
favorite differed from Gold. The v6 live cohort therefore stays at `$5`; `$10`
remains a separate future scaling decision after prospective replication.

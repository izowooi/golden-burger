# Golden Apricot MLB Tick90 / floor .90 / TP .95 / `$15` v8

## Frozen live contract

- Eco and Fruit remain independent-wallet replication arms with identical MLB-only policy.
- The first complete HOME/AWAY common tick is tick 0. Entry uses the first valid tick in `[90,92]` minutes.
- The midpoint favorite must have a baseline exact `$5` ask VWAP in `[.90,.999]`.
- Target notional is `$15`. From one fresh book, submit one full FOK BUY at the largest completely executable rung in `$15/$10/$5`; otherwise submit no order.
- Full-holding bid VWAP `.95` is only an exit candidate. Submit the FOK SELL only when proceeds minus SELL fee exceed confirmed BUY principal plus BUY fee. Otherwise hold for exact token-aligned one-hot resolution.
- No stop or time exit is added. Existing positions retain the parameters stored at their confirmed BUY.
- Per-account experiment capital remains `$100`; total open capacity is six positions, event capacity one, and new positions per cycle five. Confirmed economic-loss guard remains `-$300`.

## Frozen evidence and selection

- Source: verified Gold MLB pin `20260918T093226Z`, SHA-256 `a4b2f4f8b76bb87d22501dd6673a4636e25a2b4fa50b415d91aa01f2980cf543`.
- Quality-eligible resolved events: 161. Incomplete terminal pairs: 60; invalid live observations: 9. No event overlapped the recorded VPN/Gamma exclusion interval.
- Same-game, exact-depth, exact-fee replay of Tick90/floor `.90`/TP `.95` with the net-positive guard produced 48 trades at every tested target `$5/$10/$15/$20/$25`.
- `$15`: full-depth target coverage 48/48; net `+$14.31697`, ROI `+1.9835%`, all five chronological folds positive, validation `+$3.26710`, untouched final split `+$2.04245`, and no negative exit after the net-positive guard.
- `$25` also had full displayed-depth coverage, but the staged scale path is `$10 → $15 → $25`; v8 therefore advances only one step. A later `$25` promotion requires forward confirmed execution under this guard.

Displayed-book replay is counterfactual execution evidence, not an actual fill guarantee. Live performance uses only CONFIRMED fills with fee evidence and exact resolution.

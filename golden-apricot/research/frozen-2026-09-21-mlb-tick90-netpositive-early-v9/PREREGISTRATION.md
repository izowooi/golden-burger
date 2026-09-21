# Golden Apricot MLB earliest net-positive exit v9

- Entry remains MLB-only Tick90 `[90,92]`, baseline `$5` ask VWAP `[.90,.999]`, target `$15`, and discrete `$15/$10/$5` full FOK selection.
- Exit changes from absolute bid `.95` plus net-positive guard to the first fresh full-holding bid at or above `.90` whose proceeds minus SELL fee exceed confirmed BUY principal plus BUY fee. No loss-making TP is allowed.
- If no such exit is confirmed, hold for exact token-aligned one-hot resolution. No stop or time exit is added.
- Existing holdings retain BUY-time parameters. The new policy starts only with the first post-deploy config hash; pre-v9 results are historical cohorts.
- Verified Gold MLB pin `20260918T093226Z` supplied 161 quality-eligible resolved events and 48 current entry signals. At `$15`, all five chronological folds, validation and untouched test were positive for TP floors `.90` through `.95` with the guard.
- `.90` produced `+$7.96472`, zero replay drawdown, 44 TP exits and 4 resolution holds. `.95` produced `+$14.31697`, zero replay drawdown, 42 TP exits and 6 resolution holds. Median TP wait fell from 12.26 to 8.22 minutes. The lower-profit `.90` policy is selected to reduce market exposure.
- This displayed-book replay is not an actual-fill guarantee. Only CONFIRMED fills with fee evidence and exact resolution count as live performance.

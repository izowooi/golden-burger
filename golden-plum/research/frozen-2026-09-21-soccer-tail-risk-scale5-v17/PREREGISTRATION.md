# Golden Plum Soccer tail-risk scale `$5` v17

- King/Queen keep direct six-book entry `[.70,.73]`, one observation, source minute `<60`, TP `.85/.90`, SL `entry-.12`, and minute65 full-position FOK exit.
- Only target notional changes `$10 → $5`; baseline remains exact `$5`, so there is no intermediate rung or partial-fill assumption.
- Confirmed live replay covered King 72 and Queen 68 entries. TP `.78-.90`, early net-positive exit minutes 45-60, forced exits 50-65, stop none/`.30/.40/.50`/`entry-.12`, and entry cutoff/band grids produced no policy positive in train, validation and untouched test for both arms.
- Because no exit or entry parameter passed the stability gate, those parameters remain unchanged. Reducing size is the only supported tail-risk improvement.
- Existing holdings retain BUY-time amount and exits. New `$5` entries begin with the first post-deploy cohort.

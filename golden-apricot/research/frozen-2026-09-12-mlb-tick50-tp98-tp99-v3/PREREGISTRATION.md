# Golden Apricot MLB Tick50 TP98/TP99 A/B v3

- Universe, entry and size remain MLB direct HOME/AWAY only, first common tick + `[50,52]`
  minutes, midpoint favorite, baseline `$5` candidate book and MLB-only `$10` FOK target.
- Eco A exits the full holding at bid VWAP `0.98`; Fruit B exits at bid VWAP `0.99`.
  If the target is not reached, hold to proven resolution. No stop or wall-clock time exit is added.
- The single A/B treatment is the full-holding TP price `.98/.99`.
- The same event-once and 720-hour reentry controls prevent another entry after exit.
- Evidence: 117 resolved MLB games at target `$10`. Eco resolution hold returned `+$31.54061`;
  TP98 returned `+$30.84287` with 52 early exits. Fruit TP99 returned `+$38.70323` with 40 early
  exits. Forced exits at ticks 80-160 were negative for both arms, so no time cutoff is promoted.
- This is displayed-book replay, not confirmed FOK performance. Keep the 20% / `$20` economic
  guard and evaluate the new source/config cohort prospectively.

# Golden Plum soccer minute-75 late-volatility exit v11

- Universe and entry: registered soccer direct HOME/DRAW/AWAY YES/NO six-book only, unique
  midpoint leader, one complete observation, exact `$5` ask VWAP `[0.70,0.73]`.
- Entry is permitted only while the explicit source minute is `<75`.
- King keeps absolute TP `0.90`; Queen keeps absolute TP `0.95`; both keep entry minus `0.15` SL.
- At source minute `>=75`, any remaining holding uses the first complete full-position bid book
  for one FOK time-exit SELL. Partial time exits are forbidden.
- A confirmed time exit ends the event. The existing event-once and 720-hour reentry controls
  prevent another BUY.
- If full depth or FOK confirmation is unavailable, keep the economic exposure and retry; never
  claim that a displayed trigger was a fill. Proven resolution remains the final fallback.
- Evidence: in the 36-event Silver cohort, 1-minute 10pp moves rose from about 1.7% before minute
  60 to 6.27% at minutes 85-90. On the current 14-signal replay, minute75 changed King
  `+$2.17187 -> +$6.13823`, Queen `+$5.00623 -> +$3.90506`, combined
  `+$7.17810 -> +$10.04329`, and reduced stop exits from 10 to 6.
- This is in-sample displayed-book evidence with no actual FOK guarantee. Keep `$5` and evaluate
  the new source/config cohort prospectively.

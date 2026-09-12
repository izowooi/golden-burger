# Golden Watermelon Soccer .91/.92 three-day forward A/B v3h

- Deployment may start at or after `2026-09-12T12:00:00Z`.
- Entry ends at `2026-09-15T12:00:00Z`; follow-up ends at `2026-09-22T12:00:00Z`.
- Soccer Cat changes only its lower entry bound from `.96` to `.91`.
- Soccer Dog changes only its lower entry bound from `.99` to `.92`.
- Both retain upper `.999`, exact baseline `$5`, target `$5`, no TP, effective stop `.70`,
  resolution hold, event-once controls, capacity and the existing economic guard history.
- MLB account children are close-only. Unprepared NFL runtimes are removed from the Cat/Dog account
  runner and remain unscheduled; no other sport inherits the Soccer bounds.
- Exploratory evidence: 40 strict raw soccer games split chronologically 20/20. `.91` produced
  32 signals and +$9.77829 total (+$3.42445/+6.35384 by half); `.92` produced 31 signals and
  +$7.99061 (+$2.31080/+5.67981). This is displayed-book research selected after viewing ten
  thresholds and is not independent confirmation.
- Primary forward evidence is confirmed FOK fill/fee and resolution or confirmed stop P&L. Keep
  `$5`; three elapsed days without enough entries do not authorize scaling.

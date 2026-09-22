# NFL 2026 season-series identity repair

The September 21 Giants–Rams whole-game moneyline was missed by King and Queen because Gamma reported `sport.series=12185` and exact event series `nfl-2026`/`12185`, while the classifier accepted only legacy NFL root `10187`. The live sweeps rejected every returned NFL market with `SPORT_SERIES_ID_MISMATCH`.

Accept `12185` only when sport family is NFL and the event has exactly the `nfl-2026` season slug and sole series ID `12185`. Existing sport ID/name/tag, team league, source-time, top-level two-team moneyline, fee, and live/closed gates remain unchanged. Preserve legacy root `10187`. This is a discovery bug fix, not a parameter or notional change. King/Queen remain `$5`, entry `[.70,.73]`, TP `.85/.90`, SL `entry-.12`, no NFL time exit. No missed historical trade is fabricated.

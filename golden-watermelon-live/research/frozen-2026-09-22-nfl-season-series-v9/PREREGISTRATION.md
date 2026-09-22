# Cat/Dog NFL 2026 season-series identity repair

The September 21 Giants–Rams whole-game moneyline was missed by Cat and Dog because Gamma reported `sport.series=12185` and exact event series `nfl-2026`/`12185`, while the classifier accepted only legacy NFL root `10187`. Live sweeps rejected all returned NFL markets with `SPORT_SERIES_ID_MISMATCH`.

Accept `12185` only for NFL with exact `nfl-2026` season slug and sole series ID `12185`; preserve legacy root `10187`. Sport ID/name/tag, team league, top-level direct whole-game moneyline, liquidity, price, fee, and resolution gates remain unchanged. Cat/Dog NFL retain `$5`, entry `.91/.94`, stop `.70`, no TP, exact resolution hold. Soccer `$5` and MLB close-only are unchanged. No missed historical trade is fabricated.

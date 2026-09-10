# Golden Apricot MLB Tick50 A/B v1

- Universe: MLB whole-game direct HOME/AWAY two-team moneyline only.
- First durable common valid HOME/AWAY snapshot is tick0. Enter once in `[50,51]` elapsed minutes.
- Buy the higher midpoint side using exact $5 full-depth ask FOK. No probability band.
- Eco A `apricot-live-eco-mlb-tick50-hold-v1`: resolution hold, no TP/SL.
- Fruit B `apricot-live-fruit-mlb-tick50-tp99-v1`: full bid VWAP 0.99 TP, otherwise resolution.
- Event/account limits and confirmed fill/fee reconciliation inherit the audited Apricot runtime.
- Evidence: 112 historical events, A +5.83%, B +6.37%; chronological halves both positive.
- This is small-live exploratory evidence, not scale approval. Re-estimate only after 200 resolved games.

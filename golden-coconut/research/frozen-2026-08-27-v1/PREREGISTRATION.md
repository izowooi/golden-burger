# Golden Coconut v1 Preregistration

Freeze date: 2026-08-27 UTC. Runtime: `coconut-major-sports-5m-v1`.

Primary objective is prospective evidence health for five equal macro families: soccer, MLB, NBA, NFL,
and NHL. Each family has its own cursor-complete Gamma keyset sweep. No liquidity or volume discovery
gate is permitted.

Eligible markets are in-play, top-level, whole-game `moneyline` only. Soccer uses exact result-specific
Yes/No negRisk markets from the frozen domestic/UCL/UEL registry. US sports use exact official major
identity and direct two-team non-negRisk markets. Official major-league preseason is eligible only in the
separate `PRESEASON` stratum. It is never pooled with `REGULAR`, `POSTSEASON`, or `UNKNOWN`. Minor leagues,
G League, AHL, ECHL, NCAA, e-sports, child/period/spread/total/prop/future/advancement are excluded.

The executable notional ladder is 5, 10, 25, 50, 100, 250, and 500 USDC. The threshold vector is 0.75
through 0.99 by 0.01. First observation above a threshold is left-censored. A possible crossing after more
than 450 seconds or without a prior full observation is gap-censored. Neither creates an episode. Only a
genuine prior-below/current-at-or-above crossing inside 450 seconds creates one episode.

The first review reports collection health, sport-equal coverage, phase-separated liquidity/volume/depth
and threshold strata, game clustering, storage, and missing-sport nulls. It cannot select a best threshold,
sport, phase, or claim profitability. Displayed books are not fills.

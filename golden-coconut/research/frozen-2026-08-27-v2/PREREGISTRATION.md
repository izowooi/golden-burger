# Golden Coconut v2 Lifecycle Preregistration

Freeze date: 2026-08-27 UTC. Runtime: `coconut-major-sports-lifecycle-5m-v2`.

The primary objective is prospective lifecycle and executable-depth evidence health for soccer, MLB,
NBA, NFL, and NHL. The v1 files and databases are immutable historical evidence and are never an input
to v2 runtime state.

## Discovery and follow-up

Each five-minute slot performs one terminal-cursor keyset sweep per family with no liquidity, volume,
probability, or live-state discovery gate. The request window is the UTC half-open evidence envelope
from slot minus 24 hours through slot plus 48 hours, serialized as exact `start_date_min` and
`start_date_max` parameters. Child events are excluded server-side and client-side.

Accepted games are followed by immutable event ID and canonical event slug after they leave the
discovery window or become closed. The lifecycle vocabulary is `DISCOVERED_OPEN`, `PREGAME`,
`IN_PLAY`, `ENDED`, `POSTPONED`, `CANCELLED`, `RESOLVED`, `VOID`, and `TIE`. Only explicit Gamma,
Sports WSS, or CLOB resolution evidence may advance a state. Postponement and schedule revision do not
erase earlier schedules. A missing WSS message cannot create an absent or terminal state.

## Frozen population and measurements

Eligible competition and whole-game moneyline identity is unchanged from the five-family v1 registry.
Official major-league preseason is eligible only in the separate `PRESEASON` stratum. Minor leagues,
G League, AHL, ECHL, NCAA, e-sports, child events, and period/spread/total/prop/future/advancement
markets are excluded.

Pregame and in-play observations are never pooled. Scheduled-time distance is permitted only for
pregame anchor selection; it is not match elapsed time. The analyzer selects the closest available
T-24h and T-60m observation and the final observation before the then-observed scheduled start, and
reports anchor error/missingness.

The executable notional ladder is 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 250, 500, 750,
and 1,000 USDC. Every notional independently
uses thresholds 0.75 through 0.99 by 0.01 and the same 450-second crossing continuity rule.
`LEFT_CENSORED` and `GAP_CENSORED` are not episodes. One canonical book supplies all notional walks;
notional/threshold episodes from the same game are correlated measurements, not independent N.

## Health gates

The first decision gate requires all of the following:

1. one config hash × strategy source digest × mode × runtime job cohort;
2. only uniquely successful, five-family cursor-complete cycles in estimands;
3. no CRITICAL/HIGH issue in selected cycles and `PRAGMA quick_check=ok` for every shard;
4. at least seven distinct UTC collection dates;
5. at least one unique canonical game in every sport family;
6. complete notional-vector rows for every observed canonical book;
7. explicit lifecycle coverage through a terminal state for discovered ended games, with schedule
   revisions and WSS/Gamma source provenance retained;
8. T-24h, T-60m, and last-prestart anchor coverage reported as measured missingness, never imputed.

Until all gates pass, threshold/sport/notional selection and profit are `null`. After health gates pass,
any selection or profitability analysis requires a separately frozen prospective analysis plan. This
epoch is accountless displayed-book research and can never be interpreted as actual fills or realized
P&L.

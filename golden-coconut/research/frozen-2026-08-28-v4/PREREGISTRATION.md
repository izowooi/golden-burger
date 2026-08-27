# Golden Coconut v4 Competition-Bounded Lifecycle Preregistration

Freeze time: 2026-08-27T16:20:00Z. Runtime: `coconut-major-sports-lifecycle-5m-v4`.

The objective is prospective displayed-book research and collection health for
soccer, MLB, NBA, NFL, and NHL whole-game moneyline markets. V1 through v3 runtimes,
databases, and frozen files are immutable historical evidence and are never imported into v4.

V3 correctly changed Gamma discovery from event creation time to scheduled game time, but its
single broad soccer tag returned 2,030 source events over 21 pages for one 72-hour window. The
20-page cap therefore produced a cursor-incomplete failed cycle before book collection. A read-only
comparison on the same source window found that the eight frozen competition tags returned 230
unique events over eight terminal-cursor pages and contained all 30 soccer event IDs accepted by
the broad v3 sweep. V3 is an invalid collection cohort and is not used for inference.

## Discovery and schedule boundary

Each five-minute slot performs one logical sweep per family. MLB, NBA, NFL, and NHL each use their
single frozen family tag. Soccer fans out to the frozen EPL, Bundesliga, Ligue 1, LaLiga, MLS,
Serie A, UEFA Champions League, and UEFA Europa League tag IDs. Every physical query uses
`closed=false`, `include_children=false`, `related_tags=false`,
`start_time_min=slot-24h`, and `start_time_max=slot+48h`; liquidity, volume, probability, and live
state are not discovery gates. All query-tag cursors must terminate inside the family page budget.

The server response is independently revalidated against the UTC half-open scheduled-start
interval. Missing, malformed, or out-of-window new events are retained as raw evidence and
rejected. Previously tracked games remain follow-up eligible by immutable event ID and canonical
slug even after leaving the discovery interval. Duplicate event IDs across query tags are retained
in their raw pages but normalized only once per family cycle.

## Lifecycle and book strata

Lifecycle states remain `DISCOVERED_OPEN`, `PREGAME`, `IN_PLAY`, `ENDED`, `POSTPONED`, `CANCELLED`,
`RESOLVED`, `VOID`, and `TIE`. Only explicit Gamma, Sports WSS, or CLOB evidence advances state.
Wall time and WSS no-message never infer match elapsed time or terminal state.

Accepted `DISCOVERED_OPEN` games keep that explicit unknown-phase label while their eligible books,
notional ladder, and threshold vectors are collected. They are never pooled with `PREGAME` or
`IN_PLAY`. A future scheduled start may create `PRESTART_CANDIDATE` anchor evidence without changing
the lifecycle label.

## Frozen population and measurements

Official major-league preseason remains a separate `PRESEASON` stratum. Minor leagues, G League,
AHL, ECHL, NCAA, e-sports, child events, and period/spread/total/prop/future/advancement markets are
excluded after raw collection.

The executable notional ladder is 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 250, 500, 750, and
1,000 USDC. Every notional independently evaluates thresholds 0.75 through 0.99 by 0.01 under the
same 450-second continuity rule. `LEFT_CENSORED` and `GAP_CENSORED` are not episodes. Displayed-book
walks are neither fills nor executable guarantees.

## Health gates

The first decision gate requires one cohort; unique successful five-family cursor-complete cycles;
all frozen query tags accounted; no CRITICAL/HIGH issue; `PRAGMA quick_check=ok`; at least seven
distinct UTC dates; at least one canonical game in every family; schedule-window accounting;
nonzero eligible book and notional-vector coverage where open markets exist; explicit
lifecycle/follow-up provenance; and measured prestart/terminal missingness. Until all gates pass,
profitability, best sport, best threshold, best notional, live promotion, and trade-size
recommendation remain `null`.

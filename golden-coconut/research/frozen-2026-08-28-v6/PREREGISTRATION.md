# Golden Coconut v6 Production-Identity Lifecycle Preregistration

Freeze time: 2026-08-27T17:10:00Z. Runtime: `coconut-major-sports-lifecycle-5m-v6`.

The objective is prospective displayed-book research and collection health for
soccer, MLB, NBA, NFL, and NHL whole-game moneyline markets. V1 through v5 runtimes,
databases, and frozen files are immutable historical evidence and are never imported into v6.

V3 correctly changed Gamma discovery from event creation time to scheduled game time, but its
single broad soccer tag returned 2,030 source events over 21 pages for one 72-hour window. The
20-page cap therefore produced a cursor-incomplete failed cycle before book collection. A read-only
comparison on the same source window found that the eight frozen competition tags returned 230
unique events over eight terminal-cursor pages and contained all 30 soccer event IDs accepted by
the broad v3 sweep. V3 is an invalid collection cohort and is not used for inference.

V4 completed discovery and book collection, but its US event-series contract incorrectly required
the event series ID to equal `sport.series`. A public source probe showed real 2026 NFL games use
sport root `10187` while their season series is `12185 / nfl-2026`; all 14 NFL games were therefore
false HIGH drift. The same v4 run also rejected every production draw descriptor because Gamma uses
`Draw (<exact event title>)`, while the parser allowed only bare `Draw`. V4 remains an immutable
invalid collection cohort. V5 correctly froze semantic root-or-season event-series identity and
exact production draw descriptors. Its first two published cycles were healthy, but two subsequent
natural cycles exposed a transport flaw: one successful Gamma body could stream for 58.8 seconds
even though the per-read timeout was 12 seconds. End-to-end receipt skew reached 96.1 seconds and
then exceeded the frozen 90-second boundary again. Both cycles were correctly failed and never
entered the estimand, but the unbounded total response time wasted most of a collection slot. V5
remains immutable. V6 retains the same universe and estimand while adding a 15-second total
wall-clock boundary per HTTP attempt, 5-second socket-read timeout, bounded retries,
partial-response receipts, and mandatory response close.

## Discovery and schedule boundary

Each five-minute slot performs one logical sweep per family. MLB, NBA, NFL, and NHL each use their
single frozen family tag. Soccer fans out to the frozen EPL, Bundesliga, Ligue 1, LaLiga, MLS,
Serie A, UEFA Champions League, and UEFA Europa League tag IDs. Every physical query uses
`closed=false`, `include_children=false`, `related_tags=false`,
`start_time_min=slot-24h`, and `start_time_max=slot+48h`; liquidity, volume, probability, and live
state are not discovery gates. All query-tag cursors must terminate inside the family page budget.

Each public HTTP attempt is streamed under both the socket connect/read timeout and a separate
15-second total wall-clock boundary. A response that keeps sending small chunks cannot extend an
attempt indefinitely. Partial bytes and the timeout type are recorded, the response is closed, and
at most two bounded retries are allowed. An exhausted physical page still fails its logical family
sweep; no missing page is imputed and no partial cycle is published as `SUCCEEDED`.

The server response is independently revalidated against the UTC half-open scheduled-start
interval. Missing, malformed, or out-of-window new events are retained as raw evidence and
rejected. Previously tracked games remain follow-up eligible by immutable event ID and canonical
slug even after leaving the discovery interval. Duplicate event IDs across query tags are retained
in their raw pages but normalized only once per family cycle.

US family identity requires the exact frozen sport object/root/tags, two teams whose league equals
the family, and exactly one semantic event series. A root series must use the frozen root ID/name.
A season series may use a distinct positive numeric ID, but slug/ticker/title/type/recurrence and
its year relative to scheduled start must match the frozen policy. Soccer draw descriptors allow
only bare `Draw`/`Tie` or the exact canonical event title in parentheses.

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

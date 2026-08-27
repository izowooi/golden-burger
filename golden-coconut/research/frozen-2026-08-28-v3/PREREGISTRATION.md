# Golden Coconut v3 Schedule-Bounded Lifecycle Preregistration

Freeze date: 2026-08-28 UTC. Runtime: `coconut-major-sports-lifecycle-5m-v3`.

The objective is prospective displayed-book research and collection health for
soccer, MLB, NBA, NFL, and NHL whole-game moneyline markets. The v1 and v2 runtimes, databases, and
frozen files are immutable historical evidence and are never imported into v3 runtime state. V2 is
not an analysis cohort: its Gamma
request bounded event creation time (`start_date_*`) rather than scheduled game time and then
excluded every `DISCOVERED_OPEN` market from book collection.

## Discovery and schedule boundary

Each five-minute slot performs one terminal-cursor Gamma `/events/keyset` sweep per family using
`closed=false`, `include_children=false`, `related_tags=false`, and the official sports schedule
parameters `start_time_min=slot-24h` and `start_time_max=slot+48h`. Liquidity, volume, probability,
and live state are not discovery gates.

The server response is never trusted as the sole boundary. Every newly discovered event must have a
parseable canonical scheduled start and must independently satisfy the UTC half-open interval
`[start_time_min, start_time_max)`. Missing, malformed, or out-of-window schedule evidence is retained
in the raw page and normalized event row but classified as rejected. A previously tracked event may
reappear outside the current window and remains follow-up eligible by immutable event ID and canonical
slug.

## Lifecycle and book strata

Lifecycle states remain `DISCOVERED_OPEN`, `PREGAME`, `IN_PLAY`, `ENDED`, `POSTPONED`, `CANCELLED`,
`RESOLVED`, `VOID`, and `TIE`. Only explicit Gamma, Sports WSS, or CLOB evidence assigns or advances a
state. Wall time and WSS no-message never create match elapsed time, an in-play state, or a terminal
state.

An accepted `DISCOVERED_OPEN` game is an explicit unknown-phase stratum, not an absence. Its eligible
whole-game moneyline books, notional ladder, and threshold vectors are collected and remain labeled
`DISCOVERED_OPEN`; they are never pooled with `PREGAME` or `IN_PLAY`. When the source provides a valid
future scheduled start, a displayed-book observation may be retained as `PRESTART_CANDIDATE` anchor
evidence without relabeling lifecycle. Known `IN_PLAY` or terminal states cannot create prestart
anchors.

## Frozen population and measurements

The exact five-family registry and major-competition identity rules are copied into this v3 frozen
directory. Official major-league preseason remains a separate `PRESEASON` stratum. Minor leagues,
G League, AHL, ECHL, NCAA, e-sports, child events, and period/spread/total/prop/future/advancement
markets remain excluded.

The executable notional ladder is 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 250, 500, 750, and
1,000 USDC. Every notional independently evaluates thresholds 0.75 through 0.99 by 0.01 under the
same 450-second continuity rule. `LEFT_CENSORED` and `GAP_CENSORED` are not episodes. A canonical
displayed book and its walks are neither fills nor executable guarantees.

## Health gates

The first decision gate requires one cohort; unique successful five-family cursor-complete cycles;
no CRITICAL/HIGH issue; `PRAGMA quick_check=ok`; at least seven distinct UTC dates; at least one
canonical game in every family; schedule-window rejection accounting; nonzero eligible book and
notional-vector coverage where open markets exist; explicit lifecycle/follow-up provenance; and
measured prestart/terminal missingness. Until all gates pass, profitability, best sport, best
threshold, best notional, live promotion, and trade-size recommendation remain `null`.

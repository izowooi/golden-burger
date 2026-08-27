# Golden Coconut v7 Parallel Acquisition Preregistration

Freeze time: 2026-08-27T17:28:00Z. Runtime: `coconut-major-sports-lifecycle-5m-v7`.

The objective remains prospective displayed-book collection health for soccer, MLB, NBA, NFL,
and NHL whole-game moneyline markets. V1 through v6 runtimes and databases are immutable historical
evidence and are never imported into v7. Profitability and parameter selection remain out of scope
until the seven-UTC-date health gate passes.

V6 bounded each HTTP attempt with a 15-second wall-clock target and correctly recorded partial
response receipts. Its first production cycle nevertheless failed the frozen 90-second global
receipt-skew gate. The individual requests were bounded, but the five family sweeps were still
executed sequentially. Gamma discovery ran from 17:23:54Z through 17:25:13Z, then Sports WSS and
CLOB evidence extended the final receipt to 17:25:43Z: approximately 109 seconds end to end. The
failed cycle opened no episode and remains evidence of an acquisition-scheduling defect.

V7 changes only acquisition scheduling. The v6 universe, classifier, schema, thresholds, notional
ladder, lifecycle rules, source endpoints, retry limits, 90-second global skew gate, and estimand are
unchanged. The exact immutable v6 `SPORTS_REGISTRY.json` is reused by SHA-256.

## Parallel family acquisition

Each five-minute slot starts exactly five isolated Gamma workers: soccer, MLB, NBA, NFL, and NHL.
Every worker owns a separate credential-free HTTP session and performs the same frozen family
keyset sweep. Soccer still fans out sequentially inside its worker across EPL, Bundesliga, Ligue 1,
LaLiga, MLS, Serie A, UEFA Champions League, and UEFA Europa League query tags. The four US workers
each use their single frozen family tag.

All five futures must complete successfully. A missing worker, duplicate family, cursor failure,
transport exhaustion, or exception fails the cycle; no partial family census is published as
`SUCCEEDED`. Results are normalized and published in the frozen family order
`soccer, mlb, nba, nfl, nhl`, independent of thread completion order. Each physical request keeps its
own durable attempt receipt. Concurrent receipt inserts use independent SQLite connections under
the existing busy timeout and append-only contract.

The request envelope remains `closed=false`, `include_children=false`, `related_tags=false`,
`start_time_min=slot-24h`, and `start_time_max=slot+48h`, with no liquidity, volume, probability, or
live-state discovery gate. Each HTTP attempt retains the 3-second connect timeout, 5-second socket
read timeout, 15-second total wall-clock target, at most two retries, partial-byte receipt, and
mandatory response close.

## Downstream collection and health gates

Only after all five family sweeps and tracked-game follow-ups are complete does the cycle collect
Sports WSS, same-cycle Gamma fallback, batched public CLOB books, public fee evidence, and due
resolution evidence. The global max-min receipt skew must remain at or below 90 seconds. Exceeding
it records a CRITICAL issue, suppresses new threshold episodes, atomically publishes the failed
health evidence, and fails Jenkins.

The first decision gate still requires one cohort; unique successful five-family cursor-complete
cycles; every frozen query tag accounted; no CRITICAL/HIGH issue; `PRAGMA quick_check=ok`; at least
seven distinct UTC dates; at least one canonical game in every family; complete schedule-window
accounting; nonzero eligible book/vector coverage where open markets exist; lifecycle/follow-up
provenance; and measured prestart/terminal missingness. Until then profitability, best family,
threshold, notional, live promotion, and trade-size recommendation are `null`.

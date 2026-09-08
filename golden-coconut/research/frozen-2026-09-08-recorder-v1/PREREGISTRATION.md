# Sports price recorder one-minute v1

New accountless recorder epoch. Existing v7 code, manifests, runtime and databases are unchanged.
Registered runtime: coconut-sports-recorder-1m-v1. Proposed Jenkins: polybot-white, after protected
Watermelon follow-up completion. New source/config is independently hashed and stored per claimed
UTC minute at fixed phase 30 seconds with no shell wait (observed Jenkins startup jitter crosses :00). Jitter uses the claimed slot, not invocation+60s.
The physical shard date is the **claimed slot UTC date**, not the invocation or HTTP
receipt date. An unclaimed 00:00:03 invocation belongs to the prior date's23:59:30 slot.
Receipt/publication may cross midnight. Duplicate prior slots do not rotate or issue HTTP. No credentials, wallets, orders, strategies, positions or P&L are implemented.

Five frozen major families and exact competitions reuse the v7 registry. Soccer records all six
real Yes/No token books; MLB/NBA/NFL/NHL record two real team tokens. No complement prices are
synthesized. Gamma probabilities, liquidity, volume and accepting flags do not gate raw book
preservation. Child/prop/future/non-major identity remains excluded. A full soccer triad and exact
token alignment are required; otherwise missing expected slots remain explicit.

Discovery is an ungated calendar sweep over scheduled slot−24h to slot+48h, every five minutes,
with complete frozen-family cursors, 20 events per physical page, at most 20
physical pages per family and a 32 MiB response cap. A cap is incomplete, never
cursor-complete; the universe is unchanged when page size changes. Between discoveries, indexed active obligations are refreshed
by exact event ID. Minute book targets begin at the observed scheduled start−600s and continue
through an authoritative end+600s. Missing source end means continue, not predicted duration.
Without an actual source end timestamp, the first explicit ended receipt is labelled an upper
bound and used conservatively. Gamma endDate and wall-clock estimates never stand in for actual
end. Schedule revisions and native clock evidence are retained. After the book window, terminal
metadata continues at five-minute cadence; source absence does not erase the obligation.

Request startup ends at 42s and the cycle target is 50s, shortened by the remaining
fixed slot when invoked late. Deferred groups retain due priority into the next cycle;
transport batches preserve that same event order and never split a 6/2 event group. HTTP/body/WSS bounds are cooperative;
late or incomplete work remains FAILED with raw receipts/attempts rather than a success claim.
All known expected slots are retained, including empty, missing, malformed and deferred cases.
All source price/size strings and fee metadata are preserved; an absent fee is unknown, not zero.
Sports WSS is a bounded observation window, not a full continuous game feed. Clock/score fields
remain sport-native. Repeated WSS bytes retain their exact reception ordinal, not a
hash-based deduplication that could erase repeated observations. Explicit team ordering alone establishes HOME/AWAY; unknown roles remain
unknown while original labels and real token identities are retained.

UTC daily SQLite shards are create-only and append-only, except their indexed working registry.
Rotation carries only unresolved working state with exact source-state hashes and archive linkage;
it never merges historical prices. Source SHA and integrity verification use daily-rsync before
research. A run's observations, final working state and SUCCEEDED/FAILED publication are atomic.
No per-minute historical aggregate scans, VACUUM, full quick_check, or strategy replay occurs.

Normalized read-only exports retain source/cohort/run/event/condition/token identity, actual HTTP
request/receipt time, full book, raw fee, clock, phase, terminal proof and every failure reason.
Offline hypothetical A/B calculations are not actual fills or profits. Guava provider/news and
short market-stream evidence remain distinct; a book predating news cannot become a post-news
H5 opportunity. Public probes use a separate local directory and observation_mode=PROBE in the
config hash. They may request one valid event per family outside the scheduled book
window, labelled PROBE_OUTSIDE_WINDOW. Production selection remains unchanged;
probe data are never a production collection-health or strategy cohort. The exporter
independently verifies raw HTTP SHA, status 200, request kind, exact identity and
receipt ordering; cutoff excludes terminal proof published after the requested range.

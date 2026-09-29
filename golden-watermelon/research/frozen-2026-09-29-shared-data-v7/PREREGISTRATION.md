# Watermelon shared public-data storage source epoch v7

2026-09-29 storage amendment to the immutable
`frozen-2026-09-08-strict-raw-followup-v4b-r6` protocol. This is a software source
cohort transition, not a new selection experiment or an extension of its window.
The predecessor PREREGISTRATION.md and MANIFEST.sha256 remain byte-for-byte
unchanged and are inputs to this epoch's source manifest.

Preserve the existing data contract
`watermelon-five-major-sports-inplay-match-winner-v6`, schema profile
`golden-watermelon-v4b-schema-v1`, universe/classifier, runtime job names, exact
sport/token identity, request/cycle deadlines, cadence, original entry/follow-up
windows, entry/stop/notional grids, source clocks and terminal-publication rules.
`config.yaml`, market classification and the trading/replay algorithms are not
changed by this storage amendment. Data remains accountless displayed-book
research evidence and never becomes an actual order or confirmed fill.

Only public source-body storage and public price-level storage change. Exact
public bodies are committed through the shared writer before a local reference
can be published. Public level values are stored as shared arrays, with source
snapshot/level identities retained locally. Readers restore source bodies and
levels for the existing replay queries. Configs, experiment decisions, derived
VWAP/capacity, fee calculations, clock interpretation and economic attribution
remain source-owned. Mixed envelopes that contain decisions remain local until
an explicit projection/reassembly contract is implemented.

The source digest includes the complete declared shared market-data runtime
closure through `market_data_source_digest.update_digest`. Missing shared code
is a failure; a shared runtime change produces a different source cohort.
Preregistration SHA, source digest and resolved config provenance must be recorded
for the new deployment. Do not relabel prior r6 runs with the new identity or
merge cohorts for research conclusions.

The logical-schema and migration rules are in STORAGE_CONTRACT.md. Historical
database conversion creates a verified derivative and retains the original
source; this protocol document does not itself perform or certify that migration.
Before enabling a migrated runtime, verify its original source checksum, exact
payload closure, reference completeness, level identities, private-row equality,
concurrent writer acknowledgements, reader replay and restart behavior. Configure
`PUBLIC_MARKET_DATA_REQUIRED=1` for converted writers. Missing shared storage must
not cause inline or internal-disk fallback.

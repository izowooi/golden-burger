# Sports recorder v4 — independent White/Silver replication

## Trigger

Three strategy-specific simulation jobs repeatedly fetched overlapping soccer and US-sports books.
The integrated recorder already preserves the six-outcome soccer books, direct two-team US books,
source clock, terminal evidence, and the complete `$5–$1000` depth needed for offline strategy replay.

## Frozen change

- `polybot-white/coconut-sports-recorder-1m-v1` remains the primary integrated recorder.
- `polybot-silver/coconut-sports-recorder-silver-1m-v1` runs the identical collection contract into
  a separate create-only database and UTC shards.
- Runtime/job/database identity is the only treatment difference. Universe, family workers, request
  envelopes, slot phase, clock, book, fee, terminal, retry, JSON bounds, and publication logic match.
- Full discovery is aligned to deterministic UTC five-minute slot boundaries in both replicas. A
  failed scheduled discovery remains due on the next minute instead of waiting five more minutes.
- Cron starts fluctuate across second `:00`, so both replicas keep phase `:30`: starts at `:55` and
  `:02` map to the same slot. The 50-second hard budget now starts at process execution and is not
  shortened to the next slot boundary. The response attempt wall limit is 15 seconds; the 42-second
  request boundary and at-most-two retries still cap total work.
- The old `polybot-silver/plum-shadow-silver-1m-v1` database is an immutable historical epoch. It is
  never migrated or merged into either integrated recorder.
- Golden Peach Grey remains retired after its strategy-specific simulation positions reached zero.

## Cross-validation

Pair rows by UTC slot, family, event, result role, token, and receipt time. Report population coverage,
book availability, midpoint/VWAP differences, terminal agreement, and one-sided gaps. Agreement between
two copies detects timing and transport variance; it does not prove correctness against a shared-code bug.

## Transition gate

Silver may start only after its prior Plum simulation DB has no holding, pending, or quarantined rows.
Both jobs must retain separate exact workspace markers and pass the same external APFS/storage checks.

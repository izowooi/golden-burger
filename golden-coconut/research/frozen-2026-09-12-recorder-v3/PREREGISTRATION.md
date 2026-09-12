# Sports recorder v3 — transient family retry repair

## Trigger

The scheduled one-minute recorder intermittently failed a complete five-family cycle when one
independent Gamma family request raised a transient public API error. Successful family receipts
were preserved, but the strict all-family census correctly remained unusable.

## Frozen change

- Each isolated family transport retries a retryable Gamma/CLOB failure at most twice.
- The JSON structural node ceiling is `1,000,000` instead of `500,000`. The observed NFL page
  contained 671,656 nodes inside a valid 25.2 MB body; the 32 MB byte cap and depth-30 cap remain.
- Retry waits remain bounded to 0.25–1 second and each attempt retains the existing 10-second wall
  limit inside the 42-second request budget and 50-second hard cycle limit.
- Every failed attempt keeps its own append-only receipt. A family remains incomplete after the
  final attempt, so the all-family cycle still fails closed.
- Page size 100, maximum 20 pages per family, five isolated workers, registry, universe, books,
  lifecycle, schema, storage guards, runtime name, and database path remain unchanged.
- Existing v1/v2 rows and UTC shards are not rewritten. The repaired code starts a new config hash
  and strategy source digest in the same create-only recorder runtime.

## Gate

Natural one-minute builds must finish successfully with all five family cursors complete. A retry
is healthy only when its individual failed receipt and the eventual successful receipt are both
durable; retry exhaustion must remain a failed cycle.

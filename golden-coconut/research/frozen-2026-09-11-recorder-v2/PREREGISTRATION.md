# Sports recorder v2 — cursor completion repair

## Trigger

The 2026-09-11 NFL opening audit found that the integrated recorder preserved NFL books but
failed every strict cycle because the soccer census exhausted 20 pages of only 20 events each.
The source API supports a larger keyset page, and the scheduled `[-24h,+48h]` envelope still
applies server-side and is revalidated locally.

## Frozen change

- `gamma_page_size`: `20 -> 100`
- `max_pages_per_family`: remains `20`
- five isolated family workers, exact registry, cursor-complete requirement, 50-second hard
  deadline, 42-second request budget, raw receipts, book limits, lifecycle, and storage guards
  remain unchanged.
- Runtime and append-only DB remain `coconut-sports-recorder-1m-v1`; each cycle stores the new
  config hash and source digest. Historical v1 rows and UTC shards are not rewritten.

## Gate

The deployment is healthy only after natural one-minute cycles record `SUCCEEDED`, all five
family sweeps are cursor-complete, and NFL remains independently represented. Component rows
inside a failed cycle do not satisfy this gate.

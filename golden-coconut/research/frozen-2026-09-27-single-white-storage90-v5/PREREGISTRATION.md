# Single White five-sport recorder / storage90 v5

User authorization: 2026-09-27, consolidate White/Silver and raise used-ratio stop from80% to90%.

- White remains the sole full recorder for Soccer/MLB/NBA/NFL/NHL, same registry/universe, 60s cadence, phase30, book/fee/clock/terminal receipt contract, limits and writer lock.
- Silver Jenkins is disabled and its timer removed. Keep its independent historical DB/log/shards; do not merge, delete or relabel their source/cohort identity. Registered historical runtime remains readable.
- Used-ratio stop is >=.90. Absolute reserve is50GiB; the former150GiB reserve would stop this~931GiB volume near84%, defeating the authorized90% operating threshold. Mount UUID/device/marker/APFS, symlink checks and local daily-rsync50GiB floor remain enforced.
- Same runtime/schema/data_contract continues append-only, with new config_hash/source digest recorded on each cycle. This is an operational source epoch, not a change to prices/strategy bets or a DB migration.
- White is the default dashboard/report raw source. Historical Silver remains explicitly available; absence of new Silver cycles is intentional retirement, not a collector outage. Use public schedule/receipt/schema integrity and independently verified Gold/live evidence where appropriate; identical replicas were not protection against a shared-code/shared-host failure.
- No trading job, order, amount, TP/SL or wallet changes.

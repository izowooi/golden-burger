# White recorder v2 — UEFA Nations League

This prospective accountless epoch starts on 2026-09-28 UTC. The prior
`coconut-sports-recorder-1m-v1` daily shards remain immutable. The new
`polybot-white/coconut-sports-recorder-1m-v2` runtime uses a create-only DB and
UTC daily shards. It keeps the same one-minute slot, five sport families,
50 GiB free-space floor, 90% used-ratio ceiling, strict full-book/clock/terminal
evidence, and never submits an order.

The only population change adds Polymarket tag `100816` to the Soccer query
fan-out. A UEFA Nations League event is accepted only with sport ID `297`, code
`unl`, series `11446`/`soccer-unl`, two `unl` teams, exact
`unl-<home>-<away>-YYYY-MM-DD` root slug, official `www.uefa.com` resolution
host, and the six regular-time HOME/DRAW/AWAY Yes/No books. Group winner,
exact-score, half, total, spread, prop, child, and futures markets remain
excluded. No earlier rows are backfilled or relabeled.

The v2 registry is frozen in `SPORTS_REGISTRY.json`; its SHA-256 is embedded in
`recorder_config.py`. A new strategy source digest and runtime identify this
population; old and new shards must never be counted as independent trades.

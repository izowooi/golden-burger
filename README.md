# polylab — Polymarket sports calibration lab

Research code for a study of **in-play mispricing in sports prediction markets**:

- How far do Polymarket prices for major sports (soccer, MLB, NBA, NFL, NHL) deviate from realised
  outcome frequencies, as a function of **game time** (calibration by game phase)?
- How does the market's **sensitivity to the same event** (a goal / score change) grow as the game progresses,
  and does the price over- or under-react (event study with 1/5/10-minute reversion)?
- Can simple strategies that exploit these biases earn stable profit, and at which stake size
  (5 → 10 → 25 → 50 → 100 USDC) is performance most stable?

The system runs unattended on a single Mac mini: a 1-minute collector writes one central SQLite dataset,
strategy variants trade (or paper-trade) against it, and an AI retro loop reviews results daily/weekly/monthly,
adjusts parameters inside deterministic guardrails, and publishes reports.

- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · diagrams [`docs/architecture/`](docs/architecture/README.md) · AI loop [`ai-automation.md`](docs/architecture/ai-automation.md)
- Strategy variants: [`strategies/`](strategies/) · logic in [`src/polylab/strategies`](src/polylab/strategies)
- Automated reports: [`reports/`](reports/) · live dashboard: https://poly.zowoo.uk
- API sources and field provenance: [`docs/research/api-sources.md`](docs/research/api-sources.md)

## Quick start

```bash
uv sync --extra dev
uv run pytest -q
uv run polylab --help
```

Runtime data lives outside the repository (`POLYLAB_ROOT`, default `/Volumes/t7/polylab`).
No credentials are stored in this repository.

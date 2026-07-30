# Golden Kiwi / Micro-Cascade — frozen research protocol

Frozen before inspecting any arm-level forward return or executable markout.

## Evidence and clock

- Primary database:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-honeydew/runtime/default/databases/latest/trades.db`
- Deployment identity: `macmini-m5 / polybot-bear / golden-honeydew / default`.
- Artifact SHA-256:
  `f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df`.
- Source cutoff: `2026-07-28T15:42:05.414525Z`.
- Local sync cutoff: `2026-07-30T12:13:51.689096Z`; latest sync finished
  `2026-07-30T12:14:27.341243Z`.
- `daily-rsync verify`: `SUCCESS`, checked 3,816, failed 0,
  `skipped_retention_deleted=0`.
- `compact-v1` snapshot anchor: `2026-07-28 15:21:43`; hot window 24 h.
- Full-cadence research range:
  `[2026-07-27T15:45:00Z, 2026-07-28T15:30:00Z)`.
- Mechanics/calibration signal range:
  `[2026-07-27T15:45:00Z, 2026-07-28T00:00:00Z)`.
- Temporal OOS signal range:
  `[2026-07-28T00:00:00Z, 2026-07-28T14:15:00Z)`.
- A 60-minute exit may use the first observation in
  `[signal+60m, signal+75m]`; this keeps all OOS exits before 15:30Z.

The mechanics range may be used to check that code implements this document,
but it must not be used to change thresholds or select an arm. The OOS range
alone determines the recommendation.

## Behavioral hypothesis

Humans update and coordinate with delay. Three to five consecutive small
five-minute YES repricings may represent a short social-information cascade
that continues for another hour. A single news shock is deliberately excluded.

This differs from Golden Lime's 6-hour shock and Golden Grape's 24-hour drift:
the treatment is a 15–25 minute monotone micro-trend.

## Shared universe and rule

All conditions must hold at the entry snapshot:

1. Snapshot is linked through `run_id` to a `SUCCESS` live run and a
   cursor-complete market sweep.
2. Standard binary `Yes`/`No` market, catalog first-seen no later than entry.
3. Exclude any tag slug in:
   `sports`, `games`, `esports`, `crypto-prices`, `up-or-down`,
   `multi-strikes`, `5m`, `15m`, `1h`.
4. At least 6 hours remain until catalog `end_date`.
5. YES probability is in `[0.20, 0.80]`.
6. Entry liquidity is at least $20,000 and `volume_24h` is at least $10,000.
7. `best_bid` and `best_ask` are finite, `0 < bid <= ask < 1`, and
   spread is at most 0.02 probability points.
8. The last `steps + 1` observations form `steps` consecutive positive YES
   moves. Every inter-observation gap is 3–10 minutes. Every price step is
   strictly positive and no larger than 0.02. Total movement is no larger
   than 0.04 and meets the arm's minimum.
9. Only one position per `event_id` per 6 hours. If several sibling conditions
   qualify in the same collecting `run_id`, select the highest-liquidity one,
   then lexicographically smallest `condition_id`.

No volume acceleration is used: `volume_24h` is a rolling 24-hour field and is
not a sound 15-minute treatment.

## Frozen 2 × 2 arms

| Arm | Positive 5-minute steps | Minimum cumulative YES move |
|---|---:|---:|
| A | 3 | 0.01 |
| B — primary | 3 | 0.02 |
| C | 5 | 0.01 |
| D | 5 | 0.02 |

Arm B is the candidate. A, C, and D are nested falsification/sensitivity arms;
the arm with the best observed result will not be substituted for B.

## Outcomes

Primary outcome is the 60-minute top-of-book cross proxy:

`executable_return = exit_best_bid / entry_best_ask - 1`.

It is net of the observed entry and exit spread but not proof of a fill:
depth, queue, latency, fee, and partial-fill evidence are absent. Secondary
outcomes are directional midpoint point change
`exit_probability - entry_probability`, midpoint return, quote coverage,
win rate, median return, and an additional 10.4-bps execution-cost
sensitivity from the repository's confirmed-fill cost study.

Signals without a valid post-target observation or quote are censored, never
forward-filled and never assigned zero.

## Dependence, confidence intervals, and multiple testing

- Report signal count and unique event-cluster count separately.
- Primary mean is event-equal: average within event, then average event means.
- Use a deterministic event-cluster bootstrap with 20,000 draws and seed
  `20260730`.
- Report ordinary 95% percentile intervals and family-wise 98.75% intervals
  (Bonferroni for four arms).
- OOS cooldown state is carried from the mechanics range.
- Also report a strict OOS sensitivity excluding every event that generated a
  signal in the mechanics range.
- Split OOS at `2026-07-28T07:00:00Z`; sign stability across both halves is a
  predeclared requirement.
- Results must be separated by entry
  `config_hash × git_commit × mode × job_name` collection cohort.

## Falsification and promotion gate

Do not recommend live deployment unless Arm B, in strict event-purged OOS:

1. has at least 50 quote-complete signals and 30 event clusters;
2. has a positive lower 98.75% event-cluster bound for top-of-book return;
3. retains a positive lower 98.75% bound after subtracting 10.4 bps;
4. is positive in both predeclared OOS halves;
5. has at least 90% target/quote coverage; and
6. shows no material sign reversal across adequately sampled collection
   cohorts.

Even a statistical pass authorizes only a separately instrumented simulation
or shadow test. Actual promotion still requires confirmed fill, fee, role,
depth, partial-fill, and reconciliation coverage. Failure of Arm B cannot be
rescued by selecting A, C, or D from this dataset.

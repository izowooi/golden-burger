# Golden Plum soccer single-quote A/B v10

## Trigger and evidence

The operator observed that Polymarket in-play prices often jump in one update, so requiring three
fresh observations censors the move. Silver cohort
`79313033e195234a17d81db6f9e7499a9445a9489f83ea0d924b195e65bdbc13` contains36 soccer events
and18,828 replay-eligible snapshots. With the common `.75` entry and `.15` stop, the old
three-observation arms produced7 signals and displayed-book P&L `-$1.0520/-$5.3774` for TP
`.90/.95`. A corrected one-observation replay that still requires the unique midpoint leader found
`.75` negative (`-$4.1245/-$7.4160`). The `.70` band produced14 signals and
`+$2.1719/+$5.0062`; `.65` produced only5 signals, while `.80` was near zero. The common entry is
therefore moved to `.70-.73` while retaining the original TP treatment and stop.

## Frozen live treatment

- Universe and exact six-book identity remain soccer only.
- Entry is the unique current midpoint leader with exact `$5` ask VWAP `[.70,.73]` and spread
  at most `.05`.
- One current complete six-book observation is sufficient. Prior crossing, three observations,
  cumulative move, pullback, and trend elapsed gates are removed.
- The fresh pre-POST six-book must preserve the same unique leader and entry band.
- King TP `.90`, Queen TP `.95`; common stop confirmed BUY VWAP minus `.15`; no time exit.
- Common confirmed-strategy-P&L loss limit is `$100`; wallet deposits and withdrawals are excluded.
- MLB remains close-only. NFL/NBA/NHL live runtimes remain unregistered.

The replay is displayed-book evidence without actual fill or fee certainty. It selects the policy
change but does not authorize sizing above `$5`.

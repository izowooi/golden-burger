# Golden Kiwi / Micro-Cascade — frozen-result report

- Preregistration SHA-256: `0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006`
- DB SHA-256: `f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df`
- SQLite quick_check: `ok`
- Rows loaded: 1,293,610
- Conditions loaded: 10,658

## Temporal OOS (cooldown carried across the split)

| arm | signals | events | quote n | coverage | exec event mean | 95% CI | 98.75% CI | exec -10.4bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 4 | 2 | 4 | 100.000% | -1.023% | [-1.938%, -0.109%] | [-1.938%, -0.109%] | -1.127% |
| B | 2 | 2 | 2 | 100.000% | -1.807% | [-3.750%, 0.136%] | [-3.750%, 0.136%] | -1.911% |
| C | 1 | 1 | 1 | 100.000% | 0.526% | [NA, NA] | [NA, NA] | 0.422% |
| D | 0 | 0 | 0 | NA | NA | [NA, NA] | [NA, NA] | NA |

## Strict event-purged temporal OOS

| arm | signals | events | quote n | coverage | exec event mean | 95% CI | 98.75% CI | exec -10.4bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0 | 0 | 0 | NA | NA | [NA, NA] | [NA, NA] | NA |
| B | 1 | 1 | 1 | 100.000% | 0.136% | [NA, NA] | [NA, NA] | 0.032% |
| C | 1 | 1 | 1 | 100.000% | 0.526% | [NA, NA] | [NA, NA] | 0.422% |
| D | 0 | 0 | 0 | NA | NA | [NA, NA] | [NA, NA] | NA |

## Frozen gates

| arm | decision | failed reasons |
|---|---|---|
| A | FAIL_NO_LIVE_RECOMMENDATION | quote-complete signals < 50; event clusters < 30; 98.75% executable CI not estimable; 98.75% executable -10.4bps CI not estimable; quote coverage < 90%; oos_early executable mean <= 0 or absent; oos_late executable mean <= 0 or absent |
| B | FAIL_NO_LIVE_RECOMMENDATION | quote-complete signals < 50; event clusters < 30; 98.75% executable CI not estimable; 98.75% executable -10.4bps CI not estimable; oos_early executable mean <= 0 or absent |
| C | FAIL_NO_LIVE_RECOMMENDATION | quote-complete signals < 50; event clusters < 30; 98.75% executable CI not estimable; 98.75% executable -10.4bps CI not estimable; oos_early executable mean <= 0 or absent |
| D | FAIL_NO_LIVE_RECOMMENDATION | quote-complete signals < 50; event clusters < 30; 98.75% executable CI not estimable; 98.75% executable -10.4bps CI not estimable; quote coverage < 90%; oos_early executable mean <= 0 or absent; oos_late executable mean <= 0 or absent |

Frozen primary Arm B result: **FAIL_NO_LIVE_RECOMMENDATION**.

This is a top-of-book counterfactual, not confirmed execution P&L.
No depth, queue, latency, actual fill, partial fill, or fee evidence is present.

# Golden Plum soccer early-exit live A/B v14

- Effective only for new King/Queen soccer entries after deployment. Existing holdings keep the TP, SL and force-exit minute stored when their BUY was confirmed.
- Common entry: unique midpoint leader in a complete direct HOME/DRAW/AWAY YES/NO six-book, exact `$5` ask VWAP `[.70,.73]`, one current observation, source minute `[0,60)`, leader margin `.005`, spread at most `.05`.
- Common risk/exit: `$5`, confirmed-entry VWAP minus `.12` full-position FOK stop, and the first full-position FOK bid at source minute `>=65` if TP/SL has not already closed the holding.
- A/B treatment only: King absolute full-depth bid TP `.85`; Queen absolute full-depth bid TP `.90`.
- Confirmed strategy P&L drawdown limit: `$300` for both live soccer arms. Wallet deposits and withdrawals remain excluded.
- MLB close-only and all simulation/raw runtimes retain their registered profiles and are not part of this retune.
- The retrospective input was the verified King/Queen raw path through 2026-09-13. It contained 136 soccer events per recorder; 69 events produced complete replay trades under the unchanged `.70-.73` entry band. Results are exploratory displayed-book counterfactuals, not live fills.
- At 100 bps fee sensitivity on both entry and exit, the selected common `.12` stop and minute-65 exit reduced the recent adverse cohort materially. The full-history `.85`/`.90` cells stayed positive on both independent recorder paths; the previous `.90`/`.95`, `.15`, minute-75 policy was negative in aggregate.
- Reviews do not automatically stop or revert the cohort. Any later retune creates another source/config cohort.

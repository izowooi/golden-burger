# Golden Watermelon Soccer $10 forward cohort v3j

- Cat `.91-.999` and Dog `.92-.999` continue on Soccer only. Both new Soccer BUY targets are `$10`; the Cat/Dog NFL runtimes remain `$5`, and MLB remains close-only.
- The exact `$5` ask book remains the signal baseline. A fresh full-depth `$10` FOK BUY is submitted only when executable inside the arm band; otherwise the largest fully executable configured rung down to `$5` is submitted as one FOK order. A smaller or partial confirmed BUY must be recorded at its actual fill size and fee, never as `$10`.
- Effective stop `.70`, full-holding FOK SELL, resolution hold, account/event/cycle caps `20/1/5`, and confirmed economic loss limit `-$300` stay unchanged. Existing positions keep their entry-time parameters; no DB reset or migration.
- The scale cohort begins with the first deployed `config_hash × strategy_source_digest × mode × runtime` after this change. Earlier `$5` trades are historical comparators, not `$10` results.
- Review after 24 hours, then seek at least 30 independent resolved Soccer games and 10 independent UTC game days with confirmed execution before a decision. This is a review gate, not an entry cutoff. The next sizing decision also requires depth coverage, actual FOK success and stop trigger-to-SELL VWAP gaps. Entry remains live until the operator explicitly changes it.
- The prior 34-game White replay was positive but occupied only three UTC game days; its daily-cluster sensitivity interval included zero. No profitability claim is made for the `$10` forward cohort.

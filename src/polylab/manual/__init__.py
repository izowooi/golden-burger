"""Watch-only ledger for the thesis owner's manual bets (Track 2).

Public wallet addresses from ~/.polylab/watch.env (never a key) -> Data API v2 activity/positions ->
`<data>/manual/<alias>.db` (STRATEGY_SCHEMA positions/fills with mode='manual' + manual tables).
Addresses are never stored, printed or published; everything downstream uses the alias/label.
"""

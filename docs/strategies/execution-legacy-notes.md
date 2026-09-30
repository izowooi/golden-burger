# Legacy execution mechanics (porting notes)

Per source at `a21db7c` (working tree clean for the four target projects and
`polybot-observability`). Values quoted here are **source defaults/frozen
validators**, not proof of the live runtime; live values come from the runtime
DB resolved config and Jenkins effective config (t1 `AGENTS.md`). Secrets,
wallet/funder addresses and API credentials are never reproduced (`[REDACTED]`).

Abbreviations: **WM** = `golden-watermelon-live/src/polybot`, **AP** =
`golden-apricot/src/polybot`, **PL** = `golden-plum/src/polybot`, **CH** =
`golden-cherry/src/polybot`, **OBS** =
`polybot-observability/src/polybot_observability`. SDK = installed
`py_clob_client_v2` **1.0.2** (checked in `golden-watermelon-live/.venv`).

---

## 1. Four implementations, not one

WM/AP/PL are siblings of one "exact-USDC FOK + full-book walk + dynamic fee
evidence" lineage. CH is an older "midpoint GTC limit" lineage. All four share
only `OBS/execution_ledger.py` (`ExecutionLedger`) for intent/fill evidence.

| Concern | WM | AP | PL | CH |
|---|---|---|---|---|
| API creds | `derive_api_key()` only (`WM/api/clob_client.py:1273`) | `derive_api_key()` (`AP/api/clob_client.py:1190`) | `derive_api_key()` (`PL/api/clob_client.py:1486`) | `create_or_derive_api_key()` (`CH/api/clob_client.py:218`) |
| Entry order | exact-USDC FOK `MarketOrderArgs` with explicit limit (`place_fok_buy` :1741) | same (`place_fok_buy` :1661) | same (`place_fok_buy` :1979) | **GTC limit BUY at CLOB midpoint**, shares = amount/price (`CH/strategy/trader.py:280,386`; `CH/api/clob_client.py:531,596`) |
| Entry price source | full-depth ask walk (`_walk_buy_book` :200) | same, from cached book JSON (`select_adaptive_buy_from_book_evidence` :241) | same (:270) | `get_midpoint` |
| Adaptive size ladder | yes (`ADAPTIVE_BUY_NOTIONAL_LADDER_USDC`, `WM/config.py:40`) | yes (`= SIMULATION_SCALING_NOTIONALS_USDC`, `AP/config.py:137,157`) | yes (`PL/config.py:64,84`) | no |
| Exit order | FOK `OrderArgs` SELL at full-bid-walk worst price (`_submit_full_holding_sell`, `WM/strategy/trader.py:1904`) | FOK SELL | FOK SELL; **partial TP slice** (`select_take_profit_sell_from_book_evidence`, `PL/api/clob_client.py:427`) | **GTC limit SELL at midpoint**, clamped to live token balance (`_place_sell_with_balance_retry`, `CH/strategy/trader.py:452`) |
| Dynamic fee evidence | `_attach_clob_v2_fee_evidence` (:1084) | (:1047) | (:1326) | none; uses raw trade payload fee fields (`CH/db/fill_evidence.py:364-385`) |
| Pre-POST fee quote | `get_sell_fee_quote` (:1233) for TP net check | `estimate_taker_fee_usdc` (:972), exponent-1/taker-only only | (fee schedule preflight) | none |
| SELL identity pre-hash | no | no | yes: `signed_sell_identity` + `PlumExecutionLedger` (`PL/submission_identity.py:28,53`) | no |
| PENDING_BUY handling | FOK cancel/terminal proof after 2 min; QUARANTINE after 180 min (`pending_buy_quarantine_timeout_minutes`, `WM/config.py:539`) | FOK 2 min; BUY quarantine after 180 min reusing `stop_sell_quarantine_timeout_minutes` (`AP/strategy/trader.py:1422`) | same as AP (`PL/strategy/trader.py:1801 _quarantine_buy_if_due`) | GTC TTL `pending_buy_ttl_minutes=30` (bounds 5..1440, `CH/config.py:127,224`) then cancel → UNFILLED |
| Resolution | Gamma one-hot or 0.5/0.5 void, else CLOB `get_market` one-hot | same lineage | same lineage | Gamma proof (`_settle_from_market_if_proven`, `CH/strategy/trader.py:966`) |
| Account guard | Cat/Dog shared account guard, collateral via `get_balance_allowance` (`WM/account.py:197`) | – | – | – |

---

## 2. py_clob_client_v2 usage

### 2.1 Client construction and signature types
All four build the client identically (e.g. `WM/api/clob_client.py:1250 _ensure_initialized`):

```python
from py_clob_client_v2 import ClobClient
self._client = ClobClient(
    host="https://clob.polymarket.com",
    key=self.config.private_key,          # env POLYMARKET_PRIVATE_KEY
    chain_id=self.config.chain_id,        # 137 (Polygon)
    signature_type=self.config.signature_type,  # env POLYMARKET_SIGNATURE_TYPE, default 1
    funder=self.config.funder_address,    # env POLYMARKET_FUNDER_ADDRESS
)
api_creds = self._client.derive_api_key()   # CH: create_or_derive_api_key()
self._client.set_api_creds(api_creds)
```

- Config: `ApiConfig.signature_type: int = 1`, `chain_id: int = 137`
  (`WM/config.py:564-566`, `AP/config.py:619-621`, `PL/config.py:966-968`, `CH/config.py:153-157`).
  Validator `signature_type not in {1, 3} -> ValueError` (`WM/config.py:762`, `AP:875`, `PL:1236`, `CH:252`).
- SDK enum (`py_clob_client_v2/order_utils/model/signature_type_v2.py`):
  `0=EOA`, `1=POLY_PROXY` (EOA owning a Polymarket proxy wallet),
  `2=POLY_GNOSIS_SAFE`, `3=POLY_1271` (EIP-1271 smart-contract wallet).
  Legacy only allows 1 or 3. `funder` is the proxy/1271 wallet that holds USDC
  and tokens (the order `maker`); the private key is the signer. PL asserts for
  type 3 that `signer == maker` (`PL/submission_identity.py:36`).
- AP/PL allow all three creds unset for simulation (then `funder=""`,
  `signature_type=1`), but partial presence is an error (`AP/config.py:1198-1223`, `PL/config.py:1565-1590`).
- API key: SDK `create_or_derive_api_key()` = try `POST /auth/api-key`, on
  failure `GET /auth/derive-api-key` (`client.py:508`). WM/AP/PL call
  `derive_api_key()` directly so a live cycle never mints a replacement key and
  avoids a 400 per cycle (comment at `WM/api/clob_client.py:1266-1273`).
- The client is wrapped by `install_sdk_capture(...)` (OBS
  `market_data_capture`) to record public HTTP traffic and a `CycleBudget`
  deadline (`WM/api/clob_client.py:1285 client`). `close()` closes the SDK's
  module-global httpx pool after the Jenkins cycle.

### 2.2 SDK facts relevant to porting
- `MarketOrderArgs` is an alias of `MarketOrderArgsV2` (`clob_types.py:168`):
  fields `token_id, amount, side, price=0, order_type=FOK, user_usdc_balance=0, builder_code, metadata`.
  `amount` = **USDC for BUY, shares for SELL**. If `price` is 0 the SDK computes
  it from the book via `calculate_buy_market_price` (walks levels until
  cumulative `size*price >= amount`; FOK with insufficient depth raises
  `"no match"`; `order_builder/builder.py:292`). Legacy always passes an explicit price.
- `create_market_order(args, options)` resolves tick size, `neg_risk`
  (`get_neg_risk(token_id)`), signs locally; `post_order(signed, OrderType.FOK)` POSTs.
  `create_and_post_market_order` combines both (used only by the unused
  `place_market_buy` helpers).
- Other SDK calls used: `get_order_books([BookParams(token_id=...)])` (batched
  books), `get_midpoints`, `get_tick_size`, `get_clob_market_info(condition_id)`
  (fee params), `get_market(condition_id)` (resolution), `get_order(order_id)`,
  `get_open_orders(OpenOrderParams(id=...))`, `get_pre_migration_orders()`,
  `get_trades(TradeParams(id=... | asset_id=...), only_first_page=...)`,
  `cancel_orders([order_id])`, `get_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL|CONDITIONAL, ...))`.

### 2.3 Full-depth book walk (entry price definition)
`WM/api/clob_client.py:200 _walk_buy_book` (identical in AP/PL). Asks sorted
ascending, bids descending; each level must satisfy `0 < price <= 1`, `size > 0`;
crossed book (`best_bid > best_ask + 1e-9`) raises.

```python
remaining = notional_usdc; shares = 0.0
for price, size in asks:                 # ascending
    spent = min(remaining, price * size)
    shares += spent / price
    remaining -= spent
    limit_price = price                   # worst level touched
    if remaining <= 1e-9: break
if remaining > 1e-7 or shares <= 0: raise ClobResponseUnavailableError  # depth insufficient
vwap = notional_usdc / shares             # "exact-$X average ask"
spread = best_ask - best_bid
```

SELL mirror `_walk_sell_book` (:298): walk bids descending for `shares`;
`proceeds += sold*price`; `vwap = proceeds/shares`; `limit_price` = worst bid
touched; missing depth raises (never imputed).

### 2.4 Adaptive FOK size ladder (BUY "fallback ladder")
`_select_adaptive_buy_from_book` (`WM:239`; AP/PL `select_adaptive_buy_from_book_evidence`):
1. Baseline walk at `BASELINE_EXECUTION_NOTIONAL_USDC = 5.0` must succeed and
   its `limit_price <= max_limit_price` (the strategy's entry-band ceiling), else no entry.
2. Candidates = `{target, baseline} ∪ {ladder values with baseline <= v <= target}`.
   Ladder (`WM/config.py:40`): `5,10,15,20,25,30,40,50,75,100,150,200,250,500,750,1000`
   (`MAX_TARGET_BUY_NOTIONAL_USDC = 1000`). AP/PL use the same list under `SIMULATION_SCALING_NOTIONALS_USDC`.
3. Try candidates **descending**; first one whose full walk succeeds with
   `limit_price <= max_limit_price` wins (`TARGET_FULLY_EXECUTABLE` or
   `REDUCED_TO_FULLY_EXECUTABLE_LADDER_AMOUNT`). No partial orders are ever sent.

### 2.5 Exact-USDC FOK BUY signing (`place_fok_buy`, `WM:1741`)
- `amount` must have ≤2 decimals (maker USDC precision); `limit_price` in (0,1).
- Read-only `get_tick_size(token)`; failure returns
  `{"success": False, "pre_submission_no_post": True}` (retryable, no POST).
- `rounded_price = round_up_to_tick(limit)`, `rounded_max = round_down_to_tick(max_limit_price)`;
  `rounded_price > rounded_max` → `PreSubmissionContractError`.
- Before signing: `execution_ledger.assert_submission_allowed(token, "BUY")`
  (token/side quarantine) and `_clob_v2_fee_schedule(token)` (Gamma vs CLOB fee agreement).
- Signing loop: from `rounded_price` up to `rounded_max` in tick steps, build
  `MarketOrderArgs(token_id, amount, side="BUY", price=p, order_type=FOK)`,
  `create_market_order` (with `PartialCreateOrderOptions(tick_size="0.01")` when
  the venue tick is finer but `p` is cent-aligned). Accept the first signed
  order with `makerAmount == amount*1e6` and `takerAmount % 100 == 0`
  (4-decimal share precision, `_MARKET_BUY_TAKER_QUANTUM_MICROS = 100`).
  Otherwise `PreSubmissionContractError`. Widening stays inside the frozen band.
- POST via `execution_ledger.submit_and_record(..., submit=lambda: post_order(signed, FOK), cancel=cancel_orders, signed_making_amount, signed_taking_amount)`.
  `requested_size = takerAmount / 1e6`.

### 2.6 SELL (stop / TP / time exit)
- WM/AP/PL: `place_limit_order(token, price=walk.limit_price, size=shares, side="SELL", order_type="FOK")`
  → `OrderArgs` + `create_order` + `post_order(signed, OrderType.FOK)` (`WM:1970-2137`).
  Signed share drift vs requested must be within one SDK quantum (`0 <= size - signed < 0.01`).
- SDK floors SELL shares to 2 decimals: `_sdk_sellable_shares` (floor to 0.01;
  residual must be `< 0.01+1e-6`) and `_sdk_sell_submission_shares` (`math.nextafter(x, inf)`
  to avoid a float double-floor) (`WM/strategy/trader.py:84,109`). The sub-0.01
  residual is recorded as "SDK dust".
- Min order size frozen at 5 shares, buffer 0 (`WM/config.py:540,693`, AP `:768`, PL `:1155`).
- One SELL submission per cycle: `max_emergency_sells_per_cycle` (frozen, `WM/config.py:673`); TP and stops share the budget.
- **PL partial TP slicing** (`PL/api/clob_client.py:427`): only bid levels with
  `price >= target` count; `max_profitable = floor_0.01(min(position, Σsize))`;
  must be `>= min_order_size`. If not the whole position, slice =
  `min(max_profitable, floor_0.01(position - min_order_size))` so the remainder is
  itself sellable; slice `< min` → no TP. Walk must have `limit_price, vwap >= target`.
  Result `HOLDING` with `exit_reason=partial_<kind>_confirmed_fill_remaining_holding` after confirmation.
- **CH SELL**: GTC limit at midpoint; size = `min(DB shares, get_conditional_token_balance)` floored to
  1e-6; on a balance rejection, one retry at `reported_balance - 1 micro-share`
  (or 99% for a generic balance-cache error); positions `< 5` shares are not sold
  (wait for resolution/redeem, `CH/strategy/trader.py:1180-1190`).
- Tick rounding in CH: `round(round(p/tick)*tick, 2)` clamped to `[tick, 1-tick]` (`CH/api/clob_client.py:238`).

---

## 3. Fees

### 3.1 Source of fee rate
1. Gamma market `feesEnabled` + `feeSchedule{rate, exponent, takerOnly}` is stored in
   `market_catalog(fees_enabled, fee_rate, fee_exponent, fee_taker_only)`
   (`WM/db/repository.py:1399-1454`).
2. `_catalog_fee_schedule` (`WM:716`) finds exactly one catalog row owning the token.
3. `_clob_v2_fee_schedule` (`WM:801`) calls `get_clob_market_info(condition_id)` and reads
   `fd.r` (rate), `fd.e` (exponent, integer 0..10), `fd.to` (taker-only bool); condition/token identity must match.
   CLOB is authoritative for the rate when Gamma publishes `fees_enabled=1, fee_rate=0`
   (dynamic placeholder), but exponent and taker-only must agree; otherwise the whole schedule must be equal.
   Cached per token per process; any mismatch is a fail-closed contract error (pre-POST it blocks the order).

### 3.2 Formula (WM `_attach_clob_v2_fee_evidence`, `WM/api/clob_client.py:1188-1199`; identical AP:1116, PL:1412)

```python
fee = Decimal(0)
if liquidity_role == "TAKER" or not schedule.taker_only:
    fee = size * schedule.rate * (price * (Decimal(1) - price)) ** schedule.exponent
fee = fee.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)   # 5-dp platform precision
fee_target["fee_amount_usdc"] = str(int(fee * 1_000_000))           # fixed-6
fee_target["fee_rate_bps"] = None   # legacy 0 bps field is NOT zero-fee proof
```
- `size`, `price` = this CONFIRMED trade bucket's matched quantity/price for our
  order (maker entry `matched_amount`/`price` if we were maker, else top-level
  `size`/`price` as taker). Quantity is decoded human vs fixed-6 fail-closed
  (`_positive_fill_quantity`, `WM:623`) and bounded by the signed cash/size envelope
  (`_validated_fee_quantity`, `WM:962`).
- If the venue reports `fee_amount_usdc`, it must equal the computed fee within 0.00001 USDC.
- Fee is paid in USDC per fill; maker fills are 0 when `taker_only`.
- Pre-POST estimates: WM `get_sell_fee_quote` (`WM:1233`) prices the whole quantity at
  the signed minimum (price ≥ 0.5 required, curve decreasing above 0.5), no rounding;
  AP `estimate_taker_fee_usdc` (`AP:972`) requires `exponent == 1 and taker_only`
  and returns `shares*rate*p*(1-p)` quantized to 0.00001.
- CH: no computed fee. `CH/db/fill_evidence.py:364-374` treats a missing
  `fee_amount_usdc` as known-zero if `fee_rate_bps == 0` or role is MAKER.
  This **conflicts** with the WM/AP/PL rule (a reported 0 bps is not proof;
  `WM/db/repository.py:951-959` only accepts MAKER role). Porting should use the WM rule.

### 3.3 Net-positive exit checks using fees
- WM TP (`_execute_take_profit`, `WM/strategy/trader.py:2090`):
  `net_floor = limit_price*sellable_qty - buy_vwap*buy_size - buy_fee - fee_quote - fee_rounding_reserve_usdc`
  (quantized down to 1e-6); TP only if `net_floor > 0`, `policy.price <= limit <= vwap <= best_bid < 1`,
  spread `<= entry.max_stop_spread`, and the book is younger than `max_book_age_seconds`.
- AP (`AP/strategy/trader.py:420-475`): for `tp98/tp99/absolute_tp_or_resolution`
  requires `walk.vwap >= target` and `walk.proceeds - sell_fee > buy_size*buy_vwap + buy_fee`;
  for `net_return`: `net_proceeds >= entry_cost*(1+take_profit)`.
- PL: no fee/net-positive check at TP; only every consumed bid `>= TP` (see `plum.md`).
- CH: no fee computation at all; GTC SELL at midpoint (see `cherry.md`).

---

## 4. Order ledger and CONFIRMED-fill reconciliation

### 4.1 Intent → POST → response (`OBS/execution_ledger.py:846 submit_and_record`)
1. `assert_submission_allowed(token, side)` raises `UnresolvedTokenSubmissionError`
   if that exact token×side has any unresolved intent or reconciliation gap
   (`submission_quarantine_count = unresolved_submission_count + reconciliation_gap_count`, :1397).
2. `record_intent` writes `order_submissions` row with `response_status='INTENT'` **before** POST.
3. `submit()` (only the POST). On exception `record_submission_error` (:1124):
   timeouts/connection errors/5xx/missing status → `SUBMIT_OUTCOME_UNKNOWN` and
   `SubmissionOutcomeQuarantinedError` (token×side quarantined, cycle continues);
   explicit venue rejections → `FAILED` (auto-resolved `NO_ORDER_CREATED` when proven).
4. `record_submission_result` (:1037): `success = explicit success or orderID`; anomalies
   (success=false with orderID, accepted without orderID, non-bool success) →
   `SUBMIT_OUTCOME_UNKNOWN` + best-effort `cancel_orders` + `SubmissionEvidenceError` (cycle aborts).
   Accepted live orders get `needs_reconciliation=1`; signed maker/taker amounts are kept
   when the response omits them.
- Unresolved predicate (`_unresolved_sql`, :2667): live rows with status in
  `INTENT | SUBMIT_OUTCOME_UNKNOWN | EVIDENCE_WRITE_FAILED(order_id NULL)` not resolved by an operator/auto
  `NO_ORDER_CREATED` or `ORDER_ID_LINKED` resolution with reason.
- `autoresolve_stale_sell_intents` (:1277): SELL-only, ≥30 min old, order absent from the
  exchange open-order set → `NO_ORDER_CREATED` (BUY never auto-released).
- PL extension: `signed_sell_identity` predicts the EIP-712 order hash pre-POST
  (`ExchangeOrderBuilderV2.build_order_hash`, neg-risk vs standard exchange) and
  `PlumExecutionLedger.recover_sell_order_ids` (`PL/submission_identity.py:141`) looks the hash up via `get_order` to link
  an unknown SELL outcome (`ORDER_ID_LINKED`); lookup is identity only, never fill proof.

### 4.2 Reconcile loop (`reconcile_order_ledger`, `WM/api/clob_client.py:2158`; AP:2081; PL:2406; CH:654)
For each `pending_submissions()` row (accepted, `needs_reconciliation=1`):
1. `client.get_order(order_id)` → normalize → `record_order_status` stores
   `latest_order_status`, `latest_size_matched`, `associated_trade_ids`.
2. If the order is unavailable: `get_open_orders(OpenOrderParams(id=...))`, then
   `get_pre_migration_orders()`; if still absent, scan
   `get_trades(TradeParams(asset_id=token), only_first_page=False)` for trades whose
   `taker_order_id == order_id` or a `maker_orders[].order_id == order_id`
   (proof `AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS`).
3. For every associated trade ID: `get_trades(TradeParams(id=trade_id))`, verify IDs,
   attach fee evidence (§3.2) and `record_fill` into `order_fills`
   (`trade_id, bucket_index, status, size, price, liquidity_role, fee_rate_bps, fee_amount_usdc, matched_at`).
4. `finish_reconciliation` (`OBS:3216`): order statuses terminal set
   `{MATCHED, CANCELED, CANCELLED, CANCELED_MARKET_RESOLVED, INVALID}`, trade terminal set
   `{CONFIRMED, FAILED}`. Complete when every associated trade is terminal and
   Σ CONFIRMED size == `latest_size_matched` (±1e-6), BUY confirmed notional ≤ maker envelope;
   all-FAILED → strategy row reverted (BUY→`UNFILLED`, SELL→`HOLDING`,
   `_reconcile_all_failed_strategy_trade` :3128); mixed CONFIRMED/FAILED → reconciliation error;
   canceled/invalid with explicit `size_matched == 0` → zero fill.
   Errors are stored per row (`record_reconciliation_error`) and quarantine only that token×side
   (`OBS/reconciliation_policy.py`).
- Trade statuses seen from CLOB: `MATCHED → MINED → CONFIRMED` or `RETRYING → FAILED`; only
  `CONFIRMED` counts as a fill.

### 4.3 Exact fill evidence used by strategies (`WM/db/repository.py:743 get_exact_order_fill_evidence`)
`ExactFillEvidence.state ∈ {confirmed, terminal_zero_fill, pending, unavailable}`.
`confirmed_size = Σ size`, `confirmed_vwap = Σ(size*price)/Σ size`, `confirmed_fee_usdc = Σ fee_amount_usdc`;
`fee_complete` false if any CONFIRMED taker fill lacks `fee_amount_usdc`.
`has_reconciled_full_fill` (MATCHED/authenticated full-fill or matched≈requested) vs
`has_reconciled_executed_fill` (terminal partial, `confirmed ≈ latest_size_matched`).

---

## 5. Strategy trade lifecycle, timeouts, QUARANTINED

`TradeStatus` (`WM/db/models.py:51`): `PENDING_BUY, HOLDING, PENDING_SELL, COMPLETED, RESOLVED, SKIPPED, EXPIRED, UNFILLED, QUARANTINED`.

- **PENDING_BUY** (`reconcile_pending_buy`, `WM/strategy/trader.py:1406`):
  `terminal_zero_fill` → `UNFILLED`. Executed fill with complete fee → `HOLDING`
  with `buy_price=buy_confirmed_vwap`, `buy_shares=confirmed_size`, fee, and entry stop.
  Incomplete fee keeps PENDING_BUY. After `fok_reconciliation_timeout_minutes = 2`
  (frozen, `WM/config.py:537,687`) the bot calls `cancel_order_for_reconciliation`
  (`cancel_orders` + `get_order` or, if gone, full token trade catalog;
  `OBS record_delayed_fok_zero_fill` :1543 proves zero fill for a DELAYED FOK).
  After `pending_buy_quarantine_timeout_minutes = 180` (frozen `WM/config.py:539`) → `QUARANTINED`
  with `exit_reason=pending_buy_reconciliation_timeout_3h_unknown_exposure`.
  AP/PL: same flow, 180 min via `stop_sell_quarantine_timeout_minutes`,
  reason `buy_reconciliation_timeout_3h_unknown_exposure` (`AP/db/models.py:40`).
  CH: GTC `pending_buy_ttl_minutes = 30` then cancel with terminal proof → `UNFILLED`
  (`CH/strategy/trader.py:603-700`).
- **PENDING_SELL** (`reconcile_pending_sell`, `WM:1526`): zero-fill → back to `HOLDING`;
  2-min FOK cancel/terminal proof; after `stop_sell_quarantine_timeout_minutes = 180`
  → `QUARANTINED` (`stop_sell_reconciliation_timeout_3h_unknown_exposure` or TP equivalent).
  Ledger write failure during a SELL → immediate `QUARANTINED` (`*_execution_ledger_failure_unknown_exposure`).
- **QUARANTINED semantics** (`WM:1192-1300`): economically still open; keeps one
  position-capacity/event reservation; `realized_pnl`, `hypothetical_pnl`,
  `pnl_basis` set to NULL; never treated as sold or zero-filled; stops retry
  loops so one uncertain order cannot block other events; later cycles may still
  resolve it from exact evidence. Separate from ledger token×side quarantine (§4.1).
  CH uses QUARANTINED only for zero-balance positions whose BUY order evidence is missing
  (`CH/strategy/trader.py:1310`).

---

## 6. P&L

### 6.1 Realized (SELL) — `WM/strategy/trader.py:1690-1702`
```python
allocated_buy_fee = buy.confirmed_fee_usdc * size / buy.confirmed_size
realized_pnl = (sell.confirmed_vwap - buy.confirmed_vwap) * size - allocated_buy_fee - sell.confirmed_fee_usdc
```
`size` = confirmed SELL size, which must equal signed SELL size; BUY−SELL residual must be
`< 0.01 + 1e-6` (SDK dust, recorded in `sell_residual_shares`). PL accumulates per lot
(`lot_realized_pnl`, `PL/strategy/trader.py:2388-2400`; plus cumulative proceeds/fees and
`confirmed_sell_count`); CH uses `min(1, sell/buy)` fee allocation and adds prior exact P&L
(`CH/strategy/trader.py:779-797`).

### 6.2 Resolution — `WM/strategy/trader.py:859 _record_resolution_values`
Proof sources: Gamma `get_proven_resolution` (`WM/strategy/filters.py:371`: `closed is True`,
token-aligned outcome prices exactly `[1,0]`/`[0,1]`, or `[0.5,0.5]` with
`umaResolutionStatus == "resolved"` = void), else CLOB `get_market` via
`_normalize_clob_resolution` (`WM/api/clob_client.py:377`: closed, exactly 2 tokens, one
`winner=True`, prices exactly 1/0). Requires exact executed BUY fill + fee
(`_resolution_fill_ready`).
```python
settlement_pnl_assumption = (payout - buy.confirmed_vwap) * buy.confirmed_size - buy.confirmed_fee_usdc
```
Stored as `status=RESOLVED`, `settlement_pnl_assumption`, `settlement_assumption_basis`,
resolution evidence columns; **`realized_pnl` stays NULL and no synthetic SELL is written**.
CH uses the remaining position size and a proportional BUY fee
(`CH/strategy/trader.py:895-925`). The WM drawdown guard sums
`confirmed_sell_pnl + proven_resolution_pnl` from `get_economic_pnl_guard` (`WM/bot.py:320-345`).

---

## 7. Redemption

**No legacy project redeems resolved winning positions on-chain.** A search for
`redeem|redemption|relayer|redeemPositions|ConditionalTokens|CTF_|neg_risk_adapter`
over all `golden-*`, `daily-report`, `polybot-observability` (excluding `.venv/dist/data/research/tests`)
found no web3/relayer/CTF calls and no `web3` dependency:
- WM/AP/PL: resolution is bookkeeping only (§6.2); SELL is blocked once the market
  is closed/non-accepting.
- CH: SELL blocked on closed/inactive/non-accepting markets, "must be handled by the
  resolution/redeem path" (`CH/strategy/trader.py:1087-1096`); sub-5-share residue waits for
  "resolution/redeem or an operator-supported path" (:1180-1190). No such path exists in code.
- golden-date / golden-mango: after 24 h past resolution → `EXPIRED`,
  `exit_reason=resolved_unredeemed`, "manual redeem required" (`golden-date/src/polybot/strategy/trader.py:111,287-304`;
  `golden-mango/src/polybot/strategy/trader.py:33,209-226`). golden-fig/grape/... bots log `EXPIRED` counts.
- `golden-apple/STRATEGY_ANALYSIS.md:156` only proposes CTF `redeemPositions` automation.
- `daily-report` stores Data-API `redeemable` flags (`daily-report/src/polybot_reporter/storage/evidence_store.py:329,450`).
Redemption was therefore manual (Polymarket UI/operator). The rebuild must add it
explicitly if wanted (e.g. CTF `redeemPositions` / NegRiskAdapter via proxy or relayer);
nothing to port.

---

## 8. Balances and positions

- WM Cat/Dog account guard: `get_collateral_balance` (`WM/api/clob_client.py:572`) →
  `client.get_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL, signature_type=...))`,
  `balance` = integer micro-USDC / 1e6; one GET, no retry. `AccountGuard.approve_buy`
  requires `balance - reserved_pending_cash >= amount`, account max 20 slots, max 5 BUY attempts per rolling 60 s
  (`WM/account.py:180-205`).
- CH token balance: `get_conditional_token_balance` (`CH/api/clob_client.py:300`) →
  `BalanceAllowanceParams(asset_type=CONDITIONAL, token_id=...)`, micro-shares / 1e6.
- Data API (`https://data-api.polymarket.com`, public, keyed by the funder address `[REDACTED]`):
  `daily-report/src/polybot_reporter/api/data_api_client.py`:
  - `get_positions` (:39): `GET /positions?user=<addr>&limit=500&offset=..&sizeThreshold=0`, paginated to offset 10000.
  - `get_equity_snapshot` (:91): `GET /v1/accounting/snapshot?user=<addr>` → ZIP with `equity.csv`
    (`cashBalance`, `positionsValue`, `equity`); authoritative for portfolio value incl. resolved-but-unredeemed winners
    (`/positions currentValue` is 0 for those). `get_cash_balance` (:152) wraps it.
  - `get_activity` (:161) `GET /activity`, `get_trades_by_address` (:214) `GET /trades`.
  - `get_portfolio_summary` (:296): positions + snapshot; checks `|equity - positions - cash| <= 0.02`.
  - CH has an older copy (`CH/api/data_api_client.py`: `/positions`, `/activity`, `/trades`).
- Wallet movements, balances and Data-API P&L are **not** strategy P&L (t1 `AGENTS.md`).

---

## 9. Porting checklist (derived)
1. One execution module: FOK exact-USDC BUY with full-book walk, ladder, tick-walk signing (§2.4-2.5).
2. FOK SELL at worst-bid walk, 2-dp floor + dust, optional PL partial-TP slice (§2.6).
3. Intent-before-POST ledger with token×side quarantine and the unknown-outcome rules (§4.1).
4. Reconcile via `get_order` → trade IDs → `get_trades(id)`; CONFIRMED only; fee computed from market-info (§3, §4.2).
5. Timeouts: FOK 2 min, QUARANTINE 180 min (BUY and SELL) (§5).
6. P&L: realized only from confirmed BUY/SELL; resolution is a separate settlement figure (§6).
7. Decide on redemption explicitly; legacy has none (§7).

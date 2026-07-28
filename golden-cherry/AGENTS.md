# L3 AGENTS.md — golden-cherry (Resolution Momentum)

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md`. 전역 개발·보안·Git·응답 규칙은 L1/L2를 따르며 여기서 반복하지 않는다.
- 본 문서는 이 폴더에서만 통하는 사실, 특히 **코드만 읽어서는 알 수 없는 것**에 집중한다.

## 이름이 셋 다 다르다 (가장 흔한 사고 원인)

| 축 | 값 |
|---|---|
| 코드·폴더·`RunAudit` 전략명·DB 디렉터리 | `golden-cherry` |
| 자금이 있는 계정 | **`golden-banana`** |
| Jenkins job | **`polybot-yellow`** |

셋을 혼동하면 남의 계정 잔고로 회고하거나 다른 봇의 DB를 열게 된다. Slack/대시보드에서 `GOLDEN-BANANA`로 보이는 잔고에는 cherry 거래가 섞여 있으므로 **계정 잔고 = cherry 성과가 아니다**.

## 전략 요약

확률 구간의 YES를 사서 +10% 익절 / -8% 손절 / 최고가 대비 5% 트레일링으로 관리한다. 진입 시간창은 기준시각까지 0~120h(5일). 비스포츠는 `endDate`, 스포츠는 `gameStartTime`이 기준시각이다.

진입 확률 구간은 `config.yaml` 기본이 75~92%지만, **운영(`polybot-yellow`)은 2026-07-28부터 env로 75~88%를 쓴다.** 근거는 [2026-07 파라미터 회고](../docs/retro/golden-cherry-2026-07-parameter-review.md) — 진입가 0.909 이상은 익절(`진입가 × 1.10`)이 $1.00을 넘어 구조적으로 발동하지 않는다.

## 코드만으로는 안 보이는 계약

1. **`sell_threshold`는 매도 조건이 아니라 매수 상한이다.** `filters.py`가 진입 확률 상한으로, `trader.py`가 `rapid_jump` 트리거로 쓴다. `filters.py`의 `should_sell()` 헬퍼는 어디서도 import되지 않는 죽은 코드다 — grep 결과를 근거로 판단하지 말 것.

2. **매도 GTC 승인 ≠ 체결.** `execute_sell`은 `result.get("orderID")`만 있어도 `sell_price`·`realized_pnl`을 기록하고 `COMPLETED`로 넘긴다. 가격은 `get_midpoint()` 기준 **지정가**이고 재호가·추적·취소 재발행이 없다. 따라서 `trades.realized_pnl`은 성과 지표가 아니다. 성과는 항상 `order_fills.status='CONFIRMED'`로만 계산한다. `execution_ledger`가 되돌리는 경우는 연관 CLOB trade가 **전부 FAILED**일 때뿐이라, "그냥 안 체결된" 매도는 영원히 가짜 `COMPLETED`로 남는다.

3. **`max_positions`는 `PENDING_BUY`/`HOLDING`/`PENDING_SELL`/`QUARANTINED`를 모두 센다.** 유령·좀비 행이 쌓이면 실보유가 0이어도 봇이 신규 매수를 못 한다. 2026-07-22~28 실제로 이 상태로 6일간 정지했다(HOLDING 246 + QUARANTINED 76 vs 상한 10). 파라미터를 만지기 전에 `status`별 건수부터 확인한다.

4. **해결된 시장은 스스로 정리되지 않는다.** 오더북이 사라지면 `get_midpoint`가 예외를 던지고 `execute_sell`이 매 cycle `False`만 반환한다. redeem 회계가 없어서 해결된 YES 포지션은 현금이 되지 않고 `HOLDING`으로 남는다. 정리는 `tools/wind_down.py`와 수동 redeem으로 한다.

   더 위험한 중간 상태가 있다. 해결 직후 오더북이 **완전히 죽기 전** 구간에서는 `get_midpoint`가 예외 대신 **0.50**을 돌려준다. 가격 기반 청산 3규칙에는 해결 여부 가드가 없어서, 봇은 이 0.50을 현재가로 읽고 **이미 이긴(주당 $1.00) 포지션을 손절하려 든다**. 2026-07-28 실행에서 실제로 발생했고 CLOB이 `invalid token id`로 거절해 손실을 면했다 — 봇이 막은 게 아니다. 청산 로직을 만질 때 이 가드를 먼저 넣을 것.

5. **`order_fills`에 기록이 없다고 미체결이 아니다.** 계측 시작은 2026-07-11 13:43이고 그 이후에도 누락이 있다. 지갑 잔고가 유일한 권위이므로, 유령 포지션을 정리할 때는 반드시 `tools/wind_down.py status` 또는 CLOB `balance-allowance`로 확인한다. DB만 보고 `UNFILLED` 처리하면 실보유를 날린다.

6. **격리 조건은 둘이고, 그중 하나는 대사가 보지도 않는다.** `assert_submission_allowed`는 (A) `response_status='SUBMIT_OUTCOME_UNKNOWN'` + `order_id IS NULL` + `needs_reconciliation=0`, (B) `needs_reconciliation=1` + `order_id IS NOT NULL` + `reconciliation_error IS NOT NULL` 중 하나라도 있으면 같은 token/side를 봉쇄한다. `reconcile_order_ledger`는 `needs_reconciliation=1 AND order_id IS NOT NULL`만 처리하므로 **A는 영구히 자동 해제되지 않는다** — `polybot-retro resolve-intent`로 사람이 풀어야 한다. 2026-07-28 실행에서 막힌 청산 7건은 **전부 A**였고(2026-07-11~19 발생), 같은 로그의 "대사 오류 16건"과는 무관하다. 로그만 보고 대사를 고치려 들면 헛수고다.

7. **`effective_min_liquidity = max(min_liquidity, buy_amount / max_order_liquidity_ratio)`.** `POLYBOT_BUY_AMOUNT`만 올리면 유동성 하한이 조용히 따라 올라가 후보가 0이 될 수 있다. 주문액을 키울 때는 `MAX_BUY_AMOUNT_USDC`와 `MAX_OPEN_NOTIONAL_USDC`도 같이 올리지 않으면 `_validate_config`가 run을 실패시킨다.

8. **진입 기준시각이 두 종류다.** `game_start.enabled`이고 `gameStartTime`이 파싱되면 기준시각이 `game_start_time`이 되고, 이때 `exit_hours`는 진입 검사에서 0으로 강제된다. 그래서 `hours_until_resolution_at_buy`(endDate 기준)와 `hours_until_entry_deadline_at_buy`(실제 기준시각)는 행마다 의미가 다르다. **두 컬럼을 한 집계에 섞지 말 것.**

9. **DB는 job 단위다.** `data/<job>/trades.db`(simulation은 `trades_sim.db`), 중복 진입 방지도 DB 단위다. 같은 지갑에 다른 `--job`으로 두 번 띄우면 같은 시장을 두 번 산다. 과거 운영분은 `--job` 없이 돌아서 `default`에 있다.

10. **스캔 확률은 Gamma `outcomePrices`, 청산 확률은 CLOB midpoint다.** 스캔을 통과하고도 주문 직전 midpoint 재확인에서 걸릴 수 있다.

## 알려진 계측 공백 (2026-07-28 기준)

회고 전에 반드시 확인한다. 아래는 **컬럼은 있는데 값이 없다**:

- `entry_time_reference`, `sports_phase_at_buy`, `hours_until_entry_deadline_at_buy`, `minutes_until_game_start_at_buy`, `market_game_start_time` → 기존 671행 전부 NULL. `game_start` 경로는 2026-07-22 도입 후 2026-07-28 20:09 스캔에서 처음으로 후보를 냈지만(`game_start_0.3h`, 테니스), 포지션 상한에 막혀 체결 데이터는 아직 0건이다. **스포츠 인플레이 관련 판단은 현재 데이터로 불가능하다.**
- `order_fills` → 계측 시작이 2026-07-11 13:43이고 그 이후에도 누락이 있다. 항목 5 참조.
- `market_snapshots` → 0행. `save_snapshot`은 어떤 경로에서도 호출되지 않고 `repository.py`의 `cleanup_old_snapshots`도 호출부가 없다. **가격 경로가 없으므로 경로 의존 반사실(“손절을 -15%로 했다면”)은 계산할 수 없다.** 이웃 봇 DB(`golden-nectarine`/`golden-honeydew`)의 universe snapshot을 빌려야 한다.

## 환경변수

25개 전부 `README.md`와 `docs/retro/golden-cherry.md`에 문서화되어 있다(검증 완료). 그 외에 런타임이 읽지만 cherry 문서가 다루지 않는 것:

- `POLYBOT_DB_MAINTENANCE` — 값이 있는데 `compact-v1`이 아니면 **run이 예외로 죽는다**.
- `POLYBOT_DB_BACKUP_DIR`, `POLYBOT_DB_HOT_HOURS`, `POLYBOT_DB_ROLLUP_HOURS`, `POLYBOT_DB_RETENTION_DAYS`(cherry 기본 7일), `POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS`, `POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS`
- `GIT_COMMIT` — Jenkins가 주입한다. 회고 cohort 분리가 여기에 의존하므로 비어 있으면 안 된다.

## 검증

```bash
uv sync --frozen --extra dev
uv run pytest tests
uv run python main.py config          # 거래 없이 resolved config만 출력
uv run tools/verify_strategy_contracts.py   # 저장소 루트에서
```

문서만 고쳤으면 위 단계를 생략할 수 있고, 생략 사유를 응답에 한 줄 남긴다.

## 문서 신뢰도

- **권위 있음**: `docs/retro/golden-cherry.md`, `docs/strategy-pages/strategy-cherry.html`, `config.yaml` 주석, `README.md`.
- **역사 자료**: `STRATEGY_ANALYSIS.md`는 2026-07-03 스냅샷이며 현재 코드와 광범위하게 어긋난다(진입창 24~720h, `time_exit` 활성, `max_positions: -1`, `min_liquidity` 10,000 등). 현재 동작의 근거로 인용하지 않는다.
- `docs/prediction-market-strategy-portfolio.md`의 "마감 1–30일"도 현재 `entry_hours_max: 120`과 어긋난다.

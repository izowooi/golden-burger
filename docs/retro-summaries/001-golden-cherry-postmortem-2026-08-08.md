# 001-golden-cherry-postmortem-2026-08-08

작성일: 2026-08-08  
전략: `golden-cherry`  
Jenkins job: `polybot-yellow` / runtime: `default`

## 1) 증거 위치(daily-rsync)

`daily-rsync locate --strategy golden-cherry` 결과:

- 전략/잡 매핑: `polybot-yellow` / `default`  
- DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- 로그 루트: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/logs`
- 최근 동기화: `2026-08-08T00:32:44+00:00` 부터 `2026-08-08T00:32:48+00:00` (SYNCED)

`daily-rsync verify --job polybot-yellow --strategy golden-cherry`는 다음으로 실패:
- `/.../2026/08/20260806.log` checksum mismatch
- `/.../databases/latest/trades.db` checksum mismatch
- `/.../csv/trades_2026-08.csv` checksum mismatch

=> 데이터 접근 자체는 가능하지만, verify는 정합성 경고 상태이므로 아래 분석은 **`local evidence` 기반 추정치**로 사용함.

## 2) 분석 전제

문서 상/코드 계약 규칙에 맞춰 `trades.realized_pnl` 대신 `order_fills` 기준으로 평가함.

- `order_fills` 기준 `BUY/SELL` 모두 `CONFIRMED`한 값으로 계산
- `P&L = sell_notional - buy_notional - fee`  
- DB 상태/로그 상태/레코드 누락으로 인해 미체결/미해결은 별도 분류

## 3) 회고 결과 (요약)

### 현재 설정(config_hash 기반)

최신 config(`3c3eb1ad...`)는 **2026-07-28T12:11:35+00:00** 부터 적용:

```json
{
  "buy_amount_usdc": 250,
  "sell_threshold": 0.88,
  "min_liquidity": 125000,
  "max_order_liquidity_ratio": 0.002,
  "max_positions": 10,
  "max_new_positions_per_cycle": 1,
  "max_open_notional_usdc": 15000
}
```

### 성과 (fill 기준)

- 전체(DB 현재 기준)
  - `trades`: 1,133
  - 상태: `COMPLETED 751`, `UNFILLED 377`, `HOLDING 2`, `QUARANTINED 3`
  - 실현 수익: **-$26,617.27**
  - 매수 체결금액: $195,418.38
  - 매도 체결금액: $168,801.11
  - 수익률(매수체결 대비): **-13.63%**

- 최근 30일(2026-07-09 ~ 2026-08-08)
  - `trades`: 1,080 (`COMPLETED 732`, `UNFILLED 343`, `HOLDING 2`, `QUARANTINED 3`)
  - 실현 수익: **-$35,821.02**
  - 매수 체결금액: $195,418.38
  - 매도 체결금액: $159,597.36
  - 매수 대비 손실이 확대되는 추세

- 최신 config 구간(2026-07-28 이후)
  - `trades`: 416 (`COMPLETED 380`, `UNFILLED 32`, `HOLDING 1`, `QUARANTINED 3`)
  - 실현 수익: **-$4,485.12**
  - 매수 체결금액: $92,111.66
  - 매도 체결금액: $87,626.54

일자별(최근 구간) 실현 수익은 11일 모두 중립~음수였고, 강한 회복 신호 없이 약세가 지속됨.

### 운영 상태(실행 계약)

- `order_submissions`: 100,233건
  - `success=1`: 1,805 (1.80%)
  - `response_status='FAILED'`: 98,407 (98.18%)
- `order_fills`: 3,699건(확인됨 3,698)
- 최신 config run_audit(2972회, 모두 SUCCESS)
  - `buy`: 462, `sold`: 425
  - order reconciliation 누적: `checked 80,673`, `fills 88,558`, `completed 891`, `errors 47,558`, `intent_autoresolved 14`
  - 즉 cycle는 돌지만, 체결/정리 성공은 낮고 reconcile 오류가 상시 발생

로그에서도 아래가 반복:
- `ClobResponseContractError` / `ClobResponseUnavailableError`
- `Could not create api key`(HTTP 400)
- `불확실한 CLOB intent` 경고
- `total P&L` 값이 로그에서 고정 반복 노출되며, 실제 성능 추정값으로 신뢰되지 않음

## 4) 결론

**현재 구간의 전략은 수익이 나지 않습니다.**  
최소 1개월 구간 기준으로도 `fill` 기준 실현 수익이 확실히 음수이며,
체결/해결/리콘실리에션 품질 이슈가 동반되어 전략 본연의 edge 판단보다 집행 품질 문제가 크게 작동하고 있습니다.

## 5) 추천 조치

1. 요청한 조건대로 한달 테스트를 원하면 우선 다음만 적용:
   - `POLYBOT_BUY_AMOUNT=5` (현 구조 유지)
   - 1개월 고정 실험(동일 config, 동일 job) 후 재평가

2. 1개월 테스트 중 반드시 같이 점검:
   - `run_audit`와 `order_submissions`에서 reconciliation 완료율이 회복되는지
   - `response_status=FAILED/LIVE/DELAYED/MATCHED` 비율 추이
   - `HOLDING/QUARANTINED` 신규 누적 추이

3. 성능개선 전 선결로 검토해야 할 실행 안정화(파라미터보다 우선):
   - API key 생성 400 오류 지속 여부
   - 주문 대사 재조회/결과 미확정 intent 봉쇄 패턴 누적
   - `resolve intent` 계통 절차로 미해결 주문이 순환성 매매를 방해하지 않도록 정리

> 다음 실행판에서 같은 형식으로 새 파일을 남기려면 다음 번호를 사용하세요.  
> 현재 최초 파일이므로 시퀀스는 `002-*`, `003-*`로 이어갑니다.

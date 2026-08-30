# Golden Watermelon Live 손절 실패 격리 보정 v3d — 2026-08-30

- 진입 구간: `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)` 유지.
- 후속 관찰 종료: `2026-09-12T04:00:00Z` 유지.
- 여섯 Jenkins job은 non-concurrent, 1분 주기, exact `$5`를 유지한다.
- 분석 묶음은 `config_hash × strategy_source_digest × mode × job_name`이다.

이 보정은 Soccer/MLB/NHL, 0.96/0.99 진입 하한, 손절 가격, 주문 크기와 노출 한도를 바꾸지
않는다. 한 손절 주문의 실패·대사 불확실성이 다른 경기의 신규 주문까지 전부 막은 Elversberg
장애를 재발시키지 않기 위한 주문 생명주기 안전 변경이다. v3c 이전 자료와 v3d 자료는 source
digest로 분리하며 DB를 clean, rewrite, copy, merge, backfill 또는 delete하지 않는다.

## 매도 불확실성의 국소 격리

1. BUY intent, orphan BUY, BUY fill/fee 누락은 실제 노출이 늘 수 있으므로 계속 전체 신규 진입을
   막는다.
2. SELL intent와 SELL 대사 실패는 새로운 long 노출을 만들지 않으므로 동일 token/side와 동일
   event에만 격리한다. 다른 event의 후보는 account/event/cycle capacity가 남아 있으면 계속
   실행한다.
3. 격리된 SELL의 원래 Trade는 open position으로 계속 계산한다. 따라서 account 20개 한도와
   event 1개 한도를 소비하며, 같은 경기의 중복 진입은 허용하지 않는다.
4. BUY/SELL 수량·fee가 모호하거나 confirmed SELL을 Trade에 연결할 수 없는 경제손익 증거 누락은
   기존처럼 전체 신규 진입을 막는다.

## 3시간 자동 격리 종결

- 손절 주문이 거절되거나 동일 token SELL 격리에 걸린 첫 시각을 Trade에 보존한다.
- 가격이 손절선 위로 회복하면 연속 실패 시계를 지운다.
- accepted SELL의 fill/zero-fill 대사가 180분 동안 끝나지 않거나, 손절 제출 실패가 180분 동안
  계속되면 Trade를 `QUARANTINED`로 옮긴다.
- execution ledger 자체를 Trade와 연결할 수 없는 치명적 오류는 중복 SELL 방지를 위해 즉시 같은
  격리 상태로 옮긴다.
- 이 상태는 성공한 매도, 0체결, 포지션 부재 또는 손익을 뜻하지 않는다. `realized_pnl`을 만들지
  않고 position capacity를 유지한다.
- exact confirmed SELL 또는 exact terminal zero-fill이 뒤늦게 확인되는 order-ID 보유 행은
  계속 증거를 읽어 각각 `COMPLETED` 또는 `HOLDING`으로 전환할 수 있다.

## 유지되는 안전 조건

- exact `$5` FOK, account/event/cycle `20/1/5`, emergency SELL cycle당 1건.
- effective stop `max(0.70, confirmed BUY VWAP-0.05)`와 independent Gamma/CLOB OPEN proof.
- `DELAYED` FOK 2분 zero-fill은 exact order/trade 부재와 cancellation 증거가 모두 있을 때만
  허용한다.
- confirmed SELL + proven resolution 경제손익 `<=-$10`이면 신규 BUY를 막는다.
- 1분 cadence가 체결이나 손절 가격을 보장한다고 가정하지 않는다.
- 이 변경으로 arm, sport, 수익성 또는 scale을 판정하지 않는다.

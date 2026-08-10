# 003 — Blueberry·Melon·Quince PENDING_BUY lifecycle fix — 2026-08-10

작성일: 2026-08-10

대상: `golden-blueberry`, `golden-melon`, `golden-quince`

제외: 폐쇄 중인 `golden-date`

## 0. 결론

Blueberry·Melon·Quince의 동기화 DB에서 발견된 `PENDING_BUY` 18건은 미체결 주문이
아니었다. 거래소 주문 상태는 `MATCHED`, fill 상태는 `CONFIRMED`였고, ledger의 대사도
완료돼 있었다. 봇이 거래소 반올림 전 요청 수량과 반올림 후 실제 체결 수량을
`0.000001`주 이내로 같아야 한다고 한 번 더 비교해 정상 체결을 거절한 것이 원인이었다.

세 전략의 판정을 고쳤다. 이제 ledger 대사가 완료된 `MATCHED` 주문은 거래소가 확정한
`latest_size_matched`와 `CONFIRMED` fill 합계가 정확히 같으면 full fill로 인정한다. 취소된
부분 체결은 기존처럼 요청 수량 비교를 통과하지 못하므로 포지션 전체로 오인하지 않는다.

동기화된 DB를 수정 코드로 읽기 전용 재검증한 결과 18/18건이 `HOLDING` 승격 가능 판정을
받았다. 실제 Jenkins DB 상태는 다음 bot 실행의 Phase 1에서 변경된다. 코드 배포만으로 DB를
직접 수정하지 않는다.

## 1. `CRITICAL/HIGH evidence issue`의 쉬운 뜻

이 경고는 곧바로 “전략이 반드시 손실이다” 또는 “모든 주문이 실패했다”는 뜻이 아니다.
쉽게 말하면 **통장에는 거래가 끝났다고 적혀 있는데, 영수증 일부가 없거나 영수증과
통장 수량이 맞지 않는 상태**다.

- `CRITICAL`: 실제 수익·보유 수량의 결론을 바꿀 수 있는 모순이다. 예를 들어 완료 거래에
  exact `CONFIRMED` BUY/SELL fill이 없거나, fill 수량이 주문 원장과 맞지 않는다.
- `HIGH`: 당장 한 거래의 존재를 뒤집지는 않더라도 정확한 손익 계산을 신뢰하기 어려운 큰
  공백이다. 미완료 대사, 오래된 intent, 실패한 run, fee 누락 등이 여기에 해당한다.

따라서 Cherry와 Elderberry에 대해 말할 수 있는 것은 다음과 같다.

1. 일부 exact round trip의 gross 손익은 계산할 수 있다.
2. 하지만 전체 거래의 exact fill coverage와 fee coverage가 불완전하므로 계좌 전체의 정확한
   net 수익이라고 부를 수 없다.
3. 이 상태에서 threshold나 주문액을 “수익 최적화” 목적으로 조정하면 결함이 만든 숫자를
   최적화할 수 있으므로, 먼저 원장 대사와 fee 증거를 복구해야 한다.

Date는 이미 `close_only` 폐쇄 대상이므로 이번 수정과 후속 검토에서 제외했다.

## 2. 왜 `PENDING_BUY`가 위험했는가

실제 관측 예시는 다음과 같다.

```text
반올림 전 봇 요청:       5.43478260869565 shares
거래소 주문 수량:        5.43 shares
latest_size_matched:      5.43 shares
CONFIRMED fill 합계:      5.43 shares
needs_reconciliation:     false
```

기존 코드는 마지막에 `5.43 == 5.43478260869565`도 요구했다. 차이는 거래소의 정상적인
수량 자릿수 제한 때문이지만, 허용 오차가 `0.000001`주뿐이라 full fill이 `false`가 됐다.

`PENDING_BUY`는 “주문을 보냈지만 실제 포지션으로 확정하기 전”의 안전 상태다. 청산 검사는
`HOLDING` 포지션에만 적용된다. 따라서 실제 지갑에는 토큰이 있는데 DB가 계속
`PENDING_BUY`이면 0.97/0.78 같은 익절·손절 및 resolution 관리가 시작되지 않는다.

발견된 범위:

| Strategy / arm | MATCHED + CONFIRMED인데 PENDING_BUY였던 건수 | 수정 후 승격 가능 |
|---|---:|---:|
| Blueberry A / B | 3 / 1 | 4 / 4 |
| Melon low / mid / high | 1 / 1 / 0 | 2 / 2 |
| Quince passive / nearest / cross | 4 / 4 / 4 | 12 / 12 |
| **합계** | **18** | **18 / 18** |

## 3. 수정 계약

full fill은 다음 조건을 모두 만족해야 한다.

1. execution ledger의 `needs_reconciliation = false`
2. `latest_size_matched`가 유한한 양수
3. exact `CONFIRMED` fill 합계와 `latest_size_matched`가 `0.000001`주 이내로 일치
4. 주문 상태가 terminal full-order 상태인 `MATCHED`, 또는 반올림이 없는 legacy 경로에서
   matched 수량과 요청 수량이 기존 허용 오차 안에서 일치

핵심은 `requested_size`가 주문 의도와 감사 증거로는 남지만, `MATCHED` 이후 실제 보유 수량은
거래소가 확정한 matched/fill 수량이라는 점이다. `reconcile_pending_buy()`는 이 수량과 VWAP을
`buy_shares`, `buy_price`, `buy_confirmed_*`에 기록하고 상태를 `HOLDING`으로 바꾼다.

## 4. 검증 결과

- 회귀 테스트: 세 전략 각각 3/3 통과
- 전체 테스트:
  - Blueberry: 342 passed
  - Melon: 323 passed
  - Quince: 323 passed
- 세 프로젝트 source distribution + wheel build 성공
- 모노레포 전략 계약: 19/19 PASS
- 동기화된 실제 DB 읽기 전용 판정: Blueberry 4/4, Melon 2/2, Quince 12/12

회귀 테스트는 실제 관측 형태인 `requested=5.43478260869565`, `MATCHED=5.43`,
`CONFIRMED=5.43`을 재현하고, `PENDING_BUY → HOLDING`과 실제 shares/VWAP 갱신까지 확인한다.
기존 `CANCELED` 부분 체결 테스트도 계속 통과한다.

## 5. Jenkins 재가동 권고

자동 trigger를 바로 켜기 전에 다음 순서가 안전하다.

1. Blueberry 2개, Melon 3개, Quince 3개 job의 자동 trigger를 계속 끈다.
2. 각 workspace가 이 수정 커밋을 받은 것을 확인한다.
3. 여덟 job을 임시 `POLYBOT_LIFECYCLE_MODE=close_only`로 한 번씩 수동 실행한다.
4. 로그에서 `exact full BUY fill로 HOLDING 활성화`와 `pending_buys_activated`를 확인한다.
5. DB/log를 다시 동기화해 기존 18건이 `PENDING_BUY`에서 빠졌는지 확인한다. 시장 상태에
   따라 `HOLDING`, exit 진행 상태, 또는 resolution 상태가 될 수 있다.
6. 청산 관리가 정상임을 확인한 뒤 원래 `active`로 되돌리고 자동 trigger를 켠다.

`close_only` 첫 실행을 권하는 이유는 기존 포지션을 먼저 장부에 올리고 청산 검사를 수행하면서
같은 cycle의 신규 BUY는 막기 위해서다. Date에는 이 절차를 적용하지 않는다.

## 6. Orange 후속 상태

운영자가 `polybot-orange`의 `POLYBOT_BUY_AMOUNT`를 `$500`에서 `$5`로 낮췄다고 확인했다.
이는 위험 축소 권고를 반영한 것으로 타당하다. 다만 이전 `$500` 구간과는 주문 규모 및 실효
유동성 universe가 달라지는 새 cohort이므로, 변경 시점 이후 데이터를 별도로 모아 평가한다.
이번 작업에서는 변경 후 Jenkins config/DB를 다시 동기화하지 않았으므로 운영자 보고 상태로만
기록한다.

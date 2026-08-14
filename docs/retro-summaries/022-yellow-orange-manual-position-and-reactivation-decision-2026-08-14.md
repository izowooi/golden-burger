# 022 — Yellow·Orange 수동 포지션과 active 복귀 판정

- 점검일: 2026-08-14 KST
- 방법: 공개 Polymarket position API, verified `daily-rsync` DB, 최신 redacted Jenkins console
- 변경: 없음. 주문·DB mutation·Jenkins config·코드를 수정하지 않았다.

## Yellow 미매핑 position

공개 wallet의 유효 position은 4개다.

| 분류 | 시장·방향 | 수량 | 조회 시 평가액 | 처리 |
|---|---|---:|---:|---|
| 운영자 수동 보유 | `Lee Jae-myung arrested before 2027?` — No | 약 5,000주 | 약 $4,607 | 계속 수동 관리 |
| 운영자 수동 보유 | `Lee Jae-myung out as president of South Korea in 2026?` — No | 1,000주 | 약 $907 | 계속 수동 관리 |
| 미매핑 정리 대상 | `Erdoğan out by December 31, 2026?` — No | 11.29주 | 약 $10.67 | 운영자가 UI에서 청산 |
| current DB open | UFC 330 main-card 시장 | 6.62주 | 약 $5 | Golden Cherry가 관리 |

세 번째 미매핑 position의 조회 시 평균가는 0.88, 현재가는 0.945, cash P&L은 약
+$0.73이었다. 가격은 시점에 따라 변한다.

Yellow의 `needs_reconciliation=1` submission은 BUY 20 + SELL 3이다. 전부
2026-04-27~07-21의 legacy이고, current DB open token과 겹치는 것은 0, 수정 배포 후
새로 생긴 것은 0이다. 모두 현재 `COMPLETED` 또는 `UNFILLED` row에만 연결된다. 따라서
매 cycle의 오류 16건은 historical evidence 품질과 API 비용 문제이지, 현재 position이나
새 cohort 전체를 막는 operational blocker는 아니다.

두 Lee 시장은 아직 `skipped_markets` operator guard가 없다. 지금은 해결까지 120시간보다
많이 남아 entry universe 밖이지만, 12월에 bot이 같은 시장을 건드리지 않도록 active 복귀
전에 두 condition을 `operator_wallet_guard_preexisting_untracked_position`으로 등록해야 한다.

## Orange 새 PENDING_SELL

Orange의 수동 `Lee Jae-myung arrested before 2027?` — No position은 이미
`operator_wallet_guard_preexisting_untracked_position`으로 보호되어 있다. 계속 수동 보유해도
bot이 해당 시장을 진입 대상으로 사용하지 않는다.

그러나 최신 자연 build에서 bot-managed position 하나가 새로 `PENDING_SELL`이 됐다.

- SELL confirmed full fill: 5.68주
- reconciliation terminal, 수량 일치
- liquidity role: `MAKER`
- `fee_rate_bps=NULL`, `fee_amount_usdc=NULL`
- 공개 wallet에서 해당 token은 잠시 뒤 0으로 사라져 실제 매도 완료와 일치
- DB만 fee evidence 미완결로 `PENDING_SELL` 유지

Polymarket 공식 fee 문서는 platform maker fee가 0이며 maker에게 수수료를 부과하지 않는다고
명시한다. Golden Cherry에는 builder code/fee integration도 없다. 따라서
`confirmed full fill + MAKER + no builder fee`인 경우 누락된 platform fee metadata를
known zero로 인정하는 좁은 보완이 타당하다. TAKER 또는 builder fee가 가능한 fill에는 이
추론을 적용하지 않아야 한다.

Orange의 legacy unresolved submission 2건은 모두 7월의 `COMPLETED` row이고 current open과
겹치지 않으며 수정 배포 후 신규 오류도 0이다. active 복귀를 막는 현재 이유는 legacy 2건이
아니라 위 MAKER `PENDING_SELL` 1건이다.

## 결론과 다음 순서

### Yellow

영구 `close_only`는 필요 없다. 다음 조건 후 active 복귀 가능하다.

1. 운영자가 Erdoğan position을 UI에서 청산한다.
2. 두 Lee condition을 operator wallet guard로 등록한다.
3. 재동기화 후 wallet은 수동 Lee 2개 + current DB-managed position만 남고, DB pending은
   0인지 확인한다.
4. Yellow만 `active`로 전환하고 첫 자연 build 3회를 다시 동기화한다.

legacy fill coverage는 계속 historical limitation이므로 active 복귀와 별개로 과거 수익성
평가에는 사용하지 않는다.

### Orange

지금은 `close_only H/5`를 유지한다.

1. MAKER fee-metadata omission을 known zero로 판정하는 회귀 테스트와 코드를 추가한다.
2. Orange를 `close_only`로 배포해 `PENDING_SELL → COMPLETED`, public wallet 0을 확인한다.
3. 재동기화·verify 후 pending 0이면 active로 복귀한다.
4. 수동 Lee position guard는 이미 있으므로 별도 청산이나 DB adopt가 필요 없다.

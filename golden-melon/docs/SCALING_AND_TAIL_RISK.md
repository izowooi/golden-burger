# Golden Queen 증액·꼬리손실 가이드

## 결론

`POLYBOT_BUY_AMOUNT`만 바꾸면 liquidity, volume24h, open notional이 자동 확장된다. 하지만
자동 계산은 주문 가능성과 전략 수익성을 보장하지 않는다.

```text
$100 → 최소 30일 strict evidence → $200
$200 → 새 30일 cohort strict evidence → $400
$400 → 새 30일 cohort + 별도 자본 승인 → $1,000
```

`$100`은 실제 fill이 발생하기 전에 정한 현재 운용 baseline이며, 과거 성과가 입증한
승격값이라는 뜻은 아니다. 이후에는 중간 단계를 건너뛰지 않는다. hard cap `$1,000`을
넘기는 것은 환경변수 조정이 아니라 새 리스크 설계와 코드 리뷰가 필요한 별도 전략
변경이다.

## 1. 자동 계산

```text
effective_min_liquidity = max($10,000, order / 0.001)
effective_min_volume24h = max($2,000, order / 0.02)
max_open_notional = order * 10
```

| 주문 | liquidity | volume24h | open notional | nominal full-size slots |
|---:|---:|---:|---:|---:|
| $5 | $10,000 | $2,000 | $50 | 10 |
| $100 | $100,000 | $5,000 | $1,000 | 10 |
| $200 | $200,000 | $10,000 | $2,000 | 10 |
| $400 | $400,000 | $20,000 | $4,000 | 10 |
| $1,000 | $1,000,000 | $50,000 | $10,000 | 10 |

`max_positions=20`이어도 open notional이 먼저 작동해 full-size 신규 포지션은 통상 10개가
상한이다. pending BUY/SELL도 노출 계산과 position cap에 포함된다.

## 2. 확률별 tail risk

수수료와 spread 전, YES가 1로 해결될 때의 수익률과 0으로 해결된 한 건을 복구하는 데 필요한
정상 승리 수는 다음과 같다.

| 진입 | 1 해결 gross return | 0 해결 한 건을 복구할 정상 승리 |
|---:|---:|---:|
| 0.90 | 11.11% | 9.0건 |
| 0.92 | 8.70% | 11.5건 |
| 0.94 | 6.38% | 15.7건 |

비용을 넣으면 필요한 승리 수는 더 많아진다. 0.98 take-profit은 tail을 resolution 전에
줄일 기회를 주지만, bid가 0.98에 없으면 실행하지 않으므로 보장된 출구가 아니다.

0.85 stop도 손실을 5~9센트로 보장하지 않는다. gap, no-book, partial fill, API timeout,
resolution 중단으로 훨씬 낮게 체결되거나 체결되지 않을 수 있다.

## 3. metadata와 실제 depth의 차이

Gamma liquidity `$100,000`은 “0.94 이하 ask에 $100 주문이 충분히 있다”는 뜻이 아니다.
Queen은 같은 CLOB snapshot에서 다음을 다시 확인한다.

```text
spread <= 0.02
depth_limit = min(0.94, best ask + 0.01)
ask_depth(depth_limit) >= requested_shares * 1.20
```

이 gate도 BUY 시점만 보호한다. 나중의 stop SELL depth를 예약하지 않는다. 증액 판단에는
entry와 exit의 actual VWAP/slippage를 따로 사용한다.

## 4. 단계별 승격 gate

### 모든 단계 공통: $100 → $200 → $400

- live 30일 이상
- terminal event-effective n 30 이상
- `polybot-retro audit --strict` CRITICAL/HIGH 0
- run/config/Git/job provenance와 cursor-complete sweep coverage 정상
- BUY/SELL exact full confirmed fill 및 fee coverage가 월간 결론을 만들 만큼 충분
- pending/unknown intent와 stale reconciliation 0
- 비용·slippage 후 event-cluster EV > 0
- 한 event를 제거해도 결론 방향이 유지
- 12h 또는 24h cohort를 사전 기준으로 채택하고 다른 knob는 고정
- wallet/order/DB reconciliation과 backup restore 검증 완료

각 증액 후에는 새 `config_hash` cohort에서 위 조건을 다시 충족해야 한다. `$100` 결과로
`$200`과 `$400`을 한꺼번에 승인하지 않는다.

### $400 → $1,000

위 조건을 `$400`의 새 config-hash cohort에서 다시 30일 이상 충족하고 다음을 추가한다.

- `$400` actual 주문의 p95 entry/exit slippage가 승인된 손실 예산 이내
- $1m liquidity / $50k volume universe에서 충분한 event-effective sample 예상
- $1,000 주문 수량의 1.2배 depth가 실제로 반복 관측됨
- market/category별 fee와 maker/taker role 확정
- open notional `$10,000`과 단일-event tail loss를 감당할 별도 자본 승인
- stop no-book/partial/unknown 비율이 0에 가깝고 수동 대응 runbook 검증

## 5. 증액을 즉시 중단할 신호

- accepted order를 fill로 세기 시작함
- fee 누락을 0으로 채움
- BUY와 SELL confirmed size가 다름
- stale reconciliation 또는 uncertain submission 발생
- `market_snapshots`, catalog, event ID, sports clock coverage 결손
- same-event 파생 시장을 독립 표본으로 세어 성과가 좋아짐
- liquidity 기준은 통과하지만 실제 ask/bid depth가 반복적으로 부족
- 한 번의 0 resolution이 한 달 이익 대부분을 제거
- `$100`/`$200`/`$400` 단계가 음수인데 금액을 키워 복구하려 함

## 6. A/B와 scale을 섞지 않는다

첫 달 A/B의 유일한 변경은 진입 상한 시간이다.

| 항목 | 24h job | 12h job |
|---|---:|---:|
| amount | $100 | $100 |
| crossing | 0.90~0.94 | 0.90~0.94 |
| target / stop | 0.98 / 0.85 | 0.98 / 0.85 |
| sports | 포함 | 포함 |
| liquidity / volume / depth | 동일 | 동일 |
| hours max | 24 | 12 |

A/B가 끝나기 전에 한쪽만 `$200`으로 올리면 시간 효과와 금액/체결 효과를 분리할 수 없다.
채택 cohort를 고른 뒤 양쪽 A/B를 종료하고, 새 `$200` 단일 cohort를 시작한다.

## 7. 월간 체크리스트

- [ ] UTC `REVIEW_START`/`REVIEW_END` 고정
- [ ] DB checksum과 online backup manifest 보존
- [ ] strict audit 통과
- [ ] config hash × Git commit × mode × job 분리
- [ ] exact confirmed BUY/SELL size, price, fee coverage
- [ ] pending/partial/unknown과 resolution 분리
- [ ] redeemable/실제 redeem은 미수집으로 명시하고 resolution에서 추정하지 않음
- [ ] event-cluster·category·sports phase 표본
- [ ] archive cadence, run gap, first-crossing lineage coverage
- [ ] actual book depth와 entry/exit slippage
- [ ] 12h/24h paired event 분석
- [ ] KEEP / STOP / SCALE 결정과 rollback 기준 기록

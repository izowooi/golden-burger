# Golden Blueberry 규모 확대와 Tail Risk

이 전략에서 스케일업은 단순히 `POLYBOT_BUY_AMOUNT`를 크게 만드는 일이 아니다. 과거 Cherry는
주문액이 $10에서 $1,000~$8,000으로 급격히 커졌고, 큰 주문 구간에서 주문/유동성 비율과
confirmed-fill coverage가 무너졌다. Blueberry는 이 실패를 코드 hard cap과 자동 시장 gate로
차단한다.

## 초기 단계

- 총 자금: `$300`
- A/B 각각: `$150`
- 주문액: `$5`
- arm open notional: 최대 `$50`
- arm cash reserve: 최소 `$100`
- arm kill switch: 경제손익 `-$30`

`$1`은 0.85~0.93에서 1.08~1.18 shares에 불과해 CLOB 최소 5 shares를 만족하지 않는다.
따라서 `$5`가 최소 실행 단위다.

## 자동 market gate

```text
effective liquidity = max($10,000, order / 0.0005)
effective volume24h = max($10,000, order / 0.0005)
open notional cap   = order × 10
```

| 주문액 | 최소 유동성 | 최소 24h 거래량 | open notional cap |
|---:|---:|---:|---:|
| $5 | $10,000 | $10,000 | $50 |
| $10 | $20,000 | $20,000 | $100 |
| $20 | $40,000 | $40,000 | $200 |
| $40 | $80,000 | $80,000 | $400 |
| $100 | $200,000 | $200,000 | $1,000 |
| $200 | $400,000 | $400,000 | $2,000 |
| $400 | $800,000 | $800,000 | $4,000 |

이 표는 증액 승인을 뜻하지 않는다. metadata 유동성/거래량은 실제 ask depth가 아니므로 주문
직전 동일 CLOB snapshot의 depth 1.2배 gate도 별도로 통과해야 한다.

## 단계별 승격

1. `$5` A/B 30일 또는 arm당 confirmed closed 20건 이상.
2. strict audit와 fee-complete confirmed net 결과 검토.
3. 한 arm을 고르더라도 먼저 `$10` 단일 새 cohort로 승격. 새 runtime `job_name`/DB를 써서
   이전 cohort의 손익이 새 kill switch를 상쇄하지 않게 한다.
4. 최소 20 confirmed closed와 두 시간 half 양수, kill switch 미발동, slippage/coverage 유지 확인.
5. `$20` 이후도 같은 절차를 반복한다. 한 번에 2배보다 큰 점프는 하지 않는다.

코드의 `max_buy_amount_usdc=5`를 바꾸지 않고 env만 올리면 validation이 실패하는 것이 정상이다.
이 hard cap 변경에는 README/STRATEGY/retro와 사전 등록을 함께 갱신한다.

## 왜 stop만으로 tail을 막을 수 없는가

5분 polling 사이에 YES가 0.85에서 0.01로 뛰면 0.78 stop 주문은 0.78에 체결되지 않는다.
stop은 위험을 감지하는 규칙이지 손실의 물리적 상한이 아니다. 실제 상한은:

- 작은 주문액
- event당 1건
- 전체 open notional cap
- 신규 1/cycle
- account drawdown kill switch

에서 나온다. 특히 resolution assumption을 무시하면 SELL fill 없는 손실이 kill switch에서 사라질
수 있으므로 안전 판정에는 별도 채널로 포함하되, 성과 보고에서는 confirmed round trip과 섞지
않는다.

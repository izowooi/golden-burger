# 함대 전체 로그 판정 — 2026-07-28 23:46~00:00 (13개 실행)

`uv run --script tools/jenkins_log_audit.py docs/logs/*.txt` 재현 가능.

---

## 0. 결론

**제가 방금 고친 "부분 체결 무한 재시도"는 지금 함대를 멈추고 있는 원인이 아닙니다.**
현재 매도 실패 113건 중 **111건(98%)이 CLOB intent 격리**입니다. 부분 체결 계열은
1건뿐이고, 그마저 제 수정이 정상 작동했습니다.

그리고 **13개 실행 전부 매도 0건 · 매수 0건**입니다. 함대가 통째로 멈춰 있습니다.

| | 건수 | 비중 |
|---|---|---|
| **quarantined_intent** (CLOB intent 격리) | **111** | **98.2%** |
| market_gone (시장 소멸) | 1 | 0.9% |
| balance_unparsed → `locked_in_own_orders`로 재분류됨 | 1 | 0.9% |

---

## 1. 봇별 판정

12/13이 수정 커밋 `ec4d3ae`로 실행됐습니다(papaya-#2220만 직전 `2d8885b`).

| 전략 | 빌드 | 모드 | 보유 | 매도 | 매수 | 격리 intent | 대사 오류 | 매도 실패 | 판정 |
|---|---|---|---|---|---|---|---|---|---|
| **elderberry** | 49258 | active | 75 | 0 | 0 | **52** | 13 | **46** | 🔴 최악 |
| **honeydew** | 3784 | active | 45 | 0 | 0 | **28** | 8 | **22** | 🔴 |
| honeydew | 5146 | close_only | 22 | 0 | 0 | 18 | 0 | 17 | 🟠 |
| nectarine | 4992 | close_only | 8 | 0 | 0 | 8 | 18 | 8 | 🟠 |
| fig(폐쇄) | 3685 | close_only | 650 | 0 | 0 | 32 | **122** | 6 | 🟠 |
| **nectarine** | 3502 | active | **150/150** | 0 | 0 | 7 | **95** | 6 | 🔴 상한 포화 |
| lime(폐쇄) | 4763 | close_only | 32 | 0 | 0 | 5 | 9 | 4 | 🟠 |
| cherry | 44335 | active | 10 | 0 | 0 | 20 | 16 | 3 | 🟠 |
| cherry | 49577 | active | 57 | 0 | 0 | 3 | 1 | 1 | 🟡 |
| mango(폐쇄) | 4765 | close_only | 10 | 0 | 0 | 0 | 1 | 0 | 🟢 |
| **papaya** | 2220 | active | — | 0 | 0 | **0** | **0** | **0** | 🟢 정상 |
| **papaya** | 2329 | active | — | 0 | 0 | **0** | **0** | **0** | 🟢 정상 |
| **queen** | 588 | active | — | 0 | 0 | **0** | **0** | **0** | 🟢 정상 |

`papaya`·`queen`이 깨끗한 이유는 `simulation_mode: true`라 실주문을 내지 않기
때문입니다. 즉 "문제가 없다"기보다 **"아직 노출되지 않았다"** 로 읽어야 합니다.

---

## 2. 진짜 원인: CLOB intent 격리 (predicate A)

```
결과 또는 대사 증거가 불확실한 이전 CLOB intent가 있어
동일 token/side의 신규 SELL 주문을 보류합니다: 1건
```

이건 잔고 문제가 아닙니다. **주문이 거래소에 도달하기도 전에 클라이언트가 막습니다.**

`assert_submission_allowed`의 두 predicate 중 A입니다:

> `response_status='SUBMIT_OUTCOME_UNKNOWN'` **AND `order_id IS NULL`
> AND `needs_reconciliation=0`**

`reconcile_order_ledger`는 `needs_reconciliation=1 AND order_id IS NOT NULL`만
처리하므로 **A는 대사가 쳐다보지도 않습니다.** CLOB POST가 5xx나 timeout으로
끝나면 그 token/side의 매도가 사람이 개입할 때까지 영구히 잠깁니다.

설계 자체는 옳습니다 — 주문이 실제로 들어갔는지 모르는 상태에서 중복 주문을 막는
것이니까요. 문제는 **시간 기반 에스컬레이션이 없어서 5xx 한 번이 영구 봉쇄가 된다**는
점입니다. golden-cherry 분석에서 이미 지적했고
([진단](golden-cherry-2026-07-parameter-review.md) §2-1), 지금 보니
**함대 전체로 퍼져 있습니다.**

### 대사 오류가 격리를 늘린다

`nectarine-#3502` 대사 오류 **95건**, `fig-#3685` **122건**. 대사가 실패하면
predicate B로 또 격리됩니다. 즉 격리 재고가 계속 쌓입니다.

---

## 3. 제 수정은 정상 작동했습니다 (단, 지금은 소수 사례)

`honeydew-#3784`에서 정확히 설계대로 발동했습니다:

```
매도 수량을 CLOB 가용 잔고 기준으로 축소해 1회 재시도
  - token=3024601857887977 요청=10.752688 가용=8.860000 제출=8.771400
```

`8.86 × 0.99 = 8.7714` — 안전계수까지 정확합니다.

**다만 재시도도 거절됐고, 그 이유가 새로운 발견이었습니다:**

```
balance: 8860000, sum of active orders: 8860000,
sum of matched orders: 0, order amount (inc. fees): 8770000
```

**잔고 전액이 자기 자신의 미체결 주문에 묶여 있습니다.** 부분 체결이 아니라
**직전에 낸 매도 주문이 체결되지 않고 호가에 남아 토큰을 잠근 것**입니다.
수량을 줄여도 절대 팔리지 않습니다 — 기존 주문을 먼저 취소해야 합니다.

거절 메시지 형식이 두 가지였는데 제 정규식이 첫 번째만 파싱했습니다. 수정했습니다:

- 정규식을 두 형식 모두 지원하도록 확장
- `locked_in_own_orders` 분류 추가
- 이 경우 **축소 재시도를 하지 않도록** 가드 추가 (무의미한 호출 방지)
- 11개 전략 테스트 1,400건 통과

---

## 4. 부수 발견: nectarine 포지션 상한 포화

```
포지션 현황 - 보유 150/150 (매수 0, 상한 스킵 220, 쿨다운 스킵 123, 매수 실패 0)
```

후보 343개를 찾고도 **상한 150/150에 걸려 220건을 스킵**했습니다. cherry가
2026-07-22~28 엿새간 멈췄던 것과 같은 구조입니다. 격리로 매도가 안 되니 보유가
빠지지 않고, 보유가 안 빠지니 상한에 걸려 매수도 안 됩니다.

---

## 5. 조치

### 5-1. 격리 해제가 최우선 (매도 111건을 막고 있음)

```bash
uv run --project polybot-observability polybot-retro probe-intent \
  --db golden-<name>/data/default/trades.db --strategy golden-<name> \
  --submission-id <id> --window-seconds 86400
```

대상 submission_id 목록:

```sql
SELECT submission_id, substr(submitted_at,1,16), token_id
FROM order_submissions
WHERE side='SELL' AND response_status='SUBMIT_OUTCOME_UNKNOWN'
  AND order_id IS NULL AND outcome_resolution IS NULL;
```

**주의**: `--window-seconds`는 최대 24시간인데 격리된 intent는 며칠 전 것이
많습니다. CLOB 이력이 못 미치면 후보가 비고, **후보가 비었다는 사실은
`NO_ORDER_CREATED`의 증거가 아닙니다**. 그 경우 Polymarket UI에서 해당 token의
열린 주문을 눈으로 확인한 뒤 판단해야 합니다.

우선순위: **elderberry(52) → honeydew-#3784(28) → cherry-#44335(20) →
honeydew-#5146(18)**.

### 5-2. 자기 주문에 묶인 토큰은 기존 주문 취소

`honeydew`에서 확인된 케이스입니다. `tools/wind_down.py cancel --side SELL`로
미체결 매도 주문만 취소한 뒤 재시도하면 풀립니다.

### 5-3. nectarine 상한

격리를 풀어 매도가 돌기 시작하면 자연히 해소됩니다. 급하면
`tools/reconcile_positions.py`로 지갑 대조 후 정리하세요.

### 5-4. 폐쇄된 3개는 격리를 풀 필요 없습니다

fig·lime·mango는 `close_only`로 정상 전환됐습니다. 잔여 포지션은 지갑 대조 후
수동 정리하면 되고, 격리 해제에 시간을 쓸 이유가 없습니다.

---

## 6. 정정

앞선 회고에서 lime의 `not enough balance` 1,155건을 근거로 **"부분 체결 무한
재시도가 다른 봇에도 있는지 확인이 필요하다"** 고 했습니다. 확인 결과:

- **그 문제는 실재하고 방어도 넣었지만, 지금 함대를 멈추는 원인이 아닙니다.**
  현재 로그에서 부분 체결 계열은 1건(0.9%)입니다.
- 이유는 **격리가 먼저 막기 때문**입니다. 주문이 CLOB에 도달하지 못하니 잔고
  문제가 드러날 기회조차 없습니다.
- 즉 격리를 풀면 그 뒤에 부분 체결 문제가 드러날 가능성이 큽니다. 방어를 미리
  넣어둔 것은 결과적으로 맞는 순서였습니다.

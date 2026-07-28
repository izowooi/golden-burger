# 매도 무한 재시도 루프 — 진단과 방어 (2026-07-28)

## 증상

매도 주문이 거절되면 `execute_sell`의 실패 분기가 **trade 상태를 바꾸지 않고
`return False`만** 한다. trade는 `HOLDING`으로 남고, 다음 사이클에 같은 주문이
다시 나간다. 5분 주기면 하루 288회, 영원히.

부작용은 두 가지다.

1. CLOB API 호출 낭비 (관측된 최악: 단일 token 4,002회 / 17일)
2. **해당 포지션이 `max_positions`를 영구히 잠식**한다 — golden-cherry가 이것 때문에
   2026-07-22~28 엿새간 신규 매수를 한 건도 못 했다

## 실측 (`tools/sell_retry_audit.py`)

```
### golden-cherry
  실패한 매도 제출 73,252건 / 401 token = 토큰당 평균 182.7회
    zero_balance         44,202
    partial_balance      23,100 ✔축소 가능
    market_gone           5,420 ⛔영구
    dust_unsellable         417 ⛔영구

### golden-fig (폐쇄)
  실패한 매도 제출 11,777건 / 1,657 token = 토큰당 평균 7.1회
    market_gone           4,185 ⛔영구   ← 단일 token 2,092회 x 2개
    partial_balance       3,946 ✔축소 가능
    dust_unsellable       1,663 ⛔영구

### golden-lime (폐쇄)
  실패한 매도 제출 6,311건 / 143 token = 토큰당 평균 44.1회
    partial_balance       4,830 ✔축소 가능
    dust_unsellable       1,273 ⛔영구

### golden-mango (폐쇄)
  실패한 매도 제출 4건 — 정상
```

## 원인은 셋이다

### 1. 부분 체결 잔고에 맞춰 수량을 줄이지 않음 (`partial_balance`)

`size=trade.buy_shares`로 DB 수량을 그대로 제출한다. GTC 매수가 부분 체결되면
실제 잔고가 그보다 작아 매번 거절된다. 실제 거절 메시지:

```
not enough balance / allowance: the balance is not enough
  -> balance: 2031079975, order amount: 9132420000
```

DB는 9,132.42주라 믿고 지갑엔 2,031.08주뿐이다.

### 2. 잔고와 '정확히 같은' 수량을 제출 (`balance_edge`)

```
-> balance: 9248547141, order amount: 9248550000
```

**차이가 0.002859주**다. 잔고 클램프 이후 클라이언트의 수량 반올림이 위로 올려
거절된다. golden-cherry에서 같은 token이 이 사유로 1,469회 거절됐다.

### 3. 유령 판정이 취소 증거를 못 얻으면 보류됨 (`zero_balance`)

`_mark_unfilled`가 `cancel_order`에 실패하면
`"유령 포지션 판정 보류 - ... HOLDING 유지"`를 남기고 반환한다. 상태가 안 바뀌므로
다시 무한 재시도. cherry의 zero_balance 44,202건 대비 실제 종결은 178건뿐이다.

이건 **의도된 fail-closed 설계**다(`docs/retro/EVIDENCE_CONTRACT.md`: evidence gap을
추정값으로 채우지 않는다). 자동 종결로 바꾸면 계약 위반이므로, 대신 **관측 가능하게**
만들고 운영자 도구로 해소한다.

## 적용한 방어

### A. 축소 재시도 — 원인 1·2 해결 (7개 전략에 이식)

`apple, banana, date, elderberry, grape, honeydew, orange`에
`_place_sell_with_balance_retry`를 추가했다. `nectarine`·`papaya`에 이미 있던 검증된
구현을 그대로 옮겼다.

```python
result = place(requested_size)
if 성공: return
available = 거절 메시지에서 파싱
if available is None or available <= 0: return   # 기존 유령 경로가 처리
basis = min(available, requested_size)
retry_size = floor(basis * 0.99)                  # 안전계수
if retry_size < MIN_ORDER_SIZE: 로그 남기고 return  # 먼지는 팔 수 없음
retry = place(retry_size)                         # 재시도는 정확히 1회
```

- **안전계수 0.99**: 원인 2를 해결한다. 잔고와 같은 수량은 거절되므로 여유를 둔다.
- **재시도 1회 제한**: 무한 재시도가 원래 문제였으므로 반복하지 않는다.
- 성공 시 실제 제출 수량으로 P&L을 계산한다(`sell_shares`).

`cherry`는 잔고 preflight 방식의 자체 구현이 있어 구조는 두고 진단만 추가했다.
`queen`은 exact-size 원칙으로 **의도적으로 재시도하지 않는다**(문서화된 설계) —
진단만 추가했다.

### B. 거절 사유 분류 + 진단 로그 (11개 전략 전부)

```python
classify_sell_failure(result, requested_size, min_order_size) -> str
# market_gone | dust_unsellable | partial_balance | balance_edge
# | zero_balance | transient | balance_unparsed | other
```

실패할 때마다 한 줄 남긴다:

```
매도 실패 진단 - 사유=partial_balance trade=123 token=7476839581516646 요청=8839.779006 가용=48.400000
```

Jenkins 로그에서 `grep "매도 실패 진단"` 으로 바로 집계할 수 있다.

### C. 진단 도구

```bash
uv run --script tools/sell_retry_audit.py golden-<name>/data/default/trades.db
```

DB 전 구간을 한 번에 분류한다. 로그를 기다릴 필요가 없다.

## 남은 영구 실패는 자동 종결하지 않는다

`market_gone`(시장 해결·상장폐지)과 `dust_unsellable`(최소 주문량 미만)은 재시도해도
절대 성공하지 않는다. 그래도 **코드가 자동으로 종결하지 않는다** — 상태를 임의로
바꾸는 것은 evidence 계약 위반이고, 11개 봇의 실거래 포지션을 검증 없이 마감하는
위험이 크기 때문이다.

대신 운영자가 처리한다:

```bash
# 지갑 실보유와 대조해 정리 (공개 API, private key 불필요)
uv run --script tools/reconcile_positions.py \
  --db golden-<name>/data/default/trades.db --funder <공개 주소>
```

## 검증

- 11개 전략 전체 테스트 **1,389건 통과**
- 각 전략에 `tests/test_sell_failure_defense.py` 추가 (분류 7건 + 재시도 2건)
- 테스트의 거절 메시지는 실제 CLOB 응답 형식을 그대로 사용한다
- `uv run tools/verify_strategy_contracts.py` → PASS (14 strategies)

## 확인 방법

다음 사이클 Jenkins 로그에서:

```bash
grep "매도 실패 진단" <로그>          # 사유별 발생 여부
grep "축소해 1회 재시도" <로그>       # 방어가 실제로 동작했는지
grep "영구히 매도 불가" <로그>        # 운영자 개입이 필요한 포지션
```

`매도 실패 진단`이 한 건도 없으면 그 봇은 이 문제가 없는 것이다.

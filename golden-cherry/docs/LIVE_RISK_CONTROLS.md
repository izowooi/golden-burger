# Golden Cherry 라이브 손실 방어 계약

> 적용 대상: **golden-banana 계좌에서 운용하는 golden-cherry 전략**
>
> 작성일: 2026-07-22

## 결론

`12~120시간`이 최적이라는 결론은 내릴 수 없다. 계좌명을 golden-banana로 바로잡아 확인한
데이터에서도 12시간 미만 구간을 제거해야 한다는 충분한 표본은 없었고, 7월 손실과 더 직접적으로
연결된 문제는 다음 네 가지였다.

1. 스포츠의 정산용 `endDate`를 실제 경기시각으로 오인했다.
2. 720시간 진입창에서 기존 후보가 한꺼번에 유입됐다.
3. $8,000/$1,000 주문과 무제한 포지션이 손실 노출을 키웠다.
4. DB 수량이 실제 conditional-token 잔고보다 아주 조금 커 손절 SELL 전체가 거절됐다.

따라서 비스포츠 진입창은 사용자가 의도한 `0 < endDate 잔여시간 <= 120시간`으로 유지한다.
스포츠는 경기 전 `gameStartTime`까지 120시간 이내이거나, 경기 시작 뒤 아직 주문 가능한
인플레이 시장이면 후보가 된다. 기본 시간청산은 0시간(비활성)이며, 이 변경은 기존 포지션을
시간 범위 밖이라는 이유로 매도하지 않는다.

## 구현한 방어선

### 1. 스포츠 기준시각

- 경기 전에는 `gameStartTime`까지 남은 시간이 0~120시간일 때 후보가 된다.
- 경기 시작 직전 buffer는 두지 않는다. 시작 5분 전과 경기 중에도 확률 조건을 만족하면 후보가 된다.
- 경기 시작 뒤에는 `POLYBOT_ALLOW_IN_PLAY=true`이고 Gamma가 주문 가능 상태로 제공하는 동안
  인플레이 후보가 된다.
- `sportsMarketType`은 있지만 `gameStartTime`이 없거나 파싱할 수 없으면 fail closed한다.
- 긴 Gamma keyset 순회 중 pregame이 in-play로 바뀔 수 있으므로 주문 직전에도 다시 분류한다.
- DB에는 `market_game_start_time`, `minutes_until_game_start_at_buy`,
  `entry_time_reference`, `hours_until_entry_deadline_at_buy`, `sports_market_type`,
  `sports_phase_at_buy`을 기록한다.

`gameStartTime`은 실제 경기의 남은 시간을 제공하지 않는다. 따라서 이 설정은 “경기 중”을
허용할 수는 있지만 “축구 종료 5분 전”을 식별할 수는 없다. 그 조건은 별도의 live clock
데이터가 있어야 구현할 수 있다.

### 2. 주문과 누적 노출

- 건당 주문 하드캡: $100
- 정적 최소 유동성: $50,000
- 주문금액/유동성 상한: 0.2% (`$100 / 0.002 = $50,000`)
- open 포지션 상한: 100개
- open 요청원금 상한: $5,000
- Jenkins cycle 한 번의 신규 포지션 상한: 5개

`max_buy_amount_usdc`는 운영 주문값과 별개의 하드캡이다. 주문을 키우려면
`POLYBOT_BUY_AMOUNT`와 `POLYBOT_MAX_BUY_AMOUNT_USDC`를 모두 바꿔야 한다. 0.2% 규칙에서
$1,000 주문은 $500,000, $10,000 주문은 $5,000,000의 최소 Gamma 유동성을 요구한다.
$10,000 / $500,000 = 2%이므로 $500,000은 현재 규칙보다 10배 공격적이다. 또한 $10,000
주문을 허용하려면 `POLYBOT_MAX_OPEN_NOTIONAL_USDC`도 적어도 $10,000 이상이어야 한다.
이중 변경은 실수로 과거 $8,000 설정이 재현되는 것을 막기 위한 의도적인 마찰이다.

### 3. 실제 잔고 기준 SELL

- 주문 전 authenticated CLOB conditional-token balance를 조회한다.
- 최초 SELL 수량은 `min(DB buy_shares, 실제 잔고)`를 6자리 소수로 내린 값이다.
- CLOB 오류가 더 작은 실제 잔고를 알려주면 그 값에서 1 micro-share만 뺀 뒤 한 번 재시도한다.
- 숫자 없는 CLOB cache 오류에는 99% fallback을 한 번 사용한다.
- fallback 뒤 5주 이상의 잔여가 있으면 `HOLDING`으로 유지하고 다음 cycle에서 다시 판다.
- 5주 미만의 주문 불가능한 dust만 남으면 반복 거절을 멈춘다.

과거 사례의 DB 수량 `9248.554900`과 실제 잔고 `9248.547141` 차이는 이제 최초 주문 전에
보정된다. 99% 고정 축소를 모든 대형 포지션에 적용해 큰 잔여를 만드는 방식은 사용하지 않는다.

## 운영 시 주의

- 기존 HOLDING/격리/대기 행도 노출 상한에 포함한다. 배포 시 이미 $5,000 또는 100개를
  넘었다면 기존 청산은 계속하지만 신규 BUY는 중단된다.
- 이 차단을 해제하려고 DB 행을 임의 삭제하거나 상한만 높이지 않는다. 먼저 CLOB order/fill과
  실제 지갑 포지션을 대사한다.
- Gamma `liquidity`는 실제 체결 가능한 동일 가격대의 book depth와 같지 않다. 0.2% 제한은
  최소 안전선이지 $100 이상 scale-up의 충분조건이 아니다.
- $100 → $1,000 → $10,000 확대 전에 각 단계에서 ask-side order-book depth, 예상 평균
  체결가, slippage 상한을 검증한다. 총 Gamma 유동성 숫자만 보고 $10,000으로 올리지 않는다.
- run/order/fill audit의 strict evidence 문제가 남아 있으므로, 이 변경만으로 수익성이
  검증됐다고 해석하지 않는다.

## Jenkins 권장값

```bash
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=120
export POLYBOT_EXIT_HOURS=0
export POLYBOT_GAME_START_FILTER_ENABLED=true
export POLYBOT_ALLOW_IN_PLAY=true
export POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START=true

export POLYBOT_BUY_AMOUNT=100
export POLYBOT_MAX_BUY_AMOUNT_USDC=100
export POLYBOT_MIN_LIQUIDITY=50000
export POLYBOT_MAX_ORDER_LIQUIDITY_RATIO=0.002
export POLYBOT_MAX_POSITIONS=100
export POLYBOT_MAX_OPEN_NOTIONAL_USDC=5000
export POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE=5
export POLYBOT_YES_ONLY=true
```

private key와 funder address는 golden-banana 계좌의 Jenkins Credentials Binding으로 주입한다.
전략 디렉터리와 DB/run audit 이름은 계속 golden-cherry다.

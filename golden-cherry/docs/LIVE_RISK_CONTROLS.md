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

따라서 진입창은 사용자가 의도한 `0 < 남은시간 <= 120시간`으로 유지한다. 스포츠는
`gameStartTime`, 비스포츠는 `endDate`를 남은시간의 기준으로 사용한다. 기본 시간청산은
0시간(비활성)이며, 이 변경은 기존 포지션을 시간 범위 밖이라는 이유로 매도하지 않는다.

## 구현한 방어선

### 1. 스포츠 기준시각

- `gameStartTime`이 있는 시장은 그 시각까지 남은 시간이 0~120시간일 때만 후보가 된다.
- 경기 시작 5분 전부터 신규 BUY를 차단한다.
- `sportsMarketType`은 있지만 `gameStartTime`이 없거나 파싱할 수 없으면 fail closed한다.
- 긴 Gamma keyset 순회 중 경기가 시작될 수 있으므로 주문 직전에도 다시 검사한다.
- DB에는 `market_game_start_time`, `minutes_until_game_start_at_buy`,
  `entry_time_reference`, `hours_until_entry_deadline_at_buy`, `sports_market_type`을 기록한다.

### 2. 주문과 누적 노출

- 건당 주문 하드캡: $100
- 정적 최소 유동성: $50,000
- 주문금액/유동성 상한: 0.2% (`$100 / 0.002 = $50,000`)
- open 포지션 상한: 100개
- open 요청원금 상한: $5,000
- Jenkins cycle 한 번의 신규 포지션 상한: 5개

`max_buy_amount_usdc`는 운영 주문값과 별개의 하드캡이다. 예를 들어 주문을 $1,000으로
키우려면 `POLYBOT_BUY_AMOUNT`와 `POLYBOT_MAX_BUY_AMOUNT_USDC`를 모두 바꿔야 하며,
0.2% 규칙 때문에 최소 유동성도 자동으로 $500,000까지 올라간다. 이중 변경은 실수로 과거
$8,000 설정이 재현되는 것을 막기 위한 의도적인 마찰이다.

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
- run/order/fill audit의 strict evidence 문제가 남아 있으므로, 이 변경만으로 수익성이
  검증됐다고 해석하지 않는다.

## Jenkins 권장값

```bash
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=120
export POLYBOT_EXIT_HOURS=0
export POLYBOT_GAME_START_FILTER_ENABLED=true
export POLYBOT_GAME_START_BUFFER_MINUTES=5
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

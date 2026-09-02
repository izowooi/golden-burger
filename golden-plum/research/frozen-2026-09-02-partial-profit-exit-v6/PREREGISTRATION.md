# Golden Plum 부분 익절·전량 손절 실행 보정 v6 — 2026-09-02

## 변경 경계

- 진입 `[0.75,0.78]`, 3회 확인, 누적 `+0.02`, King/Queen 익절
  `0.90/0.95`, 손절 `confirmed BUY VWAP-0.15`, 1분 주기는 바꾸지 않는다.
- 축구·MLB·NFL·NBA 모집단과 Silver/Gold 수집 계약도 바꾸지 않는다.
- 현재 live 목표 금액은 계속 `$5`다. 이번 보정은 향후 목표 금액을 늘렸을 때의
  실행 가능성과 증거 보존을 위한 것이며 증액 승인을 뜻하지 않는다.
- 기존 DB는 additive migration만 수행하고 clean, 과거 행 rewrite, 추정 backfill을
  하지 않는다. v5 이전과 v6은 `strategy_source_digest`로 분리한다.

## 부분 익절 계약

- confirmed 실제 잔여 보유량을 기준으로 fresh CLOB bid 전체를 한 번 읽는다.
- 각 bid 가격이 해당 arm의 절대 익절가 이상인 수량만 익절 가능 깊이로 센다.
  높은 가격의 초과수익으로 목표가 아래 bid를 평균 보정하지 않는다. FOK limit 자체가
  모든 share의 최저 허용 가격을 보장해야 하기 때문이다.
- SDK의 SELL 정밀도인 `0.01 share`로 내림하고, 가능한 최대 수량을 한 번의 FOK로
  제출한다. 제출한 수량은 전량 체결 또는 0체결이어야 한다.
- 부분 익절 후 잔여 수량이 venue 최소 `5 shares`보다 작아질 경우, 잔여가 최소
  5 shares가 되도록 제출량을 줄인다. 제출량도 5 shares 미만이면 그 주기는 주문하지
  않는다. 피할 수 있는 잔여 소액 포지션을 만들지 않는다.
- exact terminal fill과 fee가 확인된 뒤에만 누적 매도수량·누적 실현손익을 갱신한다.
  잔여 수량이 있으면 같은 Trade를 `HOLDING`으로 돌려 다음 주기에 다시 평가한다.
- 거래소가 FOK 요청의 일부만 terminal fill로 확정하는 예외가 생겨도, 확인된 실제
  수량만 반영하고 잔여를 계속 보유한다. 불확실한 fill은 추정하지 않는다.

예를 들어 1,000 shares를 보유하고 익절가 이상 bid가 300 shares뿐이면 300 shares만
FOK로 익절하고 700 shares는 유지한다. 이후 익절 가능 bid가 다시 생기면 잔여에 대해
독립적으로 반복한다.

## 손절 계약

- 손절은 confirmed 잔여 보유량의 SDK-signable 전량만 FOK로 제출한다.
- 전량 displayed bid depth가 없으면 일부 손절하지 않는다. 최초 실패 시각과 현재
  best bid/depth를 기록하고 다음 1분 주기에 재시도한다.
- 주문 거절·호가 부족·대사 지연은 해당 event와 그 한 포지션에만 격리한다. 다른
  event 후보는 남은 position/cycle 한도에서 계속 처리한다.
- 180분 후에도 SELL 노출을 확정하지 못하면 성공 청산이나 0체결로 꾸미지 않고
  `QUARANTINED` 경제 노출로 보존한다. 해결 증거가 확인되면 잔여 수량만 payout으로
  정산한다.

## 누적 손익과 해결

- `sell_shares`와 `realized_pnl`은 exact confirmed SELL들의 누적값이다.
- 현재 경제적 잔여량은 `buy_shares`, 원래 BUY fill은 `buy_confirmed_size`로 분리한다.
- 각 SELL lot에는 원래 BUY fee를 share 비율로 배분한다. 누적 proceeds, SELL fee,
  배분된 BUY fee와 SELL 횟수를 별도 열에 저장한다.
- 부분 익절 뒤 market이 해결되면 이미 실현한 손익은 유지하고, 잔여 shares에 대한
  payout 손익만 `settlement_pnl_assumption`에 저장한다. 두 값을 중복 합산하거나
  과거 SELL을 합성 settlement로 덮어쓰지 않는다.

## 실행 증거와 시간 예산

- 외부 SELL POST 전에 fresh book, 종목·리그, trigger, 보유/선택/잔여/최대 가능
  수량과 금액, VWAP, limit, 선택 사유를 `exit_execution_observations`에 append-only로
  커밋한다. 저장 실패 시 주문을 내지 않는다.
- holding batch에서 이미 받은 canonical book으로 첫 판단을 하고, 기존과 동일하게
  lifecycle preflight 뒤 fresh book 한 번만 다시 읽는다. 부분 익절 계산은 로컬이며
  API 호출을 추가하지 않는다.
- 한 주기는 60초 미만이어야 한다. 50초 deadline 경고, lock overlap, 추가 per-size
  book 요청이 발생하면 배포를 중단한다.

## 판정 제한

- 현재 `$5` A/B에서 두 arm의 유일한 처치는 계속 익절가 하나다.
- 종목별 해결 event 100개, arm당 confirmed closed 50개, common event 30개와
  fill/fee gap 0을 충족하기 전에는 증액이나 종목별 파라미터를 선택하지 않는다.
- displayed depth는 실행 가능성 자료이지 실제 fill이나 수익성 증거가 아니다.

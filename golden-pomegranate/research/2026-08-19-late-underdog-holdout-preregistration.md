# Late Underdog Discount — holdout 사전 고정

작성 시각: 2026-08-19 KST

상태: exploratory candidate의 첫 독립 구간 검증. live 승격 근거가 아님.

## 데이터 분할

- 전체 관측 범위: `[2026-08-07T00:00:00Z, 2026-08-19T00:00:00Z)`
- 탐색 구간: `[2026-08-07T00:00:00Z, 2026-08-13T00:00:00Z)`
- 미사용 holdout: `[2026-08-13T00:00:00Z, 2026-08-19T00:00:00Z)`
- 8월 6일은 partial day이므로 제외한다.
- holdout 계산 전에 아래 규칙과 판정 기준을 고정하며, 결과를 본 뒤 가격대나 시간대를
  바꾸지 않는다.

## 고정 가설

스포츠 시장에서 예정 종료까지 6시간 이내로 처음 들어오는 시점에 시장이 낮게 평가한
선택지가 과도하게 할인될 수 있다. 탐색 구간에서 가장 강했던 가격대만 primary로 두고,
바로 위 가격대를 adjacent control로 둔다.

## point-in-time 진입 규칙

1. Gamma tag에 `sports`가 있고, `active=true`, `closed=false`,
   `enableOrderBook=true`, `acceptingOrders=true`인 표준 2-outcome 시장만 사용한다.
2. 같은 `condition_id`의 연속 관측에서 남은 시간이 `>6h`에서 `(0h, 6h]`로 처음
   교차해야 한다.
3. 두 관측의 `end_date`가 같고 수신 간격이 5~30분이어야 한다. 최초 관측부터 이미 6시간
   이내인 시장은 제외한다.
4. 두 outcome price 합이 `[0.98, 1.02]`이고, 낮은 가격 outcome을 underdog으로 정의한다.
5. Gamma 실행가격 proxy는 outcome 0이면 `best_ask`, outcome 1이면
   `1 - best_bid`다. Gamma spread는 `<=0.03`이어야 한다.
6. Pomegranate 수집 envelope에 의해 누적 거래량 `>=2,000`, 유동성 `>=10,000`이 이미
   적용된다.
7. primary arm은 underdog ask `[0.10, 0.20)`, adjacent control은 `[0.20, 0.30)`이다.
8. 한 condition에는 첫 교차 한 번만 허용하며, 해결까지 보유한다. TP/SL은 두지 않는다.

Gamma quote는 체결 보장이 아니다. 같은 cycle에 해당 token의 CLOB snapshot이 있을 때만
별도의 exact-book subset으로 계산한다. 후속 simulation은 모든 후보에 CLOB을 직접 조회해
이 한계를 없애야 한다.

## 라벨과 누수 방지

- 분석 cutoff 이전에 관측된 `closed=true AND one_hot=true`의 최신 결과만 라벨로 쓴다.
- `redeemable`/`resolved` 필드는 현재 collector 결함 때문에 primary label 조건으로 쓰지
  않는다. 이 우회는 분석 보고서에 명시한다.
- holdout에서 탐색 구간에 등장했던 `event_id`는 제거한 event-purged 결과를 primary로 쓴다.
- 미해결 신호를 손실/승리로 추정하지 않고 label coverage 분모에 남긴다.

## 판정 기준

다음을 모두 충족해야 "후속 simulation A/B 후보"로만 인정한다.

- event-purged holdout에서 primary 라벨 50건 이상, 독립 event 30개 이상
- primary label coverage 80% 이상
- primary의 1주당 평균 edge(`outcome - entry ask`)가 양수
- event-equal gross ROI가 양수
- 진입가에 1 cent adverse move를 더한 stress에서도 event-equal ROI가 양수
- event-cluster bootstrap 95% CI와 adjacent control 결과를 함께 공개

현재 공식 sports taker fee 식 `shares × 0.03 × p × (1-p)`을 모든 신호에 보수적으로
적용한 sensitivity도 별도로 공개한다. 실제 simulation에서는 시장별 `feesEnabled`와 fee schedule을
진입 시점에 저장해야 한다.

CI가 0을 포함해도 위 방향성 gate를 충족하면 장기 accountless simulation 후보일 뿐이다.
하나라도 실패하면 현재 자료로 전략을 확정하지 않고 30일 원자료를 기다린다. 어떤 결과든
실거래, 자금 배정, P&L 주장은 금지한다.

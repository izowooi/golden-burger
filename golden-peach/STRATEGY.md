# Golden Peach — 킥오프 선두 추종

## 검증할 가설

경기 시작 직후 HOME/DRAW/AWAY 결과 명제의 직접 YES와 NO 가운데 표시 호가가 가장 높은
토큰은 짧은 시간 안에 소폭 더 상승하는 경향이 있는가를 검증한다. 최종 결과 중 하나가 1이
된다는 사실만으로 경기 초반 선두가 먼저 상승하는 것은 아니며, NO 세 토큰은 상호 배타적이지
않아 둘 이상이 1로 해결될 수 있다. 이 가정은 데이터로 반증 가능해야 한다.

## 모집단

- 종목: 축구.
- 리그: EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL.
- event와 market이 active/open/order-taking이고 실제 source clock이 있어야 한다.
- 정규시간+추가시간(90분 stoppage) 결과만 포함하고 연장·승부차기/진출/핸디캡/prop은 제외한다.
- Gamma server-side cumulative volume `$5,000`, liquidity `$5,000` gate 후 exact `$5` CLOB
  depth를 최종 gate로 쓴다.

## 같은 시각의 6개 후보

각 event에서 HOME YES/NO, DRAW YES/NO, AWAY YES/NO의 direct token을 모두 식별한다.
각 token의 전체 ask/bid level, best bid/ask, spread, exact `$5` ask VWAP을 저장한다. 하나라도
빠지면 event 전체를 건너뛴다. midpoint로 순위를 매기며 선두 차이가 0.005 미만이면 동률로
보아 진입하지 않는다.

예정 kickoff 이후 경과시간은 진입 증거가 아니다. source `elapsed/period`가 0~10분임을
증명해야 한다. 2H의 period-relative minute와 `90+N`을 명시적으로 정규화한다.

## 실행 계약

- BUY notional exact `$5`, FOK.
- entry exact ask VWAP `[0.60, 0.94]`, spread `<=0.05`.
- total/event/new-per-cycle 한도 `10/1/5`.
- event당 filled/uncertain entry 한 번. TP/SL 뒤에도 재진입 금지.
- accepted/DELAYED 응답은 fill이 아니다. full fill size/VWAP/fee를 대사하기 전에는
  `PENDING_BUY` 또는 `PENDING_SELL`을 유지한다.
- exact terminal zero-fill만 fresh observation에서 다시 시도할 수 있다.

## A/B와 종료

- Eco A: confirmed entry VWAP `+0.03`.
- Fruit B: confirmed entry VWAP `+0.05`.
- 공통 SL: confirmed entry VWAP `-0.10`.
- source minute 80부터 정상 TP의 절반을 충족하면 이익 청산한다.
- minute 80부터 손실 중이면 신규 stop을 제출하지 않고 one-hot resolution까지 보유한다.
- source minute를 증명하지 못하면 late 상태를 배제할 수 없으므로 새 stop은 fail closed한다.
- TP/SL은 best quote만이 아니라 전체 보유량의 executable VWAP으로 검증한다.

## 실패 격리

명확한 주문 전 오류와 동기식 0주문 거절은 해당 후보만 실패시키고 다음 event를 계속 처리한다.
BUY/SELL 상태 불명확도 해당 event에만 국소화하며, 실제일 수 있는 노출 1칸은 포지션 한도에
예약한다. 따라서 한 주문이 불명확해도 남은 한도에서는 다른 event 진입과 기존 holding 관리를
계속한다. 단, 원장 자체가 주문 전후를 구별할 수 없게 손상된 경우와 실제 예약 노출이 10칸에
도달한 경우에는 추가 BUY를 중지한다.

BUY/SELL pending 또는 연속 청산 실패가 180분을 넘으면 `QUARANTINED`로 옮기되, 미체결·성공
매도·실현 손익으로 꾸미지 않고 위험 한도를 계속 예약한다. 같은 event에는 재진입하지 않으며,
뒤늦은 exact fill/zero-fill 또는 resolution 증거로만 상태를 바꾼다.

## 자료와 판정

Grey는 live와 같은 1분 모집단을 credential 없이 수집한다. raw direct YES/NO order-book과
source clock을 보존해 다른 TP/SL을 사후 재생할 수 있게 한다. 표시 호가 반사실은 actual fill이나
realized P&L이 아니다.

판정은 단일 `config_hash × strategy_source_digest × mode × job_name` cohort에서 수행한다.
첫 24시간은 수집 건전성만, 신규 진입 종료 뒤에는 confirmed execution/fee와 resolution coverage를
먼저 검증한다. CRITICAL/HIGH 증거 문제가 있으면 파라미터나 수익성을 판단하지 않는다.

## 기각 기준

- direct six-book 또는 source-clock coverage가 지속적으로 부족하다.
- 평균 성과가 양수여도 중앙값·꼬리 손실·리그별 결과가 한두 경기 의존이다.
- 수수료·spread·full-depth를 반영하면 edge가 사라진다.
- +3%p/+5%p 양쪽 모두 손실이거나 한 번의 큰 손실을 정상 이익 빈도가 감당하지 못한다.
- 1분 cadence 목표를 반복적으로 넘겨 Jenkins run이 겹친다.

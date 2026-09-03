# Golden Peach — 킥오프 선두 추종

## 검증할 가설

경기 시작 직후 HOME/DRAW/AWAY 결과 명제의 직접 YES와 NO 가운데 표시 호가가 가장 높은
토큰은 짧은 시간 안에 소폭 더 상승하는 경향이 있는가를 검증한다. 최종 결과 중 하나가 1이
된다는 사실만으로 경기 초반 선두가 먼저 상승하는 것은 아니며, NO 세 토큰은 상호 배타적이지
않아 둘 이상이 1로 해결될 수 있다. 이 가정은 데이터로 반증 가능해야 한다.

## 현재 라이브 모집단

- 종목: 축구.
- 리그: EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL.
- event와 market이 active/open/order-taking이고 실제 source clock이 있어야 한다.
- 정규시간+추가시간(90분 stoppage) 결과만 포함하고 연장·승부차기/진출/핸디캡/prop은 제외한다.
- Gamma server-side cumulative volume `$5,000`, liquidity `$5,000` gate 후 exact `$5` CLOB
  depth를 최종 gate로 쓴다.

Eco/Fruit의 기존 live runtime은 계속 축구다. 별도 MLB runtime은 whole-game team moneyline의
직접 HOME/AWAY token 두 개를 사용하고, Grey의 완결 11경기 표시 호가 재생에서 고른 탐색값을
최소 `$5`로 검증한다. NBA, NFL, NHL은 아직 Grey shadow만 허용한다. direct 종목은 가격의
보수(complement)를 합성하지 않는다.

## 같은 시각의 6개 후보

각 event에서 HOME YES/NO, DRAW YES/NO, AWAY YES/NO의 direct token을 모두 식별한다.
각 token의 전체 ask/bid level, best bid/ask, spread, baseline `$5` ask VWAP을 저장한다. 하나라도
빠지면 event 전체를 건너뛴다. midpoint로 순위를 매기며 선두 차이가 0.005 미만이면 동률로
보아 진입하지 않는다.

예정 kickoff 이후 경과시간은 진입 증거가 아니다. source `elapsed/period`가 0~10분임을
증명해야 한다. 2H의 period-relative minute와 `90+N`을 명시적으로 정규화한다.

## 실행 계약

- 현재 live 목표 BUY는 `$5`, FOK 한 건이다.
- 향후 목표액은 `$5`~`$1000` cent 단위로 지정할 수 있다. fresh book에서 목표액 이하의
  동결 ladder를 큰 값부터 검사해 진입 상한 안에서 전량 체결 가능한 가장 큰 금액 하나를
  제출한다. `$5` 미만으로 쪼개거나 여러 BUY를 내지 않는다.
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

MLB는 진입 `[0.60,0.94]`를 유지하되 Eco/Fruit가 각각 `+0.07/+0.10` 익절을 비교하고 공통
손절은 `-0.20`이다. 축구의 80분 규칙은 적용하지 않으며 경기 종료까지 익절·손절·해결을
관찰한다. 예정 시작 후 0~10분과 Gamma explicit live 상태를 함께 요구하지만 sport-native
minute가 아니라는 한계가 있다. MLB와 축구는 별도 DB·config cohort다.

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
realized P&L이 아니다. simulation 주문에는 인증된 거래소 fill/fee 원장이 생기지 않으므로,
그 부재를 live BUY 증거 공백으로 간주해 다른 경기의 진입을 막지 않는다. live의 확정 체결·수수료
방어 규칙은 그대로 유지한다.

각 snapshot과 trade에는 종목·리그·원본 태그를 저장한다. Grey는 같은 원본 book에서 `$5`,
`$10`, `$15`, `$20`, `$25`, `$30`, `$40`, `$50`, `$75`, `$100`, `$150`, `$200`,
`$250`, `$500`, `$750`, `$1000`의 매수/즉시매도 표시 깊이를 계산한다. 이는 실제 체결률이
아니며 API 요청을 늘리지 않는 같은 시각의 표시 호가 반사실이다. 실제 증액 여부는 이 자료와
live confirmed fill을 함께 보고 별도 cohort로 결정한다.

판정은 단일 `config_hash × strategy_source_digest × mode × job_name` cohort에서 수행한다.
첫 24시간은 수집 건전성만, 신규 진입 종료 뒤에는 confirmed execution/fee와 resolution coverage를
먼저 검증한다. CRITICAL/HIGH 증거 문제가 있으면 파라미터나 수익성을 판단하지 않는다.

## 기각 기준

- direct six-book 또는 source-clock coverage가 지속적으로 부족하다.
- 평균 성과가 양수여도 중앙값·꼬리 손실·리그별 결과가 한두 경기 의존이다.
- 수수료·spread·full-depth를 반영하면 edge가 사라진다.
- +3%p/+5%p 양쪽 모두 손실이거나 한 번의 큰 손실을 정상 이익 빈도가 감당하지 못한다.
- 1분 cadence 목표를 반복적으로 넘겨 Jenkins run이 겹친다.

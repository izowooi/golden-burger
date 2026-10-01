# polylab 확인·결정 목록 (attention inbox)

갱신 2026-10-01 19:31 KST · 열린 항목 7건 (긴급 0 · 경고 0 · 결정 필요 1 · 참고 6)

매 회고(일일 3회·주간·월간)가 자동으로 갱신한다. **자동 규칙** 항목은 조건이 풀리면 스스로 '최근 해결'로 옮겨지고, **AI 판단** 항목은 7일 동안 다시 나오지 않으면 만료된다. 근거 경로는 이 저장소 기준이며, `metrics/…` 같은 경로는 AI context pack(공개 사본 `reports/context/latest/`)을 가리킨다.

## 열린 항목

### [결정 필요] 백테스트에서 apricot-fruit(tick=85)이 apricot-eco(tick=90)보다 ROI 2.7%p 열위 — 파라미터 조정 시점 결정 필요

- 결정 · AI 판단 · 최초 10-01 09:00 · 갱신 10-01 09:00 KST
- 근거: `backtests.json, metrics/apricot-fruit.json, metrics/apricot-eco.json`

30일 백테스트(paper replay, 실현 손익 아님): apricot-eco(tick=90) n=121, 손실 0건, ROI=+0.70%; apricot-fruit(tick=85) n=111, 손실 3건(resolution_loss), ROI=-2.00%, MDD=12.27 USDC. apricot-eco 후보 중 tick=85는 ROI=-2.0%로 열위를 재확인. 실거래 표본이 둘 다 0건이므로 현재 rules상 파라미터 변경 조건(min_trades_params=20) 미충족. 실거래 20건 누적 이전에 apricot-fruit를 tick=90으로 조기 조정할지 판단이 필요하다.

### [참고] 데이터 품질 이벤트 live_gap 107건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 09:00 · 갱신 10-01 19:31 KST
- 근거: `reports/daily/2026-10-01-evening.md`

라이브 경기 중 1분 가격 bar 공백. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 missing_book 88건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 19:31 · 갱신 10-01 19:31 KST
- 근거: `reports/daily/2026-10-01-evening.md`

missing_book. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] NHL 경기 막판(final) 0.30–0.40 버킷에서 약자 저평가 +14.1%p로 전체 스포츠 중 gap 1위

- 연구 발견 · AI 판단 · 최초 10-01 19:31 · 갱신 10-01 19:31 KST
- 근거: `calibration_summary.json`

calibration_summary.json top_gaps 기준: NHL final 구간 0.30–0.40 버킷(n=53), 평균 가격 0.3493, 실현 승률 0.4906, 95% CI [0.3612, 0.6212], gap=+0.1413(유의). 경기 최종 단계(f≥0.85)에서도 약자 저평가가 큰 폭으로 지속된다는 뜻이다. NFL에서도 동일 구간에서 gap+0.1074(유의)가 관찰되어 종목 간 공통 패턴일 수 있다. 현재 watermelon/plum/cherry 변형은 NHL을 커버하지 않는다. 논문 가설 1번(경기 시간 구간별 과대·과소평가)과 직결되는 유의한 발견이며, 특히 '후반에 편향이 더 커지는가'라는 질문에 NHL final 데이터가 긍정적 신호를 줄 수 있다.

### [참고] watermelon 계열 실거래 3건 모두 game_minute_at_entry=null — 논문 핵심 변수 미수집

- 데이터 품질 · AI 판단 · 최초 10-01 19:31 · 갱신 10-01 19:31 KST
- 근거: `trades_recent.json, metrics/watermelon-cat.json, metrics/watermelon-dog.json`

trades_recent.json 및 metrics/watermelon-cat.json, metrics/watermelon-dog.json 기준: 오늘 정산된 watermelon 계열 6건(cat 3건·dog 3건) 전부 game_minute_at_entry=null로 기록됐다. 논문 가설 1번(경기 시간 구간별 가격 편향)과 가설 2번(득점 민감도의 시간대별 변화)은 진입 시점의 게임 분 데이터를 반드시 필요로 한다. 이 필드가 null이면 watermelon 거래를 경기 시간 구간에 매핑할 수 없어 연구 분석에서 제외해야 한다. 수집 코드에서 game_minute가 기록되지 않는 이유가 의도적 설계인지 버그인지 확인이 필요하다.

### [참고] NFL 전 구간에서 정배 과대평가·약자 저평가 비대칭 패턴 관찰 (4개 버킷 유의)

- 연구 발견 · AI 판단 · 최초 10-01 09:00 · 갱신 10-01 09:00 KST
- 근거: `calibration_summary.json`

NFL 전 구간 0.60-0.70 버킷(n=114): 평균 가격 0.6446, 실현 승률 0.5263, 95% CI [0.4353, 0.6156], gap=-0.1183 — 정배 과대평가 유의. 0.30-0.40(n=117): gap=+0.1074, CI [0.3739, 0.5517] — 약자 저평가 유의. 0.20-0.30(n=83, gap=+0.096), 0.10-0.20(n=52, gap=+0.0997)도 유의. 정배 과대평가·약자 저평가가 동시에 관찰되는 비대칭 구조로 favourite-longshot bias 이론에 부합한다. 다중 비교 문제가 있으므로 해석에 주의가 필요하다.

### [참고] cherry-tiger 가설(MLB 0.76-0.78 YES 저평가)이 현재 calibration으로 뒷받침되지 않음

- 연구 발견 · AI 판단 · 최초 10-01 09:00 · 갱신 10-01 09:00 KST
- 근거: `calibration_summary.json, metrics/cherry-tiger.json`

cherry-tiger 가설: MLB 0.76-0.78 YES 토큰이 실현 확률 대비 저평가되어 1.0으로 수렴. 그러나 MLB 전 구간 0.70-0.80 버킷(n=1172)의 gap=-0.0054, 95% CI [0.7165, 0.7665]로 평균 가격 0.7477을 포함 — 유의하지 않음. 실거래 1건(stop_loss, pnl=-0.7659 USDC)도 가설 방향과 반대 결과이나, n=1로 변형에 대한 결론 자체는 유보한다. 다음 달 표본 누적 이후 calibration 재검토를 권장한다.

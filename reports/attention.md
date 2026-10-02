# polylab 확인·결정 목록 (attention inbox)

갱신 2026-10-03 03:30 KST · 열린 항목 17건 (긴급 0 · 경고 0 · 결정 필요 2 · 참고 15)

매 회고(일일 3회·주간·월간)가 자동으로 갱신한다. **자동 규칙** 항목은 조건이 풀리면 스스로 '최근 해결'로 옮겨지고, **AI 판단** 항목은 7일 동안 다시 나오지 않으면 만료된다. 근거 경로는 이 저장소 기준이며, `metrics/…` 같은 경로는 AI context pack(공개 사본 `reports/context/latest/`)을 가리킨다.

**답하는 법**: 항목 id 와 결정을 [`reports/decisions.md`](decisions.md) 에 한 줄로 적거나(GitHub 웹 편집 가능) Claude 에게 말하면 기록된다. 다음 회고가 그 항목을 '사용자 결정'으로 닫고, AI 는 결정을 전제로 판단한다.

## 열린 항목

### [결정 필요] NFL 실거래 유지 여부 결정 필요 — 오늘 5개 변형 NFL 전패, watermelon-dog만 +0.10 USDC

- 결정 · AI 판단 · 최초 10-02 19:30 · 갱신 10-02 19:30 KST · id `ai:manual-nfl-scope`
- 근거: `trades_recent.json, calibration_summary.json, metrics/plum-king.json`

오늘 NFL Steelers vs Browns: plum-king -1.15, plum-queen -1.08, cherry-tiger -1.06, cherry-blue -0.69, watermelon-cat -1.83 전패. watermelon-dog만 Browns 0.98 진입 정산 승 +0.10. 5/6 변형 NFL 전패. calibration_summary.json NFL 0.60-0.70 버킷(n=114) gap=-0.12(유의, 과대평가) — plum 진입 범위(0.70-0.73)와 인접해 해당 가격대도 과대평가 구간일 가능성 있음. 표본 1-5건으로 통계적 결론 불가하나 방향은 일관되게 부정적. 선택지: (1) NFL을 paper 전환 (2) NFL 유지하며 표본 추가 (3) NFL 전용 파라미터 탐색.

### [결정 필요] NFL 을 watermelon·plum 실거래 범위에 계속 둘지 결정 필요 (백테스트 전 조건 손실)

- 결정 · 자동 규칙 · 최초 10-02 14:11 · 갱신 10-02 14:11 KST · id `manual:nfl-scope`
- 근거: `docs/research/backtests/2026-10-02-watermelon-tp-apricot.md`

2026-10-02 watermelon 파라미터 grid 에서 NFL 은 익절 유무·진입가 하한과 관계없이 모든 조건에서 손실이었다. 다만 NFL 거래가 모두 후반 표본에 몰려 전·후반 교차검증이 불가능했고, 축구용 진입가 하한을 그대로 써서 NFL 전용 최적화는 하지 않았다. 선택지: (1) NFL 을 실거래에서 빼고 paper 로만 관찰 (2) 유지하며 표본을 더 모음 (3) NFL 전용 파라미터 탐색 후 결정. 답은 reports/decisions.md 에 manual:nfl-scope 로.

### [참고] 데이터 품질 이벤트 live_gap 228건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 09:00 · 갱신 10-03 03:30 KST · id `quality:live_gap`
- 근거: `reports/daily/2026-10-03-dawn.md`

라이브 경기 중 1분 가격 bar 공백. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 missing_book 34건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 19:31 · 갱신 10-03 03:30 KST · id `quality:missing_book`
- 근거: `reports/daily/2026-10-03-dawn.md`

missing_book. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 live_history_mismatch 67건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-02 08:01 · 갱신 10-03 03:30 KST · id `quality:live_history_mismatch`
- 근거: `reports/daily/2026-10-03-dawn.md`

라이브 가격과 history 가격 5c 이상 불일치. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] plum 축구 정방향 포지션 2건이 stop_loss로 청산 — Ireland HOME:NO, Seattle DRAW:NO

- 연구 발견 · AI 판단 · 최초 10-03 03:30 · 갱신 10-03 03:30 KST · id `ai:ai-plum-soccer-stop-loss-correct`
- 근거: `trades_recent.json, metrics/plum-king.json, decisions.md`

plum-king Ireland HOME:NO(entry 0.73, stop 0.48, pnl -1.87 USDC): 경기 2-2 무승부로 HOME:NO 정산 승 방향이었으나 경기 중 급등락에 stop_loss 발동. Seattle Sounders DRAW:NO(game_minute 44.05, entry 0.73, stop 0.61, pnl -0.98 USDC): 경기 2-1 홈 승으로 DRAW:NO 정산 승 방향이었으나 동일 이유로 청산. 두 건 모두 최종 결과와 같은 방향 포지션이 손절로 청산됐다. 2026-10-02 결정('모든 전략 조기 익절 우선')이 plum에도 적용 가능한지 검토 — 현재 plum은 가격 기반 TP(take_profit_price=0.9)를 쓰며, watermelon처럼 delta 기반 TP로 전환하거나 TP 가격을 낮춰 조기 청산하는 방향이 고려 대상이다. 표본 2건으로 결론 불가, 방향성 신호.

### [참고] watermelon game_minute_at_entry — take_profit 경로에서 정상 기록 확인, stop_loss·resolution 경로는 여전히 NaN

- 데이터 품질 · AI 판단 · 최초 10-03 03:30 · 갱신 10-03 03:30 KST · id `ai:ai-ai-watermelon-entry-minute-null`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json`

오늘 watermelon-cat take_profit 청산 3건(44.3·56.05·90.88분)과 watermelon-dog take_profit 청산 3건(56.03·44.3·90.88분)에서 game_minute_at_entry가 정상 기록됐다. 반면 stop_loss 청산(Ireland·Malta·Steelers) 및 resolution_win 경로(Germany·Wales 등) 과거 전 건은 NaN. 버그가 take_profit 트리거가 아닌 종료 경로에 국한됨을 확인. 수정 범위가 stop_loss 발동 시각 및 resolution 처리 시 진입 기록 로직으로 좁혀졌다. 논문 핵심 변수(진입 경기 시각)는 TP 청산 표본에서만 사용 가능한 상태.

### [참고] watermelon A/B arm — 동일 NFL 경기에서 cat -1.83 USDC·dog +0.10 USDC

- 연구 발견 · AI 판단 · 최초 10-03 03:30 · 갱신 10-03 03:30 KST · id `ai:ai-watermelon-nfl-arm-divergence`
- 근거: `trades_recent.json, metrics/watermelon-cat.json, metrics/watermelon-dog.json`

Steelers vs. Browns(Browns 27-24 승): watermelon-cat(NFL prob_min=0.91)은 Browns를 0.93에 진입했다가 Q4 변동성으로 0.60에 stop_loss(-1.82716085 USDC). watermelon-dog(NFL prob_min=0.94)은 같은 경기에서 Browns를 0.98에 진입해 정산 승 +0.09904 USDC. cat의 낮은 진입 기준이 결말이 불확실한 Q4 구간에서 더 일찍 진입하게 했고, dog은 더 높은 기준 덕에 사실상 확정된 시점에 진입했다. 두 arm이 동일 종목·경기에서 반대 결과를 낸 첫 사례. n=1로 NFL 진입 기준에 대한 결론은 불가하나 open 상태인 NFL 범위 결정(ai:manual-nfl-scope)에 참고할 수 있다.

### [참고] plum-king Ireland HOME:NO — 올바른 방향 포지션이 막판 급락 손절로 청산 (실결과 2-2 무승부 = HOME:NO 정산 승)

- 연구 발견 · AI 판단 · 최초 10-02 19:30 · 갱신 10-02 19:30 KST · id `ai:plum-soccer-stop-loss-correct`
- 근거: `trades_recent.json, metrics/plum-king.json, report.md`

plum-king이 Republic of Ireland vs Austria에서 HOME:NO(Ireland가 이기지 못함)를 0.73에 매수, Austria 선취점 후 Ireland 연속 동점 급등 구간에서 0.48에 stop_loss 청산(-1.87 USDC). 경기는 2-2 무승부 종료 → HOME:NO가 맞는 포지션이었다. 1분 주기 손절이 Ireland 득점 후 급등 구간을 잡지 못하고 방향이 옳은 포지션을 조기 청산했다. watermelon과 같이 plum 축구도 막판 변동성에서 손절 신뢰도 문제가 있다. 조기 익절(take-profit early) 방향이 plum 축구에도 적용 가능한지 검토 여부를 알고 싶다.

### [참고] watermelon 계열 game_minute_at_entry 전건 null — 오늘도 cat 12건·dog 14건 전부 null 지속

- 데이터 품질 · AI 판단 · 최초 10-02 19:30 · 갱신 10-02 19:30 KST · id `ai:ai-watermelon-entry-minute-null`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json, metrics/plum-king.json`

오늘 watermelon-cat 12건, watermelon-dog 14건(metrics/watermelon-cat.json, metrics/watermelon-dog.json) 전부 game_minute_at_entry=null. 논문 가설 1·2번(경기 시간 구간별 가격 편향, 득점 민감도)을 watermelon 거래에 매핑하려면 이 필드가 필수다. plum-king 거래(metrics/plum-king.json recent)에는 값이 존재(예: 2.72, 44.05)해 수집 자체가 불가능한 것이 아니라 watermelon 전략 특유의 문제로 보인다. 수집 코드에서 watermelon이 진입 시점 게임 분을 기록하지 않거나 metrics JSON에 전달하지 않는 경로 확인이 필요하다.

### [참고] [확인됨] Azerbaijan 0-0 종료 — llm-nil-draw paper 전손, watermelon-dog live 이익, LLM 첫 예측 실패

- 연구 발견 · AI 판단 · 최초 10-02 08:01 · 갱신 10-02 08:01 KST · id `ai:ai-llm-dog-opposing-bets`
- 근거: `trades_recent.json, metrics/llm-nil-draw.json, metrics/watermelon-dog.json`

정산 결과 확인: llm-nil-draw(paper)는 Azerbaijan vs. Liechtenstein Over 0.5를 0.95에 매수했으나 0-0 종료로 resolution_loss(-5.01 USDC paper). watermelon-dog(live)는 같은 경기 DRAW:YES를 0.96에 매수해 resolution_win(+0.20 USDC). LLM의 '득점 있음' 예측이 첫 거래부터 틀렸다. n=1로 LLM 예측 정확도에 대한 결론은 유보하며 표본 누적 후 재검토 필요.

### [참고] MLB 득점 jump가 이닝 후반으로 갈수록 커지는 패턴 — 가설 2번 방향과 일치

- 연구 발견 · AI 판단 · 최초 10-02 03:31 · 갱신 10-02 03:31 KST · id `ai:mlb-run-sensitivity-increasing`
- 근거: `events_summary.json`

events_summary.json 기준: MLB run jump가 0-3이닝(n=9, mean_abs_jump 0.0817) → 3-6이닝(n=11, 0.0986) → 6-9이닝(n=6, 0.1196) 순서로 증가. 9+이닝은 n=1(0.38)으로 outlier 가능성. 표본이 각 6-11건으로 작아 우연과 구분 어렵고 결론을 낼 수 없으나, 논문 가설 2번(득점 민감도가 경기 후반으로 갈수록 커지는가)의 방향과 일치한다. 대조적으로 NHL goal jump는 0-20분(0.1293) → 40-60분(0.0149)으로 반대 방향이어서 종목별 차이도 관찰된다. MLB 표본 누적 후 재검토 필요.

### [참고] llm-nil-draw(paper)와 watermelon-dog(live)이 동일 경기 반대 방향 베팅 — 첫 날 LLM 예측 실패 예상

- 연구 발견 · AI 판단 · 최초 10-02 03:31 · 갱신 10-02 03:31 KST · id `ai:llm-dog-opposing-bets`
- 근거: `trades_recent.json, metrics/llm-nil-draw.json, metrics/watermelon-dog.json`

trades_recent.json 기준: llm-nil-draw가 Azerbaijan vs. Liechtenstein Over 0.5(득점 있음)를 paper 0.95에 매수한 날, watermelon-dog이 같은 경기 DRAW:YES(무승부 — 0-0 포함)를 live 0.96에 매수. game_minute=90 시점 Over 0.5 mark가 0.0005로 사실상 0-0 종료 예상이며, 이 경우 llm-nil-draw paper 손실·watermelon-dog live 이익(unrealized +0.1957 USDC)이 된다. LLM 예측(득점 있을 것)이 첫 거래부터 빗나간 것으로 보이며, llm-nil-draw의 예측 정확도 추적을 위해 정산 결과 확인이 필요하다. paper이므로 실손익은 없음.

### [참고] NHL 경기 막판(final) 0.30–0.40 버킷에서 약자 저평가 +14.1%p로 전체 스포츠 중 gap 1위

- 연구 발견 · AI 판단 · 최초 10-01 19:31 · 갱신 10-01 19:31 KST · id `ai:nhl-final-underdog-gap`
- 근거: `calibration_summary.json`

calibration_summary.json top_gaps 기준: NHL final 구간 0.30–0.40 버킷(n=53), 평균 가격 0.3493, 실현 승률 0.4906, 95% CI [0.3612, 0.6212], gap=+0.1413(유의). 경기 최종 단계(f≥0.85)에서도 약자 저평가가 큰 폭으로 지속된다는 뜻이다. NFL에서도 동일 구간에서 gap+0.1074(유의)가 관찰되어 종목 간 공통 패턴일 수 있다. 현재 watermelon/plum/cherry 변형은 NHL을 커버하지 않는다. 논문 가설 1번(경기 시간 구간별 과대·과소평가)과 직결되는 유의한 발견이며, 특히 '후반에 편향이 더 커지는가'라는 질문에 NHL final 데이터가 긍정적 신호를 줄 수 있다.

### [참고] watermelon 계열 실거래 3건 모두 game_minute_at_entry=null — 논문 핵심 변수 미수집

- 데이터 품질 · AI 판단 · 최초 10-01 19:31 · 갱신 10-01 19:31 KST · id `ai:watermelon-entry-minute-null`
- 근거: `trades_recent.json, metrics/watermelon-cat.json, metrics/watermelon-dog.json`

trades_recent.json 및 metrics/watermelon-cat.json, metrics/watermelon-dog.json 기준: 오늘 정산된 watermelon 계열 6건(cat 3건·dog 3건) 전부 game_minute_at_entry=null로 기록됐다. 논문 가설 1번(경기 시간 구간별 가격 편향)과 가설 2번(득점 민감도의 시간대별 변화)은 진입 시점의 게임 분 데이터를 반드시 필요로 한다. 이 필드가 null이면 watermelon 거래를 경기 시간 구간에 매핑할 수 없어 연구 분석에서 제외해야 한다. 수집 코드에서 game_minute가 기록되지 않는 이유가 의도적 설계인지 버그인지 확인이 필요하다.

### [참고] NFL 전 구간에서 정배 과대평가·약자 저평가 비대칭 패턴 관찰 (4개 버킷 유의)

- 연구 발견 · AI 판단 · 최초 10-01 09:00 · 갱신 10-01 09:00 KST · id `ai:nfl-longshot-bias`
- 근거: `calibration_summary.json`

NFL 전 구간 0.60-0.70 버킷(n=114): 평균 가격 0.6446, 실현 승률 0.5263, 95% CI [0.4353, 0.6156], gap=-0.1183 — 정배 과대평가 유의. 0.30-0.40(n=117): gap=+0.1074, CI [0.3739, 0.5517] — 약자 저평가 유의. 0.20-0.30(n=83, gap=+0.096), 0.10-0.20(n=52, gap=+0.0997)도 유의. 정배 과대평가·약자 저평가가 동시에 관찰되는 비대칭 구조로 favourite-longshot bias 이론에 부합한다. 다중 비교 문제가 있으므로 해석에 주의가 필요하다.

### [참고] cherry-tiger 가설(MLB 0.76-0.78 YES 저평가)이 현재 calibration으로 뒷받침되지 않음

- 연구 발견 · AI 판단 · 최초 10-01 09:00 · 갱신 10-01 09:00 KST · id `ai:cherry-tiger-calib-mismatch`
- 근거: `calibration_summary.json, metrics/cherry-tiger.json`

cherry-tiger 가설: MLB 0.76-0.78 YES 토큰이 실현 확률 대비 저평가되어 1.0으로 수렴. 그러나 MLB 전 구간 0.70-0.80 버킷(n=1172)의 gap=-0.0054, 95% CI [0.7165, 0.7665]로 평균 가격 0.7477을 포함 — 유의하지 않음. 실거래 1건(stop_loss, pnl=-0.7659 USDC)도 가설 방향과 반대 결과이나, n=1로 변형에 대한 결론 자체는 유보한다. 다음 달 표본 누적 이후 calibration 재검토를 권장한다.

## 최근 해결

<details>
<summary>최근 14일 해결 3건</summary>

- **watermelon stop_price 미실행 수치 확인 — Republic of Ireland 0.92→0.17 (13분, 손실 –4.14 USDC)** — 사용자 결정 (2026-10-02): take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다. (10-02 09:25 KST)
- **soccer goal jump 표본 극소(n=1·2) — 논문 핵심 종목 가설 2번 검증 불가** — 사용자 결정 (2026-10-02): 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다. (10-02 09:25 KST)
- **백테스트에서 apricot-fruit(tick=85)이 apricot-eco(tick=90)보다 ROI 2.7%p 열위 — 파라미터 조정 시점 결정 필요** — 사용자 결정 (2026-10-02): 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용). (10-02 09:25 KST)

</details>

# polylab 확인·결정 목록 (attention inbox)

갱신 2026-10-05 08:00 KST · 열린 항목 31건 (긴급 0 · 경고 3 · 결정 필요 5 · 참고 23)

매 회고(일일 3회·주간·월간)가 자동으로 갱신한다. **자동 규칙** 항목은 조건이 풀리면 스스로 '최근 해결'로 옮겨지고, **AI 판단** 항목은 7일 동안 다시 나오지 않으면 만료된다. 근거 경로는 이 저장소 기준이며, `metrics/…` 같은 경로는 AI context pack(공개 사본 `reports/context/latest/`)을 가리킨다.

**답하는 법**: 항목 id 와 결정을 [`reports/decisions.md`](decisions.md) 에 한 줄로 적거나(GitHub 웹 편집 가능) Claude 에게 말하면 기록된다. 다음 회고가 그 항목을 '사용자 결정'으로 닫고, AI 는 결정을 전제로 판단한다.

## 열린 항목

### [경고] 수동 베팅 누적 실현 -3,734.74 USDC: bankroll의 -37.4% (수동 AI 베팅 (red, 메인))

- 위험 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-05 08:00 KST · id `manual_drawdown:red`
- 근거: `reports/daily/2026-10-05-morning.md`

계좌 수동 AI 베팅 (red, 메인) 실현손익이 처음 기록된 bankroll 10,000.00 USDC의 −10% 아래로 내려갔다. 베팅 금액·선택 기준 점검 권장.

### [경고] plum-king 단위 모드 변경: 5→5 USDC, 모드 live→paper

- 시스템 변경 · 자동 규칙 · 최초 10-03 08:00 · 갱신 10-03 19:30 KST · id `stake:plum-king:1790982004`
- 근거: `strategies/plum-king.yaml`

10-03 08:00 KST 자동 적용. 사유: ai:claude: mode live→paper. 증액은 결정론 ladder 게이트를 통과했을 때만, 감액·paper 전환은 손실이나 표본 규칙으로 자동 적용된다.

### [경고] plum-queen 단위 모드 변경: 5→5 USDC, 모드 live→paper

- 시스템 변경 · 자동 규칙 · 최초 10-03 08:00 · 갱신 10-03 19:30 KST · id `stake:plum-queen:1790982004`
- 근거: `strategies/plum-queen.yaml`

10-03 08:00 KST 자동 적용. 사유: ai:claude: mode live→paper. 증액은 결정론 ladder 게이트를 통과했을 때만, 감액·paper 전환은 손실이나 표본 규칙으로 자동 적용된다.

### [결정 필요] paper 변형 plum-us-paper 표본 21건 도달: live 전환 결정 필요

- 결정 · 자동 규칙 · 최초 10-05 08:00 · 갱신 10-05 08:00 KST · id `paper_ready:plum-us-paper`
- 근거: `strategies/plum-us-paper.yaml`

paper 정산 21건, 승률 47.6%, ROI -3.6%(paper 원장). live 전환은 사람만 할 수 있다: strategies/plum-us-paper.yaml 의 mode 를 live 로, 계좌 alias 를 지정해 커밋한다. 아니면 그대로 두거나 retire 한다.

### [결정 필요] paper 변형 watermelon-us-paper 표본 24건 도달: live 전환 결정 필요

- 결정 · 자동 규칙 · 최초 10-05 08:00 · 갱신 10-05 08:00 KST · id `paper_ready:watermelon-us-paper`
- 근거: `strategies/watermelon-us-paper.yaml`

paper 정산 24건, 승률 87.5%, ROI -2.3%(paper 원장). live 전환은 사람만 할 수 있다: strategies/watermelon-us-paper.yaml 의 mode 를 live 로, 계좌 alias 를 지정해 커밋한다. 아니면 그대로 두거나 retire 한다.

### [결정 필요] goal-over-all 누적 stop_loss 6건 전부 방향 옳은 경기 — stop_loss_pct 재검토 필요

- 결정 · AI 판단 · 최초 10-05 08:00 · 갱신 10-05 08:00 KST · id `ai:ai-ai-ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, metrics/watermelon-cat.json, decisions.md`

오늘 Greece/Germany(진입 0.96→stop 0.862, −0.56 USDC)·Wales/Denmark(0.94→0.84, −0.59 USDC) 2건 추가로 총 11건 중 6건 stop_loss. 6건 전부 watermelon-cat이 동일 경기에서 88-94분에 take_profit을 달성해 득점 확인됨(Malta/Andorra·Kosovo/Austria·Azerbaijan/Lithuania·Belarus/San Marino·Greece/Germany·Wales/Denmark). 모두 프리게임 또는 킥오프 직후 가격이 −10%+ 하락해 발동. allow_in_play=false + stop_loss_pct=10% 조합이 시장 변동성으로 방향 정확 포지션 6/6을 조기 청산하는 구조적 패턴. exit 파라미터(stop_loss_pct)는 owner_fixed이므로 AI 변경 불가. stop_loss_pct 확대 또는 프리게임 구간 비활성화 여부 결정 요망.

### [결정 필요] apricot-fruit 3일 이상 진입 0건 (대상 경기 8개 있었음)

- 결정 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-05 03:30 KST · id `dead_variant:apricot-fruit`
- 근거: `strategies/apricot-fruit.yaml`

진입 기록 없음. 같은 기간 대상 종목(mlb) 경기는 8개였다. 진입 조건이 지나치게 엄격하거나 버그일 수 있다. AI 회고가 백테스트 근거로 조건을 다시 맞추거나(retro 가 직접 재생해 검증), 폐기(retire) 여부를 판단해야 한다.

### [결정 필요] goal-over-all 프리게임 stop_loss 4건 모두 방향 맞은 경기 — stop_loss_pct 재검토 필요

- 결정 · AI 판단 · 최초 10-05 03:30 · 갱신 10-05 03:30 KST · id `ai:ai-ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, metrics/watermelon-cat.json, decisions.md`

오늘 Malta/Andorra(진입 0.88 → stop 0.79, −0.59 USDC), Kosovo/Austria(0.94 → 0.84, −0.59 USDC), Azerbaijan/Lithuania(0.88 → 0.79, −0.59 USDC) 3건 stop_loss 발동. 세 경기 모두 watermelon-cat이 88.15분·93.05분·92.73분에 take_profit 달성해 최종 득점 확인. 이전 Belarus/San Marino(진입 0.968 → 0.832, −0.75 USDC)도 동일 패턴이었음. 누적 7건 중 4건 stop_loss, 최소 3건이 방향 옳은 경기에서 프리게임 가격 하락으로 발동됐다. allow_in_play=false 전략에서 stop_loss_pct=10%는 킥오프 전 시장 변동(−10%)으로 조기 청산 후 경기 자체를 놓치게 만드는 구조다. stop_loss_pct 확대 또는 프리게임 구간 비활성화 여부를 결정해 주시기 바랍니다. 이 파라미터는…

### [참고] 데이터 품질 이벤트 live_gap 256건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 09:00 · 갱신 10-05 08:00 KST · id `quality:live_gap`
- 근거: `reports/daily/2026-10-05-morning.md`

라이브 경기 중 1분 가격 bar 공백. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 live_history_mismatch 149건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-05 08:00 KST · id `quality:live_history_mismatch`
- 근거: `reports/daily/2026-10-05-morning.md`

라이브 가격과 history 가격 5c 이상 불일치. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 수동 베팅 정산 패: 수동 AI 베팅 (red, 메인) · Korea Republic vs. Venezuela O/U 0.5 Over -3,544.96

- 연구 발견 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-05 08:00 KST · id `manual_loss:red:10fc507120`
- 근거: `reports/daily/2026-10-05-morning.md`

10-02 23:54 KST 정산. 트랙 2 기록용 알림(결정 불필요).

### [참고] paper 변형 plum-king 증거 수집 중 (15/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-03 19:30 · 갱신 10-05 08:00 KST · id `paper:plum-king`
- 근거: `strategies/plum-king.yaml`

가설: 경기 중 유일한 midpoint 선두 토큰이 ask VWAP 0.70-0.73에 있을 때 시장은 선두 유지·상승 확률을 과소평가한다(favourite continuation). 축구는 60분 이전 진입·65분 강제청산. A arm: SL 0.12, NFL TP 0.85.. paper 정산 15건, ROI 0.5%(paper 원장, 실손익 아님). 20건이 모이면 live 전환 여부를 사람이 결정한다(AI는 live로 올릴 수 없다).

### [참고] paper 변형 plum-queen 증거 수집 중 (14/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-03 19:30 · 갱신 10-05 08:00 KST · id `paper:plum-queen`
- 근거: `strategies/plum-queen.yaml`

가설: 경기 중 유일한 midpoint 선두 토큰(ask VWAP 0.70-0.73)은 선두 지속 확률이 과소평가된다. B arm: 축구 SL 0.17(더 넓은 손절), NFL TP 0.90 — 손절폭/익절가가 시간대별 가격 변동성에 맞는지 A와 비교.. paper 정산 14건, ROI 1.2%(paper 원장, 실손익 아님). 20건이 모이면 live 전환 여부를 사람이 결정한다(AI는 live로 올릴 수 없다).

### [참고] paper 변형 cherry-us-paper 증거 수집 중 (3/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-03 19:30 · 갱신 10-05 08:00 KST · id `paper:cherry-us-paper`
- 근거: `strategies/cherry-us-paper.yaml`

가설: 미국 종목 결과 마켓에서 0.80-0.82 YES 토큰이 정산 전에 1.0 으로 수렴하는 경향이 있는가. 실거래 전 paper 검증용.. paper 정산 3건, ROI -9.1%(paper 원장, 실손익 아님). 20건이 모이면 live 전환 여부를 사람이 결정한다(AI는 live로 올릴 수 없다).

### [참고] watermelon A/B 누적 갱신: dog ROI +0.22%(34건) vs cat ROI −1.90%(33건), 격차 3.52 USDC로 확대

- 연구 발견 · AI 판단 · 최초 10-04 03:30 · 갱신 10-05 03:30 KST · id `ai:ai-ai-ai-watermelon-soccer-tp-delta-comp`
- 근거: `metrics/watermelon-dog.json, metrics/watermelon-cat.json, trades_recent.json`

누적 live 결과: dog 34건 PnL +0.38 USDC ROI +0.22%, cat 33건 PnL −3.14 USDC ROI −1.90%, 격차 3.52 USDC(이전 회고 3.37 USDC에서 확대). 오늘 동시 진입 경기에서 dog이 cat보다 Malta/Andorra +0.02·Azerbaijan/Lithuania +0.04 높은 매도가 달성. take_profit_delta 0.04(dog)가 0.02(cat)보다 더 높은 수렴 가격을 포착하는 패턴 지속. 단 stop_price(0.60 vs 0.65)·use_stored_stop(true vs false) 차이도 있어 TP delta만의 효과 분리 불가. n=33/34로 통계 결론 불가하나 격차가 매일 확대 중이다.

### [참고] goal-over-all 첫 stop_loss: 실제 득점이 있는 경기에서 인게임 가격 임시 하락으로 발동

- 연구 발견 · AI 판단 · 최초 10-04 08:00 · 갱신 10-04 08:00 KST · id `ai:ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, trades_recent.json`

Belarus vs. San Marino Over 0.5: 진입가 0.968, 인게임 가격 임시 하락 0.832(-13.6%)으로 stop_loss_pct=10% 발동(-0.751 USDC 실현손실). 그러나 Belarus는 득점·승리로 경기를 끝냈다(watermelon-cat HOME:YES take_profit으로 확인). 옳은 방향 포지션이 임시 가격 하락으로 손절된 plum(ai:plum-soccer-stop-loss-correct)과 동일 패턴이다. n=4로 결론 불가이나, 킥오프 전 pre-game 진입에서도 1분 주기 집행 한계로 stop_loss_pct=10%가 조기 발동될 수 있음이 확인됐다.

### [참고] paper 변형 llm-nil-consensus 증거 수집 중 (0/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-04 03:30 KST · id `paper:llm-nil-consensus`
- 근거: `strategies/llm-nil-consensus.yaml`

가설: Claude 와 ChatGPT 가 독립적으로 매긴 P(0:0) 의 사전 등록 합의(두 AI 모두 top-5, 평균 P(0:0) 낮은 순 top-3)가 시장보다 0:0 을 낮게 볼 때(시장 내재 P(0:0)=1−Over ask, 차이 ≥ edge) Over 0.5 를 킥오프 직전 매수해 정산까지 보유하면 수익이 나는가. 대조군 llm-nil-draw(Cla…. paper 정산 0건, ROI –(paper 원장, 실손익 아님). 20건이 모이면 live 전환 여부를 사람이 결정한다(AI는 live로 올릴 수 없다).

### [참고] 축구 득점 jump peak가 30-45분 구간 — 가설 2번(후반 민감도 증가)과 반대

- 연구 발견 · AI 판단 · 최초 10-04 03:30 · 갱신 10-04 03:30 KST · id `ai:soccer-goal-jump-phase-pattern`
- 근거: `events_summary.json`

events_summary.json 기준 축구 득점 mean_abs_jump: 0-15분(n=10, 0.0915), 15-30분(n=9, 0.0706), 30-45분(n=7, 0.1316), 45-60분(n=16, 0.0753), 60-75분(n=18, 0.0709), 75-90분(n=7, 0.0424), 90+(n=5, 0.0061). 30-45분이 최고점. 논문 가설 2번(후반으로 갈수록 민감도 증가)과 반대 방향이다. 90+ 구간은 mean_pre_price=0.9809(상한 근접)로 jump 여지 자체가 작다. 전 구간 n‹20으로 결론 불가이나 가설 2번 보완 검토가 필요하다.

### [참고] watermelon A/B arm 누적 갱신: dog TP delta 0.04가 cat 0.02보다 일관되게 높은 성과 (24/22건)

- 연구 발견 · AI 판단 · 최초 10-03 19:30 · 갱신 10-03 19:30 KST · id `ai:ai-ai-watermelon-soccer-tp-delta-compari`
- 근거: `trades_recent.json, metrics/watermelon-cat.json, metrics/watermelon-dog.json`

오늘 UNL 10경기 동시 진입 추가 비교: 9/10 경기에서 dog이 cat보다 높은 매도가 달성(France vs Italy 1건만 cat 우위, 진입가 차이에 기인). 누적 live 결과: dog 24건 ROI -1.21% 총손익 -1.45 USDC, cat 22건 ROI -4.02% 총손익 -4.43 USDC. 누적 PnL 격차 2.98 USDC. 두 변형은 stop_price(0.60 vs 0.65)·use_stored_stop(true vs false) 차이도 있어 TP delta만의 효과 분리는 불가. n=22/24으로 통계 결론 불가이나, take_profit 경로에서 dog이 더 높은 수렴 가격을 달성하는 패턴이 매일 반복 확인됨. 논문 가설 3번(stake 단위별 안정성) 검증 전 단계로 TP delta 설계 차이가 연구 변수로 축적되고 있다.

### [참고] watermelon 축구 동시 진입 7건 비교: dog TP delta 0.04가 cat 0.02보다 일관되게 높은 매도가 달성

- 연구 발견 · AI 판단 · 최초 10-03 08:00 · 갱신 10-03 08:00 KST · id `ai:ai-watermelon-soccer-tp-delta-comparison`
- 근거: `trades_recent.json, metrics/watermelon-cat.json, metrics/watermelon-dog.json`

오늘 UNL 경기에서 cat과 dog이 동일 종목에 동시 진입한 6건의 매도 가격 비교(trades_recent.json): Kazakhstan 0.99 vs 0.97(+0.02), Latvia 0.999 vs 0.971(+0.028), Bosnia DRAW 0.99 vs 0.96(+0.03), Belgium Türkiye 0.995 vs 0.97(+0.025), Hungary 0.99 vs 0.98(+0.01), Poland Romania 동일(0.98). 6건 중 5건에서 dog이 cat보다 높게 매도했다. 24h 실현 PnL: dog +2.77 USDC vs cat +0.05 USDC. TP delta 차이(0.04 vs 0.02) 외에 stop_price(0.60 vs 0.65) 차이도 있어 TP delta만의 효과 분리는 불가하나, TP 발동 경로에서 dog이 더 높은 수렴 가격을 달성하는 패턴이 A/B 설계 의도에 부합한다. n=6으로 결론 불가하나 논문 가설 3번(st…

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

## 최근 해결

<details>
<summary>최근 14일 해결 13건</summary>

- **수동 베팅 정산 패: 수동 AI 베팅 (red, 메인) · Israel vs. Kosovo O/U 0.5 Over -565.69** — 조건 해소 (자동) (10-05 08:00 KST)
- **paper 변형 plum-us-paper 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-05 08:00 KST)
- **paper 변형 watermelon-us-paper 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-05 08:00 KST)
- **cherry-tiger n=6(이전 3건): 오늘 2건 이익 포함, 누적 ROI -8.53% — paper 전환 여부 재확인** — 사용자 결정 (2026-10-04): cherry-tiger·blue 는 paper 로 내리지 않고 live(5 USDC) 를 유지하되 전략을 재설계한다. 실거래 11건(손절 8·익절 3, tiger ROI −8.5%·blue −9.6%)과 8개월 백테스트 모두 옛 설정(0.76–0.82 밴드, TP +20%·SL −8%·trailing 15%)이 수수료·스프레드·1분 주기 손절 미끄러짐 때문에 구조적으로 손해임을 보였다. 새 설정: YES 0.90–0.95 진입, 진입 창은 경기 전 72시간 + 킥오프 후 60분, 손절·trailing·상대 TP 끔, 매도 호가 0.99 이상이면 정산까지 보유, 익절은 수수료 후 순이익일 때만 전량 — blue 는 진입가 +0.03, tiger 는 고정 0.98(청산 방식만 다른 A/B). min_liqui… (10-05 00:06 KST)
- **plum paper 전환 완료 — paper 단계 운영 방향 결정 필요** — 사용자 결정 (2026-10-04): B 로 결정: delta 기반 조기 익절을 paper 에서 테스트한다. plum-king·plum-queen 에 `take_profit_delta` 0.03(진입가 +0.03 이상이면 보유 전량을 수수료 후 순이익일 때만 매도)과 `hold_above_price` 0.99(매도 호가 0.99 이상이면 정산까지 보유)를 적용하고 mode 는 paper 그대로 둔다. 손절폭 A/B(king 0.12 vs queen 0.17)는 유지한다. NFL 과 plum-us-paper 는 백테스트에서 delta 가 오히려 손해라 기존 TP 가격을 유지한다(bounds 만 열어 둠). 백테스트상 plum 은 모든 설정이 약 −2% 로 delta 는 손실을 줄일 뿐이므로 paper 20건 후 재평가한다. 근거 `docs/re… (10-05 00:06 KST)
- **cherry-tiger 실거래 3건 모두 손실·1건 open 중 — paper 전환 고려 여부** — 사용자 결정 (2026-10-04): 위 `ai:ai-ai-cherry-tiger-calib-mismatch` 결정과 같다(paper 전환 대신 live 유지·재설계). (10-05 00:06 KST)
- **cherry-tiger 가설(MLB 0.76-0.78 YES 저평가)이 현재 calibration으로 뒷받침되지 않음** — 사용자 결정 (2026-10-04): 0.76–0.78 밴드 가설은 폐기한다(실현 승률 ≈ 가격, calibration 근거 없음). cherry 는 0.90–0.95 favourite 수렴 가설로 바꿔 검증한다(위 결정). (10-05 00:06 KST)
- **데이터 품질 이벤트 missing_book 34건 (24시간)** — 조건 해소 (자동) (10-03 19:30 KST)
- **NFL 실거래 유지 여부 결정 필요 — 오늘 5개 변형 NFL 전패, watermelon-dog만 +0.10 USDC** — 사용자 결정 (2026-10-03): NFL 실거래를 폐기한다. 데이터가 충분히 쌓여 수익이 나는 전략과 파라미터가 확보될 때까지 잠정 중단한다. (10-03 13:58 KST)
- **NFL 을 watermelon·plum 실거래 범위에 계속 둘지 결정 필요 (백테스트 전 조건 손실)** — 사용자 결정 (2026-10-03): NFL 을 watermelon·plum 실거래 범위에서 제외한다. NFL 전용 전략이나 기존 전략의 NFL 전용 분기 로직이 생기기 전까지 실거래하지 않는다. (10-03 13:58 KST)
- **watermelon stop_price 미실행 수치 확인 — Republic of Ireland 0.92→0.17 (13분, 손실 –4.14 USDC)** — 사용자 결정 (2026-10-02): take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다. (10-02 09:25 KST)
- **soccer goal jump 표본 극소(n=1·2) — 논문 핵심 종목 가설 2번 검증 불가** — 사용자 결정 (2026-10-02): 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다. (10-02 09:25 KST)
- **백테스트에서 apricot-fruit(tick=85)이 apricot-eco(tick=90)보다 ROI 2.7%p 열위 — 파라미터 조정 시점 결정 필요** — 사용자 결정 (2026-10-02): 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용). (10-02 09:25 KST)

</details>

# polylab 확인·결정 목록 (attention inbox)

갱신 2026-10-11 03:31 KST · 열린 항목 38건 (긴급 0 · 경고 2 · 결정 필요 11 · 참고 25)

매 회고(일일 3회·주간·월간)가 자동으로 갱신한다. **자동 규칙** 항목은 조건이 풀리면 스스로 '최근 해결'로 옮겨지고, **AI 판단** 항목은 7일 동안 다시 나오지 않으면 만료된다. 근거 경로는 이 저장소 기준이며, `metrics/…` 같은 경로는 AI context pack(공개 사본 `reports/context/latest/`)을 가리킨다.

**답하는 법**: 항목 id 와 결정을 [`reports/decisions.md`](decisions.md) 에 한 줄로 적거나(GitHub 웹 편집 가능) Claude 에게 말하면 기록된다. 다음 회고가 그 항목을 '사용자 결정'으로 닫고, AI 는 결정을 전제로 판단한다.

## 열린 항목

### [경고] 수동 베팅 누적 실현 -3,666.45 USDC: bankroll의 -36.7% (수동 AI 베팅 (red, 메인))

- 위험 · 자동 규칙 · 최초 10-04 03:30 · 갱신 10-11 03:31 KST · id `manual_drawdown:red`
- 근거: `reports/daily/2026-10-11-dawn.md`

계좌 수동 AI 베팅 (red, 메인) 실현손익이 처음 기록된 bankroll 10,000.00 USDC의 −10% 아래로 내려갔다. 베팅 금액·선택 기준 점검 권장.

### [경고] goal-over-all 자동 변경: mode live→paper

- 시스템 변경 · 자동 규칙 · 최초 10-11 03:31 · 갱신 10-11 03:31 KST · id `stake:goal-over-all:1791657064`
- 근거: `strategies/goal-over-all.yaml`

10-11 03:31 KST 적용(ladder). 사유: ladder: 43 trades at floor with cumulative loss. 증액은 2026-10-06 연구자 결정(stake:freeze-5)으로 동결(모든 변형 5 USDC), 감액·paper 전환은 손실이나 표본 규칙으로 자동 적용된다.

### [결정 필요] late-leader-paper 실거래용 계좌가 필요합니다

- 결정 · 자동 규칙 · 최초 10-07 03:30 · 갱신 10-11 03:31 KST · id `account:late-leader-paper`
- 근거: `reports/daily/2026-10-11-dawn.md`

막판 선두 수렴 가설(late_leader)은 계좌 없이 paper 로만 돈다. 2026-10-06 확인: 자금이 있는 여유 계좌가 없다(bear·fox·wolf·eagle·orange 현금 0, yellow 현금 0, red 는 수동 트랙). 실거래를 원하면 계좌 하나에 USDC 를 넣고 별칭을 알려 주세요(다음 작업에서 yaml 에 연결). 그 뒤에도 live 는 게이트를 통과한 종목만 5 USDC 다: 지금 후보는 NFL 하나, NHL 은 현재 재생 규칙(n ≥ 40)에 닿지 않아 창 결정이 필요하다. 근거 docs/research/hypothesis-late-leader-convergence.md

### [결정 필요] paper 변형 watermelon-cat 표본 71건 도달: live 전환 결정 필요

- 결정 · 자동 규칙 · 최초 10-07 19:30 · 갱신 10-11 03:31 KST · id `paper_ready:watermelon-cat`
- 근거: `strategies/watermelon-cat.yaml`

paper 정산 71건, 승률 95.8%, ROI -0.2%(paper 원장). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다. 아니면 그대로 두거나 retire 한다.

### [결정 필요] paper 변형 watermelon-dog 표본 71건 도달: live 전환 결정 필요

- 결정 · 자동 규칙 · 최초 10-07 19:30 · 갱신 10-11 03:31 KST · id `paper_ready:watermelon-dog`
- 근거: `strategies/watermelon-dog.yaml`

paper 정산 71건, 승률 94.4%, ROI -1.0%(paper 원장). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다. 아니면 그대로 두거나 retire 한다.

### [결정 필요] cherry-tiger 3일 이상 진입 0건 (대상 경기 23개 있었음)

- 결정 · 자동 규칙 · 최초 10-07 08:00 · 갱신 10-11 03:31 KST · id `dead_variant:cherry-tiger`
- 근거: `strategies/cherry-tiger.yaml`

마지막 진입 10-04 03:49 KST. 같은 기간 대상 종목(soccer) 경기는 23개였다. 진입 조건이 지나치게 엄격하거나 버그일 수 있다. AI 회고가 백테스트 근거로 조건을 다시 맞추거나(retro 가 직접 재생해 검증), 폐기(retire) 여부를 판단해야 한다.

### [결정 필요] paper 변형 cherry-blue 표본 30건 도달: live 전환 결정 필요

- 결정 · 자동 규칙 · 최초 10-09 19:31 · 갱신 10-11 03:31 KST · id `paper_ready:cherry-blue`
- 근거: `strategies/cherry-blue.yaml`

paper 정산 30건, 승률 100.0%, ROI 1.6%(paper 원장). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다. 아니면 그대로 두거나 retire 한다.

### [결정 필요] goal-over-all maker 주문 24시간: 448건 중 428건 미체결(95.5%) — 기준선 연구 표본 편향 심각

- 결정 · AI 판단 · 최초 10-09 19:31 · 갱신 10-09 19:31 KST · id `ai:ai-ai-ai-goal-over-all-maker-fill-rate`
- 근거: `report.json, metrics/goal-over-all.json`

오늘 24시간 기준 448건 주문 중 428건(95.5%) 미체결. 이전 보고(83% 미체결)보다 악화. goal-over-all은 Over 0.5 시장 전수 매수를 통한 기준선 연구 목적이나, 실제 체결 표본이 5% 미만이면 연구 설계 전제가 붕괴된다. 연구자 결정 필요: (1) take_profit_delta를 시장가에 가까운 값으로 조정해 체결률 개선(owner_fixed 아님, 조정 가능), (2) 현재 체결 편향이 어떤 경기·가격대에 편중되는지 분석 후 판단, (3) maker 방식 전환 여부. goal-over-all hold_above_price/stop_loss_pct/stop_loss_price는 owner_fixed로 변경 불가.

### [결정 필요] plum NBA paper 누적 손실 심화 — king -8.07%(n=13), queen -11.13%(n=13); 오늘만 king -14.45%, queen -11.55%

- 결정 · AI 판단 · 최초 10-09 08:01 · 갱신 10-09 08:01 KST · id `ai:plum-nba-paper-loss-deepening`
- 근거: `metrics/plum-king.json, metrics/plum-queen.json`

plum:nfl-only 결정(decisions.md 2026-10-06)에서 NBA·NHL·MLB는 양수 조건이 없어 paper로만 표본을 쌓는 중. 오늘 2026-10-08 NBA에서 king 8건 ROI -14.45%(stop_loss 5건: Warriors/Trail Blazers, Magic/Grizzlies, Bucks/Thunder, Suns/Bulls, Timberwolves/Pacers), queen 9건 ROI -11.55%(stop_loss 4건). 누적 king NBA 13건 -8.07%, queen NBA 13건 -11.13%로 손실이 계속 심화되고 있다. plum-king/queen 파라미터 쿨다운은 2026-10-09 12:12에 만료된다. 쿨다운 만료 후 NBA(및 NHL·MLB) sport_override를 off로 전환할지, 아니면 그리드 탐색이 충분하지 않다고 보고 계속 paper 표본을 쌓을지 결정이 필요하다.

### [결정 필요] late-leader-paper NFL 백테스트 통과 — 실거래 계좌 alias 지정 필요

- 결정 · AI 판단 · 최초 10-07 03:30 · 갱신 10-07 03:30 KST · id `ai:account-late-leader-paper`
- 근거: `metrics/late-leader-paper.json, decisions.md`

late-leader-paper NFL(T=170분, Y=0.88, Z=0.98)이 엔진 재생에서 통과 후보로 확인됐으나 현재 계좌가 없어 live 전환 불가. bear·fox·wolf·eagle·orange·yellow 계좌가 비어 있고 red는 수동 운용 중. 연구자가 계좌 alias를 지정하면 다음 회고에서 yaml에 연결하고, 기존 게이트(재생·paper 표본) 충족 후 5 USDC live 시작.

### [결정 필요] goal-over-all maker 주문 24시간 전체: 152건 중 126건 미체결(83%) — 기준선 연구 표본 편향 위험

- 결정 · AI 판단 · 최초 10-06 08:00 · 갱신 10-06 08:00 KST · id `ai:ai-ai-goal-over-all-maker-fill-rate`
- 근거: `report.json, metrics/goal-over-all.json, decisions.md`

2026-10-05T05:12 maker 방식 전환 후 24시간 전체 집계(report.json): tx 152건 중 unfilled 126건(83% 미체결). 킥오프 전 지정가 주문이 시장 호가에 닿지 않아 대부분 취소되는 것으로 보인다. goal-over-all의 핵심 가설은 주요 리그 Over 0.5를 기계적으로 전수 매수해 시장 효율성(ROI ≈ 0)을 확인하는 것이다. 83% 미체결은 체결된 경기만 분석 대상이 되는 선택 편향을 만들어 기준선 가설 검증을 왜곡한다. 체결 가능한 경기(호가 낮은 경기)만 포함되면 Over 0.5 win_rate 추정치가 편향될 수 있다. maker 유지, taker로 복귀, 또는 maker 체결 실패 시 즉시 taker 폴백 방식 중 결정이 필요하다.

### [결정 필요] goal-over-all 누적 stop_loss 6건 전부 방향 옳은 경기 — stop_loss_pct 재검토 필요

- 결정 · AI 판단 · 최초 10-05 08:00 · 갱신 10-05 08:00 KST · id `ai:ai-ai-ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, metrics/watermelon-cat.json, decisions.md`

오늘 Greece/Germany(진입 0.96→stop 0.862, −0.56 USDC)·Wales/Denmark(0.94→0.84, −0.59 USDC) 2건 추가로 총 11건 중 6건 stop_loss. 6건 전부 watermelon-cat이 동일 경기에서 88-94분에 take_profit을 달성해 득점 확인됨(Malta/Andorra·Kosovo/Austria·Azerbaijan/Lithuania·Belarus/San Marino·Greece/Germany·Wales/Denmark). 모두 프리게임 또는 킥오프 직후 가격이 −10%+ 하락해 발동. allow_in_play=false + stop_loss_pct=10% 조합이 시장 변동성으로 방향 정확 포지션 6/6을 조기 청산하는 구조적 패턴. exit 파라미터(stop_loss_pct)는 owner_fixed이므로 AI 변경 불가. stop_loss_pct 확대 또는 프리게임 구간 비활성화 여부 결정 요망.

### [결정 필요] goal-over-all 프리게임 stop_loss 4건 모두 방향 맞은 경기 — stop_loss_pct 재검토 필요

- 결정 · AI 판단 · 최초 10-05 03:30 · 갱신 10-05 03:30 KST · id `ai:ai-ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, metrics/watermelon-cat.json, decisions.md`

오늘 Malta/Andorra(진입 0.88 → stop 0.79, −0.59 USDC), Kosovo/Austria(0.94 → 0.84, −0.59 USDC), Azerbaijan/Lithuania(0.88 → 0.79, −0.59 USDC) 3건 stop_loss 발동. 세 경기 모두 watermelon-cat이 88.15분·93.05분·92.73분에 take_profit 달성해 최종 득점 확인. 이전 Belarus/San Marino(진입 0.968 → 0.832, −0.75 USDC)도 동일 패턴이었음. 누적 7건 중 4건 stop_loss, 최소 3건이 방향 옳은 경기에서 프리게임 가격 하락으로 발동됐다. allow_in_play=false 전략에서 stop_loss_pct=10%는 킥오프 전 시장 변동(−10%)으로 조기 청산 후 경기 자체를 놓치게 만드는 구조다. stop_loss_pct 확대 또는 프리게임 구간 비활성화 여부를 결정해 주시기 바랍니다. 이 파라미터는…

### [참고] 데이터 품질 이벤트 live_gap 241건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-01 09:00 · 갱신 10-11 03:31 KST · id `quality:live_gap`
- 근거: `reports/daily/2026-10-11-dawn.md`

라이브 경기 중 1분 가격 bar 공백. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 missing_book 286건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-08 19:31 · 갱신 10-11 03:31 KST · id `quality:missing_book`
- 근거: `reports/daily/2026-10-11-dawn.md`

missing_book. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] 데이터 품질 이벤트 live_history_mismatch 125건 (24시간)

- 데이터 품질 · 자동 규칙 · 최초 10-10 08:01 · 갱신 10-11 03:31 KST · id `quality:live_history_mismatch`
- 근거: `reports/daily/2026-10-11-dawn.md`

라이브 가격과 history 가격 5c 이상 불일치. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. 300건 이상이면 경고로 올린다.

### [참고] paper 변형 lts-king 증거 수집 중 (0/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-11 03:31 · 갱신 10-11 03:31 KST · id `paper:lts-king`
- 근거: `strategies/lts-king.yaml`

가설: LTS arm A(늦게): 경기 진행 80–90% 뒤 선두 가격이 임계값 Y 에 들어오면 매수호가 한 틱 아래 지정가(수수료 0)로 사서 정산까지 보유한다. 종목별 (T, Y, 대기) 는 2026-10-10 사전 등록 grid 에서 H1 데이터만으로 고른 칸. 근거 docs/research/hypothesis-lts.md.. paper 정산 0건, ROI –(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] paper 변형 lts-queen 증거 수집 중 (0/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-11 03:31 · 갱신 10-11 03:31 KST · id `paper:lts-queen`
- 근거: `strategies/lts-queen.yaml`

가설: LTS arm B(이르게): 경기 진행 60–70% 뒤 선두 가격이 임계값 Y 에 들어오면 매수호가 한 틱 아래 지정가(수수료 0)로 사서 정산까지 보유한다. 종목별 (T, Y, 대기) 는 2026-10-10 사전 등록 grid 에서 H1 데이터만으로 고른 칸. 근거 docs/research/hypothesis-lts.md.. paper 정산 0건, ROI –(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] paper 변형 ai-ou05-red 증거 수집 중 (0/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-10 19:31 · 갱신 10-10 19:31 KST · id `paper:ai-ou05-red`
- 근거: `strategies/ai-ou05-red.yaml`

가설: Claude(Opus 5.5)와 ChatGPT(GPT-6.1-Sol)가 매일(10:00 KST) 시장 가격을 보지 않고 웹 조사만으로 향후 30시간 유럽 5대 리그(EPL·라리가·분데스리가·세리에A·리그1)·MLS·챔피언스리그·유로파·네이션스리그 경기의 0:0 확률을 각자 매기고, 두 AI 모두 '0:0 이 가장 안 날' 상위 8위 안에 넣은 경기는 To…. paper 정산 0건, ROI –(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] NFL 0.60–0.70 정배가 실제 승률보다 비싸게 거래됨

- 연구 발견 · AI 판단 · 최초 10-10 03:31 · 갱신 10-10 03:31 KST · id `ai:nfl-favourite-overpriced`
- 근거: `calibration_summary.json, report.md`

calibration_summary.json(2026-09-30 생성) NFL 전 구간 0.6–0.7 가격대는 평균가 0.6446, 실제 승률 0.5263(n=114)으로 gap -0.1183이고 유의하다고 표시된다. 반대로 0.1–0.4 약세 쪽은 승률이 가격보다 높다(예: 0.3–0.4 gap +0.1074, n=117). 어제 NFL 1경기에서도 경기전 0.81 정배가 패했다(1경기라 증거는 아님). late-leader·apricot NFL 가설의 진입 가격대를 정할 때 참고할 만한 후보 논문 문장이다.

### [참고] paper 변형 apricot-fruit 증거 수집 중 (3/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-05 19:31 · 갱신 10-09 19:31 KST · id `paper:apricot-fruit`
- 근거: `strategies/apricot-fruit.yaml`

가설: 경기 후반(tick0 이후 벽시계 분)의 midpoint 선두팀은 남은 시간 대비 과소평가되어, 끝나기 전 작은 이익(TP ≤ 0.96)으로 조기 청산할 수 있다. 종목마다 진입 시각·밴드·TP 가 다르다(2026-10-06: NFL 190분, NBA 150분, NHL 50분, MLB 60분 등). B arm: 진입가 −0.30 손절(같은 진입 규칙에서…. paper 정산 3건, ROI 5.8%(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] watermelon NBA A/B n=16 결과 안정화: cat(무조기익절) ROI +4.05% 전승 vs dog(delta=0.01) ROI −0.59% demote_warni…

- 연구 발견 · AI 판단 · 최초 10-09 19:31 · 갱신 10-09 19:31 KST · id `ai:ai-ai-ai-watermelon-nba-ab-day1`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json, events_summary.json`

cat(take_profit_delta=null, stop_price=0.60)과 dog(take_profit_delta=0.01, stop_price=0.70)의 NBA 4분기 진입 A/B가 각 16건으로 안정권 진입. cat은 16전 전승 ROI +4.05%, dog은 16건 중 손실 발생 ROI −0.59%로 demote_warning. events_summary NBA 4분기 점수 이벤트 mean_abs_jump 0.0592, reversion_10m ≈ 0와 일치: 충격이 지속되므로 조기 익절이 유리하지 않다. n=16으로 아직 최소 40건 미만이나 방향성은 일관적. 이전 n=8 기록 업데이트.

### [참고] NBA score jump 4쿼터(36–48분) n=37→234로 확대 — 단조 증가 패턴 안정화, late-leader 근거 강화

- 연구 발견 · AI 판단 · 최초 10-09 03:31 · 갱신 10-09 03:31 KST · id `ai:ai-ai-nba-score-4q-jump-peak`
- 근거: `events_summary.json, metrics/late-leader-paper.json`

events_summary.json(2026-10-08T18:32) 기준 NBA score mean_abs_jump: 1Q 0–12분(n=332) 0.0408, 2Q 12–24분(n=277) 0.0404, 3Q 24–36분(n=213) 0.0466, 4Q 36–48분(n=234) 0.0592. 이전 보고 시점 대비 4Q 표본이 37→234로 약 6배 확대됐음에도 단조 증가 패턴이 유지된다. 4Q reversion_10m=−0.0006으로 득점 후 가격이 거의 되돌아오지 않는다. late-leader-paper NBA(150분 이후 진입)의 이론적 근거와 직접 일치하며, 오늘 첫 NBA 정산(156.1분 진입, ROI +6.7%)과도 방향이 같다. NHL(0–20분 최고)·축구(30–45분 최고)와 이질적인 종목별 패턴은 논문 분류 기준으로 활용 가능.

### [참고] paper 변형 late-leader-paper 증거 수집 중 (1/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-07 03:30 · 갱신 10-08 19:31 KST · id `paper:late-leader-paper`
- 근거: `strategies/late-leader-paper.yaml`

가설: 막판 선두 수렴(연구자 2026-10-06 저녁): 무승부 없는 승패 마켓에서 경기 전 정배 가격 ≤ X 인 경기의 선두가 경과 시간 ≥ T 이후 Y 를 아래에서 위로 넘으면, 남은 시간 대비 역전 확률이 가격보다 작아 1.0 쪽으로 수렴한다 — Y 에 사서 Z 에서 조기 익절. (T, Y) 는 종목별 역전 확률 표로 고른다. 근거·사전 등록 docs/r…. paper 정산 1건, ROI 6.7%(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] watermelon NBA A/B n=8: cat(stop 0.60) ROI +3.09% vs dog(stop 0.70) ROI −2.84% — 패턴 안정화

- 연구 발견 · AI 판단 · 최초 10-07 19:30 · 갱신 10-07 19:30 KST · id `ai:ai-ai-watermelon-nba-ab-day1`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json`

paper NBA 각 8건: cat(take_profit_delta=null, stop_price=0.60) all-win ROI +3.09%(+1.24 USDC), dog(take_profit_delta=0.01, stop_price=0.70) 1 stop_loss ROI −2.84%(−1.14 USDC). Suns/Pistons 동일 경기에서 dog은 stop_price 0.70 발동(−1.48 USDC), cat은 0.60 미발동 후 resolution_win(+0.17 USDC). Pistons가 최종 승리했으므로 두 arm 모두 정방향 포지션이었다. live soccer(n=45/46)에서 dog이 cat보다 우세했던 것과 반대 방향. NBA 쿼터 변동성에서 stop_price 0.70이 false stop을 더 자주 유발하는 것으로 추정. n=8로 통계 결론 불가, soccer와 미국 스포츠의 stop_price 최적값이 다를 수 있음을 시사.

### [참고] paper 변형 cherry-tiger 증거 수집 중 (0/20건)

- 시스템 변경 · 자동 규칙 · 최초 10-05 19:31 · 갱신 10-07 03:30 KST · id `paper:cherry-tiger`
- 근거: `strategies/cherry-tiger.yaml`

가설: B arm: 백테스트(2026-02~10, 9.6만 마켓)에서 카테고리별로 H1 으로 고른 셀 중 live 문턱(n≥60, 두 반기 ≥0)을 넘은 유일한 조합 — esports(e스포츠) 마켓에서 종료 36–60시간 전 앞선 결과가 0.94–0.96 이면 정산까지 보유. 8만여 셀·11개 카테고리 탐색 뒤 고른 값이라 우연일 수 있어 paper 로 표본…. paper 정산 0건, ROI –(paper 원장, 실손익 아님). live 전환: 종목별 변형은 결정론 승격 게이트가 자동으로, 또는 AI 회고가 제안하면 retro 가 직접 돌린 재생(종목별 최소 건수·전체와 두 반기 ROI ≥ 0)과 paper 표본(유의하게 음수가 아님)이 통과할 때만 (변형, 종목)을 5 USDC 로 전환한다(2026-10-06 promotion:ai-direct). 연구자가 yaml 로 직접 바꿀 수도 있다.

### [참고] plum NFL A/B: queen(무손절) +4.16% vs king(SL 0.12) −2.81% — n=13 동일 조건

- 연구 발견 · AI 판단 · 최초 10-07 03:30 · 갱신 10-07 03:30 KST · id `ai:plum-queen-nfl-ab-split`
- 근거: `metrics/plum-queen.json, metrics/plum-king.json`

2026-10-06 v7 파라미터 변경 이후 집계 기준 plum-queen NFL 13건 ROI +4.16%(PnL +2.706 USDC), plum-king NFL 13건 ROI −2.81%(PnL −1.824 USDC). 밴드(0.65–0.68), TP(0.95), min_wall_minute(60)은 동일하고 손절(queen SL=0.65≈무손절 vs king SL=0.12)만 다르다. 무손절 arm이 일관되게 우세하며 축구·NBA의 1분 주기 stop_loss 구조 문제와 같은 방향. n=13으로 통계 결론 불가이나 격차 확대 추세 주목.

### [참고] NBA score jump 4쿼터(36-48분)가 전 구간 최고 — MLB·NFL도 후반 증가, 가설 2번 지지

- 연구 발견 · AI 판단 · 최초 10-06 19:30 · 갱신 10-06 19:30 KST · id `ai:ai-nba-score-4q-jump-peak`
- 근거: `events_summary.json`

events_summary.json 기준 NBA score mean_abs_jump: 1쿼터 0-12분(n=87) 0.0406, 2쿼터 12-24분(n=65) 0.0409, 3쿼터 24-36분(n=40) 0.0444, 4쿼터 36-48분(n=37) 0.0596으로 단조 증가. NFL도 45-60분(n=51, 0.0482)이 30-45분(n=49, 0.0232)보다 높고, MLB는 6-9이닝(n=16, 0.1456)이 0-3이닝(n=16, 0.0913) 대비 60% 높다. 세 종목이 논문 가설 2번(득점 민감도가 경기 후반으로 갈수록 커지는가) 방향과 일치. 반면 NHL(0-20분 0.1028이 최고)·축구(30-45분 0.1064이 peak)는 반대 패턴. 전 구간 n이 충분하지 않아(37-87건) 결론 불가이나 종목 간 이질성이 뚜렷해 논문 분석의 분류 기준으로 참고할 수 있다.

### [참고] watermelon NBA 첫날 A/B: cat 4건 전승 ROI +3.6% vs dog 1손절 ROI -6.4% — 축구 패턴과 반대

- 연구 발견 · AI 판단 · 최초 10-06 19:30 · 갱신 10-06 19:30 KST · id `ai:ai-watermelon-nba-ab-day1`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json`

2026-10-06 NBA paper 첫 거래(각 4건): cat(take_profit_delta=null, stop_price=0.60) 4건 resolution_win ROI +3.56%, dog(take_profit_delta=0.01, stop_price=0.70) 3건 수익 + Suns/Pistons stop_loss -1.48 USDC ROI -6.36%. 동일 Suns/Pistons 경기에서 cat은 0.96 진입 후 정산승 +0.167 USDC, dog은 0.96 진입 후 stop_price 0.70 발동 -1.48 USDC(Pistons가 결국 승리). 1분 주기 stop_loss가 방향이 맞는 포지션을 청산한 것으로 축구 패턴과 동일하다. 축구에서는 TP delta 0.04(dog) › 0.02(cat)였으나 NBA 첫날은 cat 우세다. n=4로 우연과 구분 불가이나, 논문 가설 3번(stake 단위별 수익 안정성)에 앞서 TP·손절 설계 차이가 미국 스포츠에서…

### [참고] watermelon game_minute_at_entry — US sports paper 전 청산 경로 정상 기록 확인 (해소)

- 데이터 품질 · AI 판단 · 최초 10-06 19:30 · 갱신 10-06 19:30 KST · id `ai:ai-ai-ai-watermelon-entry-minute-null`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json`

오늘 watermelon-cat paper 8건(resolution_win 전건)과 watermelon-dog paper 8건(take_profit 6건·stop_loss 1건·resolution_win 1건) 모두 game_minute_at_entry가 정상 기록됐다. dog Suns/Pistons stop_loss(47.5분)에서도 값이 있어 이전에 NaN이었던 stop_loss 경로 문제가 paper US sports에서 해소됐다. MLB·NBA·NHL 각 종목 진입 분도 올바르게 기록(MLB 6.0·8.5이닝 환산, NBA/NHL 24.0·33.5·42.2·44.4·47.5·59.7분 등). 이전 live soccer 구간의 NaN은 과거 데이터로 남고, 이후 paper US sports 표본은 논문 핵심 변수로 사용 가능하다.

### [참고] watermelon soccer 90-105분 구간이 유일 음수 버킷 — cat 13건 ROI −4.91%·dog 11건 ROI −5.43%

- 연구 발견 · AI 판단 · 최초 10-06 03:30 · 갱신 10-06 03:30 KST · id `ai:ai-watermelon-90plus-entry-bucket`
- 근거: `metrics/watermelon-cat.json, metrics/watermelon-dog.json`

by_entry_minute 기준: 90-105분 구간이 두 변형 모두 전 구간 중 유일하게 음수다. cat 13건 ROI −4.91%, dog 11건 ROI −5.43%. 다른 구간(15-90분)은 모두 양수. 오늘 Cyprus DRAW:YES(92.93분 진입, 득점으로 cat 0.96→0.03 stop_loss −4.86 USDC, dog 0.97→0.02 −4.91 USDC)가 이 버킷에 추가됐다. 현재 watermelon soccer에는 진입 시점 상한(max_wall_minute)이 적용되지 않아 90분+ 구간 진입이 가능한 구조다. take-profit early 결정(decisions.md 2026-10-02) 외에 이 구간 자체를 어떻게 처리할지는 데이터가 처음으로 충분한 표본으로 확인된 시점이다.

### [참고] MLB run jump 이닝 후반 증가 패턴 표본 강화 (n 9/11/6→15/20/14)

- 연구 발견 · AI 판단 · 최초 10-05 19:31 · 갱신 10-05 19:31 KST · id `ai:ai-mlb-run-sensitivity-increasing`
- 근거: `events_summary.json`

events_summary.json(2026-10-04T23:30 갱신) 기준: MLB run mean_abs_jump 0-3이닝(n=15, 0.095)→3-6이닝(n=20, 0.105)→6-9이닝(n=14, 0.153), 9+이닝(n=1, 0.38)은 이상치 가능. 기존 n=9/11/6 대비 n=15/20/14로 성장해 이닝 후반 증가 패턴이 더 안정적으로 나타난다. 전 구간 여전히 n‹20으로 통계적 결론은 불가하나 논문 가설 2번(득점 민감도가 경기 후반으로 갈수록 커지는가) 방향과 일치. 대조적으로 NHL goal jump는 0-20분(0.107)→40-60분(0.073)으로 하락, 축구는 30-45분이 peak로 종목 간 차이가 존재한다.

### [참고] goal-over-all maker 주문 첫날: 11건 제출 중 9건 미체결 (10:31 KST 기준)

- 연구 발견 · AI 판단 · 최초 10-05 19:31 · 갱신 10-05 19:31 KST · id `ai:ai-goal-over-maker-fill-rate`
- 근거: `metrics/goal-over-all.json`

2026-10-05 05:12 maker 주문 방식 전환 후 첫날 관찰. 05:30~08:52 사이 UNL 경기 대상 11건 지정가(maker) 주문 제출, 10:31 현재 9건 entry_price=NaN(미체결), England vs Czechia 1건 0.962에 1.32주 부분 체결, Chicago Fire(전일 진입) 1건 확인. maker_ttl_minutes=60으로 60분마다 재호가. UNL 오후 경기들은 킥오프까지 시간이 남아 체결 가능성 있음. 수수료 절감 효과와 시장 접근성(미체결 기회 손실) 사이의 균형이 첫 데이터로 축적되는 중. goal-over-all 기준선 가설 검증에서 체결률이 저조하면 대조군과 표본 불균형이 발생할 수 있다.

### [참고] 주간 회고 제안 1건 거부됨 (validator)

- 시스템 변경 · 자동 규칙 · 최초 10-05 08:30 · 갱신 10-05 08:30 KST · id `rejected:weekly`
- 근거: `reports/weekly/2026-W40.md`

안전 규칙(표본·cooldown·bounds·ladder)에 걸린 제안은 적용하지 않는다: `watermelon-hawk` new_variant: second opinion (codex) 거부: proposal.json의 values.params.stop_price=0.70은 기반 변형의 0.65(metrics/watermelon-cat.json 및 boun… (review)

### [참고] 지난 7일 파라미터 변경 23건

- 시스템 변경 · 자동 규칙 · 최초 10-05 08:30 · 갱신 10-05 08:30 KST · id `params_7d`
- 근거: `reports/weekly/2026-W40.md`

10-01 01:54 apricot-eco v2: +min_game_volume_usd=20000 (registry); 10-01 01:54 apricot-fruit v2: +min_game_volume_usd=20000 (registry); 10-01 01:54 cherry-blue v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'] (registry); 10-01 01:54 cherry-tiger v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'] (registry); 10-01 01:54 plum-king v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry); 10-01 01:54 plum-queen v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry); 10-01 01:54 watermelon-cat v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry); 10-01 01:54 watermelon-dog v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry); 10-02 14:17 apricot-fruit v3: entry_tick_minute 85→95 (registry); 10-02 14:17 watermelon-cat v3: prob_min 0.92→0.93, +take_profit_cap=0.99, +take_profit_delta=0.02 (registry) 외 13건.

### [참고] watermelon A/B 누적 갱신(n=38/38): dog ROI +0.62% vs cat ROI -1.24%, 격차 3.55 USDC — 이번 주 확대 미미

- 연구 발견 · AI 판단 · 최초 10-05 08:30 · 갱신 10-05 08:30 KST · id `ai:ai-ai-ai-ai-watermelon-soccer-tp-delta-c`
- 근거: `metrics/watermelon-dog.json, metrics/watermelon-cat.json`

누적 live 결과(metrics 기준): dog 38건 PnL +1.19 USDC ROI +0.62%, cat 38건 PnL -2.36 USDC ROI -1.24%, 격차 3.55 USDC. 이전 항목(n=33/34, 격차 3.52 USDC)에서 +0.03 USDC 확대에 그쳤다. 이번 주 추가 PnL: dog +0.81 USDC(4건), cat +0.78 USDC(5건)으로 TP delta 0.04 우위가 UNL 집중 주(전주)에 비해 크지 않았다. 격차가 수렴 중인지 확인이 필요한 시점. take_profit_delta 0.04(dog) vs 0.02(cat) 외 stop_price(0.60 vs 0.65)·use_stored_stop 차이도 있어 TP delta만의 효과 분리는 불가. 두 변형 모두 쿨다운 중(~10-05 05:17)이라 이번 회고에서 변경 없음.

### [참고] watermelon A/B 누적 갱신: dog ROI +0.22%(34건) vs cat ROI −1.90%(33건), 격차 3.52 USDC로 확대

- 연구 발견 · AI 판단 · 최초 10-04 03:30 · 갱신 10-05 03:30 KST · id `ai:ai-ai-ai-watermelon-soccer-tp-delta-comp`
- 근거: `metrics/watermelon-dog.json, metrics/watermelon-cat.json, trades_recent.json`

누적 live 결과: dog 34건 PnL +0.38 USDC ROI +0.22%, cat 33건 PnL −3.14 USDC ROI −1.90%, 격차 3.52 USDC(이전 회고 3.37 USDC에서 확대). 오늘 동시 진입 경기에서 dog이 cat보다 Malta/Andorra +0.02·Azerbaijan/Lithuania +0.04 높은 매도가 달성. take_profit_delta 0.04(dog)가 0.02(cat)보다 더 높은 수렴 가격을 포착하는 패턴 지속. 단 stop_price(0.60 vs 0.65)·use_stored_stop(true vs false) 차이도 있어 TP delta만의 효과 분리 불가. n=33/34로 통계 결론 불가하나 격차가 매일 확대 중이다.

### [참고] goal-over-all 첫 stop_loss: 실제 득점이 있는 경기에서 인게임 가격 임시 하락으로 발동

- 연구 발견 · AI 판단 · 최초 10-04 08:00 · 갱신 10-04 08:00 KST · id `ai:ai-goal-over-all-stop-loss-correct`
- 근거: `metrics/goal-over-all.json, trades_recent.json`

Belarus vs. San Marino Over 0.5: 진입가 0.968, 인게임 가격 임시 하락 0.832(-13.6%)으로 stop_loss_pct=10% 발동(-0.751 USDC 실현손실). 그러나 Belarus는 득점·승리로 경기를 끝냈다(watermelon-cat HOME:YES take_profit으로 확인). 옳은 방향 포지션이 임시 가격 하락으로 손절된 plum(ai:plum-soccer-stop-loss-correct)과 동일 패턴이다. n=4로 결론 불가이나, 킥오프 전 pre-game 진입에서도 1분 주기 집행 한계로 stop_loss_pct=10%가 조기 발동될 수 있음이 확인됐다.

## 최근 해결

<details>
<summary>최근 14일 해결 51건</summary>

- **paper 변형 plum-king 표본 76건 도달: live 전환 결정 필요** — 사용자 결정 (2026-10-06): ROI −3.0% 로는 live 하지 않는다. 더 개선해 ROI 가 양수가 되는 조건을 찾는다. (10-11 03:31 KST)
- **paper 변형 plum-queen 표본 77건 도달: live 전환 결정 필요** — 사용자 결정 (2026-10-06): 마찬가지로 ROI 가 양수가 되는 조건을 찾는다. (10-11 03:31 KST)
- **LTS NFL arm A(lts-king) live 여부 — 사전 등록 규칙 중 다중 비교 점검만 실패** — 사용자 결정 (2026-10-10): (a) NFL arm A(lts-king)는 paper 로 두고 결정론 승격 게이트에 맡긴다(paper 체결 15건 이상·80% 하한 › 0·재생 통과 시 자동 5 USDC live). (10-11 03:31 KST)
- **축구 득점 jump peak가 30-45분 구간 — 가설 2번(후반 민감도 증가)과 반대** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-11 03:31 KST)
- **watermelon A/B arm 누적 갱신: dog TP delta 0.04가 cat 0.02보다 일관되게 높은 성과 (24/22건)** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-10 19:31 KST)
- **watermelon 축구 동시 진입 7건 비교: dog TP delta 0.04가 cat 0.02보다 일관되게 높은 매도가 달성** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-10 08:01 KST)
- **llm-nil-consensus 3일 이상 진입 0건 (대상 경기 11개 있었음)** — 조건 해소 (자동) (10-10 03:31 KST)
- **일일 회고 제안 1건 거부됨 (validator)** — 조건 해소 (자동) (10-10 03:31 KST)
- **paper 변형 llm-nil-consensus 증거 수집 중 (0/20건)** — 조건 해소 (자동) (10-10 03:31 KST)
- **plum 축구 정방향 포지션 2건이 stop_loss로 청산 — Ireland HOME:NO, Seattle DRAW:NO** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-10 03:31 KST)
- **watermelon game_minute_at_entry — take_profit 경로에서 정상 기록 확인, stop_loss·resolution 경로는 여전히 NaN** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-10 03:31 KST)
- **watermelon A/B arm — 동일 NFL 경기에서 cat -1.83 USDC·dog +0.10 USDC** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-10 03:31 KST)
- **paper 변형 cherry-blue 증거 수집 중 (13/20건)** — 조건 해소 (자동) (10-09 19:31 KST)
- **plum-king Ireland HOME:NO — 올바른 방향 포지션이 막판 급락 손절로 청산 (실결과 2-2 무승부 = HOME:NO 정산 승)** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-09 19:31 KST)
- **watermelon 계열 game_minute_at_entry 전건 null — 오늘도 cat 12건·dog 14건 전부 null 지속** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-09 19:31 KST)
- **[확인됨] Azerbaijan 0-0 종료 — llm-nil-draw paper 전손, watermelon-dog live 이익, LLM 첫 예측 실패** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-09 19:31 KST)
- **watermelon-cat 단위 모드 변경: 5→5 USDC, 모드 live→paper** — 조건 해소 (자동) (10-09 08:01 KST)
- **watermelon-dog 단위 모드 변경: 5→5 USDC, 모드 live→paper** — 조건 해소 (자동) (10-09 08:01 KST)
- **MLB 득점 jump가 이닝 후반으로 갈수록 커지는 패턴 — 가설 2번 방향과 일치** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-09 03:31 KST)
- **llm-nil-draw(paper)와 watermelon-dog(live)이 동일 경기 반대 방향 베팅 — 첫 날 LLM 예측 실패 예상** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-09 03:31 KST)
- **NHL 경기 막판(final) 0.30–0.40 버킷에서 약자 저평가 +14.1%p로 전체 스포츠 중 gap 1위** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-08 19:31 KST)
- **watermelon 계열 실거래 3건 모두 game_minute_at_entry=null — 논문 핵심 변수 미수집** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-08 19:31 KST)
- **NFL 전 구간에서 정배 과대평가·약자 저평가 비대칭 패턴 관찰 (4개 버킷 유의)** — 자동 만료 (AI 항목, 7일 동안 재확인 없음) (10-08 19:31 KST)
- **paper 변형 watermelon-cat 증거 수집 중 (19/20건)** — 조건 해소 (자동) (10-07 19:30 KST)
- **paper 변형 watermelon-dog 증거 수집 중 (19/20건)** — 조건 해소 (자동) (10-07 19:30 KST)
- **apricot-fruit 3일 이상 진입 0건 (대상 경기 49개 있었음)** — 사용자 결정 (2026-10-06): AI 회고가 백테스트 근거로 조건을 다시 맞춘다. (10-07 08:00 KST)
- **apricot-eco 3일 이상 진입 0건 (대상 경기 49개 있었음)** — 사용자 결정 (2026-10-06): AI 회고가 백테스트 근거로 조건을 다시 맞춘다. (10-07 08:00 KST)
- **NFL apricot 자동 승격이 구조적으로 불가 — 120일 n≥40 조건 vs NFL 시즌 경기 수 23-39건** — 사용자 결정 (2026-10-06): `manual:nfl-promotion-window` 로 해결(NFL 은 직전 365일 재생 n ≥ 20·paper ≥ 15). (10-07 03:30 KST)
- **데이터 증가 예상 82.47GB/월 (예산 50, 상한 100) — 주요: raw 36.45GB, general 26.67GB** — 조건 해소 (자동) (10-07 03:30 KST)
- **paper 변형 apricot-eco 증거 수집 중 (0/20건)** — 조건 해소 (자동) (10-07 03:30 KST)
- **plum-king 단위 모드 변경: 5→5 USDC, 모드 live→paper** — 조건 해소 (자동) (10-06 19:30 KST)
- **plum-queen 단위 모드 변경: 5→5 USDC, 모드 live→paper** — 조건 해소 (자동) (10-06 19:30 KST)
- **paper 변형 plum-queen 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-06 08:00 KST)
- **paper 변형 plum-king 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-06 08:00 KST)
- **NFL 자동 실거래 전환 기준: 최근 120일 재생 40건을 시즌 단위로 바꿀지 결정 필요** — 조건 해소 (자동) (10-06 03:30 KST)
- **paper 변형 plum-us-paper 표본 27건 도달: live 전환 결정 필요** — 조건 해소 (자동) (10-06 03:30 KST)
- **paper 변형 watermelon-us-paper 표본 31건 도달: live 전환 결정 필요** — 조건 해소 (자동) (10-06 03:30 KST)
- **수동 베팅 정산 패: 수동 AI 베팅 (red, 메인) · Korea Republic vs. Venezuela O/U 0.5 Over -3,544.96** — 조건 해소 (자동) (10-06 03:30 KST)
- **paper 변형 cherry-us-paper 증거 수집 중 (3/20건)** — 조건 해소 (자동) (10-05 19:31 KST)
- **수동 베팅 정산 패: 수동 AI 베팅 (red, 메인) · Israel vs. Kosovo O/U 0.5 Over -565.69** — 조건 해소 (자동) (10-05 08:00 KST)
- **paper 변형 plum-us-paper 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-05 08:00 KST)
- **paper 변형 watermelon-us-paper 증거 수집 중 (16/20건)** — 조건 해소 (자동) (10-05 08:00 KST)
- **cherry-tiger n=6(이전 3건): 오늘 2건 이익 포함, 누적 ROI -8.53% — paper 전환 여부 재확인** — 사용자 결정 (2026-10-04): cherry-tiger·blue 는 paper 로 내리지 않고 live(5 USDC) 를 유지하되 전략을 재설계한다. 실거래 11건(손절 8·익절 3, tiger ROI −8.5%·blue −9.6%)과 8개월 백테스트 모두 옛 설정(0.76–0.82 밴드, TP +20%·SL −8%·trailing 15%)이 수수료·스프레드·1분 주기 손절 미끄러짐 때문에 구조적으로 손해임을 보였다. 새 설정: YES 0.90–0.95 진입, 진입 창은 경기 전 72시간 + 킥오프 후 60분, 손절·trailing·상대 TP 끔, 매도 호가 0.99 이상이면 정산까지 보유, 익절은 수수료 후 순이익일 때만 전량 — blue 는 진입가 +0.03, tiger 는 고정 0.98(청산 방식만 다른 A/B). min_liqui… (10-05 00:06 KST)
- **plum paper 전환 완료 — paper 단계 운영 방향 결정 필요** — 사용자 결정 (2026-10-04): B 로 결정: delta 기반 조기 익절을 paper 에서 테스트한다. plum-king·plum-queen 에 `take_profit_delta` 0.03(진입가 +0.03 이상이면 보유 전량을 수수료 후 순이익일 때만 매도)과 `hold_above_price` 0.99(매도 호가 0.99 이상이면 정산까지 보유)를 적용하고 mode 는 paper 그대로 둔다. 손절폭 A/B(king 0.12 vs queen 0.17)는 유지한다. NFL 과 plum-us-paper 는 백테스트에서 delta 가 오히려 손해라 기존 TP 가격을 유지한다(bounds 만 열어 둠). 백테스트상 plum 은 모든 설정이 약 −2% 로 delta 는 손실을 줄일 뿐이므로 paper 20건 후 재평가한다. 근거 `docs/re… (10-05 00:06 KST)
- **cherry-tiger 실거래 3건 모두 손실·1건 open 중 — paper 전환 고려 여부** — 사용자 결정 (2026-10-04): 위 `ai:ai-ai-cherry-tiger-calib-mismatch` 결정과 같다(paper 전환 대신 live 유지·재설계). (10-05 00:06 KST)
- **cherry-tiger 가설(MLB 0.76-0.78 YES 저평가)이 현재 calibration으로 뒷받침되지 않음** — 사용자 결정 (2026-10-04): 0.76–0.78 밴드 가설은 폐기한다(실현 승률 ≈ 가격, calibration 근거 없음). cherry 는 0.90–0.95 favourite 수렴 가설로 바꿔 검증한다(위 결정). (10-05 00:06 KST)
- **NFL 실거래 유지 여부 결정 필요 — 오늘 5개 변형 NFL 전패, watermelon-dog만 +0.10 USDC** — 사용자 결정 (2026-10-03): NFL 실거래를 폐기한다. 데이터가 충분히 쌓여 수익이 나는 전략과 파라미터가 확보될 때까지 잠정 중단한다. (10-03 13:58 KST)
- **NFL 을 watermelon·plum 실거래 범위에 계속 둘지 결정 필요 (백테스트 전 조건 손실)** — 사용자 결정 (2026-10-03): NFL 을 watermelon·plum 실거래 범위에서 제외한다. NFL 전용 전략이나 기존 전략의 NFL 전용 분기 로직이 생기기 전까지 실거래하지 않는다. (10-03 13:58 KST)
- **watermelon stop_price 미실행 수치 확인 — Republic of Ireland 0.92→0.17 (13분, 손실 –4.14 USDC)** — 사용자 결정 (2026-10-02): take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다. (10-02 09:25 KST)
- **soccer goal jump 표본 극소(n=1·2) — 논문 핵심 종목 가설 2번 검증 불가** — 사용자 결정 (2026-10-02): 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다. (10-02 09:25 KST)
- **백테스트에서 apricot-fruit(tick=85)이 apricot-eco(tick=90)보다 ROI 2.7%p 열위 — 파라미터 조정 시점 결정 필요** — 사용자 결정 (2026-10-02): 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용). (10-02 09:25 KST)

</details>

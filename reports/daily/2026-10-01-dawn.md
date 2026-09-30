# polylab 일일 리포트 2026-10-01 새벽 (03:30 KST)

- 생성: 2026-10-01 01:31 KST · commit `b51516c` · 기간 09-30 01:31 ~ 10-01 01:31 KST
- 대시보드: https://poly.zowoo.uk

## 요약

| 항목 | 값 |
|---|---|
| 실현손익 오늘(KST) | +0.00 USDC |
| 실현손익 7일 / 30일 | +0.00 / +0.00 USDC |
| 실현손익 누적 | +0.00 USDC |
| 기간 내 정산 포지션 | 0건 (+0.00 USDC) |
| 보유 포지션 (미실현, 실현손익 미포함) | 4개 (-0.30 USDC) |
| 변형 live / paper | 8 / 0 |

실현손익은 CONFIRMED 체결 + 수수료 + 확인된 정산으로 청산된 live 포지션만 집계한다 (paper·평가손익·미확정 제외).

## 전략 변형 현황

| 변형 | 모드 | 단위$ | 계좌 | 종목 | 오늘 | 7일 | 30일 | 누적 | 거래(승/패) | 승률 | ROI | 보유 | ladder |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| apricot-eco | live | 5 | eco | mlb | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| apricot-fruit | live | 5 | fruit | mlb | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| cherry-blue | live | 5 | blue | soccer,mlb,nba,nfl,nhl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| cherry-tiger | live | 5 | tiger | soccer,mlb,nba,nfl,nhl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| plum-king | live | 5 | king | soccer,nfl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| plum-queen | live | 5 | queen | soccer,nfl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 0 | hold (0/20, ROI하한 –) |
| watermelon-cat | live | 5 | cat | soccer,nfl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 2 | hold (0/20, ROI하한 –) |
| watermelon-dog | live | 5 | dog | soccer,nfl | +0.00 | +0.00 | +0.00 | +0.00 | 0 (0/0) | – | – | 2 | hold (0/20, ROI하한 –) |

(paper 변형의 손익은 paper 원장 기준이며 실손익이 아니다.)

### 파라미터

- `apricot-eco` (apricot): entry_tick_minute=90, hours_max=8.0, max_entry_spread=0.05, max_exit_spread=0.1, min_leader_margin=0.005, prob_max=0.999, prob_min=0.9, take_profit_price=0.9, tick_window_minutes=2
- `apricot-fruit` (apricot): entry_tick_minute=85, hours_max=8.0, max_entry_spread=0.05, max_exit_spread=0.1, min_leader_margin=0.005, prob_max=0.999, prob_min=0.9, take_profit_price=0.9, tick_window_minutes=2
- `cherry-blue` (cherry): allow_in_play=True, buy_threshold=0.8, entry_hours_max=120.0, entry_hours_min=0.0, market_types=['moneyline', 'draw'], min_liquidity=125000.0, min_volume=5000.0, sell_threshold=0.82, stop_loss_percent=-0.08, take_profit_percent=0.2, trailing_enabled=True, trailing_percent=0.15, yes_only=True
- `cherry-tiger` (cherry): allow_in_play=True, buy_threshold=0.76, entry_hours_max=120.0, entry_hours_min=0.0, market_types=['moneyline', 'draw'], min_liquidity=125000.0, min_volume=5000.0, sell_threshold=0.78, stop_loss_percent=-0.08, take_profit_percent=0.2, trailing_enabled=True, trailing_percent=0.15, yes_only=True
- `plum-king` (plum): hours_max=12.0, max_entry_spread=0.05, max_stop_spread=0.1, min_leader_margin=0.005, prob_max=0.73, prob_min=0.7, sport_overrides={'soccer': {'max_source_minute': 60, 'force_exit_minute': 65}, 'nfl': {'take_profit_price': 0.85}}, stop_loss_delta=0.12, take_profit_price=0.9
- `plum-queen` (plum): hours_max=12.0, max_entry_spread=0.05, max_stop_spread=0.1, min_leader_margin=0.005, prob_max=0.73, prob_min=0.7, sport_overrides={'soccer': {'max_source_minute': 60, 'force_exit_minute': 65}, 'nfl': {'take_profit_price': 0.9, 'stop_loss_delta': 0.12}}, stop_loss_delta=0.17, take_profit_price=0.9
- `watermelon-cat` (watermelon): hours_max=4.0, max_entry_drawdown=0.3, max_stop_spread=0.1, prob_max=0.999, prob_min=0.92, reentry_cooldown_hours=720, sport_overrides={'nfl': {'prob_min': 0.91, 'stop_price': 0.7, 'hours_max': 6.0}}, stop_price=0.65, use_stored_stop=False
- `watermelon-dog` (watermelon): hours_max=4.0, max_entry_drawdown=0.3, max_stop_spread=0.1, prob_max=0.999, prob_min=0.92, reentry_cooldown_hours=720, sport_overrides={'nfl': {'prob_min': 0.94, 'stop_price': 0.7, 'hours_max': 6.0}}, stop_price=0.6, use_stored_stop=True

## 지난 24시간 거래 내역

기간: 09-30 01:31 ~ 10-01 01:31 KST

### watermelon-cat (계좌 cat)

| 시각(KST) | 종목/리그 | 경기 | 토큰 | 구분 | 가격 | 수량 | USDC | 수수료 | 상태 | 포지션 결과 | 실현손익 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10-01 01:30 | soccer/fif | Lithuania vs. Andorra | HOME:YES | BUY | 0.979 | 5.11 | 5.00 | 0.0053 | CONFIRMED | 보유 | – |
| 10-01 01:30 | soccer/afcq | Eritrea vs. South Africa | AWAY:YES | BUY | 0.920 | 5.43 | 5.00 | 0.0200 | CONFIRMED | 보유 | – |

- 합계: 거래 2건, 매수 10.00 / 매도 0.00 USDC, 수수료 0.0253, 미체결 0, 격리 0, 정산 0건 실현 +0.00 USDC

### watermelon-dog (계좌 dog)

| 시각(KST) | 종목/리그 | 경기 | 토큰 | 구분 | 가격 | 수량 | USDC | 수수료 | 상태 | 포지션 결과 | 실현손익 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10-01 01:30 | soccer/fif | Lithuania vs. Andorra | HOME:YES | BUY | 0.985 | 5.08 | 5.00 | 0.0037 | CONFIRMED | 보유 | – |
| 10-01 01:30 | soccer/afcq | Eritrea vs. South Africa | AWAY:YES | BUY | 0.920 | 5.43 | 5.00 | 0.0200 | CONFIRMED | 보유 | – |

- 합계: 거래 2건, 매수 10.00 / 매도 0.00 USDC, 수수료 0.0237, 미체결 0, 격리 0, 정산 0건 실현 +0.00 USDC

**전체 합계**: 거래 4건, 정산 0건, 실현손익 +0.00 USDC (paper 변형 포함 시 paper 원장 기준 표기)

## 보유 포지션 (평가손익 = 미실현, 실현손익에 합산하지 않음)

| 변형 | 진입(KST) | 종목 | 경기 | 토큰 | 진입가 | 수량 | 원가 | 현재가 | 미실현 | 경기분 | 상태 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| watermelon-cat | 10-01 01:28 | soccer | Lithuania vs. Andorra | HOME:YES | 0.979 | 5.11 | 5.01 | 0.985 | +0.02 | 31 | 보유 |
| watermelon-cat | 10-01 01:28 | soccer | Eritrea vs. South Africa | AWAY:YES | 0.920 | 5.43 | 5.02 | 0.895 | -0.16 | – | 보유 |
| watermelon-dog | 10-01 01:28 | soccer | Lithuania vs. Andorra | HOME:YES | 0.985 | 5.08 | 5.00 | 0.985 | -0.01 | 31 | 보유 |
| watermelon-dog | 10-01 01:28 | soccer | Eritrea vs. South Africa | AWAY:YES | 0.920 | 5.43 | 5.02 | 0.895 | -0.16 | – | 보유 |

## 변경 사항 (파라미터·stake)

- 10-01 01:20 apricot-eco v1: 초기 버전 (init)
- 10-01 01:20 apricot-fruit v1: 초기 버전 (init)
- 10-01 01:20 cherry-blue v1: 초기 버전 (init)
- 10-01 01:20 cherry-tiger v1: 초기 버전 (init)
- 10-01 01:20 plum-king v1: 초기 버전 (init)
- 10-01 01:20 plum-queen v1: 초기 버전 (init)
- 10-01 01:20 watermelon-cat v1: 초기 버전 (init)
- 10-01 01:20 watermelon-dog v1: 초기 버전 (init)

## 데이터 수집 상태

- 마지막 poll: 0분 전, WS: 0분 전, game state: 1분 전
- 라이브 경기 2개, 추적 마켓 35개, 백필 0/1 경기
- 디스크 여유 996.5GB, core.db 0.7MB, books 0.4MB, 전략 DB 1.0MB
- 품질 이벤트(24h): poll_gap 1

| 잡 | 상태 | 마지막 실행 | 마지막 성공 | 연속 실패 |
|---|---|---|---|---|
| discover | ok | 10-01 01:31 | 10-01 01:31 | 0 |
| polylab-backfill | stale | – | – | 0 |
| polylab-discover | ok | 10-01 01:31 | 10-01 01:31 | 0 |
| polylab-publish | ok | 10-01 01:31 | 10-01 01:31 | 0 |
| polylab-retro | stale | – | – | 0 |
| polylab-retro-daily | ok | 10-01 01:31 | – | 0 |
| polylab-retro-monthly | stale | – | – | 0 |
| polylab-retro-weekly | stale | – | – | 0 |
| polylab-stream | ok | 10-01 01:30 | – | 0 |
| polylab-tick | ok | 10-01 01:31 | 10-01 01:31 | 0 |

## 연구 하이라이트

calibration 결과 없음(표본 부족 또는 `polylab analyze calibration` 미실행).

## AI 회고

엔진: claude (시도: claude✓)

# polylab 일일 회고 — 2026-10-01 새벽

## 오늘의 한 줄 요약

초기 가동 첫날. 실현손익 **0.00 USDC** (정산 0건). watermelon 계열 2변형이 soccer 2경기에 각각 2건씩 총 4건 매수 체결·오픈 포지션 보유 중; 나머지 6변형은 체결 없음.

---

## 변형별 관찰

**watermelon-cat / watermelon-dog** (soccer, live, stake 5 USDC)

두 변형 모두 2026-09-30 16:28 UTC에 동일 경기 2건을 동시에 진입했다.

- **Lithuania vs. Andorra** (fif, HOME:YES): cat 진입 0.979 / mark 0.9845 → 평가익 +0.023 USDC. dog 진입 0.985 / mark 0.9845 → 평가손 -0.006 USDC. 진입가 차이가 0.006으로, 1분 주기 내 가격 변동이 두 계좌의 fill 단가를 갈라놓았다.
- **Eritrea vs. South Africa** (afcq, AWAY:YES): 두 변형 공통 진입 0.92 / mark 0.895 → 각 평가손 -0.156 USDC. `game_minute = null`로 기록되어 경기 진행 분 데이터가 수집되지 않았다. 이는 리스크 모니터링(인플레이 타임라인)의 맹점이 될 수 있다.

**apricot-eco / apricot-fruit** (MLB, live): 체결 0건. MLB 시즌 시간대·진입 tick 조건 미충족으로 판단.

**cherry-blue / cherry-tiger** (전 종목, live): 체결 0건. 유동성·가격 필터 조건 미충족.

**plum-king / plum-queen** (soccer/NFL, live): 체결 0건. prob 범위(0.70–0.73)에 해당하는 soccer 경기가 진입 창(60분 이전)에 없었던 것으로 추정.

모든 변형: `live.trades.all = 0` (정산 없음), `ladder.promote_ok = false` (스테이크 증액 불가).

---

## 데이터 수집 상태

- **polylab-backfill** 잡이 `stale`(미실행). `last_history_at = null`. 역사적 가격 이력 수집이 시작되지 않아 캘리브레이션 데이터베이스 구축이 막혀 있다. 논문 연구 전반의 선결 과제다.
- **backfill_progress**: games_done 0 / games_total 1. 처리 대기 상태.
- **polylab-retro-weekly / monthly**: 모두 stale. 주간·월간 회고 파이프라인 미가동.
- Eritrea vs. South Africa의 `game_minute = null`은 afcq 리그 경기 상태 수집 누락 가능성을 시사한다.

---

## 논문 관점 관찰

**calibration_summary.json**: `top_gaps`, `brier`, `by_sport_phase` 모두 빈 배열. backfill 미실행으로 정산 토큰 이력이 없어 calibration gap(실현 승률 vs 시장 가격) 계산 자체가 불가하다. 논문 (1)번 가설 검증 데이터 없음.

**events_summary.json**: soccer 골 15–30분 버킷 n=1, n_isolated=1 (표본 1건, 결론 불가). 평균 절대 가격 점프 0.199, mean_pre_price 0.795, 1분 후 되돌림 reversion_1m = −0.0105. 단일 관측이라 유의미한 해석은 불가하지만, 초반 골(경기 초기)에서도 가격 점프 후 즉시 소폭 되돌림이 나타나는 패턴은 논문 (2)번 득점 민감도 연구 방향과 일치한다. 1분 Jenkins 주기로는 이 되돌림을 실시간으로 포착·활용하기 어렵다는 운영 한계도 재확인된다.

---

## 제안 및 변경 없음

- 모든 변형은 2026-09-30 16:20 UTC에 초기화(v1)되어, 이 회고 시점 기준 경과 시간이 약 70분으로 cooldown_params_s (259200초 = 3일) 미충족.
- 정산 거래 전무(all = 0)로 min_trades_params (20건) 미충족.
- 위 두 조건 모두 validator가 params·stake 변경을 거부하므로 변경하지 않는다.
- 가장 시급한 과제는 backfill 잡 재활성화이나, 이는 인프라 운영 차원으로 이 회고의 proposal 범위 밖이다.

## 자동 적용 결과

- 제안 없음

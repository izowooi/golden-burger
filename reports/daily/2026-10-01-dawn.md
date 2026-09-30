# polylab 일일 리포트 2026-10-01 새벽 (03:30 KST)

- 생성: 2026-10-01 03:30 KST · commit `0790cf5` · 기간 09-30 03:30 ~ 10-01 03:30 KST
- 대시보드: https://poly.zowoo.uk

## 요약

| 항목 | 값 |
|---|---|
| 실현손익 오늘(KST) | +0.00 USDC |
| 실현손익 7일 / 30일 | +0.00 / +0.00 USDC |
| 실현손익 누적 | +0.00 USDC |
| 기간 내 정산 포지션 | 0건 (+0.00 USDC) |
| 보유 포지션 (미실현, 실현손익 미포함) | 4개 (+0.99 USDC) |
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

- `apricot-eco` (apricot): entry_tick_minute=90, hours_max=8.0, max_entry_spread=0.05, max_exit_spread=0.1, min_game_volume_usd=20000, min_leader_margin=0.005, prob_max=0.999, prob_min=0.9, take_profit_price=0.9, tick_window_minutes=2
- `apricot-fruit` (apricot): entry_tick_minute=85, hours_max=8.0, max_entry_spread=0.05, max_exit_spread=0.1, min_game_volume_usd=20000, min_leader_margin=0.005, prob_max=0.999, prob_min=0.9, take_profit_price=0.9, tick_window_minutes=2
- `cherry-blue` (cherry): allow_in_play=True, buy_threshold=0.8, entry_hours_max=120.0, entry_hours_min=0.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], market_types=['moneyline', 'draw'], min_liquidity=125000.0, min_volume=5000.0, sell_threshold=0.82, stop_loss_percent=-0.08, take_profit_percent=0.2, trailing_enabled=True, trailing_percent=0.15, yes_only=True
- `cherry-tiger` (cherry): allow_in_play=True, buy_threshold=0.76, entry_hours_max=120.0, entry_hours_min=0.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], market_types=['moneyline', 'draw'], min_liquidity=125000.0, min_volume=5000.0, sell_threshold=0.78, stop_loss_percent=-0.08, take_profit_percent=0.2, trailing_enabled=True, trailing_percent=0.15, yes_only=True
- `plum-king` (plum): hours_max=12.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], max_entry_spread=0.05, max_stop_spread=0.1, min_game_volume_usd=20000, min_leader_margin=0.005, prob_max=0.73, prob_min=0.7, sport_overrides={'soccer': {'max_source_minute': 60, 'force_exit_minute': 65}, 'nfl': {'take_profit_price': 0.85}}, stop_loss_delta=0.12, take_profit_price=0.9
- `plum-queen` (plum): hours_max=12.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], max_entry_spread=0.05, max_stop_spread=0.1, min_game_volume_usd=20000, min_leader_margin=0.005, prob_max=0.73, prob_min=0.7, sport_overrides={'soccer': {'max_source_minute': 60, 'force_exit_minute': 65}, 'nfl': {'take_profit_price': 0.9, 'stop_loss_delta': 0.12}}, stop_loss_delta=0.17, take_profit_price=0.9
- `watermelon-cat` (watermelon): hours_max=4.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], max_entry_drawdown=0.3, max_stop_spread=0.1, min_game_volume_usd=20000, prob_max=0.999, prob_min=0.92, reentry_cooldown_hours=720, sport_overrides={'nfl': {'prob_min': 0.91, 'stop_price': 0.7, 'hours_max': 6.0}}, stop_price=0.65, use_stored_stop=False
- `watermelon-dog` (watermelon): hours_max=4.0, leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], max_entry_drawdown=0.3, max_stop_spread=0.1, min_game_volume_usd=20000, prob_max=0.999, prob_min=0.92, reentry_cooldown_hours=720, sport_overrides={'nfl': {'prob_min': 0.94, 'stop_price': 0.7, 'hours_max': 6.0}}, stop_price=0.6, use_stored_stop=True

## 지난 24시간 거래 내역

기간: 09-30 03:30 ~ 10-01 03:30 KST

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
| watermelon-cat | 10-01 01:28 | soccer | Lithuania vs. Andorra | HOME:YES | 0.979 | 5.11 | 5.01 | 1.000 | +0.10 | 90 | 보유 |
| watermelon-cat | 10-01 01:28 | soccer | Eritrea vs. South Africa | AWAY:YES | 0.920 | 5.43 | 5.02 | 0.999 | +0.41 | – | 보유 |
| watermelon-dog | 10-01 01:28 | soccer | Lithuania vs. Andorra | HOME:YES | 0.985 | 5.08 | 5.00 | 1.000 | +0.07 | 90 | 보유 |
| watermelon-dog | 10-01 01:28 | soccer | Eritrea vs. South Africa | AWAY:YES | 0.920 | 5.43 | 5.02 | 0.999 | +0.41 | – | 보유 |

## 변경 사항 (파라미터·stake)

- 10-01 01:20 apricot-eco v1: 초기 버전 (init)
- 10-01 01:20 apricot-fruit v1: 초기 버전 (init)
- 10-01 01:20 cherry-blue v1: 초기 버전 (init)
- 10-01 01:20 cherry-tiger v1: 초기 버전 (init)
- 10-01 01:20 plum-king v1: 초기 버전 (init)
- 10-01 01:20 plum-queen v1: 초기 버전 (init)
- 10-01 01:20 watermelon-cat v1: 초기 버전 (init)
- 10-01 01:20 watermelon-dog v1: 초기 버전 (init)
- 10-01 01:54 apricot-eco v2: +min_game_volume_usd=20000 (registry)
- 10-01 01:54 apricot-fruit v2: +min_game_volume_usd=20000 (registry)
- 10-01 01:54 cherry-blue v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'] (registry)
- 10-01 01:54 cherry-tiger v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'] (registry)
- 10-01 01:54 plum-king v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry)
- 10-01 01:54 plum-queen v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry)
- 10-01 01:54 watermelon-cat v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry)
- 10-01 01:54 watermelon-dog v2: +leagues=['epl', 'bun', 'fl1', 'lal', 'sea', 'mls', 'unl', 'ucl', 'uel'], +min_game_volume_usd=20000 (registry)

## 데이터 수집 상태

- 마지막 poll: 0분 전, WS: -0분 전, game state: -0분 전
- 라이브 경기 4개, 추적 마켓 48개, 백필 9779/9780 경기
- 디스크 여유 997.2GB, core.db 1565.2MB, books 3.3MB, 전략 DB 1.0MB
- 품질 이벤트(24h): live_gap 6, poll_gap 1

| 잡 | 상태 | 마지막 실행 | 마지막 성공 | 연속 실패 |
|---|---|---|---|---|
| backfill | ok | 10-01 03:14 | 10-01 03:14 | 0 |
| discover | ok | 10-01 03:30 | 10-01 03:30 | 0 |
| polylab-backfill | ok | 10-01 03:14 | 10-01 03:14 | 0 |
| polylab-discover | ok | 10-01 03:30 | 10-01 03:30 | 0 |
| polylab-publish | ok | 10-01 03:26 | 10-01 03:26 | 0 |
| polylab-retro | stale | – | – | 0 |
| polylab-retro-daily | ok | 10-01 03:30 | 10-01 01:31 | 0 |
| polylab-retro-monthly | stale | – | – | 0 |
| polylab-retro-weekly | stale | – | – | 0 |
| polylab-stream | stale | 10-01 02:35 | – | 0 |
| polylab-tick | ok | 10-01 03:30 | 10-01 03:30 | 0 |

## 연구 하이라이트

calibration (생성 2026-09-30T18:30:08Z): gap = 실제승률 − 평균가격, 양수 = 과소평가

| 종목 | 구간 | 가격대 | n | 평균가 | 승률 | 95% CI | gap | 유의 |
|---|---|---|---|---|---|---|---|---|
| nhl | final | 0.30–0.40 | 53 | 0.349 | 0.491 | 0.361–0.621 | +0.141 | 예 |
| nfl | all | 0.60–0.70 | 114 | 0.645 | 0.526 | 0.435–0.616 | -0.118 | 예 |
| nfl | all | 0.30–0.40 | 117 | 0.354 | 0.462 | 0.374–0.552 | +0.107 | 예 |
| nfl | all | 0.10–0.20 | 52 | 0.150 | 0.250 | 0.152–0.382 | +0.100 | 예 |
| nfl | all | 0.20–0.30 | 83 | 0.253 | 0.349 | 0.256–0.457 | +0.096 | 예 |

## AI 회고

엔진: claude (시도: claude✓)

# polylab 일일 회고 2026-10-01 (새벽)

## 오늘의 한 줄 요약

전 변형 초기화 첫날 — 실현손익 +0.00 USDC, watermelon-cat/dog 4건 CONFIRMED 매수(오픈), 정산 없음.

## 변형별 관찰

**watermelon-cat(계좌 cat):** Lithuania vs. Andorra HOME:YES @0.979, Eritrea vs. South Africa AWAY:YES @0.920 각 1건 CONFIRMED 매수. 두 포지션 모두 미결 상태(mark 0.9995/0.999). 미실현 합계 +0.51 USDC(평가손익 — 실손익 아님, `metrics/watermelon-cat.json` open 필드). 첫 진입이 v1 파라미터(리그 필터 없음) 하 16:30 UTC에 이루어져 현재 화이트리스트에 없는 fif·afcq 리그에서 발생. v2 적용(16:54 UTC) 이후부터는 epl/bun/fl1 등 필터가 정상 작동한다.

**watermelon-dog(계좌 dog):** 동일 경기·토큰 각 1건 CONFIRMED 매수. Lithuania @0.985, Eritrea @0.920. 미실현 합계 +0.48 USDC(평가손익, `metrics/watermelon-dog.json`). use_stored_stop=true·stop_price=0.60(B arm)으로 A arm(0.65) 대비 손절 여유가 넓다. 현재 mark 0.999+ 수준이라 두 arm 모두 stop 발동 시나리오는 없다.

**apricot-eco·fruit, cherry-blue·tiger, plum-king·queen:** 체결 0건(각 metrics live.trades.all=0). MLB 경기 조건 미충족 또는 soccer/NFL 진입 기준 미도달로 관찰만 지속 중.

## 데이터 수집 상태

백필 9779/9780 게임 완료(거의 마무리). 24h 품질 이벤트: live_gap 6건, poll_gap 1건(경미, `report.json` health.collector.quality_24h). polylab-stream은 빌드 중(stale), polylab-retro·weekly·monthly 미실행(첫날이라 정상). 핵심 파이프라인(tick, discover, backfill, publish)은 모두 ok.

## 논문 관점

`calibration_summary.json` 상위 gap: NFL 0.60–0.70 구간 gap=−0.118(n=114, 유의) — 강팀 과대평가가 통계적으로 확인된다. 반면 NHL final 0.30–0.40 구간은 gap=+0.141(n=53, 유의)로 약팀 과소평가가 뚜렷하다. plum/cherry가 0.70–0.80대를 공략하는데, calibration은 NFL 이 구간이 오히려 고평가임을 시사해 향후 plum 전략의 NFL 적용 비중을 논문에서 별도 분석할 필요가 있다.

`events_summary.json` 축구 득점 민감도는 30–45분 bucket에서 mean_abs_jump=0.050(pre_price=0.935)으로 최대이나 n=1 단일 사례로 우연과 구분 불가. 60–75분 구간에서는 pre_price가 이미 0.999이라 mean_abs_jump=0.0 — watermelon의 0.92+ 진입 전략이 겨냥하는 후반 고정가 구간의 전형이다. Lithuania 포지션(game_minute=90, mark=0.9995)은 이를 실증한다. 1분 Jenkins 주기에서 후반 stop-loss는 사실상 실행 불가 — take-profit early 철학이 이 구간에서 특히 중요하다.

## 제안과 이유

전 변형 정산 0건으로 표본 최솟값(20건, `bounds.json` rules.min_trades_params)에 크게 못 미친다. 파라미터 cooldown(259200s = 72h)도 미경과. 모든 ladder.promote_ok=false. 데이터가 없는 상태에서 파라미터를 바꾸는 것은 근거 없는 추정이다. 변경하지 않는다.

## 자동 적용 결과

- 제안 없음

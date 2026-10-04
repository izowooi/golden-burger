# 연구자 결정 기록

`reports/attention.md` 의 질문에 대한 연구자의 답. 회고는 이 파일을 읽어 해당 항목을 "사용자 결정"으로 닫고,
AI 는 이 결정을 전제로 판단한다(같은 질문을 다시 하지 않는다). GitHub 웹에서 직접 추가해도 된다. 형식:

    ## YYYY-MM-DD
    - `<attention 항목 id>` — 결정 내용

## 2026-10-02
- `ai:watermelon-soccer-late-collapse` — take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다.
- `ai:soccer-events-sample-too-small` — 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다.
- `ai:apricot-fruit-tick-inferior` — 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용).

## 2026-10-03
- `ai:manual-nfl-scope` — NFL 실거래를 폐기한다. 데이터가 충분히 쌓여 수익이 나는 전략과 파라미터가 확보될 때까지 잠정 중단한다.
- `manual:nfl-scope` — NFL 을 watermelon·plum 실거래 범위에서 제외한다. NFL 전용 전략이나 기존 전략의 NFL 전용 분기 로직이 생기기 전까지 실거래하지 않는다.
- `policy:us-sports` — NBA·NHL(·NFL)은 수익을 내는 전략과 파라미터가 확인될 때까지 수집과 paper 시뮬레이션만 한다(`watermelon-us-paper`, `plum-us-paper`, `cherry-us-paper`). live 전환은 연구자만 결정한다.

## 2026-10-04
- `track1:goal-over-all-live` — goal-over-all 을 lion 계좌로 실거래(5 USDC) 시작한다. 진입은 킥오프 3일 전부터 5분 전까지만(한 달 전 같은 이른 매수는 하지 않는다).
- `track1:goal-over-exit-rules` — Over 0.5(goal-over-all) 청산 기준: 진입가 대비 +2% 이상이면 익절, −10% 이상 하락하면 손절하고, 익절·손절한 경기에는 다시 들어가지 않는다. 매도 호가가 0.99 이상이면 익절·손절 없이 정산까지 보유한다. 이 세 값은 연구자가 정한 기준이므로 AI 는 근거가 충분할 때 attention 으로 먼저 제안하고 임의로 바꾸지 않는다.
- `ai:ai-ai-cherry-tiger-calib-mismatch` — cherry-tiger·blue 는 paper 로 내리지 않고 live(5 USDC) 를 유지하되 전략을 재설계한다. 실거래 11건(손절 8·익절 3, tiger ROI −8.5%·blue −9.6%)과 8개월 백테스트 모두 옛 설정(0.76–0.82 밴드, TP +20%·SL −8%·trailing 15%)이 수수료·스프레드·1분 주기 손절 미끄러짐 때문에 구조적으로 손해임을 보였다. 새 설정: YES 0.90–0.95 진입, 진입 창은 경기 전 72시간 + 킥오프 후 60분, 손절·trailing·상대 TP 끔, 매도 호가 0.99 이상이면 정산까지 보유, 익절은 수수료 후 순이익일 때만 전량 — blue 는 진입가 +0.03, tiger 는 고정 0.98(청산 방식만 다른 A/B). min_liquidity 125000 유지. 백테스트도 edge 를 입증하지 못했으므로(95% 구간이 0 포함) 실거래 20건 후 재평가한다. 근거 `docs/research/backtests/2026-10-04-cherry-plum.md`.
- `ai:ai-cherry-tiger-calib-mismatch` — 위 `ai:ai-ai-cherry-tiger-calib-mismatch` 결정과 같다(paper 전환 대신 live 유지·재설계).
- `ai:cherry-tiger-calib-mismatch` — 0.76–0.78 밴드 가설은 폐기한다(실현 승률 ≈ 가격, calibration 근거 없음). cherry 는 0.90–0.95 favourite 수렴 가설로 바꿔 검증한다(위 결정).
- `ai:plum-paper-next-step` — B 로 결정: delta 기반 조기 익절을 paper 에서 테스트한다. plum-king·plum-queen 에 `take_profit_delta` 0.03(진입가 +0.03 이상이면 보유 전량을 수수료 후 순이익일 때만 매도)과 `hold_above_price` 0.99(매도 호가 0.99 이상이면 정산까지 보유)를 적용하고 mode 는 paper 그대로 둔다. 손절폭 A/B(king 0.12 vs queen 0.17)는 유지한다. NFL 과 plum-us-paper 는 백테스트에서 delta 가 오히려 손해라 기존 TP 가격을 유지한다(bounds 만 열어 둠). 백테스트상 plum 은 모든 설정이 약 −2% 로 delta 는 손실을 줄일 뿐이므로 paper 20건 후 재평가한다. 근거 `docs/research/backtests/2026-10-04-cherry-plum.md`.
- `track1:goal-over-tp-absolute` — goal-over-all 익절은 진입가 +0.02 절대값(2센트)이다. 예: 0.95 매수 → 0.97 이상에서 익절, 0.94 매수 → 0.96 이상에서 익절. 0.97 이상에 산 경우는 목표가가 0.99 보유 구간에 닿으므로 정산까지 보유한다. 손절은 진입가 대비 −10%.
- `track2:over-0.5-only` — 0:0 연구는 Over 0.5 만 거래한다. 자동 매매는 Total 0.5 Over 만 사고, 수동 기록(red)도 Total 0.5 만 트랙 2 로 집계한다(7월의 1.5·3.5·4.5·5.5 줄 거래는 '기타'로 분리).
- `ai:apricot-fruit-idle` — fruit 진입 0건은 버그가 아니라 포스트시즌 경기 수 부족과 진입 시점 0.90 이상 선두 부재 때문이다. 백테스트로 두 arm 공통 prob_min 0.89, take_profit_price 0.92 로 재조정하고, 앞으로 진입이 멈춘 변형은 AI 가 백테스트 근거로 스스로 재조정한다.

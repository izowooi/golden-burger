# 운영 회고 파일 인덱스

- 정렬 기준: 파일명 prefix 3자리 시퀀스

| 시퀀스 | 파일 | 주제 | 작성일 |
|---|---|---|---|
| 001 | [001-golden-cherry-postmortem-2026-08-08.md](001-golden-cherry-postmortem-2026-08-08.md) | golden-cherry 1차 회고 — verify 실패 snapshot; 002가 정정·대체 | 2026-08-08 |
| 002 | [002-live-strategy-fleet-sync-and-postmortem-2026-08-10.md](002-live-strategy-fleet-sync-and-postmortem-2026-08-10.md) | live fleet 동기화, 시작일, 7일 gate 회고, 신규 전략 lifecycle 결함 | 2026-08-10 |
| 003 | [003-blueberry-melon-quince-pending-buy-fix-2026-08-10.md](003-blueberry-melon-quince-pending-buy-fix-2026-08-10.md) | strict audit 쉬운 설명, MATCHED 수량 반올림 및 PENDING_BUY lifecycle 수정 | 2026-08-10 |
| 004 | [004-blueberry-melon-quince-close-only-verification-2026-08-10.md](004-blueberry-melon-quince-close-only-verification-2026-08-10.md) | 8개 close-only build·재동기화 검증 및 closed-market resolution 조회 결함 발견 | 2026-08-10 |
| 005 | [005-blueberry-melon-quince-closed-market-resolution-fix-2026-08-10.md](005-blueberry-melon-quince-closed-market-resolution-fix-2026-08-10.md) | 세 전략의 closed Gamma fallback 수정, 회귀·실 API 검증 및 재가동 절차 | 2026-08-10 |
| 006 | [006-blueberry-melon-quince-resolution-deployment-verification-2026-08-10.md](006-blueberry-melon-quince-resolution-deployment-verification-2026-08-10.md) | 8개 close-only 배포·재동기화 검증, HOLDING 0·RESOLVED 18 확인 | 2026-08-10 |
| 007 | [007-golden-kiwi-cadence-runtime-diagnosis-2026-08-11.md](007-golden-kiwi-cadence-runtime-diagnosis-2026-08-11.md) | Kiwi A/B/C/D 13~14분 runtime, off-schedule cadence invalidation 및 one-sweep 구조 권고 | 2026-08-11 |
| 008 | [008-golden-kiwi-filtered-universe-rebuild-2026-08-11.md](008-golden-kiwi-filtered-universe-rebuild-2026-08-11.md) | Kiwi Gamma server filter benchmark, 1/5 이하 universe 재작성, 새 30일 cohort 실행 계약 | 2026-08-11 |
| 009 | [009-live-strategy-start-dates-2026-08-12.md](009-live-strategy-start-dates-2026-08-12.md) | 계정 메모 12개 job의 현재 strategy/runtime별 최초 확인 live cycle 날짜 | 2026-08-12 |
| 010 | [010-golden-queen-low-trade-diagnosis-2026-08-12.md](010-golden-queen-low-trade-diagnosis-2026-08-12.md) | Queen/King DB·로그 재동기화, timer off·반복 clean 진단과 H/5 무파라미터 재가동안 | 2026-08-12 |
| 011 | [011-jenkins-clean-and-fleet-config-audit-2026-08-12.md](011-jenkins-clean-and-fleet-config-audit-2026-08-12.md) | Queen/King 재가동 검증, 22개 잡 Clean·timer·mode·보안 구성 감사 | 2026-08-12 |
| 012 | [012-papaya-queen-clean-restart-baseline-2026-08-12.md](012-papaya-queen-clean-restart-baseline-2026-08-12.md) | Cat/Dog clean 제거 확인과 Papaya·Queen 4개 잡의 정상 평가 시작일 재설정 | 2026-08-12 |
| 013 | [013-cat-dog-jenkins-build-retention-pilot-2026-08-12.md](013-cat-dog-jenkins-build-retention-pilot-2026-08-12.md) | Cat/Dog Jenkins build·console 14일 보존 pilot과 실제 LogRotator 검증 | 2026-08-12 |
| 014 | [014-golden-quince-low-trade-and-execution-axis-diagnosis-2026-08-12.md](014-golden-quince-low-trade-and-execution-axis-diagnosis-2026-08-12.md) | Quince A/B/C 재동기화, 저빈도·H/5 cadence 판정과 전 팔 TAKER 실행축 결함 진단 | 2026-08-12 |
| 015 | [015-golden-quince-execution-axis-clean-restart-2026-08-13.md](015-golden-quince-execution-axis-clean-restart-2026-08-13.md) | Quince 실행축·pending BUY lifecycle 수정, 3-arm one-time clean 재시작과 H/5·동기화 검증 | 2026-08-13 |
| 016 | [016-golden-blueberry-low-trade-and-shared-sweep-deployment-2026-08-13.md](016-golden-blueberry-low-trade-and-shared-sweep-deployment-2026-08-13.md) | Blueberry A/B 저빈도·cadence 진단, Gamma 공유 sweep 최적화와 자연 build·재동기화 검증 | 2026-08-13 |
| 017 | [017-golden-raspberry-queue-echo-deployment-2026-08-13.md](017-golden-raspberry-queue-echo-deployment-2026-08-13.md) | Queue Echo 가설 사전등록, accountless 3-shard collector, Jenkins timer·daily-rsync 배포 검증 | 2026-08-13 |
| 018 | [018-golden-raspberry-first-day-interim-health-2026-08-13.md](018-golden-raspberry-first-day-interim-health-2026-08-13.md) | Queue Echo 첫 24시간 전 9h13m collection health, MI executor queue off-slot과 control·storage 검증 | 2026-08-13 |
| 019 | [019-golden-raspberry-external-workspace-restart-2026-08-13.md](019-golden-raspberry-external-workspace-restart-2026-08-13.md) | Queue Echo 3개 job 외장 APFS workspace 이전, 새 epoch 재기동·timer·동기화 검증 | 2026-08-13 |
| 020 | [020-live-account-fleet-audit-2026-08-14.md](020-live-account-fleet-audit-2026-08-14.md) | 실제 계좌 16개 누락·clean·용량·DB·로그·wallet 대사 및 lifecycle blocker 감사 | 2026-08-14 |
| 021 | [021-live-order-lifecycle-remediation-2026-08-14.md](021-live-order-lifecycle-remediation-2026-08-14.md) | Cherry·Orange·Yellow·Red 및 zero-fee PENDING_SELL 긴급 복구, close-only 배포와 재가동 gate | 2026-08-14 |
| 022 | [022-yellow-orange-manual-position-and-reactivation-decision-2026-08-14.md](022-yellow-orange-manual-position-and-reactivation-decision-2026-08-14.md) | Yellow 미매핑 Erdoğan 식별, 수동 Lee guard와 Orange MAKER fee 누락에 따른 active 복귀 판정 | 2026-08-14 |
| 023 | [023-yellow-orange-active-reactivation-2026-08-14.md](023-yellow-orange-active-reactivation-2026-08-14.md) | Yellow 수동 position guard, Orange MAKER fee 누락 수정, 두 job active+H/5 복귀 검증 | 2026-08-14 |
| 024 | [024-quince-melon-live-review-2026-08-15.md](024-quince-melon-live-review-2026-08-15.md) | Quince clean cohort 53시간·Melon 9일 저빈도 회고, Melon first-crossing evidence 보완·배포 검증 | 2026-08-15 |
| 025 | [025-golden-strawberry-last-mile-deployment-2026-08-15.md](025-golden-strawberry-last-mile-deployment-2026-08-15.md) | Last Mile 가설 사전등록, accountless CLOB census collector, 외장 Jenkins·10분 timer·daily-rsync 배포 검증 | 2026-08-15 |
| 026 | [026-golden-cherry-orange-yellow-parameter-review-2026-08-16.md](026-golden-cherry-orange-yellow-parameter-review-2026-08-16.md) | Orange·Yellow 최신 cohort 비교, 0.95 상단 구조 검증과 partial-fill lifecycle 결함 진단 | 2026-08-16 |
| 027 | [027-golden-cherry-orange-yellow-followup-2026-08-18.md](027-golden-cherry-orange-yellow-followup-2026-08-18.md) | Orange·Yellow 후속 동기화, 0.95 반복 기각과 active partial-fill 고착 재확인 | 2026-08-18 |
| 028 | [028-golden-cherry-blueberry-runtime-remediation-2026-08-19.md](028-golden-cherry-blueberry-runtime-remediation-2026-08-19.md) | Orange 고가 진입 outcome 해석 정정, Cherry partial-fill 복구, Blueberry volume 5k·fleet Gamma cache 배포 | 2026-08-19 |
| 029 | [029-fleet-scan-and-position-cap-remediation-2026-08-19.md](029-fleet-scan-and-position-cap-remediation-2026-08-19.md) | 전 Jenkins scan·position-cap 감사, broad-universe 축소, Orange stale open 및 Strawberry cadence 복구 | 2026-08-19 |
| 030 | [030-golden-pomegranate-health-and-strategy-discovery-2026-08-19.md](030-golden-pomegranate-health-and-strategy-discovery-2026-08-19.md) | Pomegranate 12일 archive 건강성, Data API·resolution 결함, holdout 기각과 prospective 스포츠 underdog 가설 | 2026-08-19 |
| 031 | [031-golden-pomegranate-sports-favorite-grid-2026-08-19.md](031-golden-pomegranate-sports-favorite-grid-2026-08-19.md) | Pomegranate 스포츠 endDate 6h 고확률 0.75~0.97 진입·목표가·resolution grid, anchor 기각과 0.94 사후 후보 | 2026-08-19 |
| 032 | [032-golden-black-evidence-and-design-2026-08-19.md](032-golden-black-evidence-and-design-2026-08-19.md) | 전 Jenkins·local archive 확장 검증, 0.94 비보장 판정, 0.92 대조군과 Golden Black prospective collector 설계 | 2026-08-19 |
| 033 | [033-fleet-watermelon-strawberry-raspberry-review-2026-08-24.md](033-fleet-watermelon-strawberry-raspberry-review-2026-08-24.md) | 29개 전략·연구 job 현황, Watermelon 축구 v3a, Strawberry follow-up, Raspberry cadence 재시작과 14일 gate | 2026-08-24 |
| 034 | [034-papaya-queen-melon-sustainability-review-2026-08-24.md](034-papaya-queen-melon-sustainability-review-2026-08-24.md) | Papaya·Queen·Melon 7개 live job의 confirmed-fill 지속가능성, lifecycle 결함과 수익 운영 중단 판정 | 2026-08-24 |
| 035 | [035-golden-quince-melon-final-postmortem-and-closure-2026-08-27.md](035-golden-quince-melon-final-postmortem-and-closure-2026-08-27.md) | Quince·Melon 6-arm 최종 회고, Quince partial-SELL 정산 복구, close-only·timer 제거·재동기화 검증 | 2026-08-27 |
| 036 | [036-watermelon-peach-ab-calibration-2026-09-02.md](036-watermelon-peach-ab-calibration-2026-09-02.md) | Watermelon·Peach live/White/Grey 근거 교정, 파라미터 유지 판정과 Grey simulation 전역 BUY 차단 복구 | 2026-09-02 |

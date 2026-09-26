---
name: sports-trade-report
description: Report Golden Burger sports bots' confirmed fills, fees, settlements and P&L by game for a requested period. Use for live trade reviews, including reviews that also compare simulation.
---

# 스포츠 실거래 회고

작업 시작 시각을 공통 종료 시각으로 고정하고 UTC 반개구간 `[start,end)`와 KST를 함께 표시한다. 회고의 기본 비교는 **최근 24시간·7일·30일과 장기 기간**이다. 장기는 최근 1년을 기준으로 하되 자료가 1년 미만이면 확보된 전체 운영 기간을 사용하고 실제 시작일·기간을 표시한다. 사용자가 특정 구간을 지정하면 그 구간을 본문으로 삼고 같은 종료 시각의 장기 비교를 함께 제공한다. 명시적으로 기간을 한정하거나 장기 비교를 생략하라고 하면 그 요청을 따른다. 실제 손익, 표시호가 시뮬레이션, 계좌 잔고 변화는 서로 다른 증거다.

## 자료 선택

- 요청한 전략·종목·Jenkins job의 모든 active·close-only child runtime과 기간 내 carry-in을 찾는다. 종목을 생략하면 축구·MLB와 실제 등록된 NFL/NBA/NHL profile을 확인한다. 여섯 job 이름이나 폴더명을 현재 배치로 고정하지 않는다. `docs/local/jenkins-job-strategy-inventory.md`는 routing 후보이며 최신 Jenkins config·DB resolved config가 우선한다.
- 장기 비교에서는 현재 runtime뿐 아니라 요청 전략의 retired·과거 금액/parameter epoch도 catalog에서 찾는다. 종목·팔별 최초 verified evidence, 유효 경기 수·독립 경기일·누락 기간을 표시한다. 없는 과거 자료를 0손익으로 채우거나 짧은 표본을 1년 수익으로 환산하지 않는다. 같은 범위가 되는 30일/전체 기간은 중복 표 대신 범위가 같음을 알린다.
- 기존 verified pin이 **요청 기간 전체와 필요한 source cohort**를 덮으면 재사용한다. 부족하면 evidence gap과 필요한 sync 범위를 먼저 제시하고, 사용자가 동기화를 요청했을 때만 해당 source를 `daily-rsync scan → plan → sync → verify → pin`한다. DB/log의 SHA, latest sync attempt/success, verify, source cutoff, mode, runtime을 기록한다. 외장 root가 없거나 UTC 당일 research shard가 아직 mutable이면 내부 fallback이나 완료된 archive로의 추정을 하지 않는다.
- 공식 일정·점수는 필요한 경기와 결과 범위를 확인하는 데 사용한다. 공식 승패만으로 Polymarket token payout이나 주문 fill을 만들어내지 않는다. 자료 gap·장애 구간은 `docs/retro/sports-source-quality-exclusions.json`과 run 로그로 분리한다.

## 실제 손익 계약

`docs/retro/EVIDENCE_CONTRACT.md`가 상위 계약이다. 실제 원금·매도손익은 `order_fills.status='CONFIRMED'`의 token·side·size·VWAP·fee로 다시 계산하고 exact token-aligned one-hot resolution 또는 확인된 redemption만 정산으로 인정한다. accepted/live, 요청 가격, `trades.realized_pnl`, 평가손익과 simulation은 확정 합계에서 제외한다. pending·QUARANTINED·fee/reconciliation/resolution gap은 0이 아니라 미확정으로 남긴다. 수동 wallet 거래와 입출금은 전략 손익에서 제외한다. 추가 edge case는 [원장·출력 참고](references/evidence-and-output.md)를 필요할 때 읽는다.

## 출력과 검증

맨 위에 **팔×종목별 합계, 전략별 합계, 전체 확인 손익**을 먼저 놓고 매도·정산, 원금·수수료, open 노출과 미확정 건수를 분리한다. 공식 경기 모집단·봇 발견 경기·실제 체결 경기를 따로 센다. 같은 경기를 여러 팔이 거래하면 실제 계좌 손익은 모두 합산하되 unique 경기 수는 한 번만 센다. 경기별로 공식 결과, 후보/미진입 이유, 선택 token, confirmed BUY·SELL/정산, 실제 손익과 carry-in/out을 설명한다. 자세한 열은 요청의 깊이에 맞춘다. 사용자가 simulation 비교도 요청했다면 동일 경기·금액·fee·호가 품질을 맞춰 **별도 표**로 제시하고 실제 합계에 섞지 않는다.

종합표에는 기본 비교 기간을 나란히 표시하고 **겹치는 기간을 더하지 않는다**. 증액·감액·parameter 판단은 최근 7일 합계뿐 아니라 장기 이력, **현행 설정 cohort**, 같은 경기·금액·fee의 시간순 재생을 함께 사용한다. 과거 모든 설정의 누적 손익과 현행 설정을 과거에 적용한 반사실은 구분한다. 서로 다른 `config_hash × strategy_source_digest × runtime × sport × target/selected notional`을 합쳐 평균 ROI나 승격 표본으로 쓰지 않는다. 단일 손실이 작은 이익 몇 건을 지우는지, drawdown·stop 비율·full-depth·FOK 및 대사 공백을 보고 종목별로 판정하며 A/B에서 한 번에 바꾸는 처치 변수는 하나로 유지한다. 자세한 장기 비교 표는 [원장·출력 참고](references/evidence-and-output.md)의 기간 규칙을 따른다.

`tools/sports_trade_report.py`와 `tools/sports_trade_report_prepare.py`는 read-only 분석 보조 도구다. 결과의 unknown·fee gap과 경기별/합계 Decimal 합산을 원자료와 대조한다. 보고서 JSON/Markdown은 local-only에 보존한다. 사용자가 같은 요청에서 버그 수정·배포·파라미터 조정을 허가했다면 이 스킬의 read-only helper 범위가 그 허가를 취소하지 않는다. 변경은 별도 증거·테스트·운영 검증을 거쳐 실행한다.

---
name: sports-trade-report
description: Report Golden Burger sports bots' confirmed fills, fees, settlements and P&L by game for a requested period. Use for live trade reviews, including reviews that also compare simulation.
---

# 스포츠 실거래 회고

기간 시작에 UTC 반개구간 `[start,end)`를 고정하고 KST를 함께 표시한다. 기간이 없으면 최근 24시간을 사용한다. 실제 손익, 표시호가 시뮬레이션, 계좌 잔고 변화는 서로 다른 증거다.

## 자료 선택

- 요청한 전략·종목·Jenkins job의 모든 active·close-only child runtime과 기간 내 carry-in을 찾는다. 종목을 생략하면 축구·MLB와 실제 등록된 NFL/NBA/NHL profile을 확인한다. 여섯 job 이름이나 폴더명을 현재 배치로 고정하지 않는다. `docs/local/jenkins-job-strategy-inventory.md`는 routing 후보이며 최신 Jenkins config·DB resolved config가 우선한다.
- 기존 verified pin이 **요청 기간 전체와 필요한 source cohort**를 덮으면 재사용한다. 부족하면 evidence gap과 필요한 sync 범위를 먼저 제시하고, 사용자가 동기화를 요청했을 때만 해당 source를 `daily-rsync scan → plan → sync → verify → pin`한다. DB/log의 SHA, latest sync attempt/success, verify, source cutoff, mode, runtime을 기록한다. 외장 root가 없거나 UTC 당일 research shard가 아직 mutable이면 내부 fallback이나 완료된 archive로의 추정을 하지 않는다.
- 공식 일정·점수는 필요한 경기와 결과 범위를 확인하는 데 사용한다. 공식 승패만으로 Polymarket token payout이나 주문 fill을 만들어내지 않는다. 자료 gap·장애 구간은 `docs/retro/sports-source-quality-exclusions.json`과 run 로그로 분리한다.

## 실제 손익 계약

`docs/retro/EVIDENCE_CONTRACT.md`가 상위 계약이다. 실제 원금·매도손익은 `order_fills.status='CONFIRMED'`의 token·side·size·VWAP·fee로 다시 계산하고 exact token-aligned one-hot resolution 또는 확인된 redemption만 정산으로 인정한다. accepted/live, 요청 가격, `trades.realized_pnl`, 평가손익과 simulation은 확정 합계에서 제외한다. pending·QUARANTINED·fee/reconciliation/resolution gap은 0이 아니라 미확정으로 남긴다. 수동 wallet 거래와 입출금은 전략 손익에서 제외한다. 추가 edge case는 [원장·출력 참고](references/evidence-and-output.md)를 필요할 때 읽는다.

## 출력과 검증

맨 위에 **팔×종목별 합계, 전략별 합계, 전체 확인 손익**을 먼저 놓고 매도·정산, 원금·수수료, open 노출과 미확정 건수를 분리한다. 공식 경기 모집단·봇 발견 경기·실제 체결 경기를 따로 센다. 같은 경기를 여러 팔이 거래하면 실제 계좌 손익은 모두 합산하되 unique 경기 수는 한 번만 센다. 경기별로 공식 결과, 후보/미진입 이유, 선택 token, confirmed BUY·SELL/정산, 실제 손익과 carry-in/out을 설명한다. 자세한 열은 요청의 깊이에 맞춘다. 사용자가 simulation 비교도 요청했다면 동일 경기·금액·fee·호가 품질을 맞춰 **별도 표**로 제시하고 실제 합계에 섞지 않는다.

`tools/sports_trade_report.py`와 `daily-rsync/tools/sports_trade_report_prepare.py`는 read-only 분석 보조 도구다. 결과의 unknown·fee gap과 경기별/합계 Decimal 합산을 원자료와 대조한다. 보고서 JSON/Markdown은 local-only에 보존한다. 사용자가 같은 요청에서 버그 수정·배포·파라미터 조정을 허가했다면 이 스킬의 read-only helper 범위가 그 허가를 취소하지 않는다. 변경은 별도 증거·테스트·운영 검증을 거쳐 실행한다.

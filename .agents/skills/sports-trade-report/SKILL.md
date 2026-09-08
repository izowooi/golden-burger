---
name: sports-trade-report
description: "Golden Burger 스포츠 봇의 경기 결과와 실제 주문 체결·수수료·정산·손익을 경기별로 보고한다. 스포츠 전략 맥락에서 '최근 24시간 경기 결과 확인해서 보고', '어느 경기에서 어떤 전략이 얼마를 벌고 잃었는지', '제대로 체결됐는지' 요청에 사용한다. 단순 스포츠 뉴스, 시뮬레이션 수익을 실제 체결로 보여 달라는 요청, 실거래 활성화에는 사용하지 않는다."
---

# 스포츠 실거래 보고

사용자가 지정한 기간과 경기 범위에서 **실제 체결과 미확정 상태를 경기별로 설명**한다. 기본 기간은 작업 시작 때 고정한 UTC `[now−24h, now)`이며, 본문에는 UTC·KST를 함께 적는다. 이후 작업 시간이 지나도 기간을 바꾸지 않는다.

## 작업 위치와 범위

현재 workspace의 git root를 확인한다. Golden Burger가 아니라면 workspace `REPOS.md`의 해당 저장소를 찾는다. repo remote가 `github.com/izowooi/golden-burger`인지 확인하고, 아래 명령은 그 repo에서 실행한다. 설치된 skill 사본의 위치를 DB 위치로 가정하지 않는다.

먼저 `docs/sports-match-review-policy.md`, `docs/sports-strategy-review-defaults.md`, `docs/retro/EVIDENCE_CONTRACT.md`와 존재하는 `docs/local/jenkins-job-strategy-inventory.md`를 읽는다. 실제 최신 config/run/DB가 문서의 예시 job 매핑보다 우선한다.

- 종목을 생략하면 현재 등록된 축구·MLB를 기본으로, 실제 운영 중인 NFL/NBA/NHL profile도 확인한다. 새 리그·종목을 추측 편입하지 않는다.
- 사용자 경기 목록은 빠짐없이 남긴다. 공식 경기 모집단, 봇이 발견한 경기, 체결 경기 수를 구별한다.
- 현재 job을 **이름 6개로 고정하지 않는다.** active/close-only/mixed-universe live, config-only, research-only, retired를 구별한다. 현재 sports live job과 기간 내 체결·carry-in 노출이 있는 관련 epoch를 함께 찾는다. simulation/research는 실제 손익에 합치지 않는다.

## 발견·고정 사본

Jenkins 조회에는 설치된 `inspect-jenkins-job` 또는 repo의 동일 redacting reader를 사용한다. raw config.xml/console 및 credential 값을 출력하지 않는다. Job·strategy·runtime 이름은 별개이며 config-only 성공은 실거래 실행이 아니다.

`daily-rsync/README.md`, `DATA_LAYOUT.md`, `OPERATIONS.md`의 routing을 따른다. 새 자료가 필요한 각 job×strategy마다 standard scan→plan→sync→verify→pin을 사용한다. 실행 중 DB를 임의 SSH/cp로 가져오거나 snapshot을 merge하지 않는다.

```bash
uv run --project daily-rsync python tools/sports_trade_report_prepare.py discover --output <local-report-dir>/discovery.json
uv run --project daily-rsync python tools/sports_trade_report_prepare.py plan --discovery <local-report-dir>/discovery.json --output <local-report-dir>/plans.json
uv run --project daily-rsync python tools/sports_trade_report_prepare.py sync --plans <local-report-dir>/plans.json --output <local-report-dir>/inputs.json
```

discovery의 모든 candidate/제외 사유를 검토한다. 불명확한 실행 모드는 config와 최신 structured run/DB metadata로 해소하며 조용히 제외하지 않는다. plan의 파일·runtime·mode·전송량과 local/remote free space를 확인한 뒤 sync한다. 필요한 범위만 전송하고 console은 기본 제외한다. 인증 실패나 거절은 우회하지 않고 evidence gap으로 기록한다.

기존 verified pin이 기간과 필요한 source cohort를 충분히 덮으면 재사용할 수 있다. 최신 config와 과거 진입 시 config를 구별하고, 사용한 pin SHA·source cutoff·검증 결과를 보고서에 보존한다. 원본 DB/log·개인 계정 정보는 local-only다.

## 공식 경기 결과와 실제 원장

공식 리그/구단/공식 schedule 또는 game-center를 검색·열어 경기 날짜·팀·최종 score·상태를 확인하고 직접 URL과 확인 시각을 기록한다. 종료하지 않은 경기·취소·연기·연장/정규시간 범위를 구분한다. 접근 불가나 날짜 불일치는 미확인이다. 사용자 메모를 DB에 덮어쓰지 않는다.

공식 score, exact condition/token의 시장 정산, 실제 SELL, 지급권, cash redemption은 별개다. 공식 승리만으로 Polymarket payout이나 실제 체결을 만들지 않는다.

원장 해석과 출력 필드는 [references/evidence-and-output.md](references/evidence-and-output.md)를 따른다. helper는 read-only 분석이며 주문·취소·재시작·자동 파라미터 변경을 수행하지 않는다.

```bash
python3 tools/sports_trade_report.py --inputs <local-report-dir>/inputs.json \
  --start <UTC-start> --end <UTC-end-exclusive> \
  --official-results <local-report-dir>/official-results.json \
  --output <local-report-dir>/report
```

helper의 unknown/ambiguous/fee-gap 결과는 원자료에서 원인을 확인한다. 추정 0이나 요청 가격으로 고치지 않는다. 스키마가 다르면 관련 source를 미지원 evidence gap으로 남기고 읽기 adapter를 보완한다. 보고서 작성 자체를 수익성·승격 gate 때문에 중단하지 말고, 확인 가능한 사실과 미확정 범위를 완성한다.

## 보고와 검증

축구 → MLB → NFL → NBA/NHL 순으로 **모든 경기 행을 먼저** 나열한다. 각 경기 안에서 strategy/Jenkins/runtime/팔·선택 팀 또는 YES/NO·BUY/SELL UTC/KST·confirmed 수량/VWAP/원금·exit/stop/resolution·순손익 또는 unknown 사유를 적는다. 진입 당시 policy와 원장 청산 사유를 체결 행과 함께 표시해 왜 사고팔았는지 설명하며, 기록이 없으면 미확인으로 남긴다. 전략 요약과 합계는 그 뒤다. 최신 API가 응답했다는 이유만으로 체결·수익을 정상이라고 쓰지 않는다.

기간 내 entry와 carry-in의 기간 내 exit, carry-out을 분리한다. 매도된 부분의 손익과 잔여 지급권 가치/현금 상환을 따로 표시한다. 미체결 경기·발견되지 않은 경기·제외 조건·실행 공백을 누락하지 않는다. 같은 경기의 여러 전략/계좌/결과를 독립 경기 수로 합산하지 않는다.

최종 확인: 모든 source가 pin·mode·cohort와 연결됐는지, confirmed fill/fee 합계와 잔여 수량이 맞는지, 경기별 합계와 전략별 합계가 일치하는지, 공식 결과의 날짜가 맞는지 검사한다. 보고서는 JSON과 Markdown으로 local-only 보존한다. 별도 요청 없는 Slack/메일 전송, 실거래 활성화, 주문 변경을 하지 않는다.

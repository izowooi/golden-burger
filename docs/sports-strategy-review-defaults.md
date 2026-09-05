# 스포츠 전략 검증의 기본 범위

2026-09-05 사용자 요청을 따른다. 현재 배치와 실제 설정은 이 문서의 예시보다
Jenkins 설정 및 DB resolved config가 우선한다.

## 종목을 생략했을 때

- `전략들이 잘 도는지`, `스포츠 전략 검증`처럼 종목을 생략하면 Watermelon·Peach·Plum의
  현재 등록된 스포츠를 함께 점검한다.
- 실거래의 기본 범위는 축구와 MLB다. 축구는 기존 허용 대회
  EPL·Bundesliga·Ligue 1·LaLiga·Serie A·MLS·UCL·UEL이며, UEL/리그 이름은
  축구에 속한 대회로 해석한다. 야구는 MLB 정규시즌·포스트시즌·월드시리즈 개별 경기다.
- 수집/시뮬레이션은 축구·MLB·NBA·NFL·NHL이다. 해당 종목의 메이저 대회 개별 경기도
  정확한 리그·팀·시장 신원을 만족하면 포함한다.
- 특정 종목이나 경기 목록을 주면 그 목록을 주 검증 대상으로 삼는다. 이번처럼 MLB 목록이면
  MLB 체결표를 작성하되 세 전략의 종목별 구성 및 수집기 역할도 확인한다.
- 국가대표전·새 리그·KBO/NPB·e-sports 등을 조용히 추가하지 않는다. 새로운 종목 편입과
  종료된 실험 재개는 기존 등록 범위 조회와 별도다.

## 현재 이름 대응

| 사용자 이름 | 실거래 전략/잡 | 시뮬레이션/수집 |
|---|---|---|
| Watermelon | golden-watermelon-live: Cat/Dog 축구, Bear/Tiger MLB | golden-watermelon / White: 다섯 종목 |
| Peach | golden-peach: Eco/Fruit의 축구·MLB 독립 runtime | Grey: 다섯 종목 독립 DB |
| Plum, golden-plum | golden-plum: King/Queen의 축구·MLB 독립 runtime | Silver 축구 + Gold MLB/NBA/NFL/NHL |

기존 Watermelon NHL Lion/Wolf도 별도 등록돼 있으므로 현재 설정을 확인해 따로 표기한다.
이 문서가 그 잡의 신규 진입을 재개하거나 중지하는 지시는 아니다. 계정·Jenkins·전략·runtime
이름을 동일시하지 않으며, 명시적 지시 없이 지갑이나 Jenkins job을 합치지 않는다.

## 매번 확인할 경계

1. 먼저 정확한 UTC `[시작, 종료)`와 source/sync 시각을 고정한다. 목록 밖 carry-in 거래는 분리한다.
2. 용량·mount 확인 후 job별 daily-rsync scan/plan/sync/verify로 읽기 전용 사본을 확보한다.
3. 공식 경기 결과, 정확한 condition/token의 정산 결과, 실제 CONFIRMED BUY/SELL·수수료를
   각각 대조한다. 사용자 메모의 점수를 DB에 덮어쓰지 않는다.
4. 경기 시점의 설정과 현재 설정을 구분한다. 새 파라미터 적용 전에 끝난 거래로 새 A/B를
   평가하지 않는다. 최근 빌드 SUCCESS와 수익성도 같은 뜻이 아니다.
5. 종목별 독립 DB 또는 sport/league/profile/tag를 확인하고 같은 경기의 여러 계좌·가상 조합을
   독립 경기로 중복 집계하지 않는다.
6. max-position·주문 결과불명·손실 방어·실험 종료일·미체결 깊이·실행 공백을 서로 구분한다.
7. 월드시리즈 **개별 경기**는 포함하지만 **시리즈 전체 우승팀 예측**, inning/prop/season-long
   시장은 포함하지 않는다. future 플래그가 빠져도 명시적인 series-winner 문구는 거절한다.
8. 종목별 수치가 독립적으로 적용되는지 확인한다. 일부 값이 같다는 이유만으로 임의 차이를
   만들지 않고, 종목별 자료와 사용자 승인에 따라 버전별 설정을 바꾼다.
9. 누락된 최종 결과·호가·수수료를 추정으로 채우지 않는다. 가상 진입이 없는 경기를 최종까지
   추적하지 않는 수집기도 있으므로 원호가 수집률과 종료 결과 수집률을 나눠 보고한다.

최신 local-only routing과 운영 상태는 `docs/local/jenkins-job-strategy-inventory.md`를 따른다.
작업별 요청 원문과 결과는 순번이 있는 `task-summaries/YYYY/MM/`에 한 파일씩 보존한다.

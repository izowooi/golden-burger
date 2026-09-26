# 스포츠 종목별 운영 금액과 다음 증액 단계

2026-09-26 사용자가 장기 회고와 증액 권고를 수락한 운영 결정이다. 실제 실행값은
각 runtime의 DB resolved config로 대사하며 이 문서를 과거 거래에 소급하지 않는다.

| 전략 / 종목 | Jenkins 팔 | 현재 목표액 | 후속 단계 |
|---|---|---|---|
| Watermelon Soccer | Cat / Dog | 각각 $5 | 최소 단위 유지, 장기 재검증 |
| Watermelon NFL | Cat / Dog | 각각 $5 | 수수료 수정 후 신규 FOK 체결·독립 표본 검증 |
| Apricot MLB | Eco / Fruit | 각각 $15, full-depth 불가 시 $10→$5 | 현재 규모 검증, 추가 증액 보류 |
| Plum Soccer | King / Queen | 각각 $5 | 조건을 충족하면 양팔 함께 $10 검토 |
| Plum NFL | King / Queen | 각각 $5 | Soccer 증액을 전파하지 않고 별도 검증 |
| Cat/Dog·King/Queen MLB child | 기존 runtime | close-only $5 | 신규 BUY 금지, 과거 노출 대사 |

Apricot의 신호 판단은 baseline exact $5, Tick90 `[90,92]`, floor `.90`을 유지한다.
청산은 full-holding bid `.90` 이상이면서 수수료 포함 순이익일 때이며 미도달하면 exact
resolution을 사용한다. 선택된 $15/$10/$5 주문 각각은 FOK이며 임의 중간 목표액이나
partial BUY를 추가하지 않는다.

Watermelon Soccer는 Cat `.91`/Dog `.92`, effective stop `max(.65, entry-.30)`이며
NFL은 Cat `.91`/Dog `.94`, `max(.70, entry-.30)`이다. 별도 TP는 없다.
Plum Soccer는 entry `.70-.73`, source minute `<60`, King TP `.85`/Queen `.90`,
SL `entry-.12`, minute65 exit를 유지한다. NFL에는 source-time/time exit를 적용하지 않는다.

## Plum Soccer $10 검토 조건

- 해당 프로젝트의 최소 검증 기준인 팔별 confirmed 종결 50건, common event 30건,
  evidence gap 0을 cohort별로 점검한다. 다른 목표액·설정·source의 건수를 하나의
  성숙한 실험으로 합치지 않는다.
- King의 AS Roma–Inter Draw NO 과거 SELL 격리는 confirmed fill 또는 exact zero-fill/
  resolution 증거로 대사한다. 원래 signed intent나 venue proof 없이 성공·0체결로
  바꾸거나 DB에서 삭제하지 않는다. 기존 MLB 격리도 별도 노출로 계속 표시한다.
- 현행 entry/TP/SL/time exit를 같은 경기·$10 full-depth·시장별 fee로 시간순 재생한다.
  전체 합계뿐 아니라 독립 시간 구간과 최근 구간, 최대 손실·drawdown·stop 비율,
  실제 exit VWAP gap을 검증한다. 실패 run·VPN/API/storage gap을 보간하지 않는다.
- full-holding SELL 가능액, FOK confirmed 성공률, cadence와 account cash/capacity를
  확인한다. 단순한 표시호가 양수나 최근 7일 이익만으로 증액하지 않는다.
- 증액 시 양팔 모두 같은 Soccer 목표액으로 바꾸고 기존 TP A/B 축만 유지한다.
  NFL/MLB child 금액과 기존 BUY 당시 exit 계약에는 전파하지 않는다.

직전 verified 보고서 UTC cutoff `2026-09-26T03:59:57Z` 기준 현행 숫자 이력의 확인 종결은 King 45건·Queen 44건이며 46 unique 경기·7 UTC
경기일이다. 과거 $5/$10 구간은 각각 분리된 자료이고 King Soccer 격리 1건이 남아 있다.
따라서 이번 적용은 **Plum Soccer $5 유지**이며 $10 승격 완료를 의미하지 않는다.

## 회고와 계속 운영

최근 24시간·7일·30일과 최근 1년을 함께 비교한다. 1년치가 없으면 확보된 실제 전체
운영 기간과 시작일·누락을 표시한다. 장기 실제 이력, 현행 설정의 forward 성과,
동일 금액의 과거 simulation은 서로 구분하고 겹치는 기간을 더하지 않는다.

최소 표본이나 독립 경기일은 다음 증액을 판단하는 기준이다. timer를 며칠 후 자동으로
끄거나 예전 parameter로 되돌리는 기한이 아니다. 기존 1분 live는 계속 운영한다.

관련 계약: [Evidence Contract](EVIDENCE_CONTRACT.md),
[A/B 회고 절차](../ab-retro-playbook.md),
[Plum 프로젝트 지침](../../golden-plum/AGENTS.md).

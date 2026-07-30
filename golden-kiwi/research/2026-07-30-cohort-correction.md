# Golden Kiwi frozen 연구 증거 정정

기준일: 2026-07-30
대상: [`frozen-2026-07-30/`](frozen-2026-07-30/)
판정: **과거 수치는 탐색적 기록으로만 보존하며 promotion evidence로 사용하지 않는다.**

## 무엇을 발견했나

고정 분석을 독립 재검토한 결과 두 가지 계약 위반을 확인했다.

### 1. Arm C의 양수 신호가 서로 다른 Git commit을 이었다

과거 분석기의 `arm_qualifies()`는 한 condition의 연속 snapshot을 시간순으로 골랐지만,
각 snapshot의 `config_hash × git_commit × mode × job_name`이 모두 같은지 확인하지
않았다. 그 결과 strict event-purged OOS의 유일한 Arm C 신호는 다음 두 code cohort를
하나의 5-step 계단으로 연결했다.

```text
이전 관측 Git commit: 4c69c902e5da865ecec4d32d06decd437ce681a4
마지막 관측 Git commit: 9b648b38ca87aea98663c7ee3b0bd0275c4369f8
마지막 관측 시각:      2026-07-28T13:02:36.389010Z
보고된 return:         +0.5263%
```

따라서 C의 `+0.5263%`는 동일 collection cohort의 재현 가능한 신호가 아니며
**철회된 promotion 수치**다.

### 2. 원본 Honeydew DB로는 point-in-time 표준 이진 계약을 증명할 수 없다

과거 DB의 `market_snapshots`에는 관측 당시의 outcomes, outcome prices, token IDs,
tags, `negRisk`, end date가 행 단위로 고정돼 있지 않았다. 분석기는 나중에 갱신될 수
있는 `market_catalog` 한 행을 join했다. 따라서 다음을 entry 시점 기준으로 증명할 수
없다.

- outcomes가 정확히 `Yes/No` 두 개인지
- 서로 다른 token이 정확히 두 개인지
- `negRisk=false`가 명시됐는지
- 제외 tag와 end date가 entry 이후 정보로 바뀌지 않았는지

이 결함은 C 한 건만이 아니라 frozen A/B/C/D 전체에 적용된다. 과거 표의 signal 수와
return은 당시 코드를 재현하는 탐색적 산출물이지만, 현재
[`EVIDENCE_CONTRACT.md`](../../docs/retro/EVIDENCE_CONTRACT.md)가 요구하는 승격
증거는 아니다.

## 무엇이 바뀌지 않나

immutable frozen 디렉터리의 preregistration, script, CSV, JSON, 보고서와 manifest는
수정하지 않는다. 과거에 어떤 분석을 했는지 숨기지 않기 위해 그대로 보존한다.

원래 결론도 바뀌지 않는다.

- primary B는 낙관적인 과거 계산에서도 50 signals / 30 events를 못 채웠다.
- B의 clustered CI는 추정 불가였고 early/late 안정성도 실패했다.
- A/B의 일반 OOS 평균은 음수였다.
- 따라서 당시에도, 정정 후에도 판정은 `FAIL_NO_LIVE_RECOMMENDATION`이다.

정정으로 바뀌는 것은 해석의 강도다. “C에 단일 event 양수가 있었다”가 아니라
“그 C 수치는 cohort 계약 위반이며, 과거 DB 전체도 point-in-time strict-binary
promotion evidence가 부족하다”가 정확한 표현이다.

## 새 수집 코드의 교정

새 Golden Kiwi runtime은 다음을 행 단위로 보존하거나 fail closed한다.

1. 현재 run의 마지막 snapshot과 같은
   `config_hash × git_commit × mode × job_name`인 SUCCESS run history만 사용한다.
2. cursor-complete sweep에 연결된 snapshot만 lineage로 인정한다.
3. outcomes, outcome prices, token IDs, tags, `negRisk`, end date를 각 snapshot에
   point-in-time 사본으로 저장한다.
4. Gamma 전체 sweep 종료시각이 아니라 **각 keyset page를 받은 로컬 시각**을 snapshot
   관측시각으로 쓴다.
5. 실행 직전에는 한 번의 CLOB order-book snapshot에서 midpoint, bid, ask, spread와
   depth를 함께 계산하고 마지막 step/gap을 다시 평가한다.
6. 실패한 run에서 생긴 entry/exit evidence는 격리하거나 이전 상태로 되돌린다.
7. 60~75분 안에 관측한 exit만 promotion eligible로 표시한다.

## 다음 30일 판정에 미치는 영향

새 A/B/C/D DB만 사용한다. frozen 수치를 새 표본에 합치지 않는다. 모든 lineage row가
동일 cohort이고 snapshot-level catalog 증거가 완전한지 분석기가 검증하지 못하면
성과가 양수여도 `NOT_EVALUABLE`이다.

이 정정문은 frozen 파일을 대체하지 않는다. frozen 결과를 읽을 때 함께 적용해야 하는
상위 해석 계약이다.

# Golden Kiwi Micro-Cascade — 4개 실험군 30일 시뮬레이션 실행 가이드

> **역사 문서:** 2026-08-06 수집은 cadence-invalid로 종료됐다. 현재 실행에는
> [`GOLDEN_KIWI_FILTERED_4_ARM_30_DAY_RUNBOOK_2026-08-13.md`](GOLDEN_KIWI_FILTERED_4_ARM_30_DAY_RUNBOOK_2026-08-13.md)를
> 사용한다. 이 문서의 범용 window/수집 권고로 새 job을 시작하지 않는다.

- 작성일: **2026-07-31**
- 기준 시간대: Asia/Seoul
- 전략 폴더: `golden-kiwi/`
- 전략 상태: **research/simulation 전용**
- 사용 시점: 폐쇄 전략 정리가 끝난 뒤 Golden Kiwi의 독립 30일 검정을 시작할 때
- 최종 판정 대상: **Arm B 하나**

이 문서는 대화 기록이 사라져도 Golden Kiwi가 어떤 전략이고, 왜 만들었으며, 어떻게
검증해야 하는지 한 파일에서 다시 확인하기 위한 실행 문서다.

## 1. 먼저 기억할 결론

Golden Kiwi는 실제 Polymarket 계좌 네 개로 주문을 내는 전략이 아니다. 현재 버전은
실거래를 source-level hard block으로 막은 **4개 독립 Jenkins job/SQLite 시뮬레이션
실험**이다.

따라서 다음은 필요하지 않다.

- Polymarket private key
- funder address
- signature type
- 실제 USDC 입금
- 실험군별 Polymarket 계좌

폐쇄 중인 계좌가 정리되는 것은 전체 운영상 좋은 체크포인트지만, Golden Kiwi
시뮬레이션 시작의 기술적 선행 조건은 아니다. 필요한 것은 같은 strategy source digest를 실행하는
Jenkins job 네 개와 충분한 실행시간·디스크다.

## 2. 어떤 전략인가

Lime·Fig·Mango·Date·Honeydew·Nectarine 여섯 폐쇄 전략의 DB와 로그를 검토한
[통합 포스트모템](../docs/retro/closed-strategies-postmortem.md)에서 출발했다.
폐쇄 전략의 threshold를 합친 전략이 아니라 다음 실패를 반복하지 않게 만든 짧은
추세 가설이다.

- Date처럼 작은 음의 edge를 높은 회전율로 증폭하지 않는다.
- Nectarine처럼 한 event나 임의 API 순서에 결과를 의존시키지 않는다.
- Honeydew처럼 quote replay를 실제 체결 수익이라고 부르지 않는다.
- Fig처럼 실패한 tail 가격대를 다시 세분화해 승자를 찾지 않는다.
- Mango처럼 실제 funnel을 움직이지 않는 복합 hurdle을 만들지 않는다.
- Lime처럼 한 번의 큰 shock가 오래 지속된다고 가정하지 않는다.

검정할 가설은 하나다.

> YES 가격이 작게 3회 또는 5회 연속 상승하면, 그 정보 반영이 60분 뒤 실행 가능한
> 가격에도 남아 있는가?

## 3. 고정 진입 조건

다음 조건을 모두 만족해야 한다.

- outcomes가 정확히 `Yes`, `No`인 표준 이진 시장
- `negRisk=false`
- YES 가격 20% 이상 80% 이하
- 해결까지 최소 6시간
- 최대 해결시간 상한은 없음
- 유동성 최소 **$20,000**
- 최근 24시간 거래량 최소 **$10,000**
- bid/ask spread 최대 **2%p**
- 관측 간격 3분 이상 10분 이하
- 각 YES 상승폭은 0%p 초과 2%p 이하
- 누적 상승폭은 Arm별 1%p 또는 2%p 이상, 공통 최대 4%p
- 같은 event에서는 유동성이 가장 큰 condition 하나만 선택
- 같은 event의 재진입 cooldown 6시간
- 누락되거나 비정상인 tag metadata는 fail-closed

고정 제외 tag는 다음과 같다.

```text
sports
games
esports
crypto-prices
up-or-down
multi-strikes
5m
15m
1h
```

스포츠 제외는 스포츠가 수익성이 없다는 결론이 아니다. 서로 다른 시장 시계를 한
연구 표본에 섞지 않기 위한 universe 동질성 선택이다.

### 고정 가상 청산과 Primary 평가

이 실험에는 익절, 손절, trailing stop이 없다. 각 가상 position은 진입 시각으로부터
60분이 지난 뒤 처음 성공한 bot cycle의 fresh best bid로 가상 청산한다. 허용하는
청산 지연은 최대 15분이므로, 정상 평가 구간은 진입 후 **+60분부터 +75분까지**다.

승격 판정에 쓰는 Primary outcome은 runtime position의 상태가 아니라 다음 두 quote를
비교한 수익률이다.

```text
진입 가격 = raw_selected 시점에 append-only로 저장한 fresh best ask
청산 가격 = +60~75분 사이 direct Gamma 조회에서 처음 확인한 valid best bid
Primary return = (청산 bid - 진입 ask) / 진입 ask
```

이렇게 ask로 사고 bid로 파는 왕복 기준을 사용해 spread를 성과에 포함한다. +60~75분
사이에 유효한 bid를 얻지 못한 signal은 midpoint, 마지막 가격, 0 또는 1로 임의 보정하지
않고 **censored**로 기록한다. censored signal은 수익률 표본에서 제외하되 quote coverage
분모에는 남기며, coverage가 90% 미만이면 결과가 좋아 보여도 실험은 통과하지 못한다.
75분 뒤에야 bot이 복구된 경우 실제 지연과 진단용 가상 청산 기록은 보존하지만, frozen
Primary 60~75분 outcome을 사후에 대체하지 않는다.

## 4. 4개 실험군

네 개가 맞다. 다만 무처치 대조군을 둔 전통적인 A/B/C/D 실험은 아니다. B만
confirmatory primary이고 A/C/D는 희귀성과 threshold 민감도를 확인하는 비교군이다.

| Arm | 연속 상승 | 누적 상승 하한 | Jenkins job | 5분 offset | 역할 |
|---|---:|---:|---|---:|---|
| A | 3회 | +1%p | `kiwi-sim-a-3x1` | 0 | 낮은 threshold 민감도·반증 |
| **B** | **3회** | **+2%p** | `kiwi-sim-b-3x2` | 1 | **유일한 Primary** |
| C | 5회 | +1%p | `kiwi-sim-c-5x1` | 2 | 긴 확인 민감도 |
| D | 5회 | +2%p | `kiwi-sim-d-5x2` | 3 | 결합 strict 민감도 |

정확히 5분마다 관측되면 명목상 A/B는 15분, C/D는 25분 추세다. 실제 허용 gap을
적용하면 A/B의 span은 9~30분, C/D는 15~50분이다.

Primary B가 실패하면 A/C/D 중 결과가 가장 좋아 보이는 Arm으로 교체하지 않는다.
A/C/D의 양수만으로 실거래 승격을 주장하지 않는다.

## 5. 권장 테스트 기간

**정확히 30일**을 권장하며 현재 실험 계약도 30일로 고정돼 있다.

- 네 Arm은 같은 UTC `[start, end)`를 사용한다.
- `end`는 `start`의 정확히 30일 뒤여야 한다.
- 첫 유효 run 전에 날짜를 확정한다.
- 결과를 본 뒤 기간을 연장하거나 threshold를 완화하지 않는다.
- 30일 뒤 표본이 부족하면 `STOP / UNRESEARCHABLE`로 끝낸다.

5분 cadence의 이론상 기대 slot은 Arm당 다음과 같다.

```text
30일 × 24시간 × 시간당 12회 = 8,640 slots
필수 cadence coverage 90% = 최소 7,776 SUCCESS slots/Arm
4개 Arm 전체 이론상 run 수 = 34,560
```

Jenkins Mac mini가 이 주기를 감당하는지 확인하기 위해 본 실험 전에 **폐기 가능한 별도
checkout/workspace**에서 24시간 부하 점검을 권장한다. Golden Kiwi는 임의 job 이름을
거부하므로 smoke에서도 아래 네 canonical job 이름을 그대로 사용한다. 대신 smoke
workspace의 `data/`는 본 실험 workspace와 절대로 공유하지 않는다.

smoke run에는 `POLYBOT_EXPERIMENT_START_UTC`, `POLYBOT_EXPERIMENT_END_UTC`,
`POLYBOT_CADENCE_OFFSET_MINUTE`를 설정하지 않는다. `polybot config`에
`Promotion collection: DISABLED (smoke/archive evidence only)`가 표시돼야 한다. 점검이
끝나면 smoke workspace를 보존용으로 별도 복사하거나 폐기하고, 실제 30일 실험은 기록이
전혀 없는 production workspace의 canonical job/DB에서 시작한다. 기존 production
workspace의 DB를 smoke 용도로 재사용하거나 수동 삭제해서는 안 된다.

다음 중 하나라도 발생하면 30일 window를 시작하지 않는다.

- 한 cycle의 p95 실행시간이 5분을 초과
- 같은 job의 실행이 겹침
- snapshot gap이 지속적으로 3~10분을 벗어남
- 네 job의 strategy source digest가 서로 다름 (Git commit 차이는 허용)
- 디스크와 backup 공간이 부족

## 6. 권장 금액과 시장 거래량

### 가상 주문 금액

현재는 **$5/진입**을 유지한다. `$100`, `$200`, `$400`으로 올리지 않는다.

| 항목 | Arm당 값 | 네 Arm 합계 |
|---|---:|---:|
| 가상 주문 1건 | $5 | 각 Arm 독립 |
| 최대 동시 position | 3개 | 12개 |
| 최대 open notional | $15 | $60 |
| 연구 자본 장부 | $100 | $400 |
| drawdown 영구 중단 | -$20 | Arm별 독립 |
| 실제 투입 USDC | $0 | $0 |

`POLYBOT_BUY_AMOUNT`나 wallet credential을 환경변수로 추가하지 않는다. `polybot config`
출력의 `Order: $5.00`, `Simulation only: True`, `Live execution: HARD DISABLED`를
확인한다.

### 시장 유동성과 24시간 거래량

현재 고정값을 그대로 사용한다.

```text
최소 유동성             $20,000
최소 최근 24시간 거래량 $10,000
최대 spread             2%p
fresh ask depth         $5 주문 수량의 최소 1.2배
```

이 값들은 큰 주문을 안전하게 체결하기 위한 live scale-up 기준이 아니다. 먼저 $5
quote-level 신호의 기본 부호를 검증하기 위한 연구 gate다. 30일 결과가 통과하더라도
다음 단계는 $5 shadow execution review이며 바로 실거래 금액을 늘리지 않는다.

## 7. 저장공간 예산

네 Arm이 각각 archive DB와 로그를 보유하므로 같은 시장 데이터가 네 번 저장될 수 있다.
고정 GB 값을 추정하지 말고 24시간 smoke 결과로 계산한다.

```text
필요한 최소 여유 공간
  = 네 smoke job의 24시간 DB + WAL + 로그 증가량 합계 × 45
```

`×45`는 30일 수집량에 약 50%의 backup·WAL·운영 여유를 더한 값이다. 본 실험 전에
SQLite online backup과 SHA-256 manifest를 workspace 밖 저장소로 복제할 위치도
확정한다.

## 8. Jenkins 설정

각 job에서 **Execute concurrent builds if necessary**를 끈다. Build Trigger는 숨은
`H/5`가 아니라 아래 값을 그대로 사용한다.

```text
A: 0-59/5 * * * *
B: 1-59/5 * * * *
C: 2-59/5 * * * *
D: 3-59/5 * * * *
```

네 job이 공유하는 값:

```bash
export LOG_LEVEL=INFO
export POLYBOT_EXPERIMENT_START_UTC=<확정한_UTC_START>
export POLYBOT_EXPERIMENT_END_UTC=<START의_정확히_30일_뒤>
```

Arm별로 달라지는 값:

```text
A: POLYBOT_CONFIRMATION_STEPS=3
   POLYBOT_MIN_CUMULATIVE_MOVE=0.01
   POLYBOT_CADENCE_OFFSET_MINUTE=0
   --job kiwi-sim-a-3x1

B: POLYBOT_CONFIRMATION_STEPS=3
   POLYBOT_MIN_CUMULATIVE_MOVE=0.02
   POLYBOT_CADENCE_OFFSET_MINUTE=1
   --job kiwi-sim-b-3x2

C: POLYBOT_CONFIRMATION_STEPS=5
   POLYBOT_MIN_CUMULATIVE_MOVE=0.01
   POLYBOT_CADENCE_OFFSET_MINUTE=2
   --job kiwi-sim-c-5x1

D: POLYBOT_CONFIRMATION_STEPS=5
   POLYBOT_MIN_CUMULATIVE_MOVE=0.02
   POLYBOT_CADENCE_OFFSET_MINUTE=3
   --job kiwi-sim-d-5x2
```

공통 실행 형태:

```bash
#!/bin/bash
set -euo pipefail

# 위 표에서 Arm별 환경변수와 공통 30일 window를 먼저 설정한다.

cd ./golden-kiwi
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job <CANONICAL_JOB>
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job <CANONICAL_JOB>
```

trigger를 활성화하기 전에 `polybot config`에서 다음을 확인한다.

- 정확한 Arm
- 정확한 canonical job
- 절대 DB 경로
- 정확한 offset
- 네 Arm이 공유하는 30일 UTC window
- `Promotion collection: ENABLED`
- `Simulation only: True`
- `Live execution: HARD DISABLED`

완성된 Arm별 shell은 [README의 Jenkins 설정](README.md#jenkins-설정)을 사용한다.

## 9. 30일 뒤 판정

Primary B가 다음을 모두 만족해야 shadow execution review만 열 수 있다.

1. quote-complete mature raw signal 50개 이상
2. unique event cluster 30개 이상
3. event-cluster Bonferroni 98.75% lower confidence bound가 0보다 큼
4. 10.4bps 비용 stress 뒤 lower bound도 0보다 큼
5. 사전 정의한 전반 15일과 후반 15일이 모두 양수
6. +60~75분 target/quote coverage 90% 이상
7. cadence coverage 90% 이상
8. Arm별 단일 `config_hash × strategy_source_digest × mode × job_name` cohort
9. 네 Arm이 같은 strategy source digest 사용. Git commit은 provenance로만 기록
10. strict audit의 CRITICAL/HIGH가 0

통과해도 실거래 승인이 아니다. 다음 단계는 depth, queue, latency, partial fill,
maker/taker fee와 exact confirmed fill을 측정하는 별도 $5 shadow review다.

## 10. 이메일을 받은 뒤 실행 전 체크리스트

- [ ] 이 문서의 작성일과 `polybot config`의 Strategy source cohort를 확인했다.
- [ ] 기존 폐쇄 전략의 DB·로그·manifest를 보존했다.
- [ ] Golden Kiwi에 private key와 funder address를 넣지 않았다.
- [ ] 폐기 가능한 별도 workspace에서 canonical job 네 개로 24시간
      p95·cadence·디스크 증가량을 확인했다.
- [ ] canonical Jenkins job 네 개가 같은 commit을 checkout한다.
- [ ] 각 job의 concurrent build를 껐다.
- [ ] 네 Arm의 UTC 30일 window가 완전히 같다.
- [ ] Arm/job/offset/DB 매핑이 표와 일치한다.
- [ ] `polybot config`에 `Promotion collection: ENABLED`가 표시된다.
- [ ] online backup과 SHA-256 manifest 저장 위치를 확보했다.
- [ ] 30일 뒤 B가 실패하거나 표본이 부족하면 threshold를 완화하지 않기로 했다.

## 11. 관련 문서

- [Golden Kiwi README](README.md)
- [전략의 전체 증거 계약](STRATEGY.md)
- [폐쇄 전략 통합 포스트모템](../docs/retro/closed-strategies-postmortem.md)
- [30일 회고와 분석 절차](../docs/retro/golden-kiwi.md)
- [과거 OOS 증거 정정](research/2026-07-30-cohort-correction.md)

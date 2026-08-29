# Golden Kiwi — Micro-Cascade

> **⛔ 2026-08-29 실험 폐쇄 — `STOP / UNRESEARCHABLE`.**
> A/B/C/D의 cadence coverage, 단일 source/config cohort, primary B 최소 표본 gate가 모두
> 실패해 Jenkins TimerTrigger를 제거했다. 기존 DB를 더 수집해 confirmatory experiment로
> 복구하거나 arm/threshold를 사후 선택하지 않는다. 근거는
> [최종 판정](../docs/retro/2026-08-29-golden-kiwi-verdict.md)을 따른다.

Golden Kiwi는 사람들의 정보 반영이 한 번에 끝나지 않고 짧은 군집 행동으로 이어질 수
있다는 가설을 검증하는 **5분 주기 research/simulation 전용 봇**이다.

출발점은 Lime·Fig·Mango·Date·Honeydew·Nectarine 여섯 폐쇄 전략의 DB·로그를 함께
검토한 [통합 포스트모템](../docs/retro/closed-strategies-postmortem.md)이다. 여섯 전략의
threshold를 합쳐 새 점수를 만든 것이 아니라, 반복하면 안 될 실패를 코드 제약으로
제거하고 **작은 연속 상승의 지속성**이라는 독립 가설 하나만 남겼다.

네 Jenkins 실험군을 다시 시작할 때는
[2026-08-13 filtered-universe 4개 Arm 실행 가이드](GOLDEN_KIWI_FILTERED_4_ARM_30_DAY_RUNBOOK_2026-08-13.md)를
먼저 읽는다.

> **현재 결론: 폐쇄 및 live 금지.**
> 동기화된 운영 DB로 사전 등록 후 시간 외 표본(OOS)을 검사했지만 네 실험군 모두 승격
> 기준을 통과하지 못했다. 그래서 코드가 `--live`를 명시적으로 거부한다. 이 폴더는
> “수익성이 확인된 새 전략”이 아니라, 다음 30일 독립 데이터로 가설을 기각하거나
> 재검토하기 위한 계측 도구다.

## 한글로 풀어쓴 규칙

```text
공통 진입 후보
  = 표준 이진 Yes/No 시장의 YES
  + 스포츠·게임·e스포츠와 짧은 주기 crypto 계열 exact tag 제외
  + tag가 point-in-time으로 확인됨 (누락·비정상 tag는 제외)
  + 해결까지 6시간 이상 (최대 시간 상한은 없음)
  + 현재 YES 20%~80%
  + 유동성 $20,000 이상, 최근 24시간 거래량 $10,000 이상
  + 유효한 매수/매도 호가, spread 2%p 이하

추세 확인
  = 5분 안팎의 연속 가격 변화 3회 또는 5회가 모두 상승
  + 각 관측 간격 3~10분
  + 한 번의 상승폭은 0%p 초과 2%p 이하
  + 전체 상승폭은 실험군별 1%p 또는 2%p 이상, 공통 최대 4%p

진입
  = 같은 event에서 유동성이 가장 큰 후보 1개
  + event별 승자들을 다시 유동성 내림차순·condition ID 오름차순으로 정렬
  + 그 순서에서 fresh 실행 gate를 처음 통과한 최대 1개/cycle
  + 같은 event 재진입은 6시간 이후
  + fresh ask·spread·$5 수량의 1.2배 ask depth 재검증
  + 실제 주문 없이 $5 simulation position 기록

종료
  = 진입 60분 뒤 처음 실행된 cycle의 fresh best bid로 가상 청산
  + 실제 종료 지연 시간을 별도 기록
```

정확히 5분마다 수집되면 3/5회 상승은 명목상 15/25분이다. 다만 허용 gap이 3~10분이므로
실제 staircase span은 A/B가 9~30분, C/D가 15~50분이다. `volume_24h`의 증가율은 쓰지
않는다. 24시간 누적값을 이 짧은 처치의 가속도로 해석하면 시간 단위가 맞지 않기 때문이다.
price stop이나 take-profit도 없다. 60분 출구는 현재 입증된 최적 청산법이 아니라 네
실험군을 같은 자본 회전 시간으로 비교하기 위한 고정 측정창이다.

위 simulation position의 60분 CLOB 청산은 **runtime diagnostic**이다. 승격 판정의
primary outcome은 position 보유 여부와 무관하게 `raw_selected` 당시 snapshot best
ask와 +60~75분 direct Gamma 조회의 첫 유효 best bid를 비교한다.

스포츠 제외는 과거 데이터에서 수익 개선이 입증됐다는 뜻이 아니다. 서로 다른 시계와
시장 구조를 한 표본에 섞지 않기 위한 고정 universe 선택이며 네 팔에서 동일하게 적용한다.

## 서버측 filtered universe

각 arm은 Gamma `/markets/keyset`에 `liquidity_num_min=20000`과 누적 거래량
`volume_num_min=10000`을 함께 보내 pagination 전에 시장을 줄인다. 이 누적 거래량은
`volume24hr`와 다른 필드이므로 entry의 최근 24시간 거래량 $10,000 gate도 그대로 다시
검사한다.

2026-08-11 실측에서 기존 267 page·26,654 raw market은 22 page·2,182 raw market으로
줄었다(각각 약 8.2%). 기존 entry 계약의 point-in-time strict 후보 17개도 모두 남았다.
같은 시점에 `volume24hr`를 15k/20k/25k/50k로 높이면 후보가 15/13/12/8개로 줄어
primary B의 최소 표본 50개를 더 어렵게 만들었다. 따라서 이번에는 request filter만
높이고 전략의 `volume24hr=10000`은 유지한다.

한 sweep이 53 page, 5,330 raw market 또는 120초를 넘으면 partial snapshot을 저장하지
않고 run을 실패시킨다. 이 세 상한은 장애 당시 수치의 1/5 이하이며, point-in-time
benchmark이므로 재가동 뒤 실제 p95 cycle runtime과 cadence coverage를 계속 확인한다.

## 사전 등록한 네 실험군

다른 값은 모두 같고 아래 두 축만 바꾼다.

| 팔 | 연속 상승 횟수 | 누적 상승 하한 | 역할 | canonical job |
|---|---:|---:|---|---|
| A | 3 | +1%p | 낮은 threshold 민감도·반증 | `kiwi-sim-a-3x1` |
| **B** | **3** | **+2%p** | **사전 지정 primary** | `kiwi-sim-b-3x2` |
| C | 5 | +1%p | 긴 확인 민감도 | `kiwi-sim-c-5x1` |
| D | 5 | +2%p | 결합 strict 민감도 | `kiwi-sim-d-5x2` |

결과가 가장 좋아 보이는 팔을 사후에 winner로 바꾸지 않는다. Primary B가 실패하면 A/C/D의
우연한 양수로 구조하지 않는다. 무처치 control이나 시장을 무작위 배정한 실험이 아니라,
B 하나를 판정하고 A/C/D로 신호 희귀성과 threshold 민감도를 확인하는 고정 비교 grid다.

## 동기화 DB에서 먼저 확인한 결과와 정정

연구 protocol은 arm별 forward return을 보기 전에 고정했다. 주요 원본은 동기화된
Honeydew DB의 2026-07-27 15:45Z~2026-07-28 14:15Z full-cadence 구간
1,293,610 snapshot이었다.

| 팔 | 과거 일반 OOS signals / events | best bid ÷ entry ask - 1 | 과거 strict event-purged OOS |
|---|---:|---:|---:|
| A | 4 / 2 | -1.0234% | 0 / 0 |
| B | 2 / 2 | -1.8072% | 1 / 1, +0.1355% |
| C | 1 / 1 | +0.5263% | 1 / 1, +0.5263% |
| D | 0 / 0 | 추정 불가 | 0 / 0 |

독립 재검토에서 C의 유일한 양수 신호가 서로 다른 Git commit의 snapshot을 이었다는
사실과, 과거 DB가 snapshot-level strict-binary/`negRisk`를 증명하지 못한다는 사실을
확인했다. 따라서 위 표는 당시 분석의 immutable 역사 기록일 뿐 **어느 팔에도 promotion
evidence가 아니다**. C의 `+0.5263%` 해석은 철회한다. Primary B가 50 signals /
30 events와 CI gate를 실패했다는 live 금지 결론은 그대로다. 자세한 내용은
[증거 정정문](research/2026-07-30-cohort-correction.md)과
[`research/frozen-2026-07-30/`](research/frozen-2026-07-30/)에 있다.

## 로컬 실행

wallet credential은 필요하지 않다. 실제 private key나 funder를 Kiwi에 주입하지 않는다.

```bash
cd golden-kiwi
uv sync --frozen --extra dev

uv run polybot config --job kiwi-sim-b-3x2
uv run polybot run --simulate --job kiwi-sim-b-3x2
uv run polybot status --job kiwi-sim-b-3x2
uv run pytest
```

`config`는 주문을 내지 않고 resolved arm, simulation 강제 여부, DB 경로와 고정 gate를
보여준다. 기본값은 primary B이며 DB는 현재 작업 디렉터리가 아니라 항상
`golden-kiwi/data/<job>/trades_sim.db`의 절대 경로로 고정된다.

### 새 DB와 로그 저장공간

새 코드 checkout 뒤 각 canonical job에서 Jenkins clean build는 **한 번만** 실행한다. Kiwi는
새 `trades_sim.db`를 처음부터 `compact-v1`로 만들므로 migration이나 `POLYBOT_DB_*`
환경변수가 필요 없다. 첫 실행 로그의 `새 SQLite DB를 compact-v1로 생성했습니다 -
strategy=golden-kiwi`를 확인한 뒤 매-build clean 옵션은 끈다.

Kiwi만은 연구 계약상 실제 5분 snapshot을 60일 동안 cold rollup 없이 보존하므로 다른 세
전략보다 DB가 크게 유지되는 것이 정상이다. 대신 가장 큰 반복 데이터인 전체 시장 sweep
상세는 24시간마다 한 번만 저장하고, 60일 경계를 넘은 telemetry는 정리한다. append-only
decision의 3/5-step snapshot lineage는 영구 보호한다. 일일 bot log는 60일을 넘으면 정리되며,
Jenkins console log는 Jenkins `Discard old builds`에서 별도로 60일 보존한다.

30일 window 환경변수 세 개가 없으면 안전한 **smoke/archive mode**다. 이때도 archive와
raw decision 진단은 남지만 `collection_eligible=0`이므로 promotion 분석 표본으로 쓰이지
않는다. 설정 여부는 `polybot config`의 `Promotion collection` 줄에서 먼저 확인한다.

다음 명령은 **의도적으로 실패해야 정상**이다.

```bash
uv run polybot run --live --job kiwi-sim-b-3x2
```

## Jenkins 설정

네 Jenkins job은 **Execute concurrent builds if necessary를 끈다**. Promotion
collection에서는 Jenkins의 숨은 `H` 값을 추정하지 않고 다음처럼 명시적 5분 offset을
고정한다.

실행 전에는 다음을 한 번씩 확인한다.

1. 각 job의 **Build periodically**에 아래 trigger를 입력한다.
2. 네 job의 `polybot config`에 표시되는 `Strategy source cohort`가 같은지 확인한다.
   Git commit은 계속 바뀔 수 있으며 cohort 판정에는 사용하지 않는다.
3. Jenkins agent에서 `/Users/jongwoopark/.local/bin/uv` 경로가 실제로 존재하는지
   확인한다.
4. trigger를 켜기 전에 shell의 `polybot config`를 수동 실행해 arm, job, offset,
   절대 DB 경로와 `Promotion collection: ENABLED`를 확인한다.
5. wallet private key, funder address, signature type은 설정하지 않는다.

| arm | Build Trigger | `POLYBOT_CADENCE_OFFSET_MINUTE` |
|---|---|---:|
| A | `0-59/5 * * * *` | `0` |
| B | `1-59/5 * * * *` | `1` |
| C | `2-59/5 * * * *` | `2` |
| D | `3-59/5 * * * *` | `3` |

네 팔은 고정된 UTC
`[2026-08-13T00:00:00Z, 2026-09-12T00:00:00Z)`를 함께 쓴다. 같은 DB에서
날짜·offset·arm·job·preregistration hash·analyzer version을 바꾸면 시작을 거부한다.
기존 cadence-invalid DB를 migration하거나 합치지 말고 첫 build에서만 clean build로
새 DB를 만든 뒤 이후 build의 clean 옵션은 끈다. 첫 clean build도 임의 수동 시각이 아니라
A/B/C/D 각각 UTC minute 0/1/2/3의 scheduler slot에서 시작해야 한다. 정확한 one-shot
순서는 현재 실행 가이드를 따른다.

한 cycle의 p95 실행시간이 5분을 넘거나 snapshot 간격이 3~10분을 지속적으로 벗어나면
표본을 늘리려고 동시 실행이나 gap 완화를 하지 않는다. 그 cohort는 cadence 계약 실패로
표시하고 수집 구조부터 고친다. 정규 slot 밖의 SUCCESS run이나 같은 slot의 중복
SUCCESS run이 하나라도 있으면 그 run의 signal·follow-up을 primary 표본에서 제외하고
전체 promotion 판정도 fail-closed한다.

### A — `kiwi-sim-a-3x1`

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE
export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_CONFIRMATION_STEPS=3
export POLYBOT_MIN_CUMULATIVE_MOVE=0.01
export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z
export POLYBOT_CADENCE_OFFSET_MINUTE=0

cd ./golden-kiwi
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-a-3x1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-a-3x1
```

### B — `kiwi-sim-b-3x2` (primary)

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE
export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_CONFIRMATION_STEPS=3
export POLYBOT_MIN_CUMULATIVE_MOVE=0.02
export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z
export POLYBOT_CADENCE_OFFSET_MINUTE=1

cd ./golden-kiwi
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-b-3x2
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-b-3x2
```

### C — `kiwi-sim-c-5x1`

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE
export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_CONFIRMATION_STEPS=5
export POLYBOT_MIN_CUMULATIVE_MOVE=0.01
export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z
export POLYBOT_CADENCE_OFFSET_MINUTE=2

cd ./golden-kiwi
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-c-5x1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-c-5x1
```

### D — `kiwi-sim-d-5x2`

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE
export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_CONFIRMATION_STEPS=5
export POLYBOT_MIN_CUMULATIVE_MOVE=0.02
export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z
export POLYBOT_CADENCE_OFFSET_MINUTE=3

cd ./golden-kiwi
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-d-5x2
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-d-5x2
```

각 canonical job 이름은 독립 SQLite 경로를 만든다. 코드는 job과 arm 환경변수의 정확한
매핑을 검증하고, 기존 DB에 다른 arm cohort가 있으면 시작을 거부한다. 네 job이 같은
`--job`이나 DB를 공유하면 lineage·position·성과가 섞이므로 허용하지 않는다.

## 고정 환경변수

정상 실험의 처치축으로 바꿀 값은 `POLYBOT_CONFIRMATION_STEPS`와
`POLYBOT_MIN_CUMULATIVE_MOVE`뿐이다. window와 offset은 처치가 아니라 수집 계약이다.

| 변수 | 허용값 | 의미 |
|---|---|---|
| `POLYBOT_CONFIRMATION_STEPS` | `3`, `5` | 연속으로 확인할 양의 상승 횟수 |
| `POLYBOT_MIN_CUMULATIVE_MOVE` | `0.01`, `0.02` | 관측창 전체 최소 YES 상승폭 |
| `POLYBOT_EXPERIMENT_START_UTC` | `2026-08-13T00:00:00Z` | 네 팔이 공유하는 30일 반개구간 시작 |
| `POLYBOT_EXPERIMENT_END_UTC` | `2026-09-12T00:00:00Z` | 고정 exclusive end |
| `POLYBOT_CADENCE_OFFSET_MINUTE` | job별 고정: A/B/C/D=`0/1/2/3` | 해당 canonical job의 5분 schedule offset |
| `POLYBOT_LIFECYCLE_MODE` | `active`, `close_only`, `archive_only` | 운영 복구용 lifecycle; arm 축이 아님 |
| `LOG_LEVEL` | 예: `INFO` | 로그 상세도 |

window 세 변수는 **전부 비우거나 전부 채워야** 한다. 일부만 있으면 시작하지 않는다.
값이 없으면 smoke/archive mode이고 promotion 분석 대상이 아니다. 코드는 그 밖의
universe, 금액, spread, risk, archive 환경변수도 config loader에서 읽지만,
사전 등록값과 다르면 시작을 거부한다. 즉 Jenkins에서 임의로 threshold를 조정하는 숨은
팔을 만들 수 없다. `.env.example`에도 실제 secret은 없고 두 처치축, 세 collection
contract 값, lifecycle만 둔다.

## 위험 한도와 evidence

- simulation amount $5, 최대 동시 position 3개, open notional $15
- 같은 job/config/source digest/mode의 SUCCESS terminal outcome을 시간순으로 합산해 최초
  -$20 crossing을 계산한다. cycle 시작에 후보가 0개여도 평가한다. 감지 run이 성공하기
  전에는 pending row만 쓰고, FAILED면 폐기하며 SUCCESS 뒤에만 영구 latch로 승격한다.
  이후 P&L 회복이나 프로세스 재시작으로 자동 해제하지 않음
- 같은 event 최대 1개, 한 cycle 신규 1개, event cooldown 6시간
- 원자적 cursor-complete sweep과 현재 run snapshot이 없으면 진입 금지
- 현재 run의 마지막 관측과 같은 `config_hash × strategy_source_digest × mode × job_name`인
  SUCCESS history만 계단 lineage로 사용
- Gamma sweep 종료시각이 아니라 각 keyset page를 받은 로컬 시각을 관측시각으로 저장
- archive fetch는 서버에서 liquidity $20,000·누적 volume $10,000 이상만 받는다.
  request envelope에 새로 들어온 시장은 충분한 새 lineage가 쌓일 때까지 backfill 없이 제외
- 각 SUCCESS sweep은 schema v2, cursor complete, 53 page·5,330 market·120초 이하여야 함
- 실행 직전 한 번의 CLOB book에서 midpoint·bid·ask·spread·depth를 함께 읽고 마지막
  price와 gap을 다시 평가
- 60일 동안 실제 5분 cadence 원본을 보존하며 cold rollup 금지
- 가상 결과는 `hypothetical_pnl`에만 기록하고 `realized_pnl`은 `NULL`
- resolution, redeemable 상태, 실제 redeem, CLOB fill은 서로 다른 사실
- `micro_cascade_signal_decisions`는 raw signal 전체를 append-only로 저장한다. 같은
  event의 sibling rank, event/global rank, portfolio/drawdown 상태, fresh
  bid/ask/spread/depth와 탈락 사유, 선택 trade를 한 행에서 분리한다.
- `micro_cascade_followup_observations`는 `raw_selected` signal마다 +60~75분에
  condition을 Gamma에서 직접 다시 조회한다. 종료·저유동성으로 main sweep에서 빠진
  시장도 조회하며, quote 부재·source 오류를 append-only 사유로 남기고 window 안의
  첫 SUCCESS valid best bid만 primary outcome으로 쓴다.
- FAILED source/observer run의 append-only 행은 삭제해 역사를 고치지 않고 분석에서
  제외한다.

실행이 60분+15분 이후에야 돌아온 position도 실제 지연값과 가상 청산값은 보존하지만,
고정 OOS outcome과 비교하는 promotion 분석에서는 **evidence gap으로 censor**한다.
값을 0으로 채우거나 마지막 quote로 forward-fill하지 않는다.

drawdown latch가 작동한 DB는 같은 연구의 종료 증거다. row를 지워 재개하지 않는다.
새 실험은 새 preregistration·새 canonical job·새 DB와 명시적 검토가 있어야 한다.

## 30일 후 판정

다음 독립 UTC 구간을 처음부터 끝까지 고정하고, 네 DB를 arm별로 분리해 평가한다.
Primary B가 아래 조건을 모두 통과해야만 별도 shadow review를 열 수 있다.

1. quote-complete signal 50개 이상, event cluster 30개 이상
2. event-cluster 98.75% lower confidence bound가 0보다 큼
3. 추가 10.4bps stress 후 lower bound도 0보다 큼
4. 사전 정의한 전반/후반 구간이 모두 양수
5. target/quote coverage 90% 이상
6. arm마다 정확히 한 `config_hash × strategy_source_digest × mode × job_name` cohort이고
   네 arm이 같은 strategy source digest를 사용. Git commit은 판정과 무관한 provenance임
7. strict audit의 CRITICAL/HIGH가 0

통과해도 live 승격이 아니다. $5 depth, queue/latency, partial fill, fee/role과 exact
reconciliation을 계측하는 별도 shadow 단계 검토만 허용한다. B가 실패하거나 너무 희귀하면
threshold를 완화하지 않고 `STOP / UNRESEARCHABLE`로 끝낸다.

분석기는 각 DB만 대상으로 만든 exact-window strict audit JSON을 각각 요구한다. audit가
기록한 `database_sha256`은 분석 시점의 immutable DB 실제 바이트와 정확히 같아야 한다.
네 DB를 한 audit bundle로 합치면 경로별 PASS를 혼동할 수 있으므로 promotion 입력으로
받지 않는다.
모노레포 루트에서 다음처럼 실행한다.

```bash
uv run --project golden-kiwi python golden-kiwi/scripts/analyze_experiment.py \
  --db "A=$KIWI_A_DB" --db "B=$KIWI_B_DB" \
  --db "C=$KIWI_C_DB" --db "D=$KIWI_D_DB" \
  --strict-audit "A=$RETRO_OUTPUT/A/retro-audit.json" \
  --strict-audit "B=$RETRO_OUTPUT/B/retro-audit.json" \
  --strict-audit "C=$RETRO_OUTPUT/C/retro-audit.json" \
  --strict-audit "D=$RETRO_OUTPUT/D/retro-audit.json" \
  --start "$REVIEW_START" --end "$REVIEW_END_EXCLUSIVE" \
  --output "$RETRO_OUTPUT/kiwi-analysis-v2.json"
```

`NOT_EVALUABLE_FAIL_CLOSED`는 contract/audit/cadence/cohort 증거가 불완전하다는 뜻이다.
증거가 완전하지만 primary B 수치가 gate를 실패하면 `FAIL_NO_SHADOW_REVIEW`, 모두
통과하면 `ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW`다. 마지막 판정도 live 주문 승인이 아니다.

상세 계약은 [STRATEGY.md](STRATEGY.md), 회고 절차는
[docs/retro/golden-kiwi.md](../docs/retro/golden-kiwi.md), 고정 분석의 재현 정보는
[research/README.md](research/README.md)를 따른다. 실험을 중단할 때는 job을 임의로
삭제하기 전에 [전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)에 따라
simulation DB·로그·manifest를 보존하고 lifecycle을 전환한다.

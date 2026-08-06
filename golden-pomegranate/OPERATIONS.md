# Golden Pomegranate 운영 README

이 문서는 Golden Pomegranate를 Mac mini의 외장 APFS volume에서 실제로 수집·보관하는 절차만
다룬다. 수집 데이터 계약은 [README.md](README.md), 연구 가설은 [STRATEGY.md](STRATEGY.md)를
따른다.

## 먼저 보는 운영 일정

| 완결된 UTC 수집 기간 | 해야 할 일 | 아직 하면 안 되는 일 |
|---|---|---|
| 1~3 cycle | 실행시간, 시장 수, cycle당 DB 증가량, component status와 `forecast_days_to_stop` 확인 | timer 활성화, 수익성 해석 |
| 7일 | 누락·중복·UTC shard rotation·disk forecast·API 오류율을 점검하고 health report 작성 | threshold 조정, 전략 승격 |
| 14일 | feature 정의와 탐색 분석 시작. 스포츠/정치/경제, 주중/주말 slice를 미리 고정 | 관측 결과에 맞춘 사후 가설 변경 |
| 30일 | 첫 후보 전략과 반사실 backtest 작성. 이것이 권장 최소 연구 구간 | unresolved 시장을 임의 승패로 처리 |
| 60~90일 | 서로 다른 regime과 독립 시간구간에서 재검증 | 같은 30일 자료를 반복 최적화 |
| 120일 | 보존 및 capacity horizon 검토 | “120일까지 분석을 기다려야 한다”고 해석 |

7일 health gate가 실패하면 더 기다리지 말고 수집 계약부터 복구한다. 120일은 Jenkins summary
console log와 SQLite whole-shard의 **보존 상한 계획**이지 전략 연구의 대기기간이 아니다.

## 권장 storage profile

기본 profile은 2026-08-07부터 저장공간을 다음처럼 제한한다.

- Jenkins cadence: 15분 `H/15 * * * *`
- resolved cadence: `POLYBOT_CADENCE_MINUTES=15`
- `closed=false`
- 누적 유동성 `>= $10,000`
- 누적 거래량 `>= $2,000`
- `endDate <= 수집 시각 + 120일`
- 반환된 bounded envelope 안에서는 terminal cursor까지 전부 저장
- order book 최대 400 market/cycle, Data API trade tape와 resolution watcher는 기존 계약 유지

이 gate는 특정 매수 후보를 미리 고르는 전략 filter가 아니라, 거래 가능성이 거의 없는 먼 미래·
저유동성 시장이 디스크를 소모하지 않게 하는 **capacity boundary**다. 환경변수 override는 더
엄격하게만 만들 수 있고, gate를 낮춰 전역 139k 시장으로 돌아가는 것은 config에서 거부한다.

2026-08-07 실제 공개 API 비교에서는 기존 envelope 139,310 markets가 새 기본 envelope
2,899 markets로 줄었다(약 97.9% 감소). `#3`의 첫 bounded cycle은 3,030 markets,
25.597초, 49,168,384 bytes였다. 다만 이 크기에는 Data API가 요청 범위를 무시하고 반환한
10,000개 global-head row를 정규화해 중복 저장한 비용이 포함돼 있었다. 현재 코드는 이런
`SOURCE_BOUNDS_VIOLATION` 응답을 compressed sanitized raw payload + count/digest/request lineage로만
남기며, 요청 범위 밖 row와 membership 10,000개를 fact table로 확장하지 않는다.

수정 전 49.2MB/cycle을 그대로 쓰는 보수적 상한에서도 15분은 약 4.72GB/day,
`1.2 × daily × 120일 ≈ 680GB`다. 반면 5분은 약 14.16GB/day,
동일 안전계수로 약 2.04TB이므로 1TB volume의 120일 계약을 만족하지 못한다. 따라서 15분을
최종 기본값으로 사용한다. 실제 값은 첫 3 cycle의 marginal growth로 즉시 대체하며,
`1.2 × 실측 일평균 × 120일`이 680GB를 넘거나 `forecast_days_to_stop < 120`이면 timer를
중지하고 수집 계약을 재검토한다.

1시간으로만 늦추고 139k global census를 유지하면 약 33GB/day이므로 해결책이 아니다. 이번
profile은 cadence와 universe를 동시에 줄인다.

## 기존 profile에서 1회 전환

기존 `pomegranate-local`은 15분 global-open-market envelope이고,
`pomegranate-hourly-v1`은 60분 bounded envelope이지만 범위 밖 Data API row를 확장 저장한다.
새 runtime job `pomegranate-15m-v2`는 15분 bounded envelope과 compact bounds-violation evidence를
사용한다. 서로 다른 계약을 같은 UTC shard에 섞지 않는다.

```text
data/pomegranate-local/trades_sim.db          # 구 profile, 필요 없으면 검증 후 삭제
data/pomegranate-hourly-v1/trades_sim.db      # 이전 60분 bounded 검증 1회
data/pomegranate-15m-v2/trades_sim.db         # 현재 기본 profile
```

구 DB를 삭제할 때는 Jenkins를 먼저 중지하고, 정말 보존할 필요가 없는 첫 실험 cycle인지 확인한
뒤 `data/pomegranate-local/`만 대상으로 한다. collector가 자동으로 evidence를 삭제하지는 않는다.

## 최초 설치·코드 변경 후 1회 검증

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
cd golden-pomegranate
UV=/Users/jongwoopark/.local/bin/uv

"${UV}" sync --frozen --extra dev
"${UV}" run pytest
"${UV}" build
```

`pytest`와 `build`는 최초 checkout, 코드 변경 또는 dependency/build 설정 변경 때만 실행한다.
반복 수집에서는 제외한다. `set -euo pipefail`이 없으면 테스트가 실패해도 Jenkins가 뒤 명령을
계속 실행해 거짓 `SUCCESS`가 될 수 있다.

## 15분 반복 수집 shell

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export PYTHONUNBUFFERED=1
export POLYBOT_LIFECYCLE_MODE=archive_only
export POLYBOT_CADENCE_MINUTES=15

cd golden-pomegranate
UV=/Users/jongwoopark/.local/bin/uv
JOB=pomegranate-15m-v2

"${UV}" sync --frozen
"${UV}" run polybot config --simulate --job "${JOB}"
"${UV}" run polybot health --simulate --job "${JOB}"
"${UV}" run polybot run --simulate --job "${JOB}"
"${UV}" run polybot status --simulate --job "${JOB}"
"${UV}" run polybot health --simulate --job "${JOB}"
```

Build periodically는 `H/15 * * * *`를 쓴다. `H/15`는 Jenkins가 job별 시작 분을 분산하면서
15분마다 실행하므로 `*/15`보다 동시 부하가 적다. schedule과 resolved cadence는 반드시
일치해야 한다. 5분 수집은 스포츠 변화에는 매력적이지만 현재 full bounded census의 120일
capacity 계약을 위반하므로 사용하지 않는다. Golden Pomegranate는 주문 bot이 아니라 넓은
시장 research collector이며, 1분 tick 연구는 별도 WebSocket collector의 계약으로 설계해야 한다.

`--simulate`는 가짜 데이터라는 뜻이 아니다. 실제 공개 Gamma/CLOB/Data API를 읽고 실제 연구
DB에 기록하지만 계좌와 주문 경로가 없다는 뜻이다. Golden Pomegranate는 항상 simulate이며
`--live`는 source level에서 금지한다.

## 외장 APFS와 `UV_LINK_MODE=copy`

승인 workspace는 `/Volumes/t7/jenkins/golden-pomegranate`다. uv global cache는 내부 disk,
`.venv`는 외장 volume에 있을 수 있으므로 clone/hardlink가 volume 경계를 넘지 않게
`UV_LINK_MODE=copy`를 유지한다. 복사 시간과 공간은 조금 늘지만 Jenkins의 cross-device 동작이
일관된다. uv executable도 `/Users/jongwoopark/.local/bin/uv`로 고정한다.

full Jenkinsfile은 다음을 수집 전에 검사한다.

- `/Volumes/t7` exact mount point와 `Device Location=External`
- APFS personality, sentinel, host-side UUID pin
- workspace canonical path와 filesystem device identity
- concurrent build 금지와 20분 timeout
- credential 환경변수 부재

Freestyle job은 적어도 위 반복 shell을 쓰고, 가능하면 [Jenkinsfile](Jenkinsfile)의 mount
preflight를 동일하게 옮긴다.

## Disk guard와 보존

- filesystem 사용률 `>=70%`: warning
- filesystem 사용률 `>=80%`: hard stop
- free space `<150 GiB`: hard stop
- run 전과 atomic publish 후 모두 검사
- 자동 row 삭제, `VACUUM`, compact/prune 없음

dated shard는 120일 whole-shard retention을 capacity 기준으로 삼는다. 삭제가 필요하면 closed
shard의 backup, SHA-256, `PRAGMA quick_check=ok`, manifest/table count를 확인하고 파일 전체
단위로 처리한다. active DB나 table 일부를 삭제하지 않는다.

## 매 cycle 확인 항목

1. `config`: cadence 15, bounded Gamma gate, simulate, stable job 확인
2. 첫 `health`: `quick_check=ok`, append-only trigger, disk `OK` 확인
3. `run`: Gamma terminal cursor, component별 SUCCESS/EMPTY/POSSIBLE_GAP 확인
4. `status`: market count, runtime, cycle당 marginal growth, watermark 확인
5. 마지막 `health`: WAL/DB와 `forecast_days_to_stop` 확인

`run` 실패 로그에는 이제 exception type뿐 아니라 실제 error message와 traceback도 남는다.
Data API `POSSIBLE_GAP`, cursor partial, disk warning을 정상 수집으로 계산하지 않는다.

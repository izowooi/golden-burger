# 032 — Golden Black 확장 evidence 검증과 설계 — 2026-08-19

작성일: 2026-08-19 KST

## 0. 결론

스포츠 outcome을 exact `$5` ask `0.94` 부근에서 가상 매수해 unique one-hot resolution까지
보유하는 규칙은 여러 과거 DB에서 **유망한 point estimate**를 보였지만, 수익을 보장하거나
실거래를 승인할 수준은 아니다.

- Pomegranate: `0.94`의 평가 가능 60건이 모두 승리했고, 과거 sports fee 3%와 진입 1¢
  adverse stress를 적용한 event-equal ROI는 train `+4.60%`, validation `+4.75%`였다.
- 별도 Nectarine archive: `0.94`는 132건 중 130승, 122 event였고 같은 stress 기준 train
  `+2.60%`, validation `+2.41%`였다.
- Honeydew archive도 양수였지만 `0.94` 표본이 7건뿐이다.
- 그러나 Pomegranate의 `0.94` Wilson 95% 승률 하한 `93.98%`는 당시 fee 포함 손익분기
  승률 `94.55%`보다 낮다. 한 번의 패배가 약 16회의 작은 승리를 지울 수 있다.
- 세 archive는 서로 독립적인 무작위 표본이 아니며, Nectarine/Honeydew는 각 전략의 기존
  universe 선택을 거친 편향 표본이다. 행 수를 단순 합산해 신뢰도를 부풀리지 않았다.
- 현대 live DB 12개에서는 sports + 6h + exact confirmed fill + resolution을 모두 만족한 행이
  4개뿐이었고 `0.92`/`0.94` band에는 하나도 없었다.

따라서 A군은 사용자 후보 `0.94`, B군은 **`0.92`**로 고정한다. `0.92`는 Pomegranate와
Nectarine 양쪽에서 train/validation이 모두 양수이고 표본이 더 넓다. `0.95~0.97`은
Pomegranate validation이 음수였고, `0.93`은 `0.94`와 지나치게 인접해 비교력이 약하다.

이 결론을 검증하기 위해 `golden-black`을 accountless `simulation-only` collector로 만들었다.
지금 `$5`는 실제 주문액이 아니라 exact CLOB depth로 계산하는 **가상 notional**이다. 첫
prospective 검정과 별도 confirmatory cohort를 통과하기 전에는 live 주문을 넣지 않는다.

## 1. 수집·검증 범위

### 1.1 Jenkins와 local storage

- 현재 inventory의 Jenkins 26개 job config, 최근 build 상태와 workspace를 read-only로 전수
  확인했다.
- `daily-rsync` catalog의 현재·역사 strategy epoch를 분리해 동기화했다. 관련 live/research
  DB와 최근 log를 다시 검증했고 Kiwi A~D 및 Raspberry Do/Re/Mi까지 누락 없이 보완했다.
- 검증 시점 로컬 여유 공간: `313 GiB`; `daily-rsync/data`: `51 GiB`.
- DB가 threshold 검정에 필요한 sports, clock, quote/fill, resolution 필드를 갖지 않으면 행
  수가 많아도 결과 표본에 넣지 않았다.
- 모든 archive label 대조에서 610개 candidate condition을 public CLOB market으로 조회했고
  응답 오류와 one-hot label conflict는 0건이었다.

### 1.2 주요 verified evidence

| source | verified local DB / cutoff | 용도와 상태 |
|---|---|---|
| `golden-pomegranate/pomegranate-15m-v2` | 2026-08-07~18 immutable 12개 shard; 세부 SHA-256은 030·031 보고서 | 가장 넓은 sports 6h proxy grid; 12 checked / 0 failed |
| historical `golden-nectarine/default` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-eagle/strategies/golden-nectarine/runtime/default/databases/latest/trades.db`; SHA-256 `9ac1442a…cae`; source cutoff `2026-07-30T12:15:04Z` | broad snapshot 보조 표본; remote는 전략 교체로 `SOURCE_MISSING`이나 cutoff 내 local copy verify 성공 |
| historical `golden-honeydew/default` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-honeydew/runtime/default/databases/latest/trades.db`; SHA-256 `f0ae41a1…6df`; source cutoff `2026-07-28T15:42:05Z` | 작은 보조 표본; 동일한 historical limitation |
| `golden-strawberry/strawberry-shadow-one` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-shadow-one/strategies/golden-strawberry/runtime/strawberry-shadow-one/databases/latest/trades_sim.db`; SHA-256 `0945f0e6…91a7`; source cutoff `2026-08-19T00:25:30Z` | exact crossing/book architecture 확인; 12.77 GB verified immutable copy |
| Blueberry 2 + Melon 3 + Papaya 2 + Queen 2 + Quince 3 | 각 current DB `daily-rsync verify` SUCCESS | actual fill/resolution 보조 진단; threshold 직접 표본은 사실상 없음 |

Pomegranate의 최신 전체 sync attempt는 obsolete `pomegranate-local` DB 한 개를 열지 못해
`PARTIAL`이지만, 분석에 쓴 `pomegranate-15m-v2` 일별 shard 12개는 개별 checksum과 archive
coverage가 모두 성공했다. Strawberry는 원격 DB가 계속 쓰이는 중이므로 이후 unstable copy를
섞지 않고 위의 성공한 immutable local copy만 사용했다. `polybot-wolf`의 보존기간 삭제 log
17개는 DB threshold 검정에는 필요하지 않았으며 log coverage로 간주하지 않았다.

## 2. 확장 결과

수치는 각 source 안에서 event-equal로 계산했다. 과거 결과의 비용은 당시 연구 계약인 sports
taker fee 3%와 진입 1¢ adverse stress다. 현재 공식 sports taker fee rate는 5%이므로 이 과거
ROI를 새 실험의 기대수익으로 그대로 복사하지 않는다. Golden Black은 각 시장의 실제
`feeSchedule.rate`를 저장하고 누락 시 현재 5%를 사용한다.

| source | entry | evaluable / wins / events | train ROI | validation ROI | 전체 stress ROI |
|---|---:|---:|---:|---:|---:|
| Pomegranate | 0.92 | 55 / 미집계 / 53 | +6.74% | +3.67% | 양수 |
| Pomegranate | 0.94 | 60 / 60 / 57 | +4.60% | +4.75% | +4.68% |
| Nectarine | 0.92 | 134 / 129 / 122 | +3.25% | +1.23% | +1.93% |
| Nectarine | 0.94 | 132 / 130 / 122 | +2.60% | +2.41% | +2.48% |
| Honeydew | 0.92 | 3 / 3 / 소표본 | - | - | +5.47% |
| Honeydew | 0.94 | 7 / 7 / 소표본 | - | - | +3.70% |

Nectarine의 `0.95` stress ROI는 `-0.11%`, validation은 `-0.32%`였다. `0.97`은 Nectarine에서
양수였지만 Pomegranate validation은 `-1.25%`로 재현되지 않았다. 높은 진입가가 더 안전하다는
직관만으로 threshold를 올릴 수 없는 이유다.

Strawberry는 sports + liquidity `10,000` + volume `5,000` exact subset이 너무 작았다.
`0.92`는 episode 28개 중 resolution 11개가 모두 승리했고 `0.95`는 11개 중 7개가 모두
승리했지만, `0.94` arm과 신뢰할 수 있는 실제 경기 종료 6h clock이 없다. 이 DB는 exact book,
cursor, path/resolution 수집 구조를 설계하는 근거로만 사용했다.

## 3. 왜 `0.94`가 보장이 아닌가

1. `0.94`에서 이길 때 gross 이익은 약 6¢뿐이지만 지면 약 94¢를 잃는다. 비용까지 고려하면
   매우 높은 승률이 지속돼야 한다.
2. Pomegranate 85개 신호 중 25개는 평가 불가였고 24개는 이미 성숙했지만 resolution label이
   빠져 있었다. 미확인 사례를 승리로 채우지 않았다.
3. Pomegranate의 85개 신호 중 same-cycle exact CLOB entry evidence는 1개뿐이었다.
4. `0.94`는 0.75~0.97 grid를 본 뒤 발견한 post-hoc 후보다. 새 데이터에서 그대로 재현해야 한다.
5. Gamma `endDate`는 실제 경기 종료 시각과 같다고 보장되지 않는다. Pomegranate 신호 1,988개
   중 `endDate≈gameStartTime`은 424개뿐이고 1,564개는 달랐다. 따라서 현재 6h는 우선
   **Gamma endDate 6h**이며, DB에 `gameStartTime`과 `PRE_GAME/IN_PLAY`를 함께 저장한다.

## 4. Golden Black 고정 계약

- data contract: `sports-resolution-paired-v1`
- runtime job: `black-shadow-paired`
- A: exact `$5` ask VWAP `[0.94,0.95]`에서 가상 진입, one-hot resolution 보유
- B: exact `$5` ask VWAP `[0.92,0.93]`에서 가상 진입, one-hot resolution 보유
- exit sensitivity: `HOLD_TO_RESOLUTION`, `STOP_0.80`, `STOP_0.70`, `STOP_0.60`
- universe: sports strict binary, open/orderbook/accepting, market liquidity `>=10,000`,
  cumulative volume `>=5,000`, Gamma endDate `(0h,6h]`
- cadence: 5분
- entry window: `[2026-08-21T00:00:00Z, 2026-09-20T00:00:00Z)`
- follow-up end: `2026-10-20T00:00:00Z`
- credential와 `--live`는 DB와 network를 열기 전에 source-level 차단
- API receipt, gzip raw payload, market membership, exact full book/levels, `$5` walk, decision,
  episode, bid path, stop trigger/actual VWAP/partial/retry, resolution, fee,
  config/source/run/storage provenance를 append-only로 저장

2026-08-20 배포 전 후속 요청으로 stop grid를 protocol에 추가하고, 실제 Jenkins 배포보다
먼저 시작시각이 지나지 않도록 prospective start를 2026-08-21T00:00:00Z로 옮겼다. stop 가격은 fill 가격이
아니며, best bid가 기준 이하가 된 뒤 원래 share의 displayed bid depth를 walk한다. 한 cycle에
전량 depth가 없으면 실제 부분 수량·잔여 수량·fee를 남기고 다음 cycle에서 잔여분을 계속
청산하는 반사실이다. stop 값은 아직 winner가 아니며 30일 전 선택하지 않는다.

`>=0.94` 전체를 한 덩어리로 사지 않고 1¢ band를 둔 이유는 `0.99` 진입을 `0.94` 진입과 같은
처치로 섞으면 payoff와 손익분기 승률이 완전히 달라지기 때문이다.

## 5. 333-page 문제 제거

Golden Black은 CLOB `/sampling-markets` 전체를 순회하지 않는다. Gamma event keyset에 다음
필터를 **서버에서 먼저** 적용한다.

- `tag_slug=sports`
- `closed=false`
- `liquidity_min=10000`
- `volume_min=5000`
- `end_date_min=now`, `end_date_max=now+6h`
- page size 500, maximum 4 pages, terminal cursor 필수

public smoke cycle 결과는 1 page, 38 events, nested 699 markets, market-level 재검증 통과 74개,
book token 148개였다. 총 runtime은 약 100초였고 cursor complete, DB quick check, DQ 0을
확인했다. 첫 DB 크기는 4.9 MB였다. 같은 크기가 매 5분 선형 반복된다는 보수적 단순 상한은
30일 약 42 GB이므로 외장 1 TB에는 맞지만, 24시간 뒤 실제 증가량으로 다시 추정해야 한다.

## 6. 판정 일정

- 24시간: cadence, runtime p95, terminal cursor, exact book coverage, DQ, DB integrity, storage
  증가량만 판정한다. 수익과 threshold는 보지 않는다.
- 7일: arm별 episode/event 수와 resolution coverage만 판정한다. 표본이 적어도 수치를 바꾸지
  않는다.
- 30일 entry 종료: arm별 evaluable 300, unique event 200, resolution coverage 90%, exact
  `$5` book coverage 100%를 요구한다. event-cluster 비용 후 ROI 하한이 0보다 커야 한다.
- follow-up 종료: 미해결 episode를 손익으로 추정하지 않고 censored로 남긴다.
- 위 gate를 통과해도 곧바로 live로 바꾸지 않는다. 동일 규칙의 새 untouched prospective
  confirmatory cohort를 한 번 더 통과해야 한다.

## 7. 구현·검증 결과

- 신규 프로젝트: `golden-black/`
- frozen preregistration: `golden-black/research/frozen-2026-08-20/`
- tests: `23 passed`
- root strategy contract: `PASS (22 strategies)`
- preregistration manifest: all checksums OK
- package build: sdist/wheel 성공
- public-data smoke: cursor complete, 1 page, 74 eligible markets, 148 exact-book token attempts,
  DB quick check OK, DQ 0
- 기존 live/research Jenkins job은 변경하지 않았다. 새 `polybot-black` job은 사용자가 만들고
  runbook의 shell을 적용한다.

Jenkins shell, 외장 workspace 검사와 `daily-rsync` 명령은
[`golden-black/OPERATIONS.md`](../../golden-black/OPERATIONS.md)에 고정했다.

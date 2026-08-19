# 029 — 전 Jenkins scan·position-cap 감사와 fleet 보완 — 2026-08-19

작성일: 2026-08-19 KST

대상:

- 실거래/close-only 16개 Jenkins job
- simulation·shadow·research 10개 Jenkins job
- 점검 항목: Gamma page/market 수, timer 대비 실행시간, DB open 상태와
  `max_positions`, PENDING lifecycle, sync/verify, 저장공간

## 0. 결론

```text
실거래 16개 job의 DB/log를 daily-rsync로 동기화·검증했다.
DB 무결성 실패나 artifact conflict는 0건이다.

기존 broad universe 10개 job은 319~321 page/약 3.2만 market에서
Papaya·Queen·Quince 92~93 page/약 9.2천,
Melon 48 page/약 4.7천으로 줄였다.

Cherry Yellow·Orange에는 누락됐던 Gamma cumulative volume $5,000을
공통 적용했다. Yellow 8→7 page, Orange 37→18 page다.

max_positions를 stale DB가 잘못 점유한 유일한 현재 사례는 Orange였다.
wallet과 exact-fill evidence가 없는/종결된 4행만 대사해 open 10→6으로
복구했다. 이후 정상 신규 주문으로 open 10/10이 됐으며 이는 실제 위험 한도다.

나머지 live job은 Dog 5/20, Yellow 5/10, 그 외 0으로 cap에 막히지 않았다.

별도 연구 병목은 Strawberry였다. 수집 뒤 12GiB DB 전수 status/health를
두 번 실행해 빌드가 26~28분으로 늘었다. timed shell에서 두 deep check를
제거하고 10분의 frozen 수집 cadence는 유지했다. 자연 build는 7분 51초와
8분 6초에 연속 성공했고 최종 DB/log sync·verify도 성공했다.
```

이번 작업은 fleet runtime과 lifecycle 안전성 감사다. 수익성 또는 entry/exit
파라미터의 우열을 판정하지 않는다.

## 1. Evidence와 범위

- live DB source cutoff: 2026-08-19 08:26~08:47 KST
- Yellow/Orange 최종 sync cutoff: 2026-08-19 08:47 KST
- Jenkins console/config 관측: 2026-08-19 08:31~09:54 KST
- daily-rsync verify: live 16개 모두 `SUCCESS`, conflict 0
- Strawberry 최종 sync: run `a2108a10aa6c43ffbb9b317b2f3dc99a`, 141 transferred,
  0 skipped/failed; verify 310 checked, 0 failed/conflict
- `polybot-wolf`만 이미 retention으로 지워진 과거 console 17개가 explicit skip이다.
  최신 DB와 현재 요청 구간의 로그 검증 실패는 아니다.
- MacBook local free: 작업 전 약 349GiB, Strawberry sync 후 약 340GiB
- Mac mini internal free: 약 70GiB
- T7 free: 약 891GiB

## 2. Live fleet 판정

아래 open은 `PENDING_BUY + HOLDING + PENDING_SELL + QUARANTINED`다.
시간과 시장 수는 점검 시점의 최신 성공 cycle 표본이다.

| Jenkins job | 전략/runtime | page / raw market | cumulative volume | open / max | 최신 상태 | 판정 |
|---|---|---:|---:|---:|---|---|
| `polybot-red` | Date/default, close-only | 60 / 5,916 | 0 | 0 / unlimited legacy | SUCCESS 45.4s | 신규 scan 없음; cap 문제 없음 |
| `polybot-yellow` | Cherry/default | 7 / 696 | 5,000 | 5 / 10 | SUCCESS 27.3s | HOLDING 5, pending 0; cap 여유 |
| `polybot-cherry` | Elderberry/default, close-only | 48 / 4,796 | 0 | 0 / 20 | SUCCESS 48.2s | cap 문제 없음 |
| `polybot-orange` | Cherry/default | 18 / 1,724 | 5,000 | 10 / 10 | SUCCESS 18.9s | stale 4행 복구; 현재 10건은 실제/신규 주문 |
| `polybot-eagle` | Blueberry +2pp | 61 / 6,062 | 5,000 | 0 / 10 | SUCCESS 6.5s | 정상 |
| `polybot-fox` | Blueberry +5pp | 61 / 6,062 | 5,000 | 0 / 10 | SUCCESS 6.9s | 정상 |
| `polybot-cat` | Papaya 24h | 93 / 9,202 | 1,000 | 0 / 20 | SUCCESS 10.4s | 정상 |
| `polybot-dog` | Papaya 72h | 93 / 9,202 | 1,000 | 5 / 20 | SUCCESS 12.1s | 실제 HOLDING 5; cap 아님 |
| `polybot-queen` | Queen 24h | 93 / 9,202 | 1,000 | 0 / 20 | SUCCESS 7.1s | 정상 |
| `polybot-king` | Queen 12h | 93 / 9,202 | 1,000 | 0 / 20 | SUCCESS 59.3s | 정상; 5분보다 충분히 짧음 |
| `polybot-bear` | Quince passive | 92 / 9,178 | 1,000 | 0 / 20 | SUCCESS 11.2s | 정상 |
| `polybot-eco` | Quince nearest | 92 / 9,178 | 1,000 | 0 / 20 | SUCCESS 10.3s | 정상 |
| `polybot-tiger` | Quince cross | 92 / 9,178 | 1,000 | 0 / 20 | SUCCESS 6.7s | 정상 |
| `polybot-fruit` | Melon high | 48 / 4,700 | 10,000 | 0 / 20 | SUCCESS 6.0s | 정상 |
| `polybot-lime` | Melon mid | 48 / 4,715 | 10,000 | 0 / 20 | SUCCESS 5.5s | 정상 |
| `polybot-wolf` | Melon low | 48 / 4,700 | 10,000 | 0 / 20 | SUCCESS 8.2s | 정상 |

모든 live job은 `buildable=true`이고 최신 관측 build가 성공했다. 최악의 배포 직후
refresh 표본도 75.6초로 `H/5`보다 충분히 짧다.

## 3. Broad scan 수정

### 3.1 Papaya·Queen·Quince·Melon

이전에는 동일 public Gamma sweep을 cache로 공유했지만 cache leader가 여전히 약
320 page/3.2만 market을 읽어야 했다. 서버 측 고정 universe 하한을 추가했다.

| 계열 | fixed Gamma cumulative volume | 배포 후 universe |
|---|---:|---:|
| Papaya | 1,000 | 93 page / 9,202 |
| Queen | 1,000 | 92~93 page / 9,178~9,202 |
| Quince | 1,000 | 92 page / 9,178 |
| Melon | 10,000 | 48 page / 4,700~4,715 |

이 값은 Gamma `volume_num_min`인 개설 후 누적 거래량이다. 각 전략의 client-side
`volume24hr` entry gate와 CLOB depth는 별개이며 변경하지 않았다. server filter가
membership을 바꾸므로 배포 전/후 first-crossing evidence는 같은 cohort로 합치지 않는다.

### 3.2 Cherry Yellow·Orange

두 Jenkins shell에는 cumulative-volume 환경변수가 없었고 실제 RunAudit도
`min_volume=0`이었다. 이 A/B 공통 조건을 환경변수 실수에 의존하지 않도록
Golden Cherry scanner의 fixed universe contract `$5,000`으로 넣었다.

| Job | 이전 | 이후 |
|---|---:|---:|
| Yellow | 8 page / 726 | 7 page / 696 |
| Orange | 37 page / 3,660 | 18 page / 1,724 |

Yellow와 Orange의 서로 다른 liquidity/entry band는 그대로다. 이번 변경은 두 job에
동일한 누적 거래량 universe만 적용한다.

Gamma keyset API의 `volume_num_min`과 `liquidity_num_min`은 서버가 pagination 전에
membership을 줄이는 필터다.
([공식 Gamma keyset 문서](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination))

## 4. `max_positions`와 PENDING lifecycle

### 4.1 Orange 복구

작업 전 Orange DB는 `HOLDING 9 + PENDING_BUY 1 = open 10`이었다. public wallet과
exact fill evidence로 read-only dry-run한 결과는 다음과 같았다.

- wallet에 실제 존재: 6행 유지
- confirmed fill은 있으나 wallet에 없음: 3행 `COMPLETED`
- fill도 wallet 보유도 없음: 1행 `UNFILLED`

`tools/reconcile_positions.py --execute --confirm CLOSE_4`가 자동 backup을 만든 뒤 이 4행만
종결했다. clean build나 DB 삭제는 하지 않았다. 재대사 결과는 `close=0`, open 6이었다.

active 복귀 뒤 정상 cycle이 신규 BUY를 만들면서 최종 sync에는
`HOLDING 8 + PENDING_BUY 2 = 10/10`이 기록됐다. 두 PENDING_BUY의 생성 시각은
2026-08-18 23:42:59Z 이후로 30분 TTL보다 짧았고 `PENDING_SELL=0`이었다. 이는 stale DB
오류가 아니라 설정된 실제 위험 한도이며, 주문 fill/TTL/청산에 따라 slot이 돌아온다.

### 4.2 재발 방지

Papaya·Queen·Melon에 Quince와 같은 exact pending BUY lifecycle을 이식했다.

- 주문 접수만으로 `HOLDING` 처리하지 않고 `PENDING_BUY`
- terminal zero fill은 `UNFILLED`
- TTL 뒤 authoritative cancel이 확인된 exact zero fill만 `UNFILLED`
- terminal partial/full은 confirmed 실제 shares만 활성화
- 매 cycle `PENDING_BUY/HOLDING/PENDING_SELL/total/max`를 명시적으로 로그

Quince와 Blueberry는 이미 같은 계열의 방어가 있었다. Golden Cherry도 30분 TTL과 exact
fill lifecycle을 보유한다. 현재 fleet에서 오래된 PENDING 때문에 cap을 소모하는 다른 사례는
발견하지 못했다.

## 5. Simulation·shadow·research job

| Job | 역할 | 최신 규모 | 최근 duration | cadence 판정 |
|---|---|---:|---:|---|
| `golden-pomegranate` | full public observatory | 약 21 page | 362.2s | 의도적 full census, 15분 내 |
| `polybot-kiwi-a` | simulation | 약 20 page / 1.9k | 76.5s | 5분 내 |
| `polybot-kiwi-b` | simulation | 약 20 page / 1.9k | 49.3s | 5분 내 |
| `polybot-kiwi-c` | simulation | 약 20 page / 1.9k | 34.1s | 5분 내 |
| `polybot-kiwi-d` | simulation | 약 20 page / 1.9k | 23.1s | 5분 내 |
| `polybot-shadow` | Blueberry shadow | 61 page / 6,062 | 42.5s | 5분 내 |
| `polybot-do` | Raspberry research | 약 20 page / 1.9k | 56.8s | 5분 내 |
| `polybot-re` | Raspberry research | 약 20 page / 1.9k | 45.1s | 5분 내 |
| `polybot-mi` | Raspberry research | 약 20 page / 1.9k | 50.7s | 5분 내 |
| `polybot-shadow-one` | Strawberry research | 14 page / 약 13.1k | 수정 전 26~28분, 자연 검증 7분 51초 | 10분 cadence 내; overlap 없음 |

Pomegranate와 Strawberry는 가설상 full population evidence가 필요해 live 전략처럼 volume
filter를 추가하면 안 된다. Kiwi/Raspberry/Blueberry shadow는 이미 bounded universe이며
주기 중첩이 없다.

### Strawberry 병목

Strawberry 수집 자체는 약 80~88초지만 append-only bundle을 12.6GiB DB에 atomic publish하는
시간까지 합쳐 약 8분이다. 문제는 Jenkins shell이 그 뒤 아래를 연속 실행한 점이다.

```text
polybot status  -> PRAGMA quick_check + 9개 대형 테이블 exact COUNT
polybot health  -> status를 다시 호출한 뒤 24h health query
```

두 deep check가 약 19분을 추가해 최근 10개 build가 1,594~1,667초였고 timer trigger가
2~3개씩 합쳐졌다. cycle 404의 DB publish와 `SUCCEEDED` evidence를 확인한 뒤 읽기 전용
status 단계만 중단했으며 DB 수집 결과는 보존했다. timed shell에서 status/health를 제거하고
문서 계약도 함께 수정했다. frozen 10분 cadence와 수집 population은 바꾸지 않았다.

수정 후 수동 build `#405`는 동일한 14-page population과 atomic publication을 보존한 채
492.570초(8분 13초)에 `SUCCESS`였다. 이어 `Started by timer`인 자연 build `#406`은
14 page, membership 13,096, crossing 63, executable episode 58, path 7,431,
resolution 3,997을 기록하고 470.632초(7분 51초)에 `SUCCESS`였다. 다음 10분 slot과
queue overlap은 없었다.

`#407`도 자연 timer로 시작해 486.141초(8분 6초)에 성공했다. 동기화 중 새 publication이
끼어들지 않도록 `#407` 종료 뒤 다음 slot만 잠깐 막았고, 검증 후 같은
`7-59/10 * * * *` trigger를 다시 활성화했다. 최종 26개 job snapshot은 모두
`buildable=true`, `in_queue=false`, latest `SUCCESS`다.

### Strawberry 최종 sync evidence

- remote path:
  `/Volumes/t7/jenkins/polybot-shadow-one/golden-strawberry/data/strawberry-shadow-one/trades_sim.db`
- verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-shadow-one/strategies/golden-strawberry/runtime/strawberry-shadow-one/databases/latest/trades_sim.db`
- size: 12,768,776,192 bytes
- local/remote SHA-256:
  `0945f0e64edd4e44a1f59cdb76c4ad3a75cc0212ce8ce6adb59c1ca6a2c191a7`
- source cutoff: `2026-08-19T00:25:30.238000Z`
- sync finished: `2026-08-19T00:51:48.408364Z`
- sync run: `a2108a10aa6c43ffbb9b317b2f3dc99a`, 141 transferred,
  0 skipped/failed, 12,770,320,813 bytes written
- verify: `SUCCESS`, 310 checked, retention skip 0, failed 0, conflict 0

동기화 DB의 최신 `cycle_stats`는 cycle 407이며 14 page, membership 13,064,
crossing 84, executable episode 77, path 7,503, resolution 4,038이다. sync plan을
`#407` 실행 중 고정했기 때문에 catalog console coverage는 `#406`까지지만, `#407`의
Jenkins 결과와 redacted console은 inspector로 별도 확인했고 DB source cutoff에는 cycle 407이
포함됐다.

## 6. 검증

- Papaya tests: 263 passed
- Queen tests: 293 passed
- Quince tests: 347 passed
- Melon tests: 339 passed
- 위 4개 합계: 1,242 passed; Ruff PASS
- Golden Cherry tests: 96 passed
- Golden Cherry 변경 파일 Ruff: PASS
- 저장소 strategy contract: 21 strategies PASS
- Golden Cherry 전체 Ruff는 이번 변경과 무관한 기존 오류 3건이 남아 별도로 기록했다.
- daily-rsync verify: live 16/16 SUCCESS
- Strawberry daily-rsync verify: 310 checked, 0 failed/skip/conflict
- Jenkins: live 16/16과 research/simulation 10/10 모두 enabled, latest SUCCESS

## 7. 변경 commit

- `8c48ed8` — broad scan server filter, pending BUY lifecycle, open-state logging
- `396c2a3` — Golden Cherry cumulative volume $5,000 공통 적용
- `3b80299` — Strawberry timed build에서 deep status/health 제거 계약 문서화

## 8. 운영 해석

1. Papaya·Queen·Quince·Melon과 Cherry의 2026-08-19 배포 전/후 first-crossing 자료는
   서로 다른 membership cohort로 분리한다.
2. `open == max_positions`만으로 장애라고 판단하지 않는다. PENDING age, exact order 상태,
   wallet 실보유를 함께 보고 stale일 때만 대사한다.
3. Orange는 현재 10/10이지만 실제 신규 주문이다. max를 올려 우회하지 말고 fill/TTL/SELL로
   slot이 정상 순환하는지 다음 점검에서 확인한다.
4. Strawberry의 deep integrity check는 job을 멈춘 maintenance window 또는 daily-rsync의
   verified immutable copy에서 수행한다. timed collection shell에는 다시 넣지 않는다.
5. 이 감사로 거래 빈도가 낮은 이유를 entry 조건 탓으로 확정하지 않았다. 현재는 모든
   live job이 cap/scan runtime 때문에 조용히 멈춘 상태가 아니라는 것까지만 판정한다.

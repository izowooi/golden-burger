# 016 — Golden Blueberry 저빈도 진단·공유 sweep 배포 — 2026-08-13

작성일: 2026-08-13

대상: Jenkins `polybot-eagle` / `polybot-fox`, strategy `golden-blueberry`, runtime
`blueberry-live-a-2pp` / `blueberry-live-b-5pp`

## 0. 결론

```text
Decision: INCONCLUSIVE_KEEP_PARAMETERS / OPERATIONS_FIX_DEPLOYED
Cadence: */5 * * * * 유지
Live parameter: A +2%p / B +5%p, liquidity·volume $10k, spread 0.02, $5 유지
저빈도 주원인: first-crossing·시장 quality·fresh-book gate
Jenkins 주기: 주원인 아님
성과: confirmed BUY+SELL round trip 0건 — 수익성·A/B 우열 판정 불가
운영 수정: 두 A/B job의 동일 Gamma full sweep 중복 조회 제거
배포: commit 471bf1a, main/origin-main push, final natural cycle 2/2 per arm SUCCESS
```

일주일 동안 두 팔은 같은 102개 first crossing을 관측했다. A는 정적 gate를 6건, B는
2건만 통과했고, fresh CLOB 재검증 뒤 exact confirmed BUY는 A 3건, B 1건이었다. 따라서
“Jenkins가 드물게 실행돼 신호를 놓친 것”보다 전략 정의와 실행 안전장치가 희소한 진입을
만든 것이 핵심이다.

다만 기존 구조는 같은 호스트에서 A와 B가 매 5분마다 약 300페이지의 같은 Gamma universe를
각각 다시 조회했다. 이는 신호 수를 늘리지 않으면서 runtime tail과 같은 시각의 API timeout
위험만 키웠다. 한 job만 terminal cursor까지 조회하고 다른 job이 검증된 동일 sweep을
재사용하도록 수정했다. 시장 universe와 signal/market/execution gate는 바꾸지 않았다.

현재 파라미터는 변경하지 않는다. 후보 수만 늘리는 반사실에서는 liquidity와 volume을 둘 다
`$2.5k`까지 내리면 A 6→14, B 2→6으로 늘지만, 이 시장들의 해결 결과와 실행비용 evidence가
없다. 얇은 시장을 live에 넣는 변경은 edge 확인이 아니라 새 위험을 추가하므로 새 사전 등록
cohort 없이 적용하지 않는다.

## 1. Evidence boundary와 최종 동기화

Timezone은 UTC이며 KST는 UTC+9다.

- 운영 관측 범위(성과 pooling이 아닌 descriptive 전체 DB):
  Eagle `2026-08-05T13:13:37Z`부터 source cutoff `2026-08-12T17:37:14Z`,
  Fox `2026-08-05T13:13:47Z`부터 source cutoff `2026-08-12T17:37:12Z`
- strict audit 완결 범위:
  `[2026-08-06T00:00:00Z, 2026-08-12T00:00:00Z)`
- 최종 압축-cache 배포 검증 범위:
  `[2026-08-12T17:30:14Z, 2026-08-12T17:37:14Z)`

두 job은 각각 별도 `daily-rsync sync-job`으로 DB·bot log·Jenkins console을 가져왔다.
최신 sync attempt와 latest successful sync는 동일한 SUCCESS run이고, 두 `verify` 모두
retention skip·checksum failure·artifact conflict가 0이다.

### Eagle / A +2%p

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-eagle/golden-blueberry/data/blueberry-live-a-2pp/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eagle/strategies/golden-blueberry/runtime/blueberry-live-a-2pp/databases/latest/trades.db`
- Local/remote SHA-256:
  `71e03dcc543134fcf0be0932e4c1b0bc11aa008b096fbf26934535bab5a10b39`
- Sync run: `5f19f542f4064f67a01fa6629808b776`, SUCCESS
- Sync finished: `2026-08-12T17:39:20.749364Z`
- DB synced at: `2026-08-12T17:39:19.036840Z`
- Source cutoff/mtime: `2026-08-12T17:37:14.265107Z`
- Verify: SUCCESS, 2,138 checked
- SQLite `quick_check`: `ok`

### Fox / B +5%p

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-fox/golden-blueberry/data/blueberry-live-b-5pp/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-fox/strategies/golden-blueberry/runtime/blueberry-live-b-5pp/databases/latest/trades.db`
- Local/remote SHA-256:
  `f362fc645140ebf491d39335011edf0cd88fc4be3850f8f9ff92dd5abf6d0cbc`
- Sync run: `8f46087870ee45ea8355f20892854fbb`, SUCCESS
- Sync finished: `2026-08-12T17:39:39.425525Z`
- DB synced at: `2026-08-12T17:39:37.864288Z`
- Source cutoff/mtime: `2026-08-12T17:37:12.914655Z`
- Verify: SUCCESS, 2,139 checked
- SQLite `quick_check`: `ok`

## 2. Jenkins 실제 구성

`inspect-jenkins-job`으로 익명 read-only 재조회한 결과다.

| Job | Runtime / arm | TimerTrigger | Concurrent | Config SHA-256 | 최종 검증 build |
|---|---|---|---|---|---|
| `polybot-eagle` | `blueberry-live-a-2pp` / +2%p | `*/5 * * * *` | false | `48563ba6d598…` | `#7341 SUCCESS` |
| `polybot-fox` | `blueberry-live-b-5pp` / +5%p | `*/5 * * * *` | false | `d029a02085ee…` | `#9136 SUCCESS` |

두 job은 enabled/live/active이고 `$5`, capital `$150`, liquidity/volume `$10k`,
entry `[0.85,0.93]`, horizon 72h, spread `0.02`, target/stop `0.97/0.78`이 같다.
처치축은 `min_surge=0.02/0.05` 하나다. `*/5`는 두 팔을 같은 wall-clock 경계에
맞추며, `concurrentBuild=false`라 같은 job의 중첩 build는 없다.

anonymous config write는 HTTP 403으로 거부되어 Jenkins XML은 바뀌지 않았다. 대신 표준
Jenkins가 제공하는 `JENKINS_URL`을 코드가 감지해 실행 사용자 홈의 공유 cache를 자동으로
선택하도록 했다. 두 config SHA가 그대로인 상태에서 실제 동작을 확인했다.

shell에는 credential이 inline으로 존재하고 config가 anonymous read 가능하다는 별도 보안
finding이 있다. 값은 이 문서와 출력에 남기지 않았다. 이번 저빈도의 원인은 아니다.

로그의 `POST /auth/api-key` 400은 즉시 `GET /auth/derive-api-key` 200으로 복구되고
authenticated client와 order 조회가 정상화된다. 이것도 주문 부재의 원인이 아니다.

## 3. 두 팔이 같은 시장을 관측했는가

- 각 DB의 first-crossing decision: 102건
- condition ID 집합: 102/102 완전 동일
- 같은 condition의 prior/current/surge: 102/102 완전 동일
- paired successful sweep: 1,967쌍
- membership digest 동일: 1,958쌍
- sweep 시작시각 차이: 평균 0.32초, 최대 15.71초

9개 digest 차이는 API 응답이 움직이는 동안 두 process가 독립 full sweep을 하던 기존 구조의
시간차다. 그럼에도 실제 first crossing은 완전히 같았다. 따라서 B에서 position이 더 적은
것은 universe 누락보다 사전 등록된 +5%p gate가 의도대로 더 엄격하기 때문이다.

## 4. first-crossing → BUY 퍼널

DB 전체 first-crossing 102건을 각 arm의 순차 gate로 분류했다.

| Decision reason | Eagle A +2%p | Fox B +5%p |
|---|---:|---:|
| `signal_and_metadata_gates_passed` | 6 | 2 |
| `low_liquidity` | 37 | 25 |
| `low_volume` | 5 | 1 |
| `price_out_of_band` | 27 | 27 |
| `too_early` | 20 | 20 |
| `surge_below_min` | 7 | 27 |
| 합계 | 102 | 102 |

순차 reason은 “그 행에서 처음 만난 실패 원인”이다. 예를 들어 `low_liquidity` 37건을
모두 liquidity 하나만 낮추면 후보가 된다는 뜻이 아니다. 뒤의 volume/time/price gate도
동시에 통과해야 한다.

### A의 정적 후보 6건과 fresh-book 결과

| Crossing UTC | Surge | Fresh CLOB 결과 |
|---|---:|---|
| 2026-08-05 14:13 | +6.0%p | confirmed BUY |
| 2026-08-05 19:19 | +2.0%p | confirmed BUY |
| 2026-08-05 19:31 | +3.5%p | spread 0.05 > 0.02, 주문 없음 |
| 2026-08-06 14:43 | +3.0%p | fresh price 0.945 > 0.93, 주문 없음 |
| 2026-08-07 08:46 | +4.0%p | confirmed BUY |
| 2026-08-07 12:15 | +7.5%p | spread 0.03 > 0.02, 주문 없음 |

B의 두 후보는 위 +6.0%p와 +7.5%p event다. 전자는 confirmed BUY, 후자는 같은 spread
0.03 거절이었다. 따라서 두 팔 모두 정적 후보→confirmed BUY 전환율은 50%다.
spread 0.03/0.05나 fresh 0.945를 허용하면 거래는 늘지만, stale Gamma signal 뒤 비싼
fresh ask를 추격하지 않도록 만든 실행 안전장치를 없애는 변경이다.

## 5. 거래와 수익성

| 지표 | Eagle A | Fox B |
|---|---:|---:|
| Trades | 3 | 1 |
| Exact CONFIRMED BUY | 3 | 1 |
| Current status `RESOLVED` | 3 | 1 |
| Open/stuck exposure | 0 | 0 |
| Confirmed BUY+SELL round trip | **0** | **0** |
| Gross resolution settlement assumption | +$2.0307 | +$0.4887 |

네 BUY는 모두 TAKER exact fill이고 해결 outcome은 Yes다. 하지만 CLOB confirmed SELL,
redeem, 완전한 fee evidence가 없으므로 위 settlement assumption을 realized/net P&L로
간주하지 않는다. 거래가 적더라도 “현재까지 4전 4승”으로 전략을 승격하거나 +2%p arm이
우월하다고 말할 수 없다. 네 BUY 중 양 팔의 한 쌍은 같은 underlying event에 대한 중복
노출이고 전체 표본도 작으며, 사전 등록된 최소 confirmed closed 20건/arm보다 크게 부족하다.

## 6. Cadence와 runtime

운영 시작부터 최종 cutoff까지의 RunAudit 기준이다.

| 지표 | Eagle | Fox |
|---|---:|---:|
| Total / SUCCESS / FAILED | 1,970 / 1,967 / 3 | 1,970 / 1,967 / 3 |
| Runtime mean | 175.3s | 175.3s |
| Runtime p50 / p95 | 116.4s / 383.2s | 116.1s / 382.8s |
| Runtime >5m / >10m | 292 / 21 | 290 / 21 |
| Start gap p50 / p95 | 300.1s / 388.7s | 300.1s / 389.1s |
| Gap >7.5m / >12.5m | 52 / 11 | 52 / 11 |
| Max start gap | 45.62m | 45.48m |

start gap의 97.3%가 7.5분 이내이고 strict audit의 5분 snapshot cadence coverage도
양쪽 93.9%다. 5분 주기가 너무 넓다는 증거가 없다. H/3 또는 H/2로 줄이면 p95 runtime보다
짧아 queue를 늘리고 Gamma 부하를 키운다.

FAILED 3건은 양쪽 같은 시각의 upstream 실패다.

- 2026-08-05T14:40Z: `ChunkedEncodingError`
- 2026-08-11T13:00Z: Gamma `ReadTimeout`
- 2026-08-11T13:06Z: Gamma `ReadTimeout`

즉 스케줄을 더 촘촘하게 만드는 것보다 중복 full sweep과 runtime tail을 줄이는 것이 먼저다.

## 7. 파라미터 민감도 — 진단용, tuning 근거 아님

102개 실제 first crossing의 저장된 price/time/liquidity/volume을 사용해 정적 gate만 다시
계산했다. fresh book 체결 가능성과 해결 outcome은 포함하지 않은 **상한 후보 수**다.

| Min liquidity / min volume24h | +2%p arm | +5%p arm |
|---|---:|---:|
| `$10k / $10k` 현재값 | 6 | 2 |
| `$5k / $10k` | 6 | 2 |
| `$10k / $5k` | 8 | 2 |
| `$5k / $5k` | 8 | 2 |
| `$2.5k / $2.5k` | 14 | 6 |

현재 `$10k/$10k`를 유지한 min-surge 민감도는 0~1%p 7건, 2%p 6건, 3%p 5건,
4%p 3건, 5%p 2건이다. A의 2%p를 더 낮춰도 고작 한 건 늘기 때문에 surge 완화는
저빈도 해결책이 아니다. horizon 72→120/168h 또는 price max 0.93→0.95를 각각 단독으로
바꿔도 현재 quality gate 아래 후보 수는 6/2에서 늘지 않았다.

둘 다 `$2.5k`로 낮추는 경우만 눈에 띄게 늘지만 다음 이유로 지금 적용하지 않는다.

1. lower-quality 시장의 해결 성과와 fee-complete execution evidence가 없다.
2. `max_spread=0.02`라 실제 주문 전환은 정적 후보 수보다 더 적을 수 있다.
3. first-week checkpoint에서는 winner 선택과 threshold 변경을 금지했다.
4. source/config cohort가 이미 여러 개라 현재 strict 성과 비교가 불가능하다.

향후 빈도가 계속 부족하면 live gate를 즉시 낮추지 말고 shadow에서 liquidity/volume grid의
resolved outcome과 executable spread/depth를 먼저 수집한 뒤 새 cohort를 사전 등록한다.

## 8. Strict audit와 성과 판정 제한

최종 verified DB로 다음 audit을 다시 실행했다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db <eagle-verified-db> --db <fox-verified-db> \
  --days 6 --as-of 2026-08-11 \
  --output-dir daily-rsync/data/analysis/blueberry-ab-20260806-20260812/audit \
  --strict
```

결과는 exit 1, HIGH 2 / MEDIUM 2다.

- 각 DB HIGH `failed_runs`: strict 범위에 같은 Gamma timeout 2건
- 각 DB MEDIUM `logs_missing`: audit CLI가 daily-rsync의 별도 log root를 자동 연결하지
  못한 항목. 실제 bot/console log는 catalog에서 동기화·verify됐고 수동 진단에 사용했다.
- strict 범위의 각 arm은 config/source lifecycle 변화로 cohort 4개다.
- A/B analyzer 상태: `NOT_EVALUABLE_EVIDENCE_CONTRACT`
- confirmed closed round trip: 0/0

따라서 Evidence Contract상 수익 비교, winner 선택, parameter tuning을 중단한다. HIGH는
“봇이 계속 고장 나 있었다”는 뜻이 아니라 완결 구간에 두 번의 실패 sweep이 있어 경제적
판정을 위한 무결점 표본이 아니라는 뜻이다.

## 9. 코드 수정

market universe를 1/5로 임의 축소하거나 `end_date_max`를 넣지 않았다. 스포츠는 가까운
`gameStartTime`을 entry clock으로 쓰면서 Gamma `endDate`는 더 멀 수 있어, end date로
server-side 축소하면 유효 in-play/pregame 시장을 조용히 누락할 수 있기 때문이다.

대신 두 팔이 원래부터 같은 full universe를 필요로 한다는 점을 이용했다.

1. 동일 호스트·동일 5분 bucket·동일 filter에서 process 하나만 Gamma keyset terminal
   cursor까지 조회한다.
2. follower는 owner-private `fcntl` lock에서 기다린 뒤 cursor-complete, filter identity,
   market ID 집합, qualified membership SHA-256을 모두 재검증한다.
3. follower는 자기 `sweep_id`를 만들되 공통 `source_sweep_id`를 RunAudit에 남긴다.
4. cache는 owner-only directory/file mode `700/600`, symlink 거부, 최대 크기,
   atomic temp+fsync+replace, 12분 lock timeout으로 fail closed한다.
5. 현재 bucket 하나만 gzip level 1로 보존하고 filter별 고정 lock을 사용한다.
6. Jenkins에서는 env가 없어도 `JENKINS_URL`을 감지해
   `~/.cache/golden-blueberry/gamma-sweeps-v1`을 사용한다. `off`로 명시하면 비활성화된다.
7. leader의 page interval은 0.1초라 순차 상한 10 request/s다. 공식 `/markets` 한도
   300 requests/10s보다 낮다. 참고:
   [Polymarket keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination),
   [Polymarket rate limits](https://docs.polymarket.com/api-reference/rate-limits).

관련 commit은 모두 `main`과 `origin/main`에 push했다.

- `260baab` — Gamma full sweep 공유와 검증 계약
- `d21bd9f` — Jenkins owner-home cache 자동 활성화
- `471bf1a` — gzip 단일 bucket·고정 lock으로 SSD 쓰기량 절감

검증 결과:

```text
golden-blueberry pytest: 351 passed
19-project strategy contract verifier: PASS
git diff --check: PASS
```

## 10. 실제 Jenkins 배포 검증

첫 shared-cache 버전부터 총 5개 자연 cycle/arm이 모두 SUCCESS였다. 최종 압축 버전의 두
cycle에서는 leader가 실제로 교대했다.

| Bucket / UTC | Remote leader | Eagle | Fox | 공통 evidence |
|---|---|---:|---:|---|
| 5955186 / 17:30 | Eagle | 72.4s / cache miss | 73.9s / cache hit | source `0f521190…`, digest `ecd506d6…` |
| 5955187 / 17:35 | Fox | 119.7s / cache hit | 118.2s / cache miss | source `4b49b16b…`, digest `38ebdf82…` |

두 cycle 모두 cursor-complete, 동일 qualified market count, 동일 membership digest,
RunAudit SUCCESS, `Finished: SUCCESS`다. 어느 job이 먼저 lock을 얻어도 작동한다.

- 배포 전 평균 runtime: 약 175초/arm, p95 약 383초
- shared-cache 배포 후 관측 5 cycle: 모두 SUCCESS, arm별 max 약 120초
- cache footprint: raw JSON 3개 약 482MB → gzip current bucket 약 20MB
- 최종 cache directory/file: owner-only `700/600`
- 이전 버전의 public-data JSON은 현재 bucket prune으로 교체됐고, 남은 0-byte legacy lock
  3개는 잠금 중이 아님과 inode/size를 확인한 뒤 삭제했다. 거래 DB와 bot/Jenkins log는
  삭제하지 않았다.

짧은 배포 표본이므로 장기 p95 개선을 확정하지는 않는다. 다만 원격 Gamma full sweep이
bucket당 두 번에서 한 번으로 줄고, 두 팔이 정확히 같은 source sweep을 썼다는 것은 로그와
DB에서 직접 확인했다.

## 11. 새 cohort와 다음 점검

source code digest가 바뀌었으므로 계약상 경제적 평가는 다음 새 cohort부터 분리한다. DB를
clean하지 않았고 과거 evidence는 보존한다.

| Arm | Start UTC | KST | Config hash | Source digest |
|---|---|---|---|---|
| A +2%p | `2026-08-12T17:30:14.053866Z` | 2026-08-13 02:30:14 | `589bbbd56a7a…` | `069f092ee63a…` |
| B +5%p | `2026-08-12T17:30:14.057747Z` | 2026-08-13 02:30:14 | `3fa2f66f1d23…` | `069f092ee63a…` |

현재 사용자가 Jenkins에서 추가로 바꿀 항목은 없다.

1. `*/5 * * * *`, concurrent false, `$5`, +2/+5%p, liquidity/volume `$10k`, spread
   `0.02`를 유지한다.
2. 2026-08-20 02:31 KST 이후 day-7에는 success/cursor/cache/cadence/fill lifecycle만
   점검하고 P&L winner나 parameter를 고르지 않는다.
3. 2026-09-12 02:31 KST 이후 30일 strict review를 수행한다.
4. 그때도 arm당 fee-complete confirmed closed `<20`이면 `INCONCLUSIVE`로 기간을
   연장한다. 현재 cohort 중간에 threshold를 낮추지 않는다.
5. shared cache hit pair, Gamma FAILED run, start-gap >15m, confirmed round trip과 fee
   coverage를 함께 확인한다.

재점검 요청 예시:

```text
Golden Blueberry의 Eagle/Fox를 다시 동기화해주세요.
2026-08-13 02:30 KST 이후 source digest 069f092e cohort만 사용해서
shared sweep pair, cadence, first-crossing funnel, confirmed round trip과 fee coverage를
strict audit하고 A/B를 평가해주세요.
```

# 030 — Golden Pomegranate 12일 수집 건강성과 전략 탐색 — 2026-08-19

작성일: 2026-08-19 KST

## 0. 결론

`golden-pomegranate`는 돈을 거래하는 전략이 아니라 공개 시장 원자료를 모으는
accountless research collector다. 현재 Jenkins shell은 실제 Gamma/CLOB/Data API를 읽지만
wallet·주문·포지션·P&L은 만들지 않는다.

검증 범위는 첫 partial day를 제외한 다음 12개 완전 UTC day다.

```text
[2026-08-07T00:00:00Z, 2026-08-19T00:00:00Z)
```

이 범위의 일별 shard 12개는 `daily-rsync verify`에서 12/12 `SUCCESS`, SQLite
`quick_check=ok`, SHA-256 일치, 날짜 누락 0으로 확인됐다. Gamma census와 membership은
좋지만 전체 수집 건강성 gate는 실패다.

- Gamma: 1,150/1,150 cursor-complete, 3,311,865 market membership과 observation이 정확히
  일치하고 missing ID·duplicate는 0
- CLOB: 138,086 token 시도 중 snapshot 138,074건, 1,145 cycle 성공·5 cycle partial
- Data API trade tape: 1,150/1,150 cycle gap, normalized trade 0건
- resolution: 1,150/1,150 cycle gap, 8월 18일 watchlist 24,907건, terminal 0건
- cadence: 예상 1,152 slot 중 성공 1,150(99.83%), p50 15.004분, p95 15.358분,
  collector runtime p95 74.88초·최대 277.25초

따라서 15분 cadence는 유지한다. runtime과 disk는 10분도 감당하지만 preregistration의
data-health gate를 통과하지 못했으므로 10분으로 올리지 않는다.

탐색 구간에서 강했던 10–20¢ 스포츠 underdog 후보를 미사용 6일 holdout에 그대로 적용한
결과는 기각됐다. 반면 사전 대조군이던 20–30¢ 구간은 양쪽 기간에서 방향이 같았지만,
resolution label coverage와 exact CLOB 표본이 부족하고 holdout을 본 뒤 선택한 값이므로
검증된 전략이 아니다.

현재 만들 수 있는 최선의 결과는 다음 **전향적 simulation 가설**이다.

> 스포츠 표준 이진 시장이 종료 6시간 이내로 처음 들어올 때, 정확한 CLOB ask가
> 20–30¢인 underdog을 가상 $5 taker 매수하고 resolution까지 보유한다.

사용자 화면 이름은 `경기 직전 저평가 언더독`, 내부 과일명 후보는
`golden-tangerine`으로 한다. 이것은 수익전략 확정이 아니라 앞으로 30일을 새로 모아
기각할 대상이다. 현재 자료로 live bot을 만들거나 자금을 배정하지 않는다.

## 1. Jenkins shell의 의미

현재 job `golden-pomegranate` 구성 SHA-256은
`2f7b70cb300aaf5a7073311661e1337491aba17ba2c2232be02c363430cc46d9`이고,
timer는 `H/15 * * * *`, external workspace는
`/Volumes/t7/jenkins/golden-pomegranate`다. `concurrentBuild=false`라 한 DB에 writer가
겹치지 않는다.

| 명령 | 의미 |
|---|---|
| `uv sync --frozen` | lockfile 그대로 실행환경을 맞춘다. dependency 해석을 새로 하지 않는다. |
| `polybot config --simulate` | resolved config, simulation-only·archive-only·경로·credential 부재를 검증한다. |
| 첫 `polybot health` | API 호출 전에 mount, 현재 UTC shard, WAL, append-only trigger, quick check, disk guard를 읽기 전용 검사한다. |
| `polybot run --simulate` | 공개 Gamma census, 회전 CLOB book sample, Data API 시도, resolution follow-up을 한 cycle 수집한다. 실제 돈이나 주문은 없다. |
| `polybot status` | 현재 shard·archive·component·storage 상태를 요약한다. |
| 마지막 `polybot health` | 수집 후 DB와 storage가 여전히 정상인지 다시 검사한다. |

여기서 `--simulate`는 가짜 가격을 만든다는 뜻이 아니다. 실제 공개 시장 응답을 저장하되
credential·wallet·order path가 source-level로 금지된다는 뜻이다.

최신 Jenkins build `#1206`은 `SUCCESS`, 전체 shell duration은 140.803초였다. pre/post
`health`가 출력한 `healthy=true`는 물리 DB·storage 건강성이다. 아래 Data API와 resolution의
경제 데이터 완전성까지 성공했다는 뜻은 아니다.

## 2. Evidence provenance

- source: `macmini-m5`
- Jenkins job: `golden-pomegranate`
- strategy/runtime: `golden-pomegranate × pomegranate-15m-v2`
- mode/contract: `sim × research-full-v1`
- cohort:
  - config hash: `0b04b323431661c54a8d5a39b335eab2a7e6ed3cf51169f5ff62fff0abfd925a`
  - strategy source digest:
    `bd9c5ebc288318c4e531051e48d199359dc267b02f9b5cef8bbcf6fd7d46710e`
- remote DB root:
  `/Volumes/t7/jenkins/golden-pomegranate/golden-pomegranate/data/pomegranate-15m-v2`
- verified local DB root:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/golden-pomegranate/strategies/golden-pomegranate/runtime/pomegranate-15m-v2/databases/research/2026/08`
- initial sync run: `c326b7f2e53d45e4a78b92ba3f82cd1f`은 현재 cohort와 무관한
  pre-contract `pomegranate-local/trades_sim.db` 하나 때문에 `PARTIAL`
- corrected latest sync run: `3555ab97ae7644aaa66ddd067acdc3a0`, `SUCCESS`,
  finished `2026-08-19T11:08:24.286658Z`, failed 0
- verify: 12 checked, 0 failed, 12/12 archive coverage complete
- latest requested source cutoff: `2026-08-19T00:03:02.840977Z`

날짜별 local file은 위 local root 아래
`<DD>/trades_sim_202608<DD>.db`다.

| UTC date | local/remote SHA-256 | source completed | synced at |
|---|---|---|---|
| 08-07 | `c211e0a20955b4615dc7f0e6ddb307da6d25fa5adf911f9b8a73989394c453a9` | 08-08 00:12:37Z | 08-19 10:20:25Z |
| 08-08 | `293b6136ef271abe332f4ebde1c548c52c6358858bb5d9fb3a6cd781ff67e26f` | 08-09 00:12:59Z | 08-19 10:23:59Z |
| 08-09 | `e70ee1baf32b4b5c9f645bf04dc399cd6315103df13364ef62603b3bb29e6ce2` | 08-10 00:12:56Z | 08-19 10:27:14Z |
| 08-10 | `81825747cddb9ec2672a8ebd9b13ecfe1d779f269316594a6c22b2f7b7967cac` | 08-11 00:12:49Z | 08-19 10:30:06Z |
| 08-11 | `b3ca9787c78b3898e1916f992ba98af59b6092d3f7b6f50f984d00aaf452f985` | 08-12 00:12:55Z | 08-19 10:32:52Z |
| 08-12 | `50e7dd1e9b86ea3ad4fa213b6ccde41136852d83555e542501b2e9e7d5f423c9` | 08-13 00:13:02Z | 08-19 10:35:58Z |
| 08-13 | `38a586e63be3d9aa3eccd210114ed41231708dcd74a5579ece8878811341191f` | 08-14 00:12:46Z | 08-19 10:39:03Z |
| 08-14 | `105e1faaa262e3cf3c99db62afb1d51cd1b896fa897b3068e26262a1b6c66725` | 08-15 00:13:36Z | 08-19 10:42:27Z |
| 08-15 | `9ee66a22e51837a6f9ac02031b18dc84e18df3314ccdc745060e2b361ec25b53` | 08-16 00:13:12Z | 08-19 10:46:19Z |
| 08-16 | `14bd4fb0a29745916980630edf4d0197b6ca8d06b0e302944347396fc8375bf2` | 08-17 00:13:05Z | 08-19 10:50:01Z |
| 08-17 | `1140e7b25951c5bdb4e0e982e51511c706e42006f3bfe1eec98e0dae83e41d99` | 08-18 00:17:26Z | 08-19 10:54:30Z |
| 08-18 | `c3faab70bd3dc02326e05fecf5ba635cc0e3a0e555ca85d8d4310846b2b0a63b` | 08-19 00:03:02Z | 08-19 10:57:51Z |

## 3. Collection health

### 3.1 cadence와 DB

| 지표 | 결과 | 판정 |
|---|---:|---|
| expected 15-minute slots | 1,152 | 기준 |
| STARTED / SUCCEEDED / sweep | 1,150 / 1,150 / 1,150 | 99.83%, prereg 95% gate 통과 |
| cadence p50 / p95 / max | 15.004m / 15.358m / 29.992m | 장기 overlap 없음; 1회 slot gap |
| collector runtime p50 / p95 / max | 29.06s / 74.88s / 277.25s | 15분보다 충분히 짧음 |
| mixed cohort | 0 | 통과 |
| SQLite quick check | 12/12 ok | 통과 |
| append-only triggers | 각 shard 46 | 통과 |

8월 16일과 18일은 95 cycle, 나머지 10일은 96 cycle이다. 최대 간격 한 건은
8월 16일 03:27:19Z→03:57:19Z의 29.992분이다.

### 3.2 source별 판정

| component | 결과 | 판정 |
|---|---|---|
| Gamma census | 1,150 SUCCESS, 33,682 page, 3,311,865 market | 통과 |
| Gamma membership | membership=observation=unique 3,311,865, missing/duplicate 0 | 통과 |
| outcome | 6,623,730 observation | binary pair coverage와 일치 |
| CLOB rotation | 69,043 market selection, 138,086 token attempt, 138,074 snapshot | 1,145 SUCCESS·5 PARTIAL, 명시적 gap 5 |
| public Data API | 1,066 POSSIBLE_GAP + 84 ERROR, normalized trade 0 | 실패, HIGH 1,150 |
| resolution watcher | 1,143 PARTIAL + 7 ERROR, terminal 0 | 실패, MEDIUM 1,150 |

Data API 실패 원인은 명확하다. collector는 `/trades?start=...&end=...`를 요청했지만 공식
Data API `/trades` query contract에는 시간 범위 파라미터가 없고 `limit/offset`은 각각
10,000 cap이다. endpoint가 최신 10,000행을 돌려주자 collector는 요청 범위 밖임을 감지해
정규화 행을 저장하지 않고 watermark를 전진시키지 않았다. 즉 잘못된 trade를 정상 자료로
받아들이지는 않았지만, 12일 내내 trade tape는 0%다.

공식 계약: [Data API Get trades](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets).

resolution은 `closed=true AND one_hot=true` 결과를 15,156 condition에서 실제 관측했다.
하지만 terminal 판정이 Gamma 응답에 대부분 없는 `redeemable=true`까지 요구해 모든 row가
watchlist에 남았다. 여기에 closed-only lookup이 아직 닫히지 않은 disappeared condition을
`MISSING`으로 기록하면서 8월 18일 backlog가 24,907까지 늘었다. 공식 resolution 설명상
시장 해결 후 winning token은 $1, losing token은 $0이며, `closed + one-hot outcome`과
redeemability를 별도 상태로 보존해야 한다.

공식 계약: [Polymarket Resolution](https://docs.polymarket.com/concepts/resolution).

### 3.3 저장공간

- 12개 완전 shard: 23,568,228,352 bytes = 21.95 GiB
- 실측 평균: 1.83 GiB/day
- 단순 30일 전망: 약 54.9 GiB
- 단순 120일 전망: 약 219.5 GiB
- Jenkins #1206 시점 T7 free: 953,256,230,912 bytes, collector forecast stop까지 약 476일
- MacBook sync 후 local Pomegranate: 약 22 GiB, local free 약 318 GiB

현재 저장공간은 안전하다. Data API bounds-violation raw는 8월 18일 약 90 MB compressed라
없애면 일부 절감되지만, 하루 약 224 MB의 Gamma raw payload와 normalized tables가 더 크다.

## 4. 누수 없는 후보 검증

분할과 규칙은 holdout 결과를 보기 전에
`golden-pomegranate/research/2026-08-19-late-underdog-holdout-preregistration.md`에 고정했다.

- train: `[2026-08-07T00:00:00Z, 2026-08-13T00:00:00Z)`
- holdout: `[2026-08-13T00:00:00Z, 2026-08-19T00:00:00Z)`
- sports tag, active/open/orderbook/accepting-orders, binary outcome
- 같은 condition의 관측이 `>6h`에서 `(0h,6h]`로 처음 교차
- end date 동일, 관측 간격 5~30분, Gamma spread `<=3¢`
- Pomegranate envelope: liquidity `>=10,000`, cumulative volume `>=2,000`
- underdog proxy ask 10–20¢가 primary, 20–30¢가 사전 adjacent control
- resolution까지 보유, TP/SL 없음
- holdout의 train event overlap 제거; 실제 overlap은 0 event

분석기는 `golden-pomegranate/scripts/evaluate_late_underdog.py`로 남겼다.

### 4.1 사전 primary 10–20¢

| 지표 | train | untouched holdout |
|---|---:|---:|
| signals / labeled / label coverage | 113 / 77 / 68.14% | 147 / 101 / 68.71% |
| labeled events | 66 | 90 |
| mean proxy ask | 14.67¢ | 14.77¢ |
| underdog win rate | 24.68% | 14.85% |
| mean edge | +10.01pp | **+0.08pp** |
| event-equal gross ROI | +52.09% | **−0.20%** |
| sports taker fee 적용 | +48.32% | **−2.67%** |
| fee + 1¢ adverse | +39.17% | **−8.68%** |
| event-cluster edge 95% CI | [−0.18pp, +19.23pp] | [−6.29pp, +8.02pp] |

holdout 방향성·80% label coverage·1¢ stress gate를 모두 실패했다. train의 큰 숫자는
짧은 표본과 선택 과정의 낙관 편향이었다. 이 규칙은 구현하지 않는다.

### 4.2 사전 control 20–30¢

| 지표 | train | holdout |
|---|---:|---:|
| signals / labeled / label coverage | 128 / 97 / 75.78% | 156 / 93 / 59.62% |
| labeled events | 91 | 84 |
| mean proxy ask | 24.72¢ | 25.60¢ |
| underdog win rate | 26.80% | 30.11% |
| mean edge | +2.08pp | +4.51pp |
| event-equal gross ROI | +5.80% | +16.50% |
| sports taker fee 적용 | +3.45% | +13.95% |
| fee + 1¢ adverse | **−0.65%** | +9.64% |
| event-cluster edge 95% CI | [−7.54pp, +10.52pp] | [−5.32pp, +13.90pp] |

두 기간의 raw 방향은 같지만 다음 이유로 수익성 증거가 아니다.

1. 이 band를 새 primary로 고르는 행위 자체가 holdout을 본 뒤의 선택이다.
2. 두 기간 모두 CI가 0을 크게 포함한다.
3. label coverage가 80%보다 낮고 오래된 train도 75.78%뿐이라 right censoring만으로 설명되지
   않는다.
4. Gamma proxy가 아닌 같은 cycle exact CLOB ask 표본은 train 2건, holdout 4건뿐이다.
   holdout exact subset은 4건 모두에서 edge −1.25pp, ROI −7.41%지만 표본이 너무 작다.
5. 스포츠 taker fee는 시장별로 확인해야 한다. 공식 현재 식은
   `shares × 0.03 × p × (1-p)`이며 maker fee는 0이다.

공식 계약: [Polymarket Fees](https://docs.polymarket.com/trading/fees),
[batch order books](https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body).

## 5. 전향적 전략 설계

### 5.1 이름과 가설

- 과일 코드네임 후보: `golden-tangerine`
- 사용자 표시명: `경기 직전 저평가 언더독`
- 가설: 종료가 가까운 스포츠에서 favorite 선호가 과도해지면 20–30¢ underdog이 실제 승률보다
  낮게 거래된다.
- 강한 대안 설명: Gamma quote가 stale하고, scheduled end가 실제 game state와 어긋나며,
  unresolved sample이 선택적으로 빠져 보이는 착시다.

### 5.2 권장 paired A/B

별도 Jenkins 두 개가 서로 다른 시각에 시장을 보는 것보다 **한 collector가 같은 Gamma sweep과
CLOB batch에서 두 arm을 동시에 기록**하는 편이 낫다.

| arm | 규칙 | 역할 |
|---|---|---|
| A | exact underdog ask `[0.20,0.30)` | 새 prospective candidate |
| B | exact underdog ask `[0.10,0.20)` | 사전 falsification/negative control |

공통 조건:

- 5분 cadence; 첫 6시간 교차 시점을 15분보다 정확히 고정
- sports tag, 표준 binary, active/not-closed/orderbook/accepting-orders
- cumulative volume `>=2,000`, liquidity `>=10,000`, spread `<=3¢`
- 신호 순간 두 token의 CLOB full top/depth를 직접 batch 조회
- top ask에 $5가 실제로 소화되는 depth가 있을 때만 virtual fill
- per-market `feesEnabled`와 fee schedule을 저장하고 taker fee 포함
- virtual $5, resolution까지 보유, TP/SL 없음
- condition당 한 번, event를 독립 분석 단위로 사용
- credential 주입·`--live`·order client를 source-level hard fail

### 5.3 기간과 gate

- 첫 24시간: cadence, Gamma/CLOB pair, exact quote/depth, DB integrity만 검사
- 7일: collection health만 검사, 수익성으로 band를 바꾸지 않음
- 30일: 첫 전략 판정
- 30일 판정 전에 A/B, window, price band, volume/liquidity, exit을 변경하지 않음

30일 승격 gate:

- matured signal resolution coverage `>=90%`
- A arm labeled `>=300`, independent event `>=200`
- exact CLOB/depth/fee metadata coverage `100%`
- A의 event-equal fee-net ROI `>0`
- fee + 1¢ adverse event-equal ROI `>0`
- event-cluster 95% CI lower bound `>0`
- A가 B보다 event-cluster 기준 우월

하나라도 실패하면 폐쇄한다. 통과해도 바로 live가 아니라 별도 small-live review가 필요하다.

## 6. 다음 구현 순서

이번 결과만으로 새 `golden-*` 실행 폴더를 만들지는 않았다. 사전 primary가 holdout에서
실패했는데 자동매매 프로젝트를 먼저 만드는 것은 새 전략 플레이북의 gate 위반이기 때문이다.

다음 구현 요청에서는 아래를 한 변경으로 처리한다.

1. `golden-tangerine` accountless paired simulation collector 구현
2. A/B가 같은 sweep/book을 공유하는 단일 SQLite schema와 analyzer 구현
3. external workspace용 Jenkins shell·5분 timer 제시
4. 첫 build와 자연 timer build 검증
5. `daily-rsync` routing과 24h/7d/30d 프롬프트 등록

Pomegranate 자체는 별도 cohort migration이 필요하다.

- `pomegranate-15m-v3`에서 unsupported global Data API time-window 가정을 제거하거나 source를
  정식 교체
- resolution terminal을 `closed + one-hot`으로 판정하고 redeemability를 별도 상태로 유지
- closed-only miss와 still-open condition을 구분하는 lookup
- 기존 v2 shard를 수정하지 않고 보존

이 변경은 수집 lineage를 바꾸므로 현재 v2 DB에 in-place 적용하지 않는다.

## 7. 검증 명령

```bash
cd daily-rsync
uv run daily-rsync verify \
  --job golden-pomegranate \
  --strategy golden-pomegranate \
  --from-date 2026-08-07 \
  --to-date 2026-08-18

cd ../golden-pomegranate
uv run pytest tests/test_late_underdog_analysis.py
uv run ruff check scripts/evaluate_late_underdog.py \
  tests/test_late_underdog_analysis.py
```

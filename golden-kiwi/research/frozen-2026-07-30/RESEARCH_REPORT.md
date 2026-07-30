# Golden Kiwi 후보 연구 — Micro-Cascade

## 결론

추천 후보명은 **`golden-kiwi` / Micro-Cascade**다. 다만 추천 상태는
**research-only / shadow-only**이며, live 전략으로는 추천하지 않는다.

사전에 고정한 primary Arm B는 strict event-purged OOS에서 신호가 **1건 / event
cluster 1개**뿐이었다. 관측된 top-of-book 60분 return은 +0.1355%(+13.55 bps),
추가 10.4 bps cost stress 후 +0.0315%(+3.15 bps)였지만, 단일 event라 clustered
95% CI와 Bonferroni 98.75% CI는 **추정 불가**다. 최소 50 signals / 30 event
clusters gate를 크게 못 채웠고 OOS 전반부에는 신호가 없었다.

더 느슨하게 event를 purge하지 않은 OOS에서는 Arm B가 2건 / 2 events였고,
event-equal top-of-book return은 **-1.8072%**, 95% 및 98.75% bootstrap 범위는
**[-3.7500%, +0.1355%]**였다. 작은 양수 한 건을 profitability로 해석할 수 없다.

**어느 arm도 frozen gate를 통과하지 않았다.** C의 +0.5263%도 단일 event라
선택할 수 없고, preregistration상 B 실패를 C로 교체하는 것도 금지했다.

## 1. Evidence discovery와 고정 범위

### Primary evidence

| 항목 | 값 |
|---|---|
| deployment | `macmini-m5 / polybot-bear / golden-honeydew / default` |
| local DB | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-honeydew/runtime/default/databases/latest/trades.db` |
| remote path | `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-honeydew/data/default/trades.db` |
| DB SHA-256 | `f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df` |
| source cutoff | `2026-07-28T15:42:05.414525Z` |
| synced_at | `2026-07-30T12:13:51.689096Z` |
| latest successful sync finished | `2026-07-30T12:14:27.341243Z` |
| locate | `analysis_ready=true`, `SYNCED`, local/remote SHA 일치 |
| verify | `SUCCESS`, checked 3,816 / failed 0 / retention skip 0 |
| SQLite | `PRAGMA quick_check=ok` |

`compact-v1`의 snapshot anchor는 `2026-07-28 15:21:43`, hot window는 24시간이다.
따라서 5분 신호에는 full-cadence인 다음 범위만 사용했다.

- history 시작: `2026-07-27T15:20:00Z`
- mechanics signal: `[2026-07-27T15:45:00Z, 2026-07-28T00:00:00Z)`
- temporal OOS signal: `[2026-07-28T00:00:00Z, 2026-07-28T14:15:00Z)`
- 60분 target 허용창: target 이후 첫 관측, 최대 +15분
- 마지막 OOS exit cutoff: `2026-07-28T15:30:00Z`

분석기는 `SUCCESS / live` run과 `cursor_complete=1` sweep에 연결된 snapshot만 읽었다.
이 범위에는 sweep 290개가 있었고 290개 모두 cursor-complete였다. 실제로 읽은 것은
snapshot **1,293,610행 / 10,658 conditions**이며, catalog는 81,572 conditions였다.

### 다른 evidence의 사용 여부

| source | 발견 결과 | 5분 OOS 사용 여부 |
|---|---|---|
| Nectarine, `polybot-eagle` | locate/verify `SUCCESS`; SHA `9ac1442a…0cae`; source cutoff `2026-07-30T12:15:04Z` | `compact-v1` hot window가 1시간뿐이라 3/5-step history + 60분 exit의 독립 OOS에 부족 |
| Queen, `polybot-king` | locate/verify `SUCCESS`; SHA `5a5f6e0b…1747`; uncompressed | probability가 전부 0.80 이상이고 condition 관측 평균 gap 11.97분, 4–7.5분 gap은 0.67%뿐이라 frozen universe/cadence와 다름 |
| Date | local-only DB, quick_check `ok`, SHA `619c8583…5b28`, 1,415,521 snapshots | daily-rsync catalog에 없음; `compact-v1` 이전 구간은 12h rollup |
| Fig | local-only DB, quick_check `ok`, SHA `d69c80f9…ff39`, 2,051,421 snapshots | 동일 |
| Mango | local-only DB, quick_check `ok`, SHA `0b4f85b9…e1ff`, 1,064,081 snapshots | 동일 |
| Lime | local-only DB, quick_check `ok`, SHA `088f16a8…74e`, 1,036,657 snapshots | 동일 |

Date/Fig/Mango/Lime는 2026-07-21~28을 덮는 것처럼 보이지만, `compact-v1` 때문에
anchor 이전 24시간보다 오래된 row는 12시간 `latest` rollup이다. 이들을 7일 5분
history로 잘못 해석하지 않았다. 또한 같은 Gamma universe와 같은 시간대를 중복 수집한
source들이어서 독립 표본으로 합산하지 않았다.

이전 closed-strategy 결과도 설계 제약으로 반영했다.

- Lime의 6시간 shock-follow는 기각되었다.
- Grape의 24시간 drift와 중복하지 않게 15–25분 micro-trend만 처치했다.
- Honeydew actual fill에서는 optimistic snapshot replay와 실제 fill 성과의 부호가
  달랐다. 따라서 이번 결과는 fill/P&L이 아니라 offline quote counterfactual이다.
- 저장소의 경로 연구는 3–10분 return autocorrelation이 전체적으로 음수임을 이미
  보였다. 이번 hypothesis는 그 평균적 반례 속에서 “연속된 작은 상승”만 사전 정의해
  분리한 좁은 예외 가설이다.

## 2. Frozen hypothesis와 2×2 arms

Hypothesis:

> 사람들이 정보를 한 번에 반영하지 않고 서로의 거래를 따라갈 때, 3–5회의 작은
> 연속 5분 YES 상승이 60분 추가 상승으로 이어질 수 있다.

공통 entry rule:

1. standard binary Yes/No.
2. sports/games/esports 및 short-horizon crypto tags 제외.
3. resolution까지 최소 6시간.
4. YES probability 0.20–0.80.
5. liquidity ≥ $20,000, volume24h ≥ $10,000.
6. 유효 bid/ask, spread ≤ 0.02 probability points.
7. 각 관측 gap 3–10분.
8. 각 step은 `0 < ΔYES ≤ 0.02`, 누적은 최대 0.04.
9. 동일 event는 6시간 cooldown; 같은 run의 sibling market은 최고 liquidity 하나만 선택.
10. +60분 이후 첫 snapshot의 bid로 exit하며, target +15분을 넘으면 censor.

Frozen arms:

| arm | positive steps | 누적 상승 하한 | 역할 |
|---|---:|---:|---|
| A | 3 | 0.01 | loose sensitivity |
| **B** | **3** | **0.02** | **primary candidate** |
| C | 5 | 0.01 | longer-confirmation sensitivity |
| D | 5 | 0.02 | strict sensitivity |

`volume_24h` acceleration은 쓰지 않았다. 24시간 rolling field를 15분 처치로 쓰면
시간축이 맞지 않기 때문이다.

Preregistration은 arm forward return을 조회하기 전에 작성하고 SHA-256
`0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006`
으로 고정했다. B가 primary이고 observed winner로 바꾸지 않는다는 규칙도 미리 썼다.

## 3. Outcome 정의

Primary metric:

```text
top_of_book_return = exit_best_bid / entry_best_ask - 1
```

이는 entry와 exit의 관측 spread를 지불하는 cross proxy다. 추가로 저장소의 $5 confirmed
round-trip 연구에서 얻은 **10.4 bps**를 빼는 sensitivity도 사전 등록했다.

주의:

- top-of-book depth를 저장하지 않아 $5 전량 실행을 증명하지 않는다.
- queue, latency, partial fill, fee, adverse selection이 없다.
- 관측 spread를 이미 지불했으므로 10.4 bps 추가 차감은 보수적 stress이며 정확한
  additive fee 추정치가 아니다.
- 실제 order를 제출하지 않았으므로 `CONFIRMED` fill 성과가 아니다.

Primary mean은 trade-weighted가 아니라 event 안에서 먼저 평균낸 뒤 event를 동일 가중한
event-equal mean이다. seed `20260730`, 20,000 draw event-cluster bootstrap을 썼다.
일반 95% CI와 4-arm Bonferroni 98.75% CI를 보고한다. event가 1개면 clustered CI를
계산할 수 없으므로 `NA`로 두었다.

## 4. Signal funnel

| arm | raw candidates | event/run 선택 + 6h cooldown 후 전체 | mechanics | temporal OOS |
|---|---:|---:|---:|---:|
| A | 14 | 7 | 3 | 4 |
| B | 3 | 3 | 1 | 2 |
| C | 4 | 2 | 1 | 1 |
| D | 0 | 0 | 0 | 0 |

Arm B의 2pp/3-step 조건은 하루에도 몇 번 나오지 않았다. D는 하나도 없었다.
희귀성 자체가 운용과 검정 가능성에 대한 falsification evidence다.

## 5. Temporal OOS 결과

Cooldown state는 mechanics 구간에서 OOS로 이어서 적용했다. 같은 event가 mechanics와
OOS 양쪽에서 신호를 낼 수 있으므로, 아래 첫 표 다음에 완전 event-purge sensitivity를
따로 제시한다.

### 5.1 Cooldown-carried OOS

| arm | signals | events | quote n | midpoint Δp | event-equal top-of-book return | clustered 95% CI | Bonferroni 98.75% CI | +10.4bps stress 후 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 4 | 2 | 4 | -0.10pp | **-1.0234%** | [-1.9379%, -0.1089%] | [-1.9379%, -0.1089%] | -1.1274% |
| B | 2 | 2 | 2 | -0.20pp | **-1.8072%** | [-3.7500%, +0.1355%] | [-3.7500%, +0.1355%] | -1.9112% |
| C | 1 | 1 | 1 | +0.50pp | +0.5263% | NA | NA | +0.4223% |
| D | 0 | 0 | 0 | NA | NA | NA | NA | NA |

2 event bootstrap에서는 재표집 가능한 event mean이 둘뿐이라 95%와 98.75% endpoints가
같아진다. 이를 정밀한 interval로 해석하면 안 된다. A의 음수도 regime 하나의 2-event
기술 통계이고, 보편적 음의 alpha 확정값이 아니다.

Arm B의 두 signal:

- early OOS: -3.7500% top-of-book return.
- late OOS: +0.1355%.

따라서 split sign-stability가 없고, 평균은 음수다.

### 5.2 Strict event-purged OOS

Mechanics 구간에 한 번이라도 같은 arm signal을 낸 event를 OOS에서 제외했다.

| arm | signals | events | quote n | midpoint Δp | event-equal top-of-book return | 95% / 98.75% CI | +10.4bps stress 후 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 0 | 0 | 0 | NA | NA | NA | NA |
| **B** | **1** | **1** | **1** | +0.20pp | **+0.1355%** | **추정 불가** | **+0.0315%** |
| C | 1 | 1 | 1 | +0.50pp | +0.5263% | 추정 불가 | +0.4223% |
| D | 0 | 0 | 0 | NA | NA | NA | NA |

B와 C의 양수는 각각 단일 event다. bootstrap CI를 `[관측값, 관측값]`로 꾸미지 않고
추정 불가로 처리했다.

### 5.3 Cohort separation

Strict OOS B signal:

```text
config_hash = 30be03ee6262cee4d16595da53ec23de76b86b2a16992a0e1576afc0fe439852
git_commit  = d6c52f7a1ab46a00dc31e9ba89683c832dff9ac5
mode/job    = live/default
n/events    = 1/1
```

Strict OOS C signal:

```text
config_hash = 30be03ee6262cee4d16595da53ec23de76b86b2a16992a0e1576afc0fe439852
git_commit  = 9b648b38ca87aea98663c7ee3b0bd0275c4369f8
mode/job    = live/default
n/events    = 1/1
```

검토 구간은 config hash가 같아도 여러 Git commit을 포함한다. 어느 collection cohort도
10 signal 이상이 아니므로 code-cohort sign stability는 검정할 수 없다.

## 6. Frozen gate 판정

Strict event-purged OOS의 frozen gate:

1. quote-complete signals ≥ 50.
2. event clusters ≥ 30.
3. top-of-book return의 98.75% lower CI > 0.
4. 10.4 bps stress 후에도 98.75% lower CI > 0.
5. OOS early/late 양쪽 mean > 0.
6. quote coverage ≥ 90%.
7. 충분한 표본의 collection cohort에서 sign reversal 없음.

| arm | 판정 | 직접 실패 이유 |
|---|---|---|
| A | **FAIL** | 0 signals/events, CI 불가, 두 OOS half 모두 비어 있음 |
| **B** | **FAIL** | 1 < 50 signals, 1 < 30 events, 두 CI 불가, early half 비어 있음 |
| C | **FAIL** | 1 < 50 signals, 1 < 30 events, 두 CI 불가, early half 비어 있음 |
| D | **FAIL** | 0 signals/events, CI 불가, 두 OOS half 모두 비어 있음 |

따라서:

- profitability claim: **금지**
- live deployment: **금지**
- parameter tuning으로 B를 C나 A로 교체: **금지**
- 현 데이터에서 할 수 있는 결론: **신호가 너무 희귀하며 edge가 식별되지 않았다**

## 7. 추천 규칙과 운영 안전장치

향후 독립 데이터에서 다시 검정할 유일한 후보는 preregistered **Arm B**다.

추천 research/shadow specification:

- codename: `golden-kiwi`
- label: `Micro-Cascade`
- entry: 공통 rule + 3 consecutive positive 3–10분 steps, 누적 +2~4pp,
  단일 step ≤2pp
- position: YES only
- exit: entry 후 60분 첫 유효 bid; target +15분을 넘기면 evidence gap
- sizing: shadow에서는 주문 없음; future small-live gate를 통과해도 $5부터
- event exposure: 동일 event 1개, 6시간 cooldown
- finite risk defaults: `max_positions=3`, `max_open_notional_usdc=$15`,
  `max_new_positions_per_cycle=1`
- quote gate: fresh bid/ask, spread ≤2pp, $5 depth가 양쪽에 있을 때만
- execution truth: GTC ack/order ID를 fill로 보지 않고 BUY/SELL 모두
  `CONFIRMED` size/price/fee로만 terminal 처리
- reconciliation error, stale/uncertain intent, archive/run audit 실패 시 신규 BUY fail-closed
- 60분 exit은 capital turnover rule이지 현재 evidence로 입증된 alpha exit이 아니다

이 값들은 live 수익 파라미터 추천이 아니라 기존 폐쇄 전략의 무제한 position,
ack-as-fill, stale reconciliation 실패를 반복하지 않기 위한 보수적 연구 계약이다.

## 8. 다음 독립 검정의 falsification gate

현재 OOS는 이미 열어봤으므로 다시 test로 쓰면 안 된다. 다음 검정은 완전히 새로운
UTC 기간에서 같은 B rule을 변경 없이 실행한다.

1. full-cadence 5분 data를 별도 research archive에 최소 30일 보존한다.
   `compact-v1` cold rollup을 raw 5분 history로 해석하지 않는다.
2. point-in-time catalog, `source_updated_at`, bid/ask, $5 depth, spread, run/config/Git
   provenance를 모두 보존한다.
3. 30일 후에도 B가 50 signals / 30 events를 못 채우면 threshold를 완화하지 않고
   **unresearchable/too-rare로 중단**한다.
4. 표본을 채우면 frozen 98.75% event-cluster lower bound > 0 및 10.4 bps stress 후
   lower bound > 0을 요구한다.
5. early/late half와 주요 collection cohort가 모두 같은 양의 부호여야 한다.
6. shadow signal 전체와 actual fill subset을 분리해 fill selection을 검정한다.
7. live 승격 전 BUY/SELL confirmed fill, fee, role, partial, depth, reconciliation
   coverage가 각각 최소 95%여야 한다.
8. strict audit에 CRITICAL/HIGH가 있으면 결과와 무관하게 승격하지 않는다.

## 9. 재현

환경:

```text
Python 3.11.4
SQLite 3.41.2
```

명령:

```bash
python3 /Users/izowooi/.Codex/_workspace/new-strategy-2026-07-30/closed-data-research/micro_cascade_analysis.py
```

독립 산술 검산에서는 생성된 12 signal row 전부에 대해:

- +60~75분 창의 첫 SUCCESS/live/cursor-complete snapshot이 맞는지,
- `exit_bid / entry_ask - 1`이 CSV와 일치하는지

다시 계산했고 **12/12 일치, 문제 0건**이었다.

Artifact manifest:

| artifact | SHA-256 |
|---|---|
| `PREREGISTRATION.md` | `0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006` |
| `micro_cascade_analysis.py` | `1fc84158e37b03176f6933db4cc00a6eb1321d9c40b4a3c0fcaaddd4c498a08a` |
| `results.json` | `9b36a732303b0a2897957a016a12597d3df8aafa06ae352881dc2fa062ff6a5e` |
| `RESULTS.md` | `f11a3e724db9c69da30e69ac0db5f73910624b4732c8f652efa8e70b0486931c` |
| `signals.csv` | `7dc5177ad2164e4b348b6e41c9fdc59a604538cce3edce44ece4ea03e00ad307` |

Repository files were not edited by this research task. All artifacts are under
`/Users/izowooi/.Codex/_workspace/new-strategy-2026-07-30/closed-data-research`.

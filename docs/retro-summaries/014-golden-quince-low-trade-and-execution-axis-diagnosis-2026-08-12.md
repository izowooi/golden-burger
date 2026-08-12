# 014 — Golden Quince 저빈도·실행축 진단 — 2026-08-12

작성일: 2026-08-12

대상: Jenkins `polybot-bear` / `polybot-eco` / `polybot-tiger`, strategy
`golden-quince`, runtime `polybot-quince-passive` / `polybot-quince-nearest` /
`polybot-quince-cross`

## 0. 결론

```text
Decision: IMPLEMENTATION_FAIL / FIX_AND_RESTART_NEW_COHORT
Cadence: H/5 유지
Parameter tuning: 현재 금지
관측: 팔당 CONFIRMED BUY 4건, 공통 scanner candidate 8건, 현재 open position 0건
핵심 결함: passive / nearest / cross의 12개 BUY fill이 전부 동일한 TAKER 체결
```

사용자가 본 “포지션 1개”와 달리 각 SQLite에는 서로 다른 시장의 BUY가 **4건씩** 있다.
네 건은 모두 exact `CONFIRMED` BUY이고 이후 `RESOLVED=Yes`가 됐다. 현재 DB 기준 open
position은 0건이다.

저빈도 자체도 사실이다. 약 7.05일 동안 A(passive)의 CONFIRMED BUY는 4건이라 사전 등록한
day-7 하한 7건에 못 미친다. 같은 속도를 단순 투영하면 30일 약 17건으로, 1차 판정에 필요한
30건에 부족하다. 그러나 지금은 신호 조건을 완화할 단계가 아니다.

가장 중요한 발견은 세 팔의 처치가 작동하지 않았다는 것이다. A/B/C 모두 같은 네 시장에서
동일한 주문가와 체결가를 기록했고, 팔별 네 fill 전부 `TAKER`, 평균 진입 비용도 모두
결정 midpoint 대비 `+54.8 bps`였다. 따라서 현재 자료로 passive와 nearest/cross를 비교할
수 없다. 실행축을 수정하고 새 cohort로 재시작하기 전까지 새 체결은 실험 표본으로 쓸 수 없다.

Jenkins 주기는 원인이 아니다. 세 job의 실제 TimerTrigger는 모두 `H/5 * * * *`이고 start
gap 중앙값은 5분이다. 15분을 넘긴 gap은 0.21~0.26%에 불과하다. 반대로 cycle runtime
p95가 6.3분이므로 주기를 더 짧게 하면 중첩/queue만 늘 가능성이 높다.

이번 작업은 read-only 진단이다. Jenkins, strategy code, parameter는 수정하지 않았다.

## 1. Evidence boundary와 동기화

Timezone은 UTC다. strict audit의 재현 가능한 완결 구간은
`[2026-08-06T00:00:00Z, 2026-08-12T00:00:00Z)` 6일이다. 세 팔이 모두 기동된 뒤의
운영 관측 구간은 `2026-08-05T13:21:37Z`부터 공통 최소 source cutoff인
`2026-08-12T14:31:31Z`까지 약 7.05일이다.

각 job을 `scan`한 뒤 별도 14일 plan으로 DB·bot log·Jenkins console log를 동기화했다.
세 sync 모두 SUCCESS, failed 0이며 `verify`에서 retention skip과 artifact conflict도 0이다.

### Bear / passive

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-quince/data/polybot-quince-passive/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-quince/runtime/polybot-quince-passive/databases/latest/trades.db`
- SHA-256: `1d4b5b1b4837298a62f6cf27cab5a1538a02953b2ca6326b24df22c15cc01b1f`
- Sync: `8ae5ddc7c82e41a8a0b0ec587cb5cfb4`, SUCCESS,
  `2026-08-12T14:37:26.764413Z`
- DB synced at: `2026-08-12T14:35:08.757864Z`
- Source cutoff/mtime: `2026-08-12T14:31:31.985431Z`
- Verify: SUCCESS, 1,627 checked, failure/retention skip/conflict 0

### Eco / nearest

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-eco/golden-quince/data/polybot-quince-nearest/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eco/strategies/golden-quince/runtime/polybot-quince-nearest/databases/latest/trades.db`
- SHA-256: `b17df191d3495e47551d8894e4e001d3adc85a91658e497960de73db64e1ea3f`
- Sync: `799acdc94cef46f29c281369ab9b8c34`, SUCCESS,
  `2026-08-12T14:40:06.051425Z`
- DB synced at: `2026-08-12T14:37:55.280285Z`
- Source cutoff/mtime: `2026-08-12T14:36:35.618493Z`
- Verify: SUCCESS, 2,162 checked, failure/retention skip/conflict 0

### Tiger / cross

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-tiger/golden-quince/data/polybot-quince-cross/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-quince/runtime/polybot-quince-cross/databases/latest/trades.db`
- SHA-256: `e85ef567f038a75990010bff5d7c9d6b467aaec697b978711291e30a7b684fd1`
- Sync: `ccacda6c50de46ff9dbdc7a52c585397`, SUCCESS,
  `2026-08-12T14:42:48.664762Z`
- DB synced at: `2026-08-12T14:40:34.362010Z`
- Source cutoff/mtime: `2026-08-12T14:38:16.901873Z`
- Verify: SUCCESS, 1,620 checked, failure/retention skip/conflict 0

## 2. Jenkins current state

2026-08-12T14:31:44Z 익명 config 재조회 결과다.

| Jenkins job | Runtime / treatment | Actual TimerTrigger | Concurrent | Latest completed | Config SHA-256 |
|---|---|---|---|---|---|
| `polybot-bear` | `polybot-quince-passive` / passive | `H/5 * * * *` | false | `#7379 SUCCESS` | `bbc3ebd0c2f8…` |
| `polybot-eco` | `polybot-quince-nearest` / nearest | `H/5 * * * *` | false | `#9269 SUCCESS` | `6ee76be883f8…` |
| `polybot-tiger` | `polybot-quince-cross` / cross | `H/5 * * * *` | false | `#8885 SUCCESS` | `c7292c21b341…` |

세 job은 disabled가 아니고 `--live`, lifecycle `active`, `$5`, 24h, 0.90–0.94,
liquidity `$10,000`, volume24h `$2,000`, max spread `0.02`가 같다. BUY
`execution_mode`와 격리된 wallet/runtime만 다르다.

다만 세 job 모두 resolved config의 `experiment_capital_usdc=100`이다. 사전 등록
runbook은 **200**을 요구하므로 현재 drawdown kill switch는 의도한 `-$40`가 아니라
`-$20`에서 작동한다. 저빈도의 원인은 아니지만 새 cohort 전에는 세 팔 모두 200으로
정렬해야 한다.

## 3. Cadence와 runtime

운영 관측 구간의 run/cadence는 다음과 같다.

| 지표 | Bear passive | Eco nearest | Tiger cross |
|---|---:|---:|---:|
| SUCCESS run | 1,933 | 1,934 | 1,931 |
| FAILED run | 4 | 4 | 4 |
| Runtime p50 | 152.1s | 152.2s | 145.3s |
| Runtime p95 | 380.2s | 381.0s | 381.8s |
| Runtime > 5m | 13.84% | 13.93% | 12.97% |
| Start gap p50 | 300.0s | 300.0s | 300.1s |
| Start gap p95 | 385.9s | 385.8s | 388.1s |
| Gap > 15m | 4 / 1,937 | 4 / 1,938 | 5 / 1,935 |
| Max gap | 44.78m | 44.65m | 44.50m |

완전한 UTC 6일 구간에서 cursor-complete sweep은 Bear/Eco/Tiger 각각
1,653/1,653/1,649회였다. 완전한 하루의 run은 대체로 266~284회로 이론상 288회 대비
92~99%다. 실패는 Gamma API의 `ReadTimeout`, `ChunkedEncodingError`였고 세 팔에서 같은
시간대에 발생했다.

전역 start gap이 lineage 상한 15분을 넘은 비율은 약 0.2%뿐이다. H/5가 후보를 크게
놓친다는 증거는 없다. 오히려 p95 runtime이 5분을 넘으므로 H/3나 H/2로 줄이기 전에
Gamma full-sweep 비용을 먼저 낮춰야 한다.

## 4. 시장 수집과 진입 퍼널

strict 6일 구간의 cycle당 평균은 다음과 같다.

| 지표 | Bear | Eco | Tiger |
|---|---:|---:|---:|
| Gamma pages | 297.5 | 297.5 | 297.6 |
| Raw markets | 29,702 | 29,702 | 29,706 |
| Qualified markets | 17,125 | 17,126 | 17,126 |
| Archive eligible | 92.4 | 92.4 | 92.4 |
| Snapshots saved | 69.8 | 69.8 | 69.8 |
| Cursor-complete rate | 100% | 100% | 100% |

약 7.05일의 공통 퍼널은 세 팔에서 같았다.

```text
scanner candidate 8
  ├─ BUY submitted / exact CONFIRMED 4
  └─ fresh execution revalidation reject 4
       ├─ CLOB midpoint 0.895로 entry lower bound 0.90 미달: 3
       └─ fresh spread 0.09로 max_spread 0.02 초과: 1
```

따라서 실제 one-shot crossing 자체가 일주일에 8건으로 희소했다. 그중 절반만 주문으로
이어졌다. 0.09 spread 거절은 안전장치가 정상 작동한 것이므로 이를 완화하면 안 된다.
세 건의 0.895는 Gamma crossing 직후 CLOB midpoint가 다시 0.90 아래였던 경우다. 0.895를
허용하도록 threshold를 바꾸면 빈도는 늘 수 있지만, 이는 단순 실행 완화가 아니라 전략의
신호 정의를 바꾸는 새 실험이다.

로그의 cycle별 제외 사유에는 `low_liquidity` 약 1.7k~2.1k, `low_volume` 약
1.1k~1.3k가 반복된다. 그러나 scanner는 strict binary 뒤에 liquidity/volume을 먼저
검사하고 clock/lineage/crossing을 나중에 검사한다. 따라서 이 숫자는 “유효한 0.90 crossing이
liquidity 때문에 탈락한 수”가 아니다. 이 집계만 보고 `$10k/$2k`를 낮출 수 없다.

## 5. 과거 PENDING_BUY 결함의 영향

네 BUY는 체결 직후 exact fill이 `CONFIRMED`였지만, 기존 수량 반올림 비교 결함 때문에
로그에서 `confirmed full=False / confirmed_partial_or_unreconciled`로 남았다. 세 팔 모두
약 3,850회의 pending check를 반복했고, 2026-08-10 close-only 복구 run에서 네 건이
활성화된 뒤 closed-market resolution 복구로 모두 `RESOLVED`됐다.

이 기간에는 HOLDING lifecycle과 target/stop 관리가 정상적으로 시작되지 않았다. 네 시장이
모두 Yes로 해결돼 이번에는 손실로 드러나지 않았지만, 8월 5~10일 자료는 정상 lifecycle
cohort가 아니다. 관련 수정 commit은 다음과 같다.

- `e86769afd8f1` — live fill quantity reconciliation 수정
- `b91e9ac8cdc6` — closed market resolution lookup 수정

수정 후 active config로 돌아온 시점은 2026-08-10T15:07Z 전후다. 동기화 cutoff까지 정상
lifecycle 관측은 약 이틀뿐이다. strict 6일 audit에도 팔별 config 2개와 Git commit 17개가
섞여 있고, 네 진입은 각각 서로 다른 commit에서 발생했다. 계약상
`config_hash × git_commit × mode × job_name`별로 분리하면 한 cohort에 유효 event가
한 건을 넘지 않는다.

## 6. A/B/C 실행축 구현 결함

사전 등록의 1차 종점은 passive가 maker에 합류하고, nearest가 midpoint에 붙고, cross가
ask를 넘는지 비교하는 것이다. 실제 결과는 완전히 같았다.

| 지표 | Bear passive | Eco nearest | Tiger cross |
|---|---:|---:|---:|
| Accepted BUY | 4 | 4 | 4 |
| CONFIRMED BUY | 4 | 4 | 4 |
| MAKER / TAKER | 0 / **4** | 0 / **4** | 0 / **4** |
| Fill rate | 100% | 100% | 100% |
| 평균 entry cost vs midpoint | +54.8 bps | +54.8 bps | +54.8 bps |
| Gross resolution assumption | +$1.7775 | +$1.7775 | +$1.7775 |

네 event 모두 세 팔의 submission/fill 가격과 수량이 동일했다. 주문가는 0.92/0.93,
당시 best ask는 0.91/0.92였고 실제 fill은 best ask에서 TAKER로 체결됐다.

코드 원인도 DB 증거와 일치한다.

1. `Trader.execute_buy()`는 BUY limit price로 midpoint가 아니라 ask-side
   `depth_limit`을 넘긴다. 이 값은 `min(prob_max, best_ask + 0.01)`이라 이미 tick에
   맞고 best ask 이상이다.
2. `ClobClientWrapper._round_to_tick()`의 execution mode는 전달받은 가격을 floor/round/ceil
   할 뿐이다. 이미 0.92/0.93인 값은 세 방식에서 거의 항상 같아진다.
3. unit test는 반올림 함수와 trader의 raw `depth_limit` 전달을 따로 검사해 이 통합 결함을
   잡지 못했다.

현재 함수에 실제 전달된 tick-aligned 가격을 넣은 로컬 재현도 DB와 같다.

```text
price 0.92 → passive 0.92 / nearest 0.92 / cross 0.92
price 0.93 → passive 0.93 / nearest 0.93 / cross 0.93
```

기존 `test_execution_mode.py`와 `test_trader.py`는 103개가 모두 통과했다. 따라서 실패한
test가 방치된 문제가 아니라, candidate부터 실제 submitted price까지 세 mode를 한 번에
검사하는 integration test가 없었던 문제다.

따라서 현재 실험은 사전 등록 결정표의 `IMPLEMENTATION_FAIL`이다. 수익처럼 보이는
+$1.7775도 네 개의 서로 연동된 event가 전부 Yes였다는 gross settlement assumption일 뿐이고,
fee coverage가 완전하지 않다. 수익성이나 실행 모드 우열의 근거로 사용하지 않는다.

## 7. Strict audit

명령:

```bash
uv run --project polybot-observability polybot-retro audit \
  --db <bear-verified-db> \
  --db <eco-verified-db> \
  --db <tiger-verified-db> \
  --days 6 --as-of 2026-08-11 \
  --output-dir daily-rsync/data/analysis/quince-abc-20260806-20260812 \
  --strict
```

결과는 exit 1, **HIGH 12 / MEDIUM 3**이다. 각 DB에 같은 이슈가 있다.

| Severity | Issue | 해석 |
|---|---|---|
| HIGH | `failed_runs` | 구간 내 Gamma timeout FAILED run 2건 |
| HIGH | `archive_window_short` | compact archive로 per-market full-cadence 반사실 재생 불충분 |
| HIGH | `market_sweep_attestation_missing` | 모든 sweep의 per-market membership 분모가 없음 |
| HIGH | `market_catalog_missing` | catalog metadata coverage 약 98.1~98.2% |
| MEDIUM | `logs_missing` | auditor가 별도 daily-rsync log 경로를 자동 연결하지 못함 |

bot log와 Jenkins console log는 실제로 동기화·verify됐고 수동 진단에 사용했다. 하지만 이는
archive 관련 HIGH 세 종류를 해소하지 않는다. `compact-v1`은 오래된 5분 snapshot을 extrema로
축약하므로 과거 threshold를 바꿔 “몇 건이 더 생겼을지” 정확히 재생할 수 없다. Evidence
Contract에 따라 HIGH가 남은 상태에서 숫자 tuning을 하지 않는다.

## 8. 권고 순서

1. **세 job을 현 상태로 장기간 더 돌리지 않는다.** 새 fill은 세 팔 모두 TAKER라 1차 종점에
   기여하지 않는다. 중단은 사용자가 수행하며 이번 진단에서는 job을 변경하지 않았다.
2. BUY 가격 선택을 execution mode까지 포함해 수정한다. 최소 통합 계약은
   `passive < best_ask`(한 틱 spread에서는 best bid 합류), `nearest ≈ midpoint`,
   `cross >= best_ask`이며, 실제 submitted price와 fill role까지 검증하는 test가 필요하다.
   passive는 ask-depth 기반 즉시체결과
   다른 주문 생명주기를 가지므로 단순 한 줄 반올림 변경으로 끝내면 안 된다.
3. 기존 세 DB를 pin/보존하고 수정된 코드는 **새 cohort**로 동시에 시작한다. 기존 12개
   account-fill은 폐기하지 않되 A/B/C 판정 표본에서는 제외한다. clean은 새 cohort 시작 시
   한 번만 사용하거나 새 runtime/DB 이름으로 분리한다.
4. 새 cohort에서는 `experiment_capital_usdc=200`으로 runbook과 맞춘다. 세 팔 모두 동일하게
   적용하고 첫 build의 resolved config를 저장한다.
5. 재시작 초기값은 `$5`, 24h, 0.90–0.94, `$10k/$2k`, spread 0.02,
   `H/5`를 유지한다. 첫 공통 signal에서 반드시 A의 MAKER, C의 TAKER와 서로 다른 submitted
   price를 확인한 뒤에만 계속한다.
6. H/5보다 짧게 바꾸지 않는다. p95 runtime이 약 6.3분이다. 빈도를 높이고 싶다면 먼저
   Gamma universe fetch/runtime을 최적화하고 p95를 5분 아래로 낮춘다.
7. 향후 parameter 완화를 검토하려면 crossing 직후 각 gate의 결정과 CLOB midpoint/book을
   immutable decision row로 보존한다. 현재 broad exclusion log와 compact snapshot만으로는
   liquidity/volume의 정확한 반사실 후보 수를 계산할 수 없다.
8. 수정된 새 cohort의 day 7에도 A CONFIRMED BUY가 7건 미만이면 선택지는 runbook대로
   `60일까지 무변경 연장` 또는 `세 팔 모두 한 축만 완화해 새 cohort로 재시작`이다. 현재
   자료에서 특정 liquidity/volume 숫자를 추천하지 않는다. 특히 0.09 spread를 허용하는
   완화는 하지 않는다.

## 9. 직접 답변

- **조건이 너무 좁은가?** 표본 속도는 확실히 느리다. 다만 현재 자료는 실행축 결함과
  archive HIGH 이슈 때문에 어떤 숫자를 얼마나 낮출지 판정할 수 없다.
- **Jenkins 주기가 너무 넓은가?** 아니다. H/5는 이미 실효 중앙값 5분이며, 더 짧은 주기는
  p95 runtime보다 짧다.
- **지금 바꿀 parameter가 있는가?** 전략 gate는 없다. 새 cohort 전 사전 등록과 다르게
  들어간 `experiment_capital_usdc=100`을 200으로 복원해야 하지만, 저빈도 개선용 tuning이
  아니라 안전 계약 정렬이다.
- **가장 먼저 할 일은?** passive/nearest/cross가 실제 주문 가격과 maker/taker 역할을
  다르게 만들도록 코드를 수정하고, 세 팔을 새 DB/cohort로 동시에 재시작하는 것이다.

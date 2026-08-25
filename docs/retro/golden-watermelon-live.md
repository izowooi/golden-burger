# Golden Watermelon Live 회고 계약

공통 execution evidence 정의는 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)를 따른다.

```text
REVIEW_START=2026-08-24T13:00:00Z
REVIEW_END=2026-08-31T13:00:00Z
FOLLOWUP_END=2026-09-07T13:00:00Z
```

`polybot-cat`은 exact `$5` ask VWAP `[0.98,0.999]`, `polybot-dog`은
`[0.99,0.999]`를 사용한다. threshold 외 계약은 동일하다. 분석 cohort는
`config_hash × strategy_source_digest × mode × job_name`으로 나누고 account/job 차이도
별도로 표시한다.

## 2026-08-24 live 배포 증거

- 최종 source commit: `5bb2b12`; source digest `89afb9bf4347…`
- Cat: Jenkins config SHA-256 `d4a409e3b910…`, runtime
  `watermelon-live-cat-98`, config cohort `e5799397045a…`
- Dog: Jenkins config SHA-256 `ec4a8b4d781b…`, runtime
  `watermelon-live-dog-99`, config cohort `aa0793da1a5f…`
- 두 job 모두 clean 비활성, non-concurrent, build retention 14일, 실제
  `TimerTrigger=H/5 * * * *`다.
- 최종 cohort에서 Cat `#5158/#5159` 수동 + `#5160` timer, Dog
  `#5053/#5054` 수동 + `#5055` timer가 모두 `SUCCESS`였다. 각 DB에 3/3 SUCCESS run,
  cursor-complete sweep 3/3이 기록됐다.
- `create_or_derive_api_key()`의 create-first 400 로그를 발견해 timer를 끄고
  `derive_api_key()` fail-closed 방식으로 고친 뒤 재배포했다. 최종 6개 build는 모두
  `derive-api-key 200`이며 신규 API-key 생성 요청이 없다.
- 최초 commit `8236040`의 각 3개 run은 배포 검증 epoch다. event·market·trade·order가
  모두 0이어서 금전·선택 오염은 없지만 source digest가 다르므로 성과 분석에서는 최종
  cohort와 합치지 않는다.
- 최종 sync cutoff에서 두 DB 모두 `quick_check=ok`, FK 위반 0, pending BUY/SELL 0,
  trade/order/fill 0이다. 당시 허용 리그의 진행 중 event가 0이었으므로 실제 FOK 체결과
  stop 경로는 첫 후보 발생 후 별도로 검증해야 한다.

최종 verified DB:

- Cat: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-watermelon-live/runtime/watermelon-live-cat-98/databases/latest/trades.db`
  (`SHA-256 8a8169f25c73…`, source cutoff `2026-08-24T14:31:38.205261Z`)
- Dog: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-watermelon-live/runtime/watermelon-live-dog-99/databases/latest/trades.db`
  (`SHA-256 53dc26260cad…`, source cutoff `2026-08-24T14:31:38.235478Z`)

`daily-rsync verify`는 job별 8개 artifact를 검사해 failed/conflict/retention skip 0으로
`SUCCESS`였다. 배포 시점에 대상 경기가 없었다는 사실을 cadence 실패나 universe 결함으로
해석하지 않는다. 24시간 collection health에서 실제 리그 membership과 crossing/book
coverage를 다시 확인한다.

## 2026-08-24 1분 v2a cadence amendment

White/Grey v3a를 source cutoff `2026-08-24T14:45:46.728151Z` /
`2026-08-24T14:42:34.475880Z`까지 다시 동기화해 비교했다. `daily-rsync verify`는 White
1,105개, Grey 276개 artifact를 failed/conflict/retention skip 0으로 통과했다.

- White FAST_1M은 1,373/1,374 slot, Grey CONTROL_5M은 275/275 slot이 성공했다.
- Grey episode key 11개는 모두 White에도 있었고 White는 추가 8개를 관측했다.
- paired entry 시각 차이 p95는 1,108.52초였다.
- 5분 live epoch의 Cat/Dog DB는 각각 SUCCESS run 9개, cursor-complete sweep 9개였고
  eligible snapshot, trade, order, fill, open state가 모두 0이었다.

따라서 기존 5분 DB를 변경하지 않고 active preregistration을
`research/frozen-2026-08-24-1m-v2a`로 전환한다. 새 runtime은
`watermelon-live-cat-98-1m-v2a` / `watermelon-live-dog-99-1m-v2a`, 두 Jenkins timer는
모두 `* * * * *`다. threshold 외 treatment 차이는 없다. 이 변경은 시험한 1분/5분 중
coverage가 더 완전한 cadence를 선택한 것이며 1분의 수익 최적성을 확정한 것은 아니다.

`0.999`는 세 번째 threshold가 아니라 terminal `1.000`을 제외하는 공통 상한이다. White의
0.98 결과는 3건, 0.99 결과는 1건뿐이므로 두 하한은 계속 보수적 최소금액 pilot로만
해석한다.

배포 commit은 `0ad9442`, source/preregistration digest는
`54cc0abe48f1…` / `c2dc95cfb591…`다. Jenkins config SHA-256은 Cat
`a6ec5dfc4757…`, Dog `c4478b128eeb…`이며 clean 없이 non-concurrent
`TimerTrigger=* * * * *`로 확인했다. 첫 두 자연 실행은 Cat `#5165/#5166`, Dog
`#5060/#5061`이고 모두 `SUCCESS`였다. 실행시간은 Cat 5.264s/3.612s, Dog
5.122s/3.806s였다.

새 verified DB는 다음과 같다.

- Cat: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-watermelon-live/runtime/watermelon-live-cat-98-1m-v2a/databases/latest/trades.db`
  (`SHA-256 c3af29f889f1…`, source cutoff `2026-08-24T15:00:35.474990Z`)
- Dog: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-watermelon-live/runtime/watermelon-live-dog-99-1m-v2a/databases/latest/trades.db`
  (`SHA-256 4b43663e3973…`, source cutoff `2026-08-24T15:00:44.960287Z`)

`daily-rsync verify`는 job별 17개 artifact를 failure/conflict/retention skip 0으로
`SUCCESS` 처리했다. 두 DB 모두 `quick_check=ok`, FK 위반 0, SUCCESS run 2/2,
cursor-complete sweep 2/2, trade/order/fill/open state 0이다. 첫 run 간격은 Cat 58.762초,
Dog 68.215초였다.

## 2026-08-25 v2b execution safety amendment

함대의 과거 max-position 고착과 order/fill evidence 장애가 재발하지 않도록 timer를 먼저
끄고 `research/frozen-2026-08-25-safety-v2b`를 동결했다. threshold, `$5`, stop, 1분 cadence,
5개 리그, 실험 clock과 `20/1/20` exposure 값은 바꾸지 않았다.

- commit `5244ed2`, source digest `cef3150612d2…`, preregistration SHA-256
  `9e1852c2e57d…`
- runtime: Cat `watermelon-live-cat-98-1m-v2b`, Dog
  `watermelon-live-dog-99-1m-v2b`
- capacity denominator를 open trade + open trade에 연결되지 않은 unresolved live BUY intent로
  바꿨다. tracked pending BUY/SELL 또는 SELL reconciliation gap이 있으면 후보는 계속
  수집하되 신규 BUY 실행을 막는다.
- BUY/SELL별 unresolved intent와 reconciliation gap, entry guard reason, reserved capacity를
  `run_audits.cycle_stats_json`에 남긴다.
- membership detail checkpoint에 excluded condition도 저장하고 classifier 제외 reason에
  normalized source sport code/status를 남긴다. detail row count가 선언된 unique condition과
  다르면 다음 cycle에 즉시 repair checkpoint를 만든다.

최종 v2a DB는 Cat/Dog 각각 run/sweep `61/61 SUCCESS`, trade/entry/order/fill/open state 0이다.
한 detail sweep이 선언됐지만 excluded-only 4,478 condition 중 membership row가 0인 과거
계측 결함이 확인됐다. 따라서 v2a는 immutable zero-opportunity 배포 증거로 보존하고 v2b와
합치지 않는다.

배포 검증 결과는 다음과 같다.

- Jenkins config SHA-256: Cat `d678248262c4…`, Dog `02b2cbdc4324…`; 두 job 모두
  `TimerTrigger=* * * * *`, non-concurrent, clean 없음, lifecycle `active`다.
- 수동 Cat `#5226/#5227`, Dog `#5121/#5122`; 자연 timer Cat `#5228/#5229`, Dog
  `#5123/#5124`가 모두 commit `5244ed2`로 `SUCCESS`였다. 자연 실행시간은 Cat
  5.427s/7.063s, Dog 5.412s/7.289s이고 두 번째 자연 run 시작 간격은 각각
  61.162s/61.218s다.
- 두 DB 모두 단일 config/source cohort의 run/sweep `4/4 SUCCESS`, cursor complete `4/4`,
  pages `1/1/1/1`, `quick_check=ok`, FK 위반 0이다. bot log와 8개 Jenkins console에서
  ERROR/CRITICAL/Traceback/WARNING 0이다.
- 매 sweep은 event 29, unique market 277, qualified 0이었다. detail checkpoint는
  277/277 row를 저장했고 제외 분포는 `dfb` 186, `grc` 62, `rou1` 29였다. 이 시점의
  후보 0은 max-position 또는 0.98/0.99 gate가 아니라 허용 5개 리그의 진행 중 경기 부재다.
- 모든 run에서 reserved/open/pending BUY/pending SELL, unresolved BUY/SELL intent,
  reconciliation BUY/SELL gap과 error가 0이다. trade/entry/order/fill/snapshot도 0이다.
- 최종 `daily-rsync verify`는 job별 82 artifact를 검사해 failed, retention skip,
  conflict가 모두 0인 `SUCCESS`였다.

최종 verified v2b DB:

- Cat: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-watermelon-live/runtime/watermelon-live-cat-98-1m-v2b/databases/latest/trades.db`
  (`SHA-256 8a040449d5c6…`, source cutoff `2026-08-24T16:20:38.759987Z`, sync cutoff
  `2026-08-24T16:21:08.625444Z`)
- Dog: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-watermelon-live/runtime/watermelon-live-dog-99-1m-v2b/databases/latest/trades.db`
  (`SHA-256 33c29df73177…`, source cutoff `2026-08-24T16:20:38.902578Z`, sync cutoff
  `2026-08-24T16:21:18.515982Z`)

첫 24시간 health의 공통 half-open range는
`[2026-08-24T16:15:37Z, 2026-08-25T16:15:37Z)`
(`2026-08-25 01:15:37`–`2026-08-26 01:15:37 KST`)다. generic day-level
`polybot-retro --days 1`은 UTC 00:00부터 v2b 배포 전 시간을 포함해 schedule gap을
오탐하고 catalog의 별도 bot/Jenkins log 경로를 연결하지 못하므로, 이 health gate에서는
verified v2b DB와 동기화된 두 종류의 로그를 위 exact range로 직접 검사한다.

v2b 24시간 점검에서는 cadence, cursor completion, five-league identity, whole-match
HOME/DRAW/AWAY YES membership, exact `$5` ask depth, first episode, FOK submission,
order/fill/fee reconciliation, stop bid-depth evidence, DB integrity만 확인한다. 수익성이나
threshold 승자를 판단하지 않는다.

7일 entry 종료에는 다음을 arm별·league별로 기록한다.

- eligible unique event와 threshold-crossing event
- 한 event 한 entry 계약과 실제 FOK BUY/confirmed fill coverage
- `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, `COMPLETED`, `RESOLVED`,
  `UNFILLED`, `QUARANTINED` 상태
- best-bid `0.70` trigger, full-depth executable VWAP, trigger-to-fill gap, zero-fill/depth 부족
- exact BUY/SELL fill size·VWAP·fee와 proven payout coverage
- manual wallet position 비편입 및 과거 Papaya DB epoch 분리

성과 판정은 follow-up cutoff까지 terminal evidence가 모인 뒤 수행한다. requested order,
accepted response, requested price/size, settlement assumption을 realized P&L로 바꾸지 않는다.
CRITICAL/HIGH gap, mixed cohort, fee 누락, unresolved open state, 표본 부족이 있으면 수익성·
scale-up 판단을 중단한다. 0.98/0.99는 선행 표본이 매우 작은 보수적 pilot이며 “최적값”으로
간주하지 않는다.

동기화 후 verified catalog DB 절대 경로만 audit에 넘긴다.

```bash
cd daily-rsync
uv run daily-rsync verify --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync verify --job polybot-dog --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-dog --strategy golden-watermelon-live

cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-cat-db> \
  --db <verified-dog-db> \
  --days 7 \
  --as-of 2026-08-31 \
  --output-dir <output-dir> \
  --strict
```

## 2026-08-25 v2b 첫 health gate와 v2f 안전성 배포

### 검토 경계와 evidence cutoff

- 고정 범위: `[2026-08-24T16:15:37Z, 2026-08-25T16:15:37Z)`
  (`2026-08-25 01:15:37`–`2026-08-26 01:15:37 KST`). SQL 비교는 저장 문자열의
  `+00:00`/`Z` 표기 차이 때문에 lexical compare가 아니라 `julianday()`를 사용했다.
- 이 범위 안 v2b 실제 수집은 두 arm 모두 첫 run `16:15:37Z`부터 마지막 run 종료
  `2026-08-25T11:30:42Z`까지 약 19시간 15분이다. 수수료·lifecycle 결함을 고치기 위해
  timer를 선제 중지하고 새 epoch로 전환했으므로 나머지 약 4시간 45분은 v2b cadence
  coverage가 없다. 따라서 이를 완전한 24시간 성과 표본으로 부르지 않는다.
- 종료 시각 이후 final sync는 Cat `9d7e603a85bb4546abe0ab514fbe73cb`
  (`16:16:56Z`–`16:17:50Z`), Dog `fcc3072fcd4e4bfb8634c07c3dae1f6d`
  (`16:18:37Z`–`16:19:30Z`)다. 두 sync/verify는 고정 종료 뒤 `SUCCESS`였고
  각각 1,444/1,446 artifact에서 failed/conflict/retention skip이 0이다.
- immutable v2b DB:
  - Cat: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-watermelon-live/runtime/watermelon-live-cat-98-1m-v2b/databases/latest/trades.db`
    (`SHA-256 bc5226d2bbf1715bfda7a99610a3e10587205e06c6627664ad33e67b51c38efb`,
    source cutoff `2026-08-25T11:30:43.791966Z`)
  - Dog: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-watermelon-live/runtime/watermelon-live-dog-99-1m-v2b/databases/latest/trades.db`
    (`SHA-256 c0c6c52133589e0c703b62763ad5dfcced766c36678a063b78965b3d1b64b0d8`,
    source cutoff `2026-08-25T11:30:43.722251Z`)

### v2b collection/execution health

| 항목 | Cat 0.98 | Dog 0.99 |
|---|---:|---:|
| SUCCESS run / sweep | 1,154 / 1,154 | 1,154 / 1,154 |
| run 초 min / avg / max | 0.783 / 1.074 / 3.030 | 0.796 / 1.055 / 2.355 |
| 시작 간격 초 min / avg / max | 52.797 / 60.107 / 158.393 | 47.906 / 60.107 / 162.532 |
| cursor-complete / pages | 1,154 / 모두 1 page | 1,154 / 모두 1 page |
| raw=unique / qualified / exact-book 저장 | 201,337 / 1,020 / 978 | 201,337 / 1,020 / 978 |
| trade / entry / order / confirmed fill / resolution | 1 / 1 / 1 / 1 / 1 | 0 / 0 / 0 / 0 / 0 |
| 최대 reserved / 최소 remaining capacity | 1 / 19 | 0 / 20 |
| blocked / untracked BUY / reconciliation error | 0 / 0 / 0 | 0 / 0 / 0 |

- 두 DB 모두 `quick_check=ok`, FK 위반 0, missing condition ID 0, duplicate raw 0이다.
  1,020개 exact-book 대상 중 978개가 저장돼 95.88% coverage다. 나머지 42개는 16개
  sweep에서 CLOB book이 없었던 경우이며 candidate나 체결로 추정하지 않았다.
- 허용 리그에서 전체 HOME/DRAW/AWAY YES 세트를 확인한 경기는 오사수나–레반테,
  풀럼–첼시, 말라가–데포르티보 세 경기다. 볼로냐–라치오와 로마–피오렌티나는 frozen
  five-league 계약에 없는 Serie A라 `sport=sea`로 정상 제외됐다.
- 확인된 최고 exact `$5` ask VWAP은 오사수나 무승부 `0.98`, 첼시 승 `0.97`, 말라가
  무승부 `0.96`이었다. 따라서 “경기 5개이면 주문 5개”가 아니라, 리그·완전한 result
  membership·해당 arm 가격 범위를 모두 통과한 오사수나 무승부 한 건만 Cat 후보였다.
  Dog의 `0.99` 후보는 없었다.
- Cat은 오사수나–레반테 DRAW YES를 `2026-08-24T19:19:36Z`에 exact `$5` FOK로
  매수했고 `5.102 @ 0.98`이 `MATCHED`/`CONFIRMED`됐다. partial fill, pending 고착,
  reconciliation gap은 없다. `19:50:38Z` unique one-hot resolution에서 DRAW가 승리해
  `RESOLVED`됐다.
- v2b는 이 체결의 taker fee를 `0`으로 잘못 저장했다. 그래서 DB의
  `settlement_pnl_assumption=+0.10204`는 fee-aware 성과가 아니다. CLOB이 반환한 fee
  parameter로 재계산한 매수 fee는 `$0.00500`, 정정 순증가액은 약 `$0.09704`다. 원본
  v2b DB는 수정하지 않고 v2c 이후 dynamic fee evidence 계약으로 고쳤다.
- Cat bot log에는 resolution 직전 full-depth stop book 부재와 CLOB `404 no orderbook`이
  기록됐지만, 없는 book을 체결로 꾸미지 않고 unique one-hot resolution까지 fail-closed로
  유지했다. Dog bot log에는 같은 오류가 없다. 두 Jenkins 모두 해당 v2b build 1,154개가
  `SUCCESS`, overlap 0이며 traceback/build failure는 없다.
- 초기 DB 대비 증가는 Cat 약 5.855 MiB, Dog 약 5.867 MiB였다. bot log는 각각 약
  5.55 MB/5.52 MB, Jenkins console은 약 9.24 MB/9.20 MB다. 관측된 비율의 단순 외삽은
  DB당 약 7.3 MiB/day, 60일 약 438–439 MiB이며 retention과 경기 밀도에 따라 달라진다.

### v2f 재발 방지 변경과 배포 판정

v2b 수수료 결함을 고친 v2c를 다시 함대 장애 패턴에 대해 정적 감사했고, treatment를
바꾸지 않은 safety epoch v2d→v2e→v2f로 순차 배포했다. 최종 운영 기준은 v2f만이다.

- 기존 방어인 derive-only API key, exact `$5` signed FOK, top-level 경기의 완전한
  HOME/DRAW/AWAY YES membership, e-sports/비허용 리그 제외, no-clean DB epoch와
  non-concurrent Jenkins 실행도 그대로 유지했다.
- capacity는 정상 open state뿐 아니라 `QUARANTINED`와 아직 trade에 연결되지 않은
  불확실 live BUY를 포함한다. 동기식으로 명백히 거절된 `FAILED` BUY만 즉시 해제하고,
  timeout·5xx·malformed response처럼 거래소 결과가 불명확한 POST는 대사 전까지 계속
  예약한다. event cap도 같은 untracked BUY를 센다.
- pending BUY/SELL, unresolved intent, reconciliation gap/error가 있으면 시장 수집은
  계속하지만 신규 BUY는 fail-closed한다. 모든 candidate에 실행/차단 상태와 사유를 남겨
  “후보가 없었음”과 “용량·guard에 막힘”을 구분한다.
- Trade와 EntryEpisode 생성, orphan recovery는 한 transaction으로 commit/rollback한다.
  중간 annotation 실패가 ghost Trade를 남기지 않는다.
- orphan BUY 복구는 condition/event/token/outcome/snapshot identity와 signed exact `$5`
  금액까지 모두 일치할 때만 허용한다. 다른 수동 지갑 포지션을 봇 trade로 편입하지 않는다.
- BUY/SELL 전에 Gamma와 CLOB의 dynamic fee parameter를 일치시킨다. SELL은 먼저 SDK가
  실제 서명 가능한 2-decimal share를 확정한 뒤 그 수량으로 full-depth book을 다시 걷고,
  남는 sub-cent share는 `sell_residual_shares`로 보존한다.
- resolution은 exact condition/event/token catalog identity와 unique one-hot payout을 모두
  요구한다. 닫힌 `[0.5, 0.5]`를 resolution으로 오인하지 않는다.
- Gamma request는 connect 2초/read 5초, cycle당 단일 시도, 최대 4 page로 제한했다.
  긴 `Retry-After`를 같은 1분 build에서 기다리지 않고 다음 build가 재시도한다.

최종 commit은 `7aba490`, runtime은
`watermelon-live-cat-98-1m-v2f` / `watermelon-live-dog-99-1m-v2f`, source digest는
`c3a46fabcf83170017d97d72463bfc59eb59a4bae42af8137730a173ceb6b548`, frozen
preregistration SHA-256은
`59827f149c2666ac764ca81c92a76ccff1df41b0688b7916e979b8ce01e3a311`다.
Cat `[0.98,0.999]`, Dog `[0.99,0.999]`, exact `$5`, stop `0.70`, 5개 리그,
`20/1/20`, 1분 cadence는 그대로다.

- project test 114개, shared observability test 216개, strategy contract verifier의
  25개 전략 검사가 모두 통과했다.
- Jenkins timer를 코드 변경 전에 끄고 manual build 두 번씩 성공한 뒤 복구했다. 이후
  자연 build도 두 번 이상 arm별로 `SUCCESS`였고 checkout commit, v2f runtime,
  non-concurrent, no-clean, 매분 timer를 확인했다.
- 종료 직후 동기화된 v2f DB는 Cat run/sweep `136/136`, Dog `137/137`이 전부
  `SUCCESS`이고 cursor-complete 100%, 모두 1 page다. raw=unique 누계는
  Cat 1,488, Dog 1,674이며 전부 비허용 `spl`/`uwcl` source라 qualified/candidate/trade/
  episode/order/fill/resolution은 0이다. 두 arm 모두 최대 reserved 0, 최소 remaining
  capacity 20, blocked/pending/quarantined/untracked BUY/open BUY evidence gap/reconciliation
  error/metadata drift 0이다.
- Cat DB는 933,888 bytes, SHA-256
  `dbb26e1489e0d134850260a8a9bc9c673516755d0922caa113e22261f0cc5329`, source cutoff
  `2026-08-25T16:16:46.226304Z`; Dog DB는 942,080 bytes, SHA-256
  `e5ecaf4724944b0939933f730062f640e9d7c08828dc6a8c4d61b73faaf35dab`, source cutoff
  `2026-08-25T16:17:46.317662Z`다. 둘 다 `quick_check=ok`, FK 위반 0, single
  config/source/runtime cohort다.
- 동기화된 v2f bot log 4개 5,208행에는 WARNING/ERROR/CRITICAL/Traceback과 guard 문제
  신호가 0이다. Jenkins console은 Cat `#6448–#6582` 135개, Dog `#6343–#6479`
  137개가 모두 `SUCCESS`; arm별 수동 2개 외에는 timer이며 commit/runtime marker가 모두
  v2f와 일치하고 secret assignment 출력은 0이다. 독립 12회 표본과 후속 102개 build
  전수 검사에서도 같은 결과였고, 마지막 현장 재확인 Cat `#6591`/Dog `#6486`도
  각각 5.153s/5.200s `SUCCESS`, queue 0이다.
- Jenkins description 텍스트만 여전히 `v2d`라고 표시되지만 shell, DB runtime, commit,
  config hash는 v2f다. secret-bearing config 전체를 단순 라벨 수정 때문에 다시 POST하지
  않고 이 비기능성 metadata mismatch를 기록했다.

초기 v2f 구간에는 허용 리그 source/candidate 자체가 없어 capacity 20, pending/untracked/
reconciliation gap 0, DB integrity 정상까지만 자연 증명됐다. 실제 candidate가 발생하지 않은
상태에서 fee/order/SELL 경로가 “실전에서 검증됐다”고 과장하지 않는다. 다음 24시간 health에서
첫 candidate/fill이 있으면 해당 동적 경로를 검증하고, 없으면 opportunity 0을 명시한다.
이 health gate로 수익성이나 `0.98`/`0.99` 우열을 선택하지 않는다.

v2f 첫 24시간 health는 arm별 첫 성공 run을 기준으로 따로 고정한다.

- Cat: `[2026-08-25T13:59:54.753889Z, 2026-08-26T13:59:54.753889Z)`
- Dog: `[2026-08-25T13:59:54.767125Z, 2026-08-26T13:59:54.767125Z)`

두 종료 시각과 자연 build 완료 여유를 둔 `2026-08-26 23:05 KST` 이후 점검한다.

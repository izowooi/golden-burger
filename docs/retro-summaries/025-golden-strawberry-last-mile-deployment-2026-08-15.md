# 025 — Golden Strawberry Last Mile 시뮬레이션 배포

- 작성일: 2026-08-15 KST
- Jenkins job: `polybot-shadow-one`
- Strategy/runtime: `golden-strawberry` / `strawberry-shadow-one`
- Mode: accountless simulation, lifecycle `archive_only`
- Workspace: `/Volumes/t7/jenkins/polybot-shadow-one`
- Data contract: `last-mile-clob-v1`
- Code commit: `47c74cb`

## 결론

사용자의 “0.95를 통과한 시장이 1에 수렴하는지, 아니면 0.85/0까지 실패하는지” 가설을
falsifiable counterfactual collector로 구현하고 Jenkins 외장 workspace에 배포했다. 실제
주문·지갑·credential 경로는 source-level로 차단했다. 최초 수동 build `#1`과 DB·bot
log·Jenkins console의 `daily-rsync` sync/verify가 모두 성공했다.

수집 주기는 10분(`7-59/10 * * * *`)으로 확정했다. 전체 population cycle이 약 5.35초라
10분 간격은 충분한 반면, 5분은 현재 검증력 증가 근거 없이 API·저장량을 두 배로 만든다.
첫 cycle은 official entry window 전 baseline이다. 수익성이나 최적 parameter를 아직 판단할
수 없으며, 1주 차는 collection health와 pilot size만 판정한다.

## 검증 설계

Primary policy:

1. sampled token price의 첫 `0.95` 상향 교차;
2. full displayed ask walk 기준 가상 `$5` 진입;
3. 같은 share 수량의 full displayed bid walk가 `0.85` 이하이면 가상 stop;
4. 그 외에는 원본 episode token이 포함된 proven terminal `0/1` payout까지 추적.

같은 frozen cohort에 다음 sensitivity와 strata를 사전등록했다.

- Entry: `0.90`, `0.92`, `0.95`, `0.97`.
- Stop: `none`, `0.80`, `0.85`, `0.90`.
- Target: `none`, `0.98`, `0.99`.
- Strata: sports/non-sports/unknown, category, negRisk, liquidity, total/24h volume, horizon.
- Cost stress: base/severe bps.

첫 관측이 이미 threshold 이상이면 `LEFT_CENSORED`, 관측 gap이 25분보다 길면
`GAP_CENSORED`다. Sampling token price는 crossing 신호일 뿐 execution price가 아니며,
ask/bid depth가 실제 counterfactual economics의 근거다. Gamma metadata는 crossing 후
descriptive enrichment라 eligibility를 바꾸거나 backfill하지 않는다.

## Source와 저장 구조

초기 Gamma full keyset probe는 290 page 이후 429가 재현돼 complete census 운영 source로
부적합했다. 대신 CLOB `/sampling-markets` cursor를 primary population으로 고정했다. 배포 전
probe와 실제 build 모두 약 12.5k markets / 25k outcome tokens를 13 page에서 완주했다.
Gamma는 crossing candidate metadata와 terminal resolution에만 사용한다.

매 cycle의 full source는 raw gzip page 13개와 compact membership blob으로 보존한다. 모든
token의 최신 상태는 labeled mutable cache인 `latest_outcome_state`에 두고, append-only parsed
catalog/outcome row는 crossing 또는 censoring 같은 nontrivial evidence만 적재한다. 이 구조로
첫 DB는 약 31.7MB, 다음 cycle 예상 증가는 약 6.5MB다. 계획치는 entry week 약 6–7GB,
37일 follow-up 전체 약 35GB이며 `storage_metrics` 실측이 우선한다.

## 안전장치

- Exact workspace, APFS T7 UUID, shared sentinel, off-volume UUID pin, daily-rsync marker를
  DB/log/network 전에 검증.
- Free space 100GiB 미만 또는 filesystem 90% 사용 시 fail closed.
- Nonblocking single-writer lock과 SQLite atomic publication.
- Partial/empty/repeated/invalid terminal cursor는 sweep 미발행.
- Credential-like environment key는 빈 값도 거절; `--live`는 filesystem/network 전에 거절.
- Jenkins concurrent build off, clean/wipe 없음, build/console 14일 retention.
- Frozen preregistration SHA-256: `d42e1ff839e8fe03f88a4e653f5ced6d951ede061cd248b6a19424fb0af36ffd`.

## Jenkins 최초 배포 검증

처음에는 TimerTrigger 없이 shell만 구성하고 수동 build `#1`을 실행했다.

- Result/duration: `SUCCESS`, 11.291초.
- Collector runtime: 5.348초.
- Market pages: 13, terminal cursor complete.
- Markets / aligned outcomes: 12,556 / 25,112.
- Parsed evidence catalog/outcome: 2,938 / 2,938.
- Latest outcome state: 25,112.
- First-baseline decisions: 5,890 `LEFT_CENSORED`.
- New crossing/executable episode/path/resolution: 모두 0(official entry window 전 정상값).
- SQLite `quick_check`: `ok`; foreign-key violation과 HIGH/CRITICAL DQ issue: 0.
- DB: 31,686,656 bytes.
- T7: total 999,995,129,856 bytes, free 980,163,596,288 bytes, used ratio 1.983%.
- Strategy source digest: `2f74820f9f06874ca48a4830a7b1727b815b8849499b436a370d5baf943a78ae`.
- Config hash: `24a3c89ab83ac1f5ad5bfded36cc06af8119c5daaf2a57d706b8725911577624`.

성공 후 timer를 `7-59/10 * * * *`로 추가했다. 최종 Jenkins config SHA-256은
`4ecbfcfe261fca33cc0cfefa2f270c3d7b29a8c318003f86d739a83c9caa8835`다.

첫 자연 timer build `#2`도 `SUCCESS`(7.686초)였다. Collector는 cycle 2로 이어져 DB 삭제나
초기화가 없었고, 13 page / 12,525 markets / 25,050 aligned outcomes를 3.377초에 완주했다.
DB는 38,354,944 bytes로 6,668,288 bytes(약 6.36MiB) 증가했고 DQ issue 0,
`quick_check=ok`, source/config cohort 동일을 유지했다.

## Daily-rsync evidence

- Scan: `golden-strawberry`, artifact 3개, 약 30.23MiB.
- Plan: `bb64a3fdc55d0503`.
- Sync run: `08a56b41d6134cfb9d8bce692cc8681d`, `SUCCESS`, transferred 3/3,
  31,698,828 bytes.
- Sync finished: `2026-08-15T01:50:02.701717Z`.
- DB source cutoff: `2026-08-15T01:48:51.483501Z`.
- Local/remote DB SHA-256:
  `a65f0172ead58e356e4d8504d8575da83d26bdd40e189f02fef09b2e2cec3e8a`.
- `locate.analysis_ready=true`; `verify`: checked 3, failed 0, retention skip 0,
  conflict 0, `SUCCESS`.

자연 build `#2` 후 다시 scan/plan/sync/verify했다.

- Final plan: `760cba4070bbf990`.
- Final sync run: `d38b0ceb0bc147dcbcce47105e584270`, `SUCCESS`, transferred 3,
  unchanged/skipped 1, failed 0, 38,366,703 bytes.
- Final sync finished: `2026-08-15T01:57:52.799991Z`.
- Final DB source cutoff: `2026-08-15T01:57:17.190471Z`.
- Final local/remote DB SHA-256:
  `e36609f988cfa6c64225910b836a7ab235aa2026ab78ee9b55e7a78ea5192186`.
- Final `locate.analysis_ready=true`; `verify`: checked 4, failed 0, retention skip 0,
  conflict 0, `SUCCESS`.

Final verified DB를 immutable analyzer로 `[2026-08-15T01:47Z, 02:00Z)` smoke review했다.
Expected slot 2/2, success coverage 100%, duplicate/off-slot 0, cursor/membership/raw linkage 100%,
single cohort, STARTED→SUCCEEDED p95 6.453초로 collection health는 정상이다. 공식 entry window
전이라 executable episode가 0이므로 verdict는 예상대로 `PILOT_UNDERPOWERED`이며, 이 값은
수익성 판정이 아니다.

Verified DB:

`/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-shadow-one/strategies/golden-strawberry/runtime/strawberry-shadow-one/databases/latest/trades_sim.db`

## Frozen clocks와 다음 점검

- Entry cohort: `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)`
  (`2026-08-15 13:00`–`2026-08-22 13:00` KST).
- Follow-up end: `2026-09-21T04:00:00Z` (`2026-09-21 13:00` KST).

24시간 뒤 요청 예시:

> `polybot-shadow-one`을 daily-rsync로 다시 동기화하고 Golden Strawberry Last Mile의 첫
> 24시간 collection health를 검증해줘. 수익성이나 파라미터는 판단하지 말고 cadence,
> cursor/membership, crossing book, Gamma metadata, path/resolution, cohort, DB 무결성과
> 저장공간 증가량을 확인해줘.

Entry week 뒤 요청 예시:

> `polybot-shadow-one`을 다시 동기화하고 `[2026-08-15T04:00:00Z,
> 2026-08-22T04:00:00Z)` Golden Strawberry cohort를 strict review해줘. 0.95/0.85 primary와
> sports/non-sports, liquidity/volume strata를 보여주되 evidence gate를 통과하지 못하면
> 수익성 판단이나 파라미터 추천을 중단해줘.

`PILOT_CANDIDATE` 최소 gate는 50 executable episodes, 30 resolved known event clusters,
metadata/path/resolution 각각 90% coverage다. 통과해도 이번 1주 자료로 최적값을 선택하지
않고 새 frozen 30-day out-of-sample cohort가 필요하다.

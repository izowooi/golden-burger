# L3 AGENTS.md — Golden Strawberry

이 문서는 `golden-strawberry/`에만 적용되는 운영 지침이다. 상위 모노레포 규칙은
`../AGENTS.md`를 따르며, 여기서는 Last Mile research collector의 증거 계약과 배포 안전장치만
추가한다.

## 프로젝트 목적

Golden Strawberry는 고확률 outcome token이 처음 임계값을 상향 교차한 뒤 terminal `0/1`
payout까지 어떤 경로를 보이는지 검정하는 accountless, simulation-only collector다. 주문이나
지갑을 사용하지 않고 CLOB displayed book으로 `$5` counterfactual entry/exit만 기록한다. Entry
window가 끝난 뒤에는 v1을 동결된 read-only source로 두고 compact follow-up v2a가 이미 생성된
episode만 추적한다.

Primary policy는 `0.95` entry, `0.85` stop, 아니면 proven resolution까지 보유다. 다른
entry/stop/target과 sports, outcome type, negRisk, liquidity, total/24h volume은 같은 frozen
cohort의 sensitivity/strata로 측정하며 수집 중 eligibility나 parameter를 바꾸지 않는다.
Category는 crossing-time Gamma metadata로 보존하지만 현재 analyzer의 built-in stratum은 아니다.

## 고정 계약

- Frozen source contract: `last-mile-clob-v1`; retired runtime job: `strawberry-shadow-one`.
- Active follow-up contract: `last-mile-clob-followup-v2a`; runtime job:
  `strawberry-shadow-one-followup-v2a`.
- Failed rollout provenance: Jenkins `#761`은 v2 DB를 만들지 못했다. 기존
  `last-mile-clob-followup-v2` config/prereg/runtime identity는 수정·재사용하지 않는다.
- Jenkins job: `polybot-shadow-one`.
- Workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Timer state: OFF. 승인된 재개 시 frozen cadence는 `7-59/10 * * * *`다.
- Entry window: `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)`.
- Follow-up end: `2026-09-21T04:00:00Z`.
- Frozen v1 preregistration: `research/frozen-2026-08-15-clob/PREREGISTRATION.md`.
- Active follow-up preregistration:
  `research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md`와 같은 폴더의
  `MANIFEST.sha256`.
- v1 DB: `data/strawberry-shadow-one/trades_sim.db` — 영구 read-only source.
- v2a DB: `data/strawberry-shadow-one-followup-v2a/trades_sim.db` — append-only follow-up evidence.

Source population, clock, threshold, cadence 또는 interpretation처럼 experiment identity를
바꾸면 새 frozen directory, data contract, source digest, config hash와 DB로 분리한다. v2a는 새
entry cohort가 아니라 frozen v1 executable episode의 follow-up epoch이며 새 crossing을 만들 수
없다. Jenkins job/workspace 같은 운영 topology를 옮길 때는 새 DB를 조용히 합치지 말고
daily-rsync workspace epoch·marker·routing을 별도로 갱신한다. Git commit은 provenance일 뿐
cohort key가 아니다. Cohort는 `config_hash × strategy_source_digest × mode × job_name`이다.

## 기술 스택과 주요 파일

- Python 3.11+, uv, requests, PyYAML, SQLite, pytest, hatchling.
- `config.yaml`, `src/polybot/config.py`: retired v1 collection contract와 read-only 분석.
- `config.followup-v2a.yaml`, `src/polybot/followup_config.py`: active v2a contract와 pinned v1 source.
- `src/polybot/api/sampling_client.py`: v1 전용 complete CLOB `/sampling-markets` traversal; v2a 금지.
- `src/polybot/api/clob_client.py`: crossing/episode displayed books.
- `src/polybot/api/gamma_client.py`: post-selection metadata와 terminal payout evidence.
- `src/polybot/collector.py`, `src/polybot/db/repository.py`: retired v1 collection/schema.
- `src/polybot/v1_source.py`: v1 `mode=ro` validation, deterministic seed와 anchor drift detection.
- `src/polybot/followup_collector.py`, `src/polybot/db/followup_repository.py`: token-shared compact
  book, fixed-share path, resolution과 atomic v2a publication.
- `src/polybot/analyzer.py`: immutable v1 health/pilot 분석.
- `src/polybot/followup_analyzer.py`: v1 collection + v2a follow-up 결합 health 분석.
- `scripts/verify_external_workspace.py`: T7 APFS identity, UUID pin, marker 검증.
- `OPERATIONS.md`: Jenkins shell, first deployment, daily-rsync, failure handling.

## 실행과 검증

로컬에서는 credential 환경 변수가 없는 상태에서 config와 테스트만 검증한다.

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-15-clob && shasum -a 256 -c MANIFEST.sha256)
(cd research/frozen-2026-08-24-followup-v2a && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot-followup config --simulate \
  --job strawberry-shadow-one-followup-v2a
```

실제 public-data cycle은 Jenkins 외장 workspace에서 `OPERATIONS.md`의 v2a shell 그대로 실행한다.
CLI에는 DB override가 없으므로 로컬에서는 `run` smoke를 만들지 않고 config, mock 기반 test,
manifest와 build까지만 검증한다. v1 `polybot run`은 retired 오류로 종료되어야 한다.

## Fail-closed 안전 규칙

- `--live`와 wallet/order code를 허용하지 않는다. `src/polybot/config.py`의 정확한 9개
  supported Polymarket/CLOB credential key는 빈 값이어도 DB, log, HTTP session 전에 거절한다.
- lifecycle은 `archive_only`, simulation은 `true`, runtime job은 canonical 값만 허용한다.
- 최초 `FULL_SEED`는 1,800초 maintenance budget 안에서 canonical seed를 한 번만 import한다.
  Publication이 실패하면 seed는 immutable하게 유지하되 성공한 atomic `FULL_SEED` cycle이 생길
  때까지 deployment phase로 재검증하며 `PINNED_FAST`로 전환하지 않는다.
  이후 매 `PINNED_FAST` cycle은 HTTP/publication 전에 canonical v1 DB를 SQLite `mode=ro`로 열어
  exact stat/schema/data contract/window/job/latest successful cutoff/source anchor를 대조하고,
  imported episode/condition/threshold의 모든 row hash, exact count, aggregate hash와 terminal count를
  재검증한다. Sidecar 또는 drift가 있으면 FAILED만 기록하고 중단하며 v1이나 기존 v2를 수정하지 않는다.
- `PINNED_FAST`의 shared cooperative network deadline은 450초이고 CLOB/Gamma batch, timeout,
  retry, sleep, `Retry-After`에 전파한다. Cycle은 recurring 480초 hard SLA 전에 clean failure 또는
  atomic success로 끝나야 한다.
- Jenkins `WORKSPACE`, T7 APFS UUID, off-volume UUID pin, sentinel, daily-rsync marker를 DB/log/network
  전에 검증한다.
- 100GiB 미만 free space, 90% 이상 filesystem usage, overlapping writer, partial/repeated cursor,
  malformed source, manifest/config/source digest drift는 publish 전에 중단한다.
- Jenkins에 `clean`, workspace wipe, DB 삭제, credential binding, concurrent build를 추가하지 않는다.
- retired v1 timed shell에는 `run`을 남기지 않는다. v1 `status`/`health`는 대형
  append-only DB 전체 `quick_check`와 exact count를 수행하는 maintenance 진단이며, 12GiB에서
  함께 약 19분이 걸려 10분 cadence를 붕괴시켰다. 정기 cycle의 성공·storage guard는 run
  audit로 확인한다. v2a `status`/`health`는 quick check 없는 lightweight 진단이지만 periodic
  shell에는 넣지 않는다. v1 `quick_check`는 명시적인 maintenance 또는 combined analyzer의
  `--deep-v1`에서만 실행한다.
- 이 프로젝트 작업으로 다른 Jenkins job이나 live strategy를 수정하지 않는다.

## Evidence 규칙

- CLOB sampling token `price`는 crossing signal일 뿐 executable price가 아니다. Entry는 complete
  displayed ask walk, exit path는 원래 share 수량의 displayed bid walk로만 계산한다.
- 첫 관측이 이미 임계값 이상이면 `LEFT_CENSORED`, 25분보다 긴 gap 뒤 교차면
  `GAP_CENSORED`다. 두 경우를 정상 first crossing으로 복원하지 않는다.
- Gamma metadata는 crossing 후 측정한 descriptive evidence이며 eligibility를 바꾸거나
  backfill하지 않는다.
- `0.98/0.99` 도달은 resolution이 아니다. closed Gamma market, 유일한 winner, episode 원본
  token의 payout map 포함을 모두 확인해야 terminal 결과다.
- Source pages, compact membership, request receipts, crossings, books/levels, episodes, paths,
  metadata, resolution, quality issues, run provenance, storage metrics는 append-only로 보존한다.
  `latest_outcome_state`만 명시적인 mutable cache다.
- v2a는 v1 raw evidence를 복제하지 않는다. Imported canonical seed/anchor, API receipts, cycle마다
  distinct token당 canonical gzip full-book 한 row, episode path/threshold, unique one-hot Gamma
  resolution, run/storage/phase timing만 append한다. `clob_levels`와 row-per-level 저장은 금지한다.
- v2a는 imported unresolved episode의 distinct token/condition만 요청한다. `/sampling-markets`,
  crossing detection, Gamma candidate metadata, 새 entry는 금지하고 resolved condition은 이후
  book/resolution 요청에서 제외한다.
- Cycle/path/resolution/threshold, phase timing, successful storage metric과 terminal `SUCCEEDED`는
  하나의 transaction으로 commit한다. Post-publication 예외는 전부 rollback하고 durable
  `FAILED`만 남기며, partial cycle을 성공으로 추정하거나 missing evidence를 합성하지 않는다.

## 분석과 판정

먼저 `daily-rsync scan` 후 별도 plan/sync를 실행하고, `locate`가 반환하며 `verify`가 통과한
절대 경로의 DB만 immutable Strawberry analyzer에 넘긴다. Generic trading
`polybot-retro audit --strict`는 secondary 참고일 뿐이며 trade/fill/P&L table 부재는 이
accountless collector의 evidence gap이 아니다. 분석 range는 UTC half-open interval로 고정하고
DB SHA-256, sync/source cutoff, config hash, source digest, runtime job을 보고서에 남긴다.
Follow-up 전환 뒤에는 검증된 v1/v2a 두 DB를 `polybot-followup analyze`에 함께 넘긴다. 기본값은
30GB v1 `quick_check`를 생략하며, health-only 분석은 수익성·parameter 선택·live 승격을 계산하지
않는다. One-time `FULL_SEED`는 recurring 480초 cadence 위반으로 세지 않고 rollout health range는
first successful natural `PINNED_FAST` slot에서 시작한다.

1주 차 판정은 세 가지뿐이다. Health 또는 `quick_check` 실패는 `HEALTH_ONLY`다. Health는
통과했지만 50 executable episodes, 30 resolved known event clusters, metadata/path/resolution 각각
90% coverage 중 하나라도 부족하면 `PILOT_UNDERPOWERED`다. 모두 충족해야 최대
`PILOT_CANDIDATE`다. 자연 발생 sample 부족을 backfill하거나 frozen window를 연장하지 않는다.
Lineage/coverage 구현 결함은 가설 판정과 분리해 instrument failure로 복구한다. Gate를
만족하더라도 같은 cohort로 최적 parameter나 수익성을 확정하지 않고, 새 frozen 30-day
out-of-sample cohort 전에는 live를 추천하지 않는다.

## 변경 시 함께 확인할 것

- Source/API parser 변경: raw lineage, cursor terminal/count/duplicate 검증과 client tests.
- Crossing/path 변경: censoring precedence, fixed-share book arithmetic, resolution token mapping과
  collector/analyzer tests.
- Schema 변경: append-only triggers, foreign keys, atomic rollback, SQLite `quick_check`, source digest.
- Config/prereg 변경: YAML, Python constants, preregistration, manifest, docs, root contract verifier를
  같은 변경으로 맞춘다.
- Jenkins 변경: 처음에는 timer 없이 수동 1회 성공시키고 console·DB·disk metric을 확인한 뒤
  timer를 추가한다. 이후 최소 한 번의 natural build와 daily-rsync sync/verify를 확인한다.

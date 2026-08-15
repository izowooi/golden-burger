# L3 AGENTS.md — Golden Strawberry

이 문서는 `golden-strawberry/`에만 적용되는 운영 지침이다. 상위 모노레포 규칙은
`../AGENTS.md`를 따르며, 여기서는 Last Mile research collector의 증거 계약과 배포 안전장치만
추가한다.

## 프로젝트 목적

Golden Strawberry는 고확률 outcome token이 처음 임계값을 상향 교차한 뒤 terminal `0/1`
payout까지 어떤 경로를 보이는지 검정하는 accountless, simulation-only collector다. 주문이나
지갑을 사용하지 않고 CLOB displayed book으로 `$5` counterfactual entry/exit만 기록한다.

Primary policy는 `0.95` entry, `0.85` stop, 아니면 proven resolution까지 보유다. 다른
entry/stop/target, sports, category, liquidity, volume은 같은 frozen cohort의 sensitivity/strata로
측정하며 수집 중 eligibility나 parameter를 바꾸지 않는다.

## 고정 계약

- Data contract: `last-mile-clob-v1`.
- Jenkins job: `polybot-shadow-one`; runtime job: `strawberry-shadow-one`.
- Workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Cadence: `7-59/10 * * * *`.
- Entry window: `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)`.
- Follow-up end: `2026-09-21T04:00:00Z`.
- Active preregistration: `research/frozen-2026-08-15-clob/PREREGISTRATION.md`와 같은 폴더의
  `MANIFEST.sha256`.

이 값을 바꾸면 기존 cohort를 수정하지 말고 새 data contract와 새 frozen preregistration으로
분리한다. Git commit은 provenance일 뿐 cohort key가 아니다. Cohort는
`config_hash × strategy_source_digest × mode × job_name`이다.

## 기술 스택과 주요 파일

- Python 3.11+, uv, requests, PyYAML, SQLite, pytest, ruff, hatchling.
- `config.yaml`, `src/polybot/config.py`: frozen clocks, thresholds, storage/API contract.
- `src/polybot/api/sampling_client.py`: complete CLOB `/sampling-markets` cursor traversal.
- `src/polybot/api/clob_client.py`: crossing/episode displayed books.
- `src/polybot/api/gamma_client.py`: post-selection metadata와 terminal payout evidence.
- `src/polybot/collector.py`: crossing, censoring, book path, resolution orchestration.
- `src/polybot/db/repository.py`: append-only schema와 atomic publication.
- `src/polybot/analyzer.py`: immutable verified DB의 health/pilot 분석.
- `scripts/verify_external_workspace.py`: T7 APFS identity, UUID pin, marker 검증.
- `OPERATIONS.md`: Jenkins shell, first deployment, daily-rsync, failure handling.

## 실행과 검증

로컬에서는 credential 환경 변수가 없는 상태에서 config와 테스트만 검증한다.

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-15-clob && shasum -a 256 -c MANIFEST.sha256)
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot config --simulate --job strawberry-shadow-one
```

실제 public-data cycle은 Jenkins 외장 workspace에서 `OPERATIONS.md`의 shell 그대로 실행한다.
로컬 smoke가 필요하면 disposable temp DB만 사용하고 프로젝트 `data/`에 실험 데이터를 만들지
않는다.

## Fail-closed 안전 규칙

- `--live`, credential-like environment key(빈 값 포함), wallet/order code를 허용하지 않는다.
- lifecycle은 `archive_only`, simulation은 `true`, runtime job은 canonical 값만 허용한다.
- Jenkins `WORKSPACE`, T7 APFS UUID, off-volume UUID pin, sentinel, daily-rsync marker를 DB/log/network
  전에 검증한다.
- 100GiB 미만 free space, 90% 이상 filesystem usage, overlapping writer, partial/repeated cursor,
  malformed source, manifest/config/source digest drift는 publish 전에 중단한다.
- Jenkins에 `clean`, workspace wipe, DB 삭제, credential binding, concurrent build를 추가하지 않는다.
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
- Partial cycle을 성공으로 추정하거나 missing evidence를 합성하지 않는다.

## 분석과 판정

분석에는 Jenkins 원본 DB가 아니라 `daily-rsync locate`가 반환하고 `daily-rsync verify`가 통과한
절대 경로의 DB만 사용한다. 분석 range는 UTC half-open interval로 고정하고 DB SHA-256, sync/source
cutoff, config hash, source digest, runtime job을 보고서에 남긴다.

1주 차 판정은 `HEALTH_ONLY`, `PILOT_UNDERPOWERED`, `PILOT_CANDIDATE`만 허용한다. 50 executable
episodes, 30 resolved known event clusters, metadata/path/resolution 각각 90% coverage를 만족하지
못하면 부족한 evidence를 먼저 복구한다. 만족하더라도 같은 cohort로 최적 parameter나 수익성을
확정하지 않고, 새 frozen 30-day out-of-sample cohort 전에는 live를 추천하지 않는다.

## 변경 시 함께 확인할 것

- Source/API parser 변경: raw lineage, cursor terminal/count/duplicate 검증과 client tests.
- Crossing/path 변경: censoring precedence, fixed-share book arithmetic, resolution token mapping과
  collector/analyzer tests.
- Schema 변경: append-only triggers, foreign keys, atomic rollback, SQLite `quick_check`, source digest.
- Config/prereg 변경: YAML, Python constants, preregistration, manifest, docs, root contract verifier를
  같은 변경으로 맞춘다.
- Jenkins 변경: 처음에는 timer 없이 수동 1회 성공시키고 console·DB·disk metric을 확인한 뒤
  timer를 추가한다. 이후 최소 한 번의 natural build와 daily-rsync sync/verify를 확인한다.

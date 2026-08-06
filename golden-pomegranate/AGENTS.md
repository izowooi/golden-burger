# Project AGENTS.md

이 문서는 `golden-pomegranate` 하위 프로젝트에만 적용되는 작업 지침이다.

## 프로젝트 목적

Golden Pomegranate는 Polymarket 공개 시장 상태를 장기간 보존하는 accountless research-only collector다.
wallet, 주문, position, fill, P&L을 다루지 않으며 trading strategy 또는 수익성 evidence로 해석하지 않는다.

## 기술 스택

- Python 3.11 이상, `uv`, Hatchling을 사용한다.
- public HTTP client는 `requests`, 설정은 `PyYAML`, persistence는 SQLite WAL과 `synchronous=FULL`을 사용한다.
- `polybot-observability`는 local editable dependency다.
- test runner는 pytest이며 CI/운영 runtime은 Jenkins와 macOS external APFS volume이다.

## 주요 파일

- `README.md`: 수집, storage, cadence, Jenkins 운영 계약이다.
- `STRATEGY.md`: trading strategy가 아닌 research hypothesis와 falsification gate다.
- `research/2026-08-06-preregistration.md`: 최초 7-day collection health gate다.
- `config.yaml`, `src/polybot/config.py`: simulation-only resolved config와 fail-closed validation이다.
- `src/polybot/main.py`, `src/polybot/bot.py`: CLI와 disk, lock, shard rotation, collection orchestration이다.
- `src/polybot/run_audit.py`: local `ResearchRunAudit`와 immutable run event lifecycle을 구현한다.
- `src/polybot/collector.py`, `src/polybot/api/`: Gamma census, public CLOB sample, Data API tape를 수집한다.
- `src/polybot/db/repository.py`: `research-full-v1` schema, `research_config_versions`, `research_run_events`, UTC daily shard와 manifest를 관리한다.
- `Jenkinsfile`, `tests/`: external APFS 운영 계약과 executable contract test다.

## 실행

의존성을 설치하고 read-only operator check를 먼저 수행한다.

```bash
cd golden-pomegranate
uv sync --frozen --extra dev
uv run polybot config --simulate --job pomegranate-local
uv run polybot health --simulate --job pomegranate-local
uv run polybot status --simulate --job pomegranate-local
uv run polybot export-manifest --simulate --job pomegranate-local
```

한 번의 실제 public collection cycle이 필요한 경우에만 다음 명령을 사용한다.

```bash
uv run polybot run --simulate --job pomegranate-local
```

`--simulate`는 network mock이 아니다.
`run`은 public Gamma, CLOB, Data API를 호출하고 `data/<job>/`의 SQLite evidence를 append한다.

## 테스트

전체 contract test를 실행한다.

```bash
uv run pytest
```

safety, run provenance, source envelope 또는 Jenkins 계약을 변경하면 관련 test를 먼저 좁혀 실행한 뒤 전체 suite를 실행한다.

```bash
uv run pytest tests/test_research_safety.py tests/test_cli.py tests/test_config.py tests/test_run_audit.py
uv run pytest tests/test_gamma_client.py tests/test_clob_client.py tests/test_trade_tape.py
uv run pytest tests/test_storage.py tests/test_jenkins_contract.py
uv run pytest
```

## 빌드

```bash
uv build
```

## 배포

trading 또는 application 배포는 없다.
`Jenkinsfile`이 `H/15 * * * *`, 13분 timeout, `disableConcurrentBuilds()`로 한 cycle씩 실행한다.
Jenkins 순서는 `config → health → run → status → health`로 유지한다.
10분 cadence는 7개의 완결된 UTC day와 `STRATEGY.md`의 health gate를 모두 통과한 뒤 새 cohort로만 검토한다.

## 작업 규칙

- authenticated client, order submission, account, position, fill, `ExecutionLedger`, P&L code path나 dependency를 추가하지 않는다.
- Gamma는 `/markets/keyset`의 `closed=false` envelope를 terminal cursor까지 순회하고 반환된 모든 market과 variable outcome을 보존한다.
- Gamma에 `active`, liquidity, volume, category, probability, date, binary 여부 등의 client-side filter를 추가하지 않는다.
- repeated cursor, malformed page, page limit 또는 request failure가 있으면 Gamma census bundle 전체를 rollback한다.
- public CLOB은 complete Gamma frame을 SHA-256 bucket에 배치하고 UTC shard와 독립적인 cadence slot·deterministic cyclic window로 market을 회전 선택한다.
- 선택 market의 모든 distinct public outcome token을 `/books`로 조회하고 selection denominator, rank, truncation, empty/error metadata를 보존한다.
- public Data API는 `/trades?takerOnly=true`의 bounded polling tape만 수집한다.
- Data API는 최초 immutable 24시간 bootstrap baseline, cycle당 최대 1시간 catch-up, 300초
  safety lag, 1,800초 overlap, explicit `takerOnly=true`, 10,000-row recursive split 계약을
  유지한다. 24시간 bootstrap을 한 cycle에 전부 처리했다고 주장하지 않는다.
- 하나의 epoch timestamp만 남은 window가 cap에 닿거나 request/parser가 실패하면
  `possible_gap`을 남기고 complete watermark를 전진시키지 않는다.
- Data API display/profile field를 raw JSON이나 normalized row에 저장하지 않고 taker-side economic-event 범위를 넘어 해석하지 않는다.
- Gamma census는 primary atomic bundle이며 CLOB, Data API, resolution은 독립 source-component status와 missingness를 남긴다.
- `resolution`과 `redeemable`, `volume`과 `volume24hr`, source clock과 page receipt UTC를 서로 추론하거나 fallback하지 않는다.
- audit start가 실패하면 source fetch를 시작하지 않으며 `STARTED` 뒤에는 `SUCCEEDED` 또는 `FAILED` terminal event를 append한다.
- cohort는 `config_hash × strategy_source_digest × mode × job_name × schema_profile`로 나누고 Git commit은 provenance로만 사용한다.
- `strategy_source_digest`에는 collector source와 실제로 import하는 shared config contract만 포함한다.

## Mock / Dry-run 규칙

- unit/integration test에서는 injected fake `requests.Session`, temporary SQLite path, fixed clock과 mocked disk usage를 사용한다.
- network error, repeated cursor, partial book, 10,000-row cap, disk stop과 shard collision을 fail-visible test로 유지한다.
- 단순 검증에 `polybot run`을 사용하지 않는다. public network와 evidence write가 필요한 명시적 collection 작업에만 사용한다.
- `--live`와 credential은 negative test에서만 전달하고 DB, logger, bot 또는 HTTP session construction 이전 실패를 assert한다.

## 환경 변수

- public override의 기준은 `.env.example`과 `src/polybot/config.py`의 allowlist다. unknown `POLYBOT_*` key도 거부한다.
- `POLYBOT_LIFECYCLE_MODE`, `POLYBOT_SIMULATION_MODE`, `POLYBOT_DATA_CONTRACT`의 안전 경계를 완화하지 않는다.
- Jenkins의 `POMEGRANATE_MOUNT_ROOT`, `JOB_NAME`, `WORKSPACE`는 mount와 runtime identity다.

## 자주 깨지는 부분

- Gamma page/raw payload/full membership/count/digest는 하나의 transaction 경계다. 일부 row만 먼저 commit하지 않는다.
- `research_config_versions`와 `research_run_events`는 append-only다. config 변경은 새 hash로, run 상태 변경은 새 event row로 기록한다.
- fact table guard를 우회하는 `UPDATE`, `REPLACE` 또는 `VACUUM`을 도입하지 않는다.
- active `trades_sim.db`는 UTC day 경계에서만 완결된 WAL checkpoint와 `quick_check`를 거친 뒤,
  같은 APFS volume의 hard-link dated shard + 준비된 새 active의 atomic replace로 회전한다.
  중단된 same-inode handoff는 재개하고 기존의 다른 dated shard는 덮어쓰지 않는다.
- Jenkins는 checkout 전에 exact external APFS mount, sentinel profile, off-volume host UUID pin을 확인한다.
- workspace는 symlink가 아닌 canonical `${POMEGRANATE_MOUNT_ROOT}/jenkins/workspace/${JOB_NAME}`이며 승인 mount와 같은 filesystem device여야 한다.
- UUID 검증 뒤 그 workspace에만 exact `.daily-rsync-workspace.json` marker를 원자적으로 쓰며,
  Daily Rsync가 default workspace를 추측하도록 두지 않는다.
- filesystem 사용률 70% 이상은 warning, 80% 이상 또는 free space 150 GiB 미만은 collection stop이다.
- single-writer lock, `PRAGMA quick_check`, shard rotation 또는 disk gate 실패를 우회해 collection을 진행하지 않는다.

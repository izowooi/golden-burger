# Repository AGENTS.md

이 문서는 `golden-raspberry` 단일 프로젝트 전체에 적용되는 L3 terminal 작업 지침이다.
상위 `/Users/izowooi/git/t1/AGENTS.md`의 Git·보안·workspace 규칙을 상속하며 여기서 반복하지 않는다.

## 저장소 목적

Golden Raspberry는 public Polymarket Gamma/CLOB의 지속적인 YES/NO displayed-depth imbalance가 60분 뒤 `$5` ask-to-bid executable counterfactual return을 예측하는지 검정하는 accountless research-only collector다.
주문이나 실거래 수익을 만들지 않고 `queue-echo-v1` 가설 검정용 DB·raw payload·lineage를 수집한다.

## 구조

- `src/polybot/`: CLI, public Gamma/CLOB client, collector, append-only repository, run audit와 source digest를 구현한다.
- `scripts/`: 검증된 세 shard DB를 읽는 experiment analyzer를 둔다.
- `tests/`: config, lifecycle, research safety, public API, collector, repository, run audit와 analyzer 계약을 검증한다.
- `research/frozen-2026-08-13/`: outcome 관측 전에 고정한 `PREREGISTRATION.md`와 그 checksum을 담은 `MANIFEST.sha256`를 보존한다.
- `data/`: runtime job별 persistent SQLite DB, process lock와 bot log를 보존한다.
- `dist/`: wheel과 source distribution build artifact를 둔다.
- `__pycache__/`: 생성된 Python bytecode cache이며 source로 취급하지 않는다.

주요 root 계약은 `README.md`, `OPERATIONS.md`, `STRATEGY.md`, `config.yaml`, `pyproject.toml`, `uv.lock`에 있다.

## 공통 작업 원칙

- `simulation_mode=true`, `lifecycle_mode=archive_only`, `data_contract=queue-echo-v1` 경계를 유지한다.
- Primary는 3회 지속 `MI`다.
- 1회 `DO`와 2회 `RE`는 sensitivity로만 사용하고 관측 결과에 따라 primary로 승격하지 않는다.
- preregistration 이후 threshold, timing, feature direction, control, stress와 winner를 같은 data를 보고 바꾸지 않는다.

### 데이터 및 evidence invariants

- Gamma page는 object여야 하고 `markets`는 object로 구성된 list여야 하며 `next_cursor`는 string 또는 null이어야 한다.
- Gamma cursor는 전진하고 반복되지 않아야 하며 `max_pages` 전에 terminal cursor에 도달한 sweep만 atomically publish한다.
- malformed 또는 truncated HTTP/JSON은 transport/parser error로 실패하며 완료되지 않은 universe를 정상 evidence로 사용하지 않는다.
- YES/NO full book pair는 같은 CLOB `/books` HTTP request에서 받고 동일한 `request_id`와 2초 이하의 receipt skew를 가져야 한다.
- size chunking과 error bisection에서도 YES/NO pair를 atomic unit으로 유지하고 analyzer의 same-request pair coverage가 100%인지 확인한다.
- token book의 missing, error 또는 malformed 상태를 depth 0으로 채우지 않고 censoring reason과 raw request receipt를 남긴다.
- 매 cycle의 full Gamma membership은 normalized gzip으로, CLOB raw body는 gzip과 SHA-256으로 보존한다.
- 세 Jenkins job은 full `sha256(condition_id)` integer modulo 3으로 나눈 deterministic source shard다.
- 각 shard의 동일 raw stream에서 `DO`, `RE`, `MI`를 모두 계산하며 job 이름을 experimental arm으로 해석하지 않는다.
- frozen experiment window는 `[2026-08-13T01:00:00Z, 2026-09-12T01:00:00Z)`다.
- SQLite evidence는 append-only로 유지하고 preregistration, config/source digest, run lifecycle, raw payload hash와 decision/follow-up lineage를 함께 보존한다.
- 분석 cohort는 `config_hash × strategy_source_digest × mode × job_name`으로 분리한다.
- `SOURCE_PATHS`의 root/analysis scope는 `pyproject.toml`, `uv.lock`, `config.yaml`, `STRATEGY.md`, frozen `PREREGISTRATION.md`, `scripts/analyze_experiment.py`다.
- Runtime/collection scope는 `src/polybot/main.py`, `bot.py`, `config.py`, `run_audit.py`, `source_digest.py`, `api/gamma_client.py`, `api/clob_client.py`, `collector.py`, `db/repository.py`, `utils/retry.py`다.
- evidence에 영향을 주는 새 파일이 `SOURCE_PATHS` 밖에 있으면 수집 전에 allowlist를 확장하고 새 source digest cohort로 기록한다.
- source 또는 config가 바뀌면 confirmatory 30일 시작점을 최종 healthy cohort의 첫 complete slot로 다시 고정한다.
- follow-up은 entry 후 60분부터 75분 사이에 시작한 첫 독립 request를 due case당 정확히 한 번만 시도한다.
- window 안에서 시도되지 않은 case만 75분 뒤 `WINDOW_EXPIRED`로 terminal 처리한다.
- 첫 attempt의 성공 여부와 관계없이 case를 terminal 처리하고 analyzer도 첫 attempt를 읽으며 이후 성공 quote로 교체하지 않는다.
- follow-up quote 또는 depth가 부족하면 0, 마지막 가격, forward-fill이나 resolution payout으로 대체하지 않고 censor한다.
- neutral control은 다른 event에서 price 10pp bin, horizon bin, depth 2배 이내와 prior-15m ask move bin이 모두 같아야 한다.
- 결과는 displayed-book counterfactual evidence이며 실제 fill, realized P&L 또는 queue position 증거로 표현하지 않는다.

## 작업 전 확인

1. 이 문서와 상위 `../AGENTS.md`
2. `README.md`와 `STRATEGY.md`
3. `research/frozen-2026-08-13/PREREGISTRATION.md`와 `MANIFEST.sha256` 검증 결과
4. 운영·배포·복구 작업이면 `OPERATIONS.md`
5. `config.yaml`, `pyproject.toml`, `uv.lock`
6. 변경 대상 `src/polybot/` 또는 `scripts/` 코드와 대응하는 `tests/`

## 공통 명령어

모든 명령은 프로젝트 root에서 실행한다.

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-13 && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

세 runtime config를 read-only로 확인한다.

```bash
uv run polybot config --simulate --job raspberry-do-shard-0
uv run polybot config --simulate --job raspberry-re-shard-1
uv run polybot config --simulate --job raspberry-mi-shard-2
```

분석에는 daily-rsync catalog가 가리키는 검증된 DB 절대 경로 세 개를 모두 명시한다.

```bash
uv run python scripts/analyze_experiment.py \
  --start <UTC> \
  --end <UTC> \
  --db DO=<absolute-db> \
  --db RE=<absolute-db> \
  --db MI=<absolute-db> \
  --output <absolute-json>
```

세 DB label은 source/Jenkins lineage를 식별할 뿐이며 특정 persistence arm을 전담한다는 뜻이 아니다.

## 검증 기준

- 코드 변경 후 전체 test를 실행하고 package build를 확인한다.
- 별도 lint와 static type-check 명령은 현재 정의되어 있지 않으므로 임의 도구를 검증 기준으로 추가하지 않는다.
- frozen 계약을 사용하는 작업과 배포 전 `MANIFEST.sha256` 검증을 통과시킨다.
- lifecycle 또는 config 변경은 정확한 credential allowlist와 live 거부가 DB, log와 HTTP session 생성보다 먼저 일어나는지 검증한다.
- Gamma/CLOB 변경은 page shape, cursor progress/no repetition, terminal cursor, atomic YES/NO request, raw payload linkage와 request clock을 함께 검증한다.
- repository 또는 analyzer 변경은 append-only lineage, 세 shard 비중복, cohort 분리, first-attempt follow-up과 neutral prior-move matching을 검증한다.
- 첫 24시간에는 수익성을 판정하지 않고 7 complete UTC day에는 collection health만 판정한다.
- confirmatory 결론은 최종 단일 healthy cohort 30일과 frozen MI gate를 모두 충족한 뒤에만 낸다.
- MI가 DO보다 persistence 정보를 더하는지는 같은 episode의 severe-stress `MI−DO`
  event-cluster 95% lower bound가 양수일 때만 주장한다.
- `CRITICAL` 또는 `HIGH` evidence gap이 있으면 수익성 해석과 parameter tuning을 중단한다.

## 루트 설정 변경 기준

다음 파일은 experiment identity 또는 재현성에 직접 영향을 주므로 변경 전에 cohort와 배포 영향을 확인한다.

- `config.yaml`: mode, public endpoint, frozen threshold, experiment window와 storage guard를 정의한다.
- `research/frozen-2026-08-13/PREREGISTRATION.md`와 `MANIFEST.sha256`: frozen confirmatory 계약과 checksum이며 outcome 관측 뒤 수정하거나 덮어쓰지 않는다.
- `STRATEGY.md`: 가설, feature, control, censoring과 falsification 기준을 정의한다.
- `pyproject.toml`과 `uv.lock`: package, entry point와 dependency 해석을 함께 고정한다.
- `.env.example`: accountless public collector 경계만 문서화하고 credential 입력 경로를 추가하지 않는다.
- `README.md`와 `OPERATIONS.md`: 외부 Jenkins Freestyle job의 shell, schedule, 보존과 복구 절차의 저장소 내 권위다.

Frozen 계약을 바꿔야 하면 기존 파일을 덮어쓰지 말고 새 frozen 디렉토리, preregistration, manifest, config/source digest와 collection window를 정의한다.

## 주의사항

- Jenkins clean workspace/build, generic clean 또는 wipe로 `data/`를 지우지 않는다.
- `data/<runtime-job>/trades_sim.db`, lock와 logs는 persistent experiment evidence다.
- SQLite는 single-writer Jenkins cadence와 read-only `daily-rsync` snapshot을 위해 rollback
  `DELETE` journal을 사용한다. transaction 중 잠깐 생기는 `-journal`은 evidence 파일로
  따로 보존하지 않는다.
- DB row를 자동 thinning하거나 수정·삭제·truncate하지 않고 새 DB로 조용히 교체하지 않는다.
- `trades_sim.db`라는 파일명은 compatibility 이름이며 trade/fill/P&L evidence를 뜻하지 않는다.
- disk free가 30GiB 미만이거나 사용률이 90% 이상이면 source fetch 전에 중단한다.
- 저장공간 부족을 evidence 삭제로 우회하지 않는다.
- simulation `run`은 mock이 아니며 public network를 호출하고 DB와 log를 갱신한다.
- `DO`, `RE`, `MI`가 들어간 runtime job 이름만 보고 서로 다른 treatment stream으로 분리하지 않는다.

## 실행·배포

명시적인 smoke 또는 deployment 확인에서만 한 cycle을 실행한다.

```bash
uv run polybot run --simulate --job raspberry-re-shard-1
uv run polybot status --simulate --job raspberry-re-shard-1
uv run polybot health --simulate --job raspberry-re-shard-1
```

| Jenkins job | Runtime job | Shard | Schedule |
|---|---|---:|---|
| `polybot-do` | `raspberry-do-shard-0` | 0 | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-shard-1` | 1 | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-shard-2` | 2 | `2-59/5 * * * *` |

- 세 job의 current config SHA와 rollback copy를 확인한 뒤 변경한다.
- timer 없이 세 job을 순차 성공시킨 뒤 timer를 활성화하고 최소 2회 natural build를 검증한다.
- Freestyle concurrent build는 비활성화하고 build log retention은 14일로 유지한다.
- clean workspace/build를 배포나 장애 복구 수단으로 사용하지 않는다.

### 외부 API / Secret

- 외부 연동은 unauthenticated public Gamma와 public CLOB REST로 제한한다.
- `--live` 또는 `src/polybot/config.py`의 `_CREDENTIAL_ENV_KEYS`에 등록된 key가 존재하면 DB, log와 HTTP session을 열기 전에 실패해야 한다.
- Guard 대상은 `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`, `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASSPHRASE`의 정확히 9개 key다.
- order SDK, account, wallet, position, fill과 realized P&L 경로를 추가하지 않는다.
- 통과 판정도 `SHADOW_REVIEW_ONLY`이며 이 프로젝트의 live trading을 승인하지 않는다.

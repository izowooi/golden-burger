# L4 AGENTS.md — golden-coconut

이 문서는 `golden-coconut/`에만 적용되는 sub-project 운영 지침이다. 상위 저장소의 L2
`../AGENTS.md`와 workspace L1 `/Users/izowooi/git/AGENTS.md`를 함께 따르며, 충돌하지 않는 범위에서
이 문서의 collector-specific 계약을 우선한다.

## 프로젝트 목적과 경계

Golden Coconut은 soccer·MLB·NBA·NFL·NHL의 major-sports whole-game moneyline을 경기 전부터
종료·해결까지 5분마다 추적하는 accountless research collector다. canonical runtime은
`coconut-major-sports-lifecycle-5m-v5`, Jenkins job은 `polybot-gold`다.

- `archive_only`, simulation/shadow only다.
- wallet, account, signing, credential, private endpoint, order path를 추가하지 않는다.
- `--live`, `active`, `close_only`는 config/log/DB/network 전에 실패해야 한다.
- 모든 `POLYMARKET_*`, `CLOB_*`, legacy credential alias는 empty value도 거부한다.
- unknown `POLYBOT_*`도 거부한다. 허용 key는 lifecycle/simulation 두 개뿐이다.
- `requests.Session.trust_env=False`; dotenv와 proxy/auth environment를 사용하지 않는다.

## Frozen source of truth

우선순위는 다음과 같다.

1. `research/EPOCHS.json`의 active epoch와
   `research/frozen-2026-08-28-v5/SPORTS_REGISTRY.json` + SHA-256
2. 같은 directory의 `PREREGISTRATION.md`, `DATA_CONTRACT.md`, `MANIFEST.sha256`
3. `config.yaml`, `STRATEGY.md`
4. runtime source와 tests

registry/schema/universe/threshold/cadence를 바꿀 때 기존 DB에 migration이나 backfill하지 않는다.
새 frozen epoch, data contract, runtime job과 create-only DB를 설계한다.

## Evidence 계약

- family별 logical Gamma `/events/keyset` sweep은 `closed=false`, 실제 경기 시작 시각
  `start_time_min/max=slot-24h..slot+48h` 범위에서 서로 독립이다. Soccer는 frozen 8개 대회
  query tag로 fan-out하며 모든 physical cursor가 끝나야 logical cursor-complete다.
  `live=true` discovery gate는 쓰지 않는다. 신규 event는 응답 후에도 canonical schedule을
  UTC half-open interval로 재검증하고, 누락·오염·범위 밖 응답은 raw evidence와 함께 거절한다.
- accepted game은 Gamma event ID와 canonical slug로 terminal lifecycle까지 추적한다. WSS
  no-message나 wall time으로 경기 상태·경과시간을 추정하지 않는다. `DISCOVERED_OPEN`은
  lifecycle unknown stratum으로 유지하면서 book/ladder/vector를 수집하고 PREGAME/IN_PLAY와
  합치지 않는다.
- liquidity/volume은 discovery gate가 아니라 strata다.
- soccer Yes/No negRisk와 미국 direct two-outcome non-negRisk를 섞지 않는다.
- 미국 event series ID를 sport root ID와 동일시하지 않는다. Frozen semantic root-or-season shape와
  exact same-league two-team identity를 검증한다. Soccer draw는 bare form 또는 exact event-title
  parenthetical만 허용한다.
- official 미국 major-league preseason은 `PRESEASON`으로 수집하되 다른 season phase와 합치지 않는다.
- minor/G League/AHL/ECHL/NCAA/e-sports/child/period/spread/total/prop/future/advancement는 제외한다.
- canonical full-book gzip은 token/cycle당 한 행이며, `$5..$1000`의 frozen notional마다 독립
  threshold vector를 둔다.
- `LEFT_CENSORED`와 `GAP_CENSORED`는 episode가 아니다.
- resolution은 unique one-hot, void, tie를 별도 보존한다.
- health-only analysis에서 profitability는 `null`이다.

DB는 append-only/create-only UTC daily shard다. daily-rsync 호환 때문에 active filename은
`data/coconut-major-sports-lifecycle-5m-v5/trades_sim.db`지만 SQLite table에는 `orders`, `fills`,
`positions`, `wallets`, `trades`, P&L을 만들지 않는다.

## 작업과 검증

```bash
uv lock
uv sync --frozen --extra dev
uv run pytest
uv build
```

source를 바꾸면 frozen manifest와 strategy source digest coverage를 확인한다. public source test는
fake client만 사용하며 live network를 test에서 호출하지 않는다. safety ordering, exact identity,
cursor completion, book/crossing/resolution, append-only transaction, slot/deadline/storage/skew,
analyzer missing-sport와 season phase 분리를 모두 회귀 검증한다.

Jenkins 운영은 `OPERATIONS.md`와 project-owned `Jenkinsfile`을 따른다. exact workspace marker는
`/Volumes/t7/jenkins/polybot-gold/.daily-rsync-workspace.json`이며
`schema_version=1`, `job=polybot-gold`, exact workspace의 세 key만 가져야 한다.

## 결정 출처

이 L4 지침은 2026-08-27 사용자 요청의 exact project purpose와 이후 daily-rsync filename,
workspace marker, official preseason strata 보정을 승인된 결정으로 사용해 작성했다.

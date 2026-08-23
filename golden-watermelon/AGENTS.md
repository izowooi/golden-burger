# L3 AGENTS.md — Golden Watermelon

이 문서는 `golden-watermelon/`에만 적용된다. 상위 모노레포 규칙은 `../AGENTS.md`를
따른다.

## 목적

Golden Watermelon은 경기 시작 후의 **whole-match winner moneyline**만 대상으로,
exact CLOB $5 ask가 0.95~0.99 threshold를 통과했을 때 가상 진입하고 resolution 또는
0.95~0.70 stop까지의 displayed-book 경로를 수집하는 accountless simulation이다.
실제 주문·wallet·realized P&L은 없다.

## 고정 계약

- Strategy name: **In-Play Match Winner**.
- Active data contract: `soccer-inplay-major-league-match-winner-v2`.
- Schema/universe/classifier: `golden-watermelon-v3a-schema-v1` /
  `soccer-major-leagues-2026-08-v3a` / `soccer-major-league-identity-v1`.
- Runtime jobs:
  - `watermelon-white-1m-v3a`: `FAST_1M`, 1분 cadence.
  - `watermelon-grey-5m-v3a`: `CONTROL_5M`, 5분 cadence.
- Jenkins/workspace:
  - `polybot-white` → `/Volumes/t7/jenkins/polybot-white`.
  - `polybot-grey` → `/Volumes/t7/jenkins/polybot-grey`.
- Freeze decision: `2026-08-23T15:07:00Z`.
- Entry window: `[2026-08-23T16:00:00Z, 2026-08-30T16:00:00Z)`
  (`2026-08-24 01:00 KST` 즉시 시작).
- Follow-up end: `2026-09-06T16:00:00Z` (`2026-09-07 01:00 KST`).
- First requested health review: after `2026-08-25T00:00:00Z`.
- Entry grid: `0.95/0.96/0.97/0.98/0.99`.
- Exit grid: `HOLD_TO_RESOLUTION`과
  `STOP_0.95/0.93/0.90/0.85/0.80/0.70`.
- Notional: exact displayed-book `$5`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Preregistration: `research/frozen-2026-08-24-soccer-v3a/PREREGISTRATION.md`.

실험 identity나 threshold/stop/cadence를 바꾸면 기존 DB에 섞지 않는다. 새
preregistration과 runtime job/DB epoch를 만든다. Git commit은 provenance이지 cohort key가
아니다.

## Universe와 분류

- Gamma `/events/keyset`에 `closed=false`, `live=true`, exact numeric
  `tag_id=100350`, `related_tags=false`를
  server-side로 적용하고 nested market의 `sportsMarketType=moneyline`을 client-side로
  재검증한다.
- EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`), LaLiga(`lal`), MLS(`mls`)의 sport
  id/code/exact name/primaryTagId/series id+slug/team league와 required numeric tag IDs를
  `config.yaml` frozen tuple과 모두 비교한다. 공통 tags는 `1/100639/100350`이다.
- e-sports tag `64`, cup, 2부와 다른 league는 `REJECTED`; allowlisted code의 누락·충돌은
  `DRIFT`이며 CLOB/episode를 금지하고 HIGH issue를 남긴다. title/slug로 보정하지 않는다.
- liquidity/volume 하한은 두지 않는다. exact $5 CLOB depth 부족은 exclusion이 아니라
  측정값으로 저장한다.
- `sportsMarketType=moneyline`만 허용한다. `child_moneyline`(map/game/set winner),
  spreads, totals, score, goal, foul·player prop은 source field로 제외한다.
- aligned two-team moneyline은 두 team outcome을 허용한다.
- negRisk soccer 등의 market은 event team과 정확히 대응하는 team-win `YES`만 허용하고
  `NO`와 Draw/Tie를 제외한다.
- 정확히 두 team, 두 token, `gameStartTime <= observed_at`, not-ended/open/orderbook/
  accepting-orders를 모두 요구한다. 명시적 `live=false`는 fail closed한다.
- Gamma `endDate`는 실제 경기 종료시각으로 해석하지 않는다. `gameStartTime`,
  event `live/ended/gameStatus`, source receipt time을 각각 보존한다.

## 실행 근사

- entry는 Gamma probability가 아니라 exact full-depth $5 ask VWAP으로 판정한다.
- 첫 full-depth observation이 threshold 이상이면
  `FIRST_FULL_DEPTH_ABOVE`, 직전 VWAP 미만→현재 이상이면 `UPWARD_CROSS`로 분리한다.
- 여러 threshold와 stop은 같은 episode path의 counterfactual이며 독립 거래로 합산하지 않는다.
- stop은 best bid trigger와 full-depth VWAP을 분리한다. gap, partial fill, 잔여 retry,
  fee를 append-only로 남긴다.
- entry ask와 같은 book의 bid를 path/stop에 쓰지 않는다. 첫 exit 관측은 다음 natural
  cadence cycle부터 시작한다.
- CLOB closed + exactly one winning token만 resolution으로 인정한다.
- White/Grey 중복 episode는 cadence pair이며 독립 표본으로 세지 않는다.

## 안전 규칙

- `--live`, private/funder/signature/API credential은 빈 문자열이어도 파일·DB·HTTP 전에
  거절한다.
- lifecycle은 `archive_only`, simulation은 `true`만 허용한다.
- 주문 SDK, signer, order submission 코드를 추가하지 않는다.
- free space 50GiB 미만, filesystem 90% 이상, overlapping writer, incomplete cursor,
  malformed source, SQLite check 실패는 fail closed한다.
- Jenkins clean/wipe와 research DB 삭제를 금지한다.
- 이 프로젝트 작업으로 다른 live/research Jenkins job을 변경하지 않는다.

## Evidence

API attempt, gzip raw page, complete sweep, classification evidence, market/outcome, exact book/
levels, decision, episode, path, stop attempt/retry/exit, resolution, config/source/run, storage,
DB check를 append-only로 보존한다. `OBSERVED` book은 fill이 아니며 모든 결과는
displayed-book counterfactual로만 표현한다.

v3a에서는 source event당 sweep별 `event_observations` 한 row가 raw page FK, canonical event
hash, normalized sport/tag/series/team authority와 rejection/drift reason을 소유한다. market은
`event_observation_id`만 참조하며 event JSON을 반복 저장하지 않는다.

새 DB는 CREATE-only migration으로만 생성한다. 기존 file은 DDL/WAL/writable open 전에
read-only로 exact application ID, user version, singleton contract/schema/universe/classifier/
mapping/migration/schema hash와 registry mapping을 검증한다. runtime `ALTER TABLE`은 금지한다.
v2/v3 DB 및 기존 frozen prereg는 immutable archive이므로 migration/copy/merge/backfill/delete/
clean하지 않는다. active migration SQL은 `strategy_source_digest`에 반드시 포함한다.

pair analyzer는 두 DB의 contract/schema/universe/classifier/mapping/migration/schema/source
digest와 exact FAST/CONTROL jobs를 일치시킨다. paired episode league가 다르면 실패한다.
primary estimator는 event 내 equal → league 내 event-equal → 다섯 league 20% macro이며,
evaluable league가 하나라도 없으면 macro estimate/CI는 `null`이다.

## 검증

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-24-soccer-v3a && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

24시간에는 collection health만 본다. 첫 7일도 collection/league coverage를 우선하며,
수익성·threshold·stop 선택은 resolution follow-up과 사전 표본 gate가 끝나기 전에 하지 않는다.

Legacy discovery literals는 archive 식별용이다: `soccer-inplay-major-league-match-winner-v1`,
`watermelon-white-1m-v3`, `watermelon-grey-5m-v3`. active runtime에 재사용하지 않는다.

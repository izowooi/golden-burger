# Golden Watermelon — In-Play Match Winner

Active v3b data contract: `soccer-inplay-major-league-match-winner-v2`.

경기 시작 뒤 whole-match `moneyline`의 exact $5 ask가
`0.95/0.96/0.97/0.98/0.99`에 도달했을 때 가상 진입하고, resolution까지 보유하는 정책과
`STOP_0.95/STOP_0.93/STOP_0.90/STOP_0.85/STOP_0.80/STOP_0.70` policy를
같은 path에서 함께 재생한다.

`polybot-white`의 `watermelon-white-1m-v3b`는 1분(`FAST_1M`), `polybot-grey`의
`watermelon-grey-5m-v3b`는 5분(`CONTROL_5M`) cadence로
동일한 전체 모집단을 관측한다. 두 DB의 같은 `condition × token × threshold`는 paired
cadence 실험이며 두 거래로 세지 않는다.

v3b 모집단은 축구만 포함한다. EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`),
LaLiga(`lal`), MLS(`mls`), Serie A(`sea`)의 Gamma sport id/code/exact
name/primaryTagId/series id+slug,
event와 sport의 required numeric tag IDs, 두 team league가 frozen mapping과 모두 일치해야
한다. e-sports, cup, 2부와 그 밖의 리그는 CLOB 조회·episode 생성 전에 fail closed한다.
허용 code의 authority field가 누락되거나 충돌하면 `DRIFT`로 저장하고 HIGH issue를 남긴다.

이 프로젝트는 accountless, simulation-only다. 실제 주문과 wallet이 없고 `--live` 및 모든
credential을 source-level로 거절한다. DB의 ask→bid/resolution 값은 actual fill이나 realized
P&L이 아니라 displayed-book counterfactual이다.

## Universe

Gamma `/events/keyset`에 exact numeric `tag_id=100350`, `related_tags=false`,
`closed=false`, `live=true`를 적용해 event를 먼저 가져온 뒤 nested market에서
`sportsMarketType=moneyline`을 엄격히 재검증한다. title/slug는 league authority로 쓰지
않는다.
`child_moneyline`, map/game/set winner, handicap, score, goal, foul·player prop은 제외한다.
exact negRisk `[Yes,No]` market 중 정확히 home/draw/away result proposition에 대응하는
`YES`만 사용하며 `NO`, non-negRisk, `Draw No Bet`은 제외한다.
market description이 정규 90분과 stoppage time만 settlement에 포함한다고 명시해야 한다.
연장전과 승부차기를 포함하거나 서로 모순되거나 범위가 누락된 market은 fail closed한다.

volume/liquidity 하한은 없다. 대신 실제 $5 ask depth가 없으면 진입하지 않고 그 부족 자체를
evidence로 남긴다. parent가 없는 event가 명시적으로 open/live/not-ended이고
`gameStartTime` 후 `[0h,4h]`이며 market이 open/accepting-orders일 때만 entry eligible이다.

## 로컬 검증

```bash
cd golden-watermelon
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job watermelon-white-1m-v3b
uv run polybot config --simulate --job watermelon-grey-5m-v3b
```

public API cycle은 Polymarket credential이 전혀 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job watermelon-white-1m-v3b
```

가설·판정 gate는 [STRATEGY.md](STRATEGY.md), Jenkins와 daily-rsync 절차는
[OPERATIONS.md](OPERATIONS.md), frozen 계약은
[PREREGISTRATION.md](research/frozen-2026-08-26-serie-a-v3b/PREREGISTRATION.md)를 따른다.

## v3b evidence와 immutable legacy epochs

새 DB는 CREATE-only migration으로만 만들고 `application_id`, `user_version`, contract,
schema/universe/classifier/mapping/migration/schema hash를 저장한다. 기존 file은 writable
connection·WAL·DDL 전에 read-only exact preflight를 통과해야 한다. `event_observations`가 raw
page FK, normalized sport/tag/series/team authority와 `ACCEPTED/REJECTED/DRIFT` reason을 한 번
소유하고 market은 FK만 저장하므로 event JSON을 market마다 복제하지 않는다. migration은
`strategy_source_digest` 입력이다.

`research/frozen-2026-08-23/`, `research/frozen-2026-08-24-soccer/`,
`watermelon-*-v2`, `watermelon-white-1m-v3`, `watermelon-grey-5m-v3`,
`watermelon-white-1m-v3a`, `watermelon-grey-5m-v3a` 및 그 이전 DB는 immutable archive
evidence다. v3b가 이를
migration, ALTER, copy, merge, backfill, delete 또는 clean하지 않는다.
이 archive에는 과거 `soccer-inplay-major-league-match-winner-v1` data contract도 포함된다.

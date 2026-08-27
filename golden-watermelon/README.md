# Golden Watermelon — Elite Soccer In-Play Evidence

Active v3d data contract는 `soccer-inplay-elite-competition-match-winner-v4`다. 축구
whole-match `moneyline`의 exact displayed book을 계정 없이 수집하며 실제 주문이나 realized
P&L을 만들지 않는다. `--live`는 source-level로 거절한다.

| Jenkins | runtime | cadence |
|---|---|---:|
| `polybot-white` | `watermelon-white-1m-v3d` | `FAST_1M`, `* * * * *` |
| `polybot-grey` | `watermelon-grey-5m-v3d` | `CONTROL_5M`, 5분 |

두 잡은 cadence만 다르다. 같은 `condition × token × threshold`를 paired evidence로 다루며
두 거래로 세지 않는다.

## 수집 범위

국내 리그는 EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`), LaLiga(`lal`), MLS(`mls`),
Serie A(`sea`)다. UEFA 대회는 UEFA Champions League(`ucl`)와 UEFA Europa League(`uel`)다.
국내 리그는 sport id/name/`primaryTagId`/series/team-league exact tuple, UEFA 대회는 numeric
competition tag/series/event prefix/UEFA resolution host exact tuple로 식별한다. title 추정은
사용하지 않는다. e-sports, 2부, UEFA Conference League와 그 밖의 대회는 제외한다.

Gamma `/events/keyset`은 `tag_id=100350`, `related_tags=false`, `closed=false`, `live=true`로
cursor-complete하게 읽는다. nested market은 top-level `sportsMarketType=moneyline`, exact
negRisk `[Yes,No]`, HOME/DRAW/AWAY YES, 정규 90분과 stoppage time만 payout에 포함하는지 다시
검증한다. accepted event마다 서로 다른 condition/token의 HOME/DRAW/AWAY triad가 정확히 하나씩
없으면 `RESULT_TRIAD_COVERAGE_GAP` HIGH다. child moneyline, extra time, penalty shoot-out 또는
advancement market은 제외한다.

volume/liquidity 하한은 없다. 각 eligible outcome의 full-depth CLOB ask/bid를 저장해 `$5`부터
`$1000`까지 사후 replay할 수 있게 한다. displayed depth는 guaranteed fill이 아니다.

## 보강된 증거

Polymarket public Sports WebSocket의 production `gameId`/camelCase `eventState`를 Gamma
event의 numeric `gameId`와 exact join하고 `period`, raw `elapsed` 또는 `clock`, `score`,
`live`, `ended`, source update time을 보존한다. bounded WebSocket 창에 update가 없으면 같은
cycle의 Gamma event에 명시된 clock fields를 사용한다. 두 source의 원문·provenance와
`SPORTS_CLOCK_UPDATE`를 보존하고, 둘 다 명시적 minute가 없으면
`SOURCE_CLOCK_COVERAGE_GAP`/`SOURCE_CLOCK_MINUTE_FIELD_GAP` HIGH다. kickoff wall time으로 경기
분을 추정하지 않는다. 사후 timing strata는 source regulation minute `75/80/85`이며, 이는
정규 90분 기준 마지막 15/10/5분 가설이지 실제 종료까지 남은 wall-clock time의 보장은 아니다.

full book은 frozen notional ladder `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`를
같은 snapshot에서 replay한다. ask coverage/VWAP/worst ask, `$5` 대비 slippage, 동일 시점의
full bid depth와 instant round-trip haircut을 계산한다. 이 정보는 향후 한 단계씩 scale-up을
검토하기 위한 evidence일 뿐 현재 live 금액을 바꾸는 허가가 아니다.

## 실행

```bash
cd golden-watermelon
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job watermelon-white-1m-v3d
uv run polybot config --simulate --job watermelon-grey-5m-v3d
```

public cycle은 credential이 전혀 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job watermelon-white-1m-v3d
```

가설과 판정 gate는 [STRATEGY.md](STRATEGY.md), Jenkins 및 daily-rsync 절차는
[OPERATIONS.md](OPERATIONS.md), 동결 계약은
[PREREGISTRATION.md](research/frozen-2026-08-27-source-clock-triad-scale-v3d/PREREGISTRATION.md)를
따른다.

## Immutable epochs

v3d는 기존 DB를 migration, `ALTER TABLE`, copy, merge, backfill, delete 또는 clean하지 않고
새 runtime DB를 만든다. application ID, user version, schema/universe/classifier/mapping hash와
source digest를 exact preflight한다. `event_observations`가 raw page와 identity 판정을 소유한다.

`watermelon-white-1m-v3c`, `watermelon-grey-5m-v3c`,
`watermelon-white-1m-v3b`, `watermelon-grey-5m-v3b` 및 이전 runtime은 immutable archive다.
과거 data contract `soccer-inplay-elite-competition-match-winner-v3`,
`soccer-inplay-major-league-match-winner-v2`와
`soccer-inplay-major-league-match-winner-v1`, runtime `watermelon-white-1m-v3`와
`watermelon-grey-5m-v3`는 archive 식별용으로만 남기며 재실행하지 않는다.

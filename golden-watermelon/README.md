# Golden Watermelon — Soccer/MLB/NHL In-Play Evidence

Active v4a data contract는 `watermelon-soccer-mlb-nhl-inplay-match-winner-v5`다. Soccer, MLB,
NHL whole-game winner의 exact displayed CLOB book을 계정 없이 수집하며 실제 주문이나 realized
P&L을 만들지 않는다. `--live`와 credential 환경은 source-level로 거절한다.

| Jenkins | runtime | cadence |
|---|---|---:|
| `polybot-white` | `watermelon-white-1m-v4a` | `FAST_1M`, `* * * * *` |
| `polybot-grey` | `watermelon-grey-5m-v4a` | `CONTROL_5M`, `H/5 * * * *` |

두 잡은 cadence만 다르며 같은 event/token/grid의 paired observation이다.

## 수집 범위

- Soccer numeric tag `100350`: EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`),
  LaLiga(`lal`), MLS(`mls`), Serie A(`sea`), UEFA Champions League(`ucl`),
  UEFA Europa League(`uel`) exact identity와 정규시간 HOME/DRAW/AWAY YES.
- MLB numeric tag `100381`: sport `8`, root series `3`, exact two-team whole-game direct moneyline.
- NHL numeric tag `899`: sport `35`, root series `10346`, exact two-team whole-game direct moneyline.
- MLB postseason/World Series와 NHL postseason/Stanley Cup Final은 같은 exact major-league
  root/season/team identity일 때 포함한다.
- e-sports, MiLB/AHL/ECHL/NCAA, child/period/spread/total/prop/future/advancement는 제외한다.

세 family는 `/events/keyset`에서 `closed=false`, `live=true`, `related_tags=false`와 각 numeric
tag로 독립 cursor를 완결한다. research collector는 volume/liquidity로 표본을 먼저 버리지 않는다.
eligible outcome의 full ask/bid levels를 저장해 `$5`부터 `$1000`까지 같은 snapshot에서 replay한다.
이 displayed evidence는 actual fill 또는 realized P&L이 아니다.

entry threshold는 `0.95/0.96/0.97/0.98/0.99`, stop은
`0.95/0.93/0.90/0.85/0.80/0.70`이다. displayed depth는 guaranteed fill이 아니다. Soccer는
source-explicit regulation minute `75/80/85` strata를 만들지만 kickoff wall clock으로 추정하지
않는다. MLB/NHL clock은 raw evidence로 남기고 Soccer minute strata에 합치지 않는다.

accepted Soccer event는 distinct HOME/DRAW/AWAY condition/token 3개, accepted MLB/NHL event는
한 condition의 HOME/AWAY token 2개가 정확히 있어야 한다. 누락·중복은 HIGH collection-health
issue다.

## 실행

```bash
cd golden-watermelon
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job watermelon-white-1m-v4a
uv run polybot config --simulate --job watermelon-grey-5m-v4a
```

public cycle은 credential이 전혀 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job watermelon-white-1m-v4a
```

가설과 판정 gate는 [STRATEGY.md](STRATEGY.md), Jenkins 및 daily-rsync 절차는
[OPERATIONS.md](OPERATIONS.md), 동결 계약은
[PREREGISTRATION.md](research/frozen-2026-08-29-major-sports-v4a/PREREGISTRATION.md)를 따른다.

v3d 이하 data contract와 runtime DB는 immutable archive다. v4a는 application ID `GWM4`, user
version `401`의 새 CREATE-only DB를 만들며 기존 DB를 migrate, `ALTER TABLE`, copy, merge,
backfill, delete 또는 clean하지 않는다.

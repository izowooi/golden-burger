# Golden Watermelon — Five-Sport In-Play Evidence

Active v4b data contract는 `watermelon-five-major-sports-inplay-match-winner-v6`다. Soccer,
MLB, NBA, NFL, NHL whole-game winner의 exact displayed CLOB book을 계정 없이 수집하며 실제
주문이나 realized P&L을 만들지 않는다. `--live`와 credential 환경은 source-level로 거절한다.

| Jenkins | runtime | cadence |
|---|---|---:|
| `polybot-white` | `watermelon-white-1m-v4b` | `FAST_1M`, `* * * * *` |
| 예약(미배포) | `watermelon-grey-5m-v4b` | `CONTROL_5M`, 5분 |

`polybot-grey` Jenkins는 현재 Golden Peach 수집기다. Watermelon의 5분 runtime은 향후 주기
대조가 필요할 때 사용할 예약 계약이며, 현재 Grey의 역할을 덮어쓰지 않는다.

## 수집 범위

- Soccer numeric tag `100350`: EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`),
  LaLiga(`lal`), MLS(`mls`), Serie A(`sea`), UEFA Champions League(`ucl`),
  UEFA Europa League(`uel`) exact identity와 정규시간 HOME/DRAW/AWAY YES.
- MLB numeric tag `100381`: sport `8`, root series `3`, exact two-team whole-game direct moneyline.
- NBA numeric tag `745`: sport `34`, root series `10345`, exact two-team whole-game direct moneyline.
- NFL numeric tag `450`: sport `10`, root series `10187`, exact two-team whole-game direct moneyline.
- NHL numeric tag `899`: sport `35`, root series `10346`, exact two-team whole-game direct moneyline.
- World Series, NBA Finals, Super Bowl, Stanley Cup Final은 같은 exact major-league
  root/season/team identity일 때 포함한다.
- e-sports, MiLB/G League/AHL/ECHL/NCAA, child/period/quarter/inning/spread/total/prop/future/
  advancement는 제외한다.
- NBA G League와 NBA Summer League는 NBA root/tag/team metadata가 다른 조건을 만족해도 명시적으로
  제외한다.

다섯 family는 `/events/keyset`에서 `closed=false`, `live=true`, `related_tags=false`와 각 numeric
tag로 독립 cursor를 완결한다. research collector는 volume/liquidity로 표본을 먼저 버리지 않는다.
각 family는 별도 HTTP session/worker에서 동시에 읽고 결과는 항상
`soccer → mlb → nba → nfl → nhl` 순서로 조립한다. 전체 public network는 cooperative 42초,
cycle은 50초이며 process signal이나 hard kill은 쓰지 않는다. network cutoff 이후 요청은 명시적
incomplete receipt로 기록되고, 한 family라도 미완결이면 partial cycle을 성공으로 게시하지 않는다.
eligible outcome의 full ask/bid levels를 저장해 `$5`부터 `$1000`까지 같은 snapshot에서 replay한다.
이 displayed evidence는 actual fill 또는 realized P&L이 아니다.

entry threshold는 `0.95/0.96/0.97/0.98/0.99`, stop은
`0.95/0.93/0.90/0.85/0.80/0.70`이다. displayed depth는 guaranteed fill이 아니다. Soccer는
source-explicit regulation minute `75/80/85` strata를 만들지만 kickoff wall clock으로 추정하지
않는다. MLB/NBA/NFL/NHL clock은 raw evidence로 남기고 Soccer minute strata에 합치지 않는다.

accepted Soccer event는 distinct HOME/DRAW/AWAY condition/token 3개, accepted
MLB/NBA/NFL/NHL event는 한 condition의 HOME/AWAY token 2개가 정확히 있어야 한다.
누락·중복은 HIGH collection-health issue다.

resolution follow-up은 Gamma current view 뒤 exact `closed=true` fallback을 사용한다. token/outcome이
정렬된 `[1,0]`/`[0,1]` one-hot 또는 authoritative binary void `[0.5,0.5]`만 terminal payout으로
인정한다. void는 두 token 각각 0.5로 분석하며, CLOB 자체 resolution은 계속 closed + unique
one-hot winner만 허용한다.

## 실행

```bash
cd golden-watermelon
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job watermelon-white-1m-v4b
uv run polybot config --simulate --job watermelon-grey-5m-v4b
```

public cycle은 credential이 전혀 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job watermelon-white-1m-v4b
```

가설과 판정 gate는 [STRATEGY.md](STRATEGY.md), Jenkins 및 daily-rsync 절차는
[OPERATIONS.md](OPERATIONS.md), 동결 계약은
[PREREGISTRATION.md](research/frozen-2026-09-03-five-major-sports-v4b/PREREGISTRATION.md)를 따른다.

v4a 이하 data contract와 runtime DB는 immutable archive다. v4b는 application ID `GWM4`, user
version `401`의 CREATE-only schema를 새 runtime DB에 적용하며 기존 DB를 migrate,
`ALTER TABLE`, copy, merge, backfill, delete 또는 clean하지 않는다.

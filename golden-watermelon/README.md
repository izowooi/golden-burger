# Golden Watermelon — In-Play Match Winner

Data contract: `soccer-inplay-major-league-match-winner-v1`.

경기 시작 뒤 whole-match `moneyline`의 exact $5 ask가
`0.95/0.96/0.97/0.98/0.99`에 도달했을 때 가상 진입하고, resolution까지 보유하는 정책과
`STOP_0.95/STOP_0.93/STOP_0.90/STOP_0.85/STOP_0.80/STOP_0.70` policy를
같은 path에서 함께 재생한다.

`polybot-white`는 1분(`FAST_1M`), `polybot-grey`는 5분(`CONTROL_5M`) cadence로
동일한 전체 모집단을 관측한다. 두 DB의 같은 `condition × token × threshold`는 paired
cadence 실험이며 두 거래로 세지 않는다.

현재 v3 모집단은 축구만 포함한다. EPL(`epl`), Bundesliga(`bun`), Ligue 1(`fl1`),
LaLiga(`lal`), MLS(`mls`)의 Gamma sport code/name과 `soccer` tag가 모두 일치해야 한다.
e-sports와 그 밖의 리그는 실제 CLOB 조회·episode 생성 전에 fail closed한다. sport family,
league code/name, series와 event tag를 DB에 정규화해 리그별 역전·resolution을 나중에
분리 분석한다.

이 프로젝트는 accountless, simulation-only다. 실제 주문과 wallet이 없고 `--live` 및 모든
credential을 source-level로 거절한다. DB의 ask→bid/resolution 값은 actual fill이나 realized
P&L이 아니라 displayed-book counterfactual이다.

## Universe

Gamma `/events/keyset`의 공식 `tag_slug=soccer`, `live=true` filter로 현재 경기 중
event를 먼저 가져온 뒤 nested market에서 `sportsMarketType=moneyline`을 엄격히
재검증한다.
`child_moneyline`, map/game/set winner, handicap, score, goal, foul·player prop은 제외한다.
aligned two-team market은 양 team outcome을, negRisk market은 정확히 team에 대응하는
`YES`만 사용하며 Draw와 `NO`는 제외한다.

volume/liquidity 하한은 없다. 대신 실제 $5 ask depth가 없으면 진입하지 않고 그 부족 자체를
evidence로 남긴다. `gameStartTime` 이후이며 event가 ended가 아니고 market이 open/
accepting-orders일 때만 entry eligible이다.

## 로컬 검증

```bash
cd golden-watermelon
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job watermelon-white-1m-v3
uv run polybot config --simulate --job watermelon-grey-5m-v3
```

public API cycle은 Polymarket credential이 전혀 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job watermelon-white-1m-v3
```

가설·판정 gate는 [STRATEGY.md](STRATEGY.md), Jenkins와 daily-rsync 절차는
[OPERATIONS.md](OPERATIONS.md), frozen 계약은
[PREREGISTRATION.md](research/frozen-2026-08-24-soccer/PREREGISTRATION.md)를 따른다.

`research/frozen-2026-08-23/`과 `watermelon-*-v2` DB는 전체 스포츠 예비 코호트로
보존하며 v3와 합치거나 다시 쓰지 않는다.

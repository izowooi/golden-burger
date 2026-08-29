# Golden Watermelon Live

동일한 실행·대사·안전 로직으로 Soccer, MLB, NHL의 경기 중 승자 시장을 exact `$5`로 검정하는
live A/B 프로젝트다. 종목은 Jenkins의 `POLYBOT_SPORT_FAMILY`로 고르며 별도 전략 fork를 만들지
않는다. 기존 wallet credential과 수동 position은 건드리지 않는다.

| family | arm A | arm B |
|---|---|---|
| Soccer | `polybot-cat` / `watermelon-live-cat-96-1m-v2h` / `[0.96,0.999]` | `polybot-dog` / `watermelon-live-dog-99-1m-v2h` / `[0.99,0.999]` |
| MLB | `polybot-bear` / `watermelon-live-bear-mlb-96-1m-v3a` / `[0.96,0.999]` | `polybot-tiger` / `watermelon-live-tiger-mlb-99-1m-v3a` / `[0.99,0.999]` |
| NHL | `polybot-lion` / `watermelon-live-lion-nhl-96-1m-v3a` / `[0.96,0.999]` | `polybot-wolf` / `watermelon-live-wolf-nhl-99-1m-v3a` / `[0.99,0.999]` |

두 arm의 유일한 family 내 treatment는 진입 하한이다. cadence는 모두 1분, 주문은 `$5`다.
0.96/0.99는 아직 최적값이 아니라 rare-loss tail과 opportunity 수를 prospective 비교하는 값이다.

## 거래 계약

- Soccer: EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UEFA Champions League(UCL),
  UEFA Europa League(UEL) exact identity와 정규시간 HOME/DRAW/AWAY YES. in-play `[0h,4h]`.
- MLB: numeric tag `100381`, sport `8`, root series `3`의 exact two-team whole-game direct
  moneyline. in-play `[0h,8h]`.
- NHL: numeric tag `899`, sport `35`, root series `10346`의 exact two-team whole-game direct
  moneyline. in-play `[0h,5h]`.
- World Series와 Stanley Cup Final은 같은 exact major-league identity일 때 포함한다.
- e-sports, MiLB/AHL/ECHL/NCAA, child/period/spread/total/prop/future/advancement는 주문 전
  제외한다.
- Gamma는 `liquidity_min=5000`, `volume_min=5000`으로 서버에서 먼저 축소하고 최대 4페이지
  cursor를 완결한다. 실제 실행 가능성은 fresh exact `$5` full CLOB depth로 다시 검증한다.

BUY는 marketable FOK만 사용한다. venue tick grid에서 exact maker `$5`와 signable taker shares를
찾되 arm 상한 `0.999`를 넘지 않는다. 주문 accepted는 fill이 아니며 terminal fill과 dynamic fee를
확인하기 전에는 position lifecycle을 확정하지 않는다.

best bid `<=0.70` stop은 current Gamma event와 CLOB condition의 독립 OPEN proof, 그 proof 뒤
fresh complete bid book, spread `<=0.10`을 요구한다. 정상 연속 book에는 `0.65` 실행 floor와
35% projected-loss cap을 적용한다. 반면 OPEN 상태에서 가격이 stop을 한 번에 건너뛴 gap은 이 두
cap이 손절 자체를 막지 않게 하며 actual worst bid/VWAP/gap을 기록한다. 이미 종료된 market의
`0.001` cleanup bid는 OPEN proof에서 계속 차단한다.

account/event/cycle 한도는 `20/1/5`, cycle emergency SELL은 1건이다. PENDING,
QUARANTINED, orphan BUY, fill/fee gap 또는 모호한 execution ledger가 있으면 후보만 기록하고 신규
BUY를 막는다. confirmed SELL + proven resolution 경제손익이 `-$10`이면 기존 position 관리는
계속하지만 신규 BUY를 중단한다.

## 1분 cadence

경과시간 때문에 Gamma/CLOB 요청이나 주문을 중단하지 않는다. 각 HTTP 요청에 finite socket
timeout을 적용하고 cycle 50초 초과는 경고 evidence로 남긴다. DB별 nonblocking run lock이 다음
분 trigger와 겹치면 새 process가 즉시 skip하므로 동일 DB 쓰기와 중복 주문을 방지한다.

Jenkins 정기 shell은 lockfile hash가 변했을 때만 `uv sync --frozen`하며, 평소에는
`uv run --frozen --no-sync polybot run --live --job ...` 하나만 실행한다. `uv sync`, `config`,
`status`와 release commit 검증은 timer를 끈 배포 build에서 수행한다.

entry는 `[2026-08-29T04:00:00Z,2026-09-05T04:00:00Z)`, follow-up은
`2026-09-12T04:00:00Z`까지다. cohort는
`config_hash × strategy_source_digest × mode × job_name`이고 Git commit은 provenance다.

Cat/Dog v2h DB는 기존 미해결 position을 관리하기 위해 이어 쓴다. 그보다 오래된 live DB와 신규
MLB/NHL DB는 immutable epoch로 분리하며 clean, merge, migration, backfill하지 않는다.

## 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

실주문은 Jenkins에서 명시적 `--live`와 기존 credential이 모두 있을 때만 허용한다.
`POLYBOT_LIFECYCLE_MODE`는 `active`, `close_only`, `archive_only`를 지원한다. 중단·청산은
[공통 wind-down 절차](../docs/strategy-wind-down-playbook.md)를 따른다.

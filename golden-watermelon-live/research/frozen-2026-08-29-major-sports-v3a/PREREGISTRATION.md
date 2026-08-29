# Golden Watermelon Live major-sports A/B v3a — 2026-08-29

- Frozen decision timestamp: `2026-08-29T00:00:00Z`.
- Entry: `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)`.
- Follow-up cutoff: `2026-09-12T04:00:00Z`.
- All six Jenkins jobs: non-concurrent, `* * * * *`, exact `$5`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.

기존 Cat/Dog v2h DB는 미해결 bot-owned position을 계속 관리하기 위해 그대로 사용한다. 새 MLB/NHL
job은 각각 새 runtime DB를 만든다. 어떤 DB도 clean, rewrite, migrate, copy, merge, backfill 또는
delete하지 않는다. source amendment 전후 evidence는 `strategy_source_digest`로 분리한다.

## Frozen arms

| family | arm | Jenkins | runtime | exact `$5` ask VWAP |
|---|---|---|---|---:|
| Soccer | A | `polybot-cat` | `watermelon-live-cat-96-1m-v2h` | `[0.96,0.999]` |
| Soccer | B | `polybot-dog` | `watermelon-live-dog-99-1m-v2h` | `[0.99,0.999]` |
| MLB | A | `polybot-bear` | `watermelon-live-bear-mlb-96-1m-v3a` | `[0.96,0.999]` |
| MLB | B | `polybot-tiger` | `watermelon-live-tiger-mlb-99-1m-v3a` | `[0.99,0.999]` |
| NHL | A | `polybot-lion` | `watermelon-live-lion-nhl-96-1m-v3a` | `[0.96,0.999]` |
| NHL | B | `polybot-wolf` | `watermelon-live-wolf-nhl-99-1m-v3a` | `[0.99,0.999]` |

family 안에서 유일한 treatment는 lower entry bound다. 이 두 값은 최적값이라는 사후 결론이
아니라 동일한 prospective A/B 축이다. family 사이 P&L은 합쳐 threshold winner를 고르지 않는다.

## Frozen universe

- Soccer: numeric tag `100350`; EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UCL, UEL의
  exact HOME/DRAW/AWAY regulation-time YES identity; in-play `[0h,4h]`.
- MLB: numeric tag `100381`, sport `8`, root series `3`, exact two-team direct whole-game
  moneyline; in-play `[0h,8h]`.
- NHL: numeric tag `899`, sport `35`, root series `10346`, exact two-team direct whole-game
  moneyline; in-play `[0h,5h]`.
- MLB postseason/World Series와 NHL postseason/Stanley Cup Final은 같은 exact major-league
  root/season/team identity일 때 포함한다. title 문자열만으로 승인하지 않는다.
- MiLB/AHL/ECHL/NCAA/college/reserve, e-sports, child/period/spread/total/prop/future/
  advancement는 주문 전에 fail closed한다.

각 family는 Gamma `/events/keyset`에서 `closed=false`, `live=true`, exact numeric `tag_id`,
`related_tags=false`, `liquidity_min=5000`, `volume_min=5000`으로 먼저 축소한다. cursor는 최대
4페이지 안에 완결되어야 한다. fresh exact `$5` full-depth CLOB ask walk가 최종 실행 gate다.

## Execution and recurrence safety

- exact `$5` FOK BUY. venue tick grid를 따라 limit을 올리더라도 arm의 `0.999` 상한을 넘지 않고,
  signed maker/taker amount의 정밀도를 POST 전에 검증한다.
- accepted order는 fill이 아니다. terminal exact fill과 fee 전에는 `HOLDING`으로 전환하지 않는다.
- unresolved PENDING/QUARANTINED/orphan/fill-fee gap이 하나라도 있으면 신규 BUY를 막는다.
- account/event/cycle `20/1/5`, emergency SELL cycle당 `1`; manual wallet position 미편입·미청산.
- best bid `<=0.70` stop은 current Gamma와 CLOB의 독립 OPEN proof, proof 뒤 fresh complete bid book,
  spread `<=0.10`을 요구한다. 정상 연속 book에는 기존 `0.65`/35% envelope를 적용한다.
- 독립 OPEN proof 뒤 가격이 stop을 불연속적으로 건너뛴 gap에서는 `0.65`/35%가 손절 자체를
  무력화하지 않도록 full-depth FOK SELL을 허용한다. 종료 후 cleanup `0.001`은 OPEN proof에서
  계속 차단한다. trigger, worst bid, VWAP와 gap을 모두 보존한다.
- confirmed SELL + proven resolution 경제손익이 `-$10`이면 신규 BUY를 자동 차단한다.
- 한 cycle의 경과시간은 거래 신호나 request permission이 아니다. 각 HTTP 요청은 finite socket
  timeout을 갖고, 50초 초과는 telemetry warning으로 남긴다. process alarm이나 “42초 이후 요청
  금지”를 사용하지 않는다.
- DB별 nonblocking run lock이 겹친 다음 Jenkins trigger를 안전하게 skip한다. 중복 run은 같은
  DB를 동시에 쓰거나 같은 주문을 두 번 내지 않는다.

## Gates

첫 24시간은 family별 cursor/identity/market structure/opportunity/order/fill/fee/pending state,
runtime, overlap skip과 DB integrity만 확인한다. 7일 entry 전에는 arm winner나 수익성을 결정하지
않는다. follow-up 전에는 sport winner나 scale-up을 결정하지 않는다. CRITICAL/HIGH evidence gap,
mixed cohort 또는 수동 position 혼입이 있으면 수익성·parameter 판단을 중단한다.

# Golden Watermelon Live — Elite Soccer A/B v2h

## 질문과 arm

허용 축구 경기의 HOME/DRAW/AWAY YES가 높은 executable 가격에 도달했을 때 exact `$5` FOK로
진입하고 `0.70` emergency stop 또는 proven resolution까지 관리하면 confirmed fill·fee 후
양의 기대값이 있는가?

| arm | Jenkins | runtime | band |
|---|---|---|---:|
| Cat | `polybot-cat` | `watermelon-live-cat-96-1m-v2h` | `[0.96, 0.999]` |
| Dog | `polybot-dog` | `watermelon-live-dog-99-1m-v2h` | `[0.99, 0.999]` |

직전 v2f Cat `[0.98, 0.999]`, Dog `[0.99, 0.999]`와 v2g는 immutable historical cohort다.
0.96/0.99는 작은 선행 표본에서 확정한 최적값이 아니라 signal quantity와 rare-loss tail을
prospective 비교하기 위한 treatment다. `0.999`는 terminal `1.000`을 제외하는 공통 상한이다.

## Universe

Gamma `/events/keyset`을 `closed=false`, `live=true`, soccer tag `100350`,
`related_tags=false`로 최대 4페이지 cursor-complete하게 읽는다.

국내 리그 exact identity는 EPL/epl, Bundesliga/bun, Ligue 1/fl1, LaLiga/lal, MLS/mls,
Serie A/sea다. UEFA는 다음 cross-league authority를 사용한다.

| code | tag | series | prefix | resolution host |
|---|---:|---|---|---|
| `ucl` | 100977 | `10204/ucl-2025` | `ucl-` | `www.uefa.com` |
| `uel` | 101787 | `10209/uel-2025` | `uel-` | `www.uefa.com` |

UEFA 팀은 서로 다른 국내 league code를 가질 수 있다. exact common tags, competition tag,
single series, prefix, UEFA source, exactly two teams가 모두 맞아야 한다. title로 추정하지 않는다.

market은 top-level `sportsMarketType=moneyline`, exact `[Yes,No]`, `negRisk=true`, open/book/
accepting이어야 한다. team identity와 `groupItemTitle`을 대조해 HOME/DRAW/AWAY YES만 거래한다.
description이 정규 90분과 stoppage time만 payout이라고 명시하지 않거나 advancement, extra time,
penalty shoot-out scope면 fail closed한다. e-sports, child/halftime, prop, parent event도 제외한다.

## Entry와 capacity

1. eligible YES token의 full ask book을 읽어 exact `$5` shares/VWAP/worst ask를 계산한다.
2. 한 event에서 여러 result가 동시에 threshold를 넘으면 anomaly로 fail closed한다.
3. 한 `event × arm`의 첫 threshold observation만 `entry_episodes`에 claim한다.
4. `QUARANTINED` 포함 open Trade와 unresolved/untracked BUY intent를 capacity에 함께 넣는다.
5. 주문 직전 market/clock/book/fee identity를 다시 확인한다.
6. marketable FOK BUY를 제출하고 exact terminal fill·fee가 대사될 때까지 `PENDING_BUY`다.

FOK는 가격 band를 관측했다는 이유만으로 체결되지 않는다. 주문 시점 full depth, tick, signature,
fee identity가 모두 맞아야 하고, full fill이 아니면 0 fill이다. 첫 episode가 guard나 fresh-book
재검증에서 막히면 같은 cohort에서 재선택하지 않고 exact reason을 저장한다.

unresolved `PENDING_BUY`, `PENDING_SELL`, `QUARANTINED`, orphan BUY, order reconciliation 또는
fill/fee evidence gap이 하나라도 있으면 신규 BUY를 막는다. 명시적 terminal zero-fill/no-order
증거 없이 reservation을 해제하지 않는다. bot DB가 만든 Trade만 관리하며 manual wallet
position은 조회·편입·청산하지 않는다.

## Stop과 resolution

best bid `<=0.70`은 trigger일 뿐 체결가가 아니다. SDK가 sign 가능한 전체 shares를 fresh bid
levels에 walk하고 full depth가 있을 때만 marketable FOK SELL한다. irreversible SELL 직전에
cursor-complete Gamma live sweep과 exact CLOB condition이 각각 live/open임을 독립 확인하고,
그 확인 뒤 full book을 다시 읽어 TOCTOU stale quote를 배제한다. 최저 실행 level과 full-depth
VWAP는 모두 `0.65` 이상, spread는 10%p 이하, 매수가 대비 projected gross loss는 35% 이하라야
한다. 한 cycle에는 emergency SELL 한 건만 제출한다. 따라서 경기 종료 후 cleanup/dust bid,
얇은 gap book 또는 여러 position의 동시 오매도는 주문 전에 차단된다.

confirmed SELL P&L과 proven-resolution settlement P&L을 안전 판정에서만 합산한 경제손익이
`$100 × 10% = -$10`에 도달하면 신규 BUY를 자동 차단한다. 기존 position의 resolution·대사는
계속한다. 이 합계는 근거 등급이 다른 두 P&L을 성과로 혼합한다는 뜻이 아니라, golden-date의
문서뿐인 중단 기준과 resolution 손실 누락을 반복하지 않기 위한 보수적 kill switch다.

안전 손익 계산은 `Trade.realized_pnl`/settlement 합계만 신뢰하지 않는다. 모든 live
`CONFIRMED` SELL을 execution ledger에서 다시 읽고 exact `sell_order_id`, 또는 유일한 token의
confirmed BUY 근거로 Trade에 연결한다. 실제 SELL 뒤 Trade가 잘못 RESOLVED가 된 legacy 행은
매도된 shares 비율만 settlement에서 빼고 원장 VWAP·fee 손익으로 대체한다. 중복·모호한 연결,
fee 결손, 범위 밖 size/price는 추정하지 않고 신규 진입을 차단한다. 원본 DB 행은 회고 증거로
그대로 둔다.

CLOB v2의 legacy `fee_rate_bps=0`은 zero-fee 증거가 아니다. exact authenticated fill의
maker/taker role과 dynamic fee schedule로 fee amount를 저장한다. closed two-token market에서
exact one-hot `0/1` winner만 RESOLVED로 인정하며 `0.5/0.5`나 synthetic SELL/redeem은 허용하지
않는다.

## Cadence와 scale

Cat/Dog는 공통 1분 cadence다. 선행 White 1분이 Grey 5분보다 막판 episode coverage가 높았고
live cycle은 1분 안에 끝났지만, 이는 1분이 수익 최적이라는 뜻이 아니다. strategy process
p95 ≥15초 또는 Jenkins end-to-end p95 ≥45초이면 timer를 끈다. timed job은 검증된 commit을
pin하고 매 cycle 원격 SCM fetch·`config`·`status` subprocess를 실행하지 않는다.

live notional은 `$5`로 고정한다. White/Grey v3c가 source minute `75/80/85`와 displayed
notional `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500` evidence를 모은다.
future scale은 한 rung씩만 검토하며 이 v2h cohort에서 금액을 올리지 않는다.

## 판정

- 첫 24시간: cadence, cursor, domestic/UEFA identity, opportunity, order/fill/fee, pending state,
  DB integrity만 확인.
- 7일 entry 종료: arm별 unique event, opportunity→episode→order→confirmed fill funnel과 stop
  execution evidence 확인.
- follow-up: event-clustered fee-net interval과 rare-loss tail 비교.
- CRITICAL/HIGH, mixed cohort, incomplete fill/fee 또는 수동 position 혼입 시 수익성·scale 판단 중단.

entry `[2026-08-26T18:30:00Z,2026-09-02T18:30:00Z)`, follow-up
`2026-09-09T18:30:00Z`; threshold 외 universe/notional/cadence/exposure/clock은 두 arm에서 같다.

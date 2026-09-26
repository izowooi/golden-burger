# L4 AGENTS.md — Golden Apricot

> **현행 A/B — 2026-09-26:** MLB A=Eco: first complete pair + [90,92] minutes. B=Fruit: first complete pair + [85,87] minutes. Sole treatment is entry tick. Both midpoint favorite, baseline exact ask5 .90-.999, target $15/$10/$5 full FOK ladder, full-holding bid .90+ and positive net proceeds after all BUY/SELL fees; otherwise exact resolution. No stop/time exit. Existing DB/runtime names retained; the tick90 substring in Fruit runtime is historical, not its resolved tick.
> 아래 과거 epoch의 고정값보다 이 현행 계약과 resolved config가 우선한다. 후보의 과거 재생은 미래 수익 보장이 아니다.


상위 `../AGENTS.md`를 따른다. MLB direct HOME/AWAY two-token만 허용한다. Eco/Fruit의 live
목표 금액은 MLB에만 `$15`이며, 다른 종목 profile로 전파하지 않는다. baseline 신호는 exact
`$5` book을 유지하고 fresh depth에서 `$15/$10/$5` 중 전량 체결 가능한 최대 금액 한 건만 FOK로 제출한다. Eco/Fruit은 독립 계좌의 진입 tick A/B다. Eco는 첫 공통 tick 후 `[90,92]`분, Fruit은 `[85,87]`분, baseline `$5` favorite ask
VWAP `.90-.999`를 사용하고, full-holding bid가 `.90` 이상이면서 수수료 포함 순이익인 첫 시점에 청산한다. tick0과 tick90은 durable DB snapshot으로 증명한다. 실제
confirmed fill/fee만 손익으로 인정하며 다른 종목, 가격 band, stop을 추가하지 않는다.
confirmed 경제손익 누적 한도는 두 arm 공통 절대 `$300`이고 지갑 입출금은 제외한다. 계좌별
experiment capital `$100`을 넘지 않도록 open capacity는 6개로 제한한다.

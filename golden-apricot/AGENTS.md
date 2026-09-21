# L4 AGENTS.md — Golden Apricot

상위 `../AGENTS.md`를 따른다. MLB direct HOME/AWAY two-token만 허용한다. Eco/Fruit의 live
목표 금액은 MLB에만 `$15`이며, 다른 종목 profile로 전파하지 않는다. baseline 신호는 exact
`$5` book을 유지하고 fresh depth에서 `$15/$10/$5` 중 전량 체결 가능한 최대 금액 한 건만 FOK로 제출한다. Eco/Fruit은 독립 계좌의
동일 조건 replication cohort다. 첫 공통 tick 후 `[90,92]`분, baseline `$5` favorite ask
VWAP `.90-.999`, full-holding TP `.95`를 사용한다. tick0과 tick90은 durable DB snapshot으로 증명한다. 실제
confirmed fill/fee만 손익으로 인정하며 다른 종목, 가격 band, stop을 추가하지 않는다.
confirmed 경제손익 누적 한도는 두 arm 공통 절대 `$300`이고 지갑 입출금은 제외한다. 계좌별
experiment capital `$100`을 넘지 않도록 open capacity는 6개로 제한한다.

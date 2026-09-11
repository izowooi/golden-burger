# Golden Apricot — MLB Tick50 Favorite

첫 HOME/AWAY 공통 유효 틱부터 50분 뒤 MLB direct two-team moneyline의 midpoint favorite를
baseline exact `$5` book으로 후보를 판정하고, 같은 fresh book에서 목표 `$10` 전량이
가능하면 `$10` FOK로 한 번 매수한다. `$10`이 불가능하면 사전 등록된 사다리의 `$5`로
축소한다. 가격 band는 없다. Eco는 resolution까지 보유하고 Fruit는
전체 보유량 bid VWAP 0.99에서 조기 청산하며 미도달하면 resolution까지 보유한다.

1분 Jenkins cadence가 60초를 조금 넘겨 정확한 50분 직후 틱을 건너뛸 수 있으므로 최초
유효 진입은 `[50,52]`분에서만 허용한다. scan과 주문 직전 revalidation은 모두 DB에 저장된
최초 완전 HOME/AWAY 공통 틱을 같은 tick 0으로 사용한다.

실제 inning이 아니라 collector가 live event에서 처음 성공한 공통 tick을 기준으로 하므로 live에서
재현 가능하다. 동일 event는 두 wallet의 paired unit이며 독립 경기 두 개로 세지 않는다.
NBA/NFL/NHL 확장은 종목별 tick calibration과 새 runtime 전에는 금지한다.

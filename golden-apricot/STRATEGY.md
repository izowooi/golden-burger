# Golden Apricot — MLB Tick90 High-Confidence Favorite

첫 HOME/AWAY 공통 유효 틱부터 `[90,92]`분 뒤 MLB direct two-team moneyline의 midpoint
favorite를 baseline exact `$5` book으로 판정한다. favorite의 exact `$5` ask VWAP이
`.90-.999`일 때만 목표 `$15` FOK로 한 번 매수한다. 같은 fresh book에서 `$15/$10/$5` 중
전량 체결 가능한 최대 금액 한 건만 FOK로 제출하며 partial fill은 허용하지 않는다. Eco/Fruit 모두 전체 보유량 bid VWAP
`.90` 이상에서 수수료 포함 순이익이 되는 첫 full-holding bid에 조기 청산하며, 그렇지 않으면 resolution까지 보유한다. 두 계좌는 동일 조건을 독립
검증하는 replication cohort다.

이전 절대 TP `.95` cohort는 historical이며, 현재는 `.90` floor와 net-positive guard를
결합해 이익이 확인되는 첫 full-depth bid에서 더 일찍 청산한다.

1분 Jenkins cadence가 60초를 조금 넘겨 정확한 90분 직후 틱을 건너뛸 수 있으므로 최초
유효 진입은 `[90,92]`분에서만 허용한다. scan과 주문 직전 revalidation은 모두 DB에 저장된
최초 완전 HOME/AWAY 공통 틱을 같은 tick 0으로 사용한다.

실제 inning이 아니라 collector가 live event에서 처음 성공한 공통 tick을 기준으로 하므로 live에서
재현 가능하다. 동일 event는 두 wallet의 paired unit이며 독립 경기 두 개로 세지 않는다.
NBA/NFL/NHL 확장은 종목별 tick calibration과 새 runtime 전에는 금지한다.

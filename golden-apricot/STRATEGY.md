# Golden Apricot — MLB Tick50 Favorite

첫 HOME/AWAY 공통 유효 틱부터 50분 뒤 MLB direct two-team moneyline의 midpoint favorite를
exact $5 ask FOK로 한 번 매수한다. 가격 band는 없다. Eco는 resolution까지 보유하고 Fruit는
전체 보유량 bid VWAP 0.99에서 조기 청산하며 미도달하면 resolution까지 보유한다.

실제 inning이 아니라 collector가 live event에서 처음 성공한 공통 tick을 기준으로 하므로 live에서
재현 가능하다. 동일 event는 두 wallet의 paired unit이며 독립 경기 두 개로 세지 않는다.
NBA/NFL/NHL 확장은 종목별 tick calibration과 새 runtime 전에는 금지한다.

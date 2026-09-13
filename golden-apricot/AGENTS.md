# L4 AGENTS.md — Golden Apricot

상위 `../AGENTS.md`를 따른다. MLB direct HOME/AWAY two-token만 허용한다. Eco/Fruit의 live
목표 금액은 MLB에만 `$5`이며, 다른 종목 profile로 전파하지 않는다. 유일한 A/B 차이는
full-holding TP `.98/.99`다. tick0과 tick50은 durable DB snapshot으로 증명한다. 실제
confirmed fill/fee만 손익으로 인정하며 다른 종목, 가격 band, stop을 추가하지 않는다.
confirmed 경제손익 누적 한도는 두 arm 공통 절대 `$300`이고 지갑 입출금은 제외한다.

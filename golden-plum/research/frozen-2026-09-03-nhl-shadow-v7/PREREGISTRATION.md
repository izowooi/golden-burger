# Golden Plum NHL 무계정 수집 v7

- 시작: `2026-09-03T11:00:00Z`
- 신규 episode 수집 종료: `2026-12-03T11:00:00Z`
- 결과 추적 종료: `2026-12-10T11:00:00Z`
- runtime: `plum-shadow-gold-nhl-1m-v1`
- mode: credential-free displayed-book simulation

NHL sport `35`, tag `899`, root series `10346`, 정확한 NHL 두 팀의 whole-game direct
moneyline만 수집한다. Stanley Cup과 NHL 포스트시즌은 같은 1군 identity를 만족하면
포함하고 AHL/ECHL/대학/prop/period/spread/total/future는 제외한다.

같은 cycle의 HOME/AWAY 두 token, full-depth bid/ask, `$5..$1000` 체결 가능 사다리,
종목·리그·원본 tag, trend lineage, path와 terminal 0/1 증거를 저장한다. Gold의 MLB/NFL/NBA
DB와 별도 runtime DB를 사용한다. 표시 호가는 실제 체결이나 실현 손익이 아니다.

첫 24시간에는 1분 cadence, identity, 두 token 완전성, deadline, DB 무결성과 저장공간만
판정한다. 종목별 진입·익절·손절과 주문액은 충분한 독립 경기 표본 전에는 선택하지 않는다.

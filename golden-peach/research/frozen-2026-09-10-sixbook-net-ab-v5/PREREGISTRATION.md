# Golden Peach direct-six net-exit A/B v5

## 모집단

검증된 `direct-six-result-books`만 대상으로 한다. 같은 event에 `HOME`, `DRAW`, `AWAY` 세
result kind와 각각의 직접 `YES`, `NO` token, 총 3 market/6 token이 같은 fresh cycle에 모두
있어야 한다. 이 shape가 없으면 축구 태그가 있어도 진입하지 않는다. MLB와 다른 direct-two
runtime에는 이 정책을 적용하지 않는다.

## 진입

source live clock 0~10분, unique displayed leader margin 0.005, exact `$5` ask VWAP 0.60~0.94,
spread 0.05 이하인 기존 Golden Peach leader 하나만 event당 한 번 FOK 매수한다.

## A/B와 종료

- 공통: `$5`, 수수료 후 순수익률 TP `+5%`, source minute 75 이상 첫 유효 full bid에서 전량
  강제청산, FOK confirmed fill만 청산 인정.
- Eco A `peach-live-eco-sixbook-net5-sl15-75m-v2`: 수수료 후 순회수액이 confirmed total
  entry cost의 85% 이하일 때 SL 15%.
- Fruit B `peach-live-fruit-sixbook-net5-sl12-75m-v2`: 같은 순회수액이 entry cost의 88%
  이하일 때 SL 12%.
- 같은 tick은 TP, SL, time exit 순으로 평가한다. 표시 midpoint나 best bid만으로 net exit를
  확정하지 않고 전체 sellable holding의 full-depth bid proceeds에서 현재 SELL taker fee를 뺀다.
- 기존 soccer runtime의 open trade는 매수 당시 정책으로 close-only 관리하고 새 DB/cohort로
  소급 이동하지 않는다.

## 근거와 판정 한계

동일한 Peach durable entry 9건의 displayed-book 재생에서 기존 Eco/Fruit 합계 +6.37816 대비
A/SL12 합계 +8.22417이었다. A SL15 +4.00225, B 후보 SL12 +4.22192, SL17 +4.00225,
SL20 +3.85616이었다. 표본은 9경기이며 actual fill이 아니므로 수익성 확정이나 증액 근거가
아니다. 신규 cohort에서도 `$5`, event 1개, account risk cap을 유지한다.

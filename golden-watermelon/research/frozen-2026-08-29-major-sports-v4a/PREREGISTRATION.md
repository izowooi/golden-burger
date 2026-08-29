# Frozen preregistration — Soccer, MLB, NHL collection v4a

- Frozen decision timestamp: `2026-08-29T00:00:00Z`.
- Entry window: `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)`.
- Resolution follow-up end: `2026-09-12T04:00:00Z`.
- First collection-health review: after `2026-08-30T04:00:00Z`.
- Data contract: `watermelon-soccer-mlb-nhl-inplay-match-winner-v5`.
- Schema profile: `golden-watermelon-v4a-schema-v1`.
- Universe: `watermelon-soccer-mlb-nhl-2026-08-v4a`.
- Classifier: `watermelon-major-sports-identity-v1`.
- Mode: accountless displayed-book counterfactual simulation only.

v3d와 이전 DB는 immutable archive다. v4a는 같은 external workspace에
`watermelon-white-1m-v4a`와 `watermelon-grey-5m-v4a`라는 새 runtime DB를 만든다. 이전 DB를
clean, alter, migrate, copy, merge, backfill 또는 delete하지 않는다.

## 목적

두 질문의 evidence를 동시에 수집한다.

1. live `$5`를 `$10..$1000`으로 한 단계씩 키울 때 full displayed ask/bid depth, VWAP,
   slippage와 instant round-trip haircut이 어떻게 변하는가.
2. 현재 축구 외에 MLB와 NHL의 whole-game moneyline으로 universe를 넓힐 수 있는가.

White와 Grey는 동일 source/universe/grid를 수집하고 cadence만 1분/5분으로 다르다. 둘은
독립 전략 arm이나 독립 거래가 아니며 paired observation이다.

## Frozen universe

세 family를 numeric Gamma tag로 각각 cursor-complete하게 읽는다.

| family | tag | exact identity | in-play age |
|---|---:|---|---:|
| Soccer | 100350 | EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL | 4h |
| MLB | 100381 | sport 8, root series 3, exact MLB two-team identity | 8h |
| NHL | 899 | sport 35, root series 10346, exact NHL two-team identity | 5h |

Soccer는 기존 exact HOME/DRAW/AWAY Yes/No NegRisk triad 계약을 유지한다. MLB/NHL은 exact
두 팀 label의 direct two-outcome, `negRisk=false`, top-level whole-game `moneyline`만 허용한다.
minor league/MiLB/AHL/ECHL/NCAA, e-sports, child/period/spread/total/prop/future/advancement는
제외한다. MLB postseason 및 World Series, NHL postseason 및 Stanley Cup Final은 동일한 exact
major-league season/root identity일 때 포함하고 title 문자열만으로 승인하지 않는다.

accepted Soccer event는 HOME/DRAW/AWAY condition/token 3개, accepted MLB/NHL event는 하나의
condition에서 HOME/AWAY token 2개가 정확히 있어야 한다. 누락·중복은 HIGH collection-health
issue다.

## Frozen replay grid

- Entry threshold: `0.95/0.96/0.97/0.98/0.99`.
- Primary displayed notional: `$5`.
- Notional ladder: `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`.
- Exit replay: hold and `0.95/0.93/0.90/0.85/0.80/0.70` stops.
- Soccer late-minute strata: source-explicit `75/80/85`; kickoff wall clock으로 추정하지 않는다.
- MLB/NHL의 period/clock은 raw source evidence로 보존하되 Soccer minute strata와 합치지 않는다.

displayed book은 actual fill 또는 realized P&L이 아니다. 같은 event의 threshold, stop, notional
rung을 독립 거래처럼 합산하지 않는다.

## Cadence와 판정 gate

| runtime | arm | cadence |
|---|---|---:|
| `watermelon-white-1m-v4a` | FAST_1M | 1 minute |
| `watermelon-grey-5m-v4a` | CONTROL_5M | 5 minutes |

첫 24시간에는 three-family cursor/identity/market structure/book/source clock/cohort/DB integrity,
runtime과 storage growth만 판정한다. 수익성, best threshold/stop, sport 선택, cadence 승자 또는
live notional을 선택하지 않는다. 7일 entry와 follow-up이 끝나기 전 live 승격·scale-up을
결론내리지 않는다.

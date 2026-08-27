# Golden Coconut — Five-Family Major Sports Observatory

## 연구 질문

major-sports whole-game moneyline에서 pregame부터 terminal lifecycle까지 executable ask probability,
displayed depth, liquidity와 volume strata가 sport family·season phase·game cluster에 따라 어떻게
다른가? `0.75..0.99`의 genuine upward crossing 이후 path와 terminal resolution을 prospectively
보존할 수 있는가?

이 질문은 매매 규칙이나 수익성 주장이 아니다. primary 5분 collector의 첫 gate는 census
completeness와 evidence health다.

## Frozen treatment

- runtime/cadence: `coconut-major-sports-lifecycle-5m-v6`, 5분
- families: soccer, MLB, NBA, NFL, NHL을 동일 가중 macro 대상에 포함
- discovery: 미국 4종목은 family별 exact numeric tag, soccer는 frozen 8개 대회 query tag fan-out,
  `closed=false`, 실제 경기 시작 시각 `start_time_min/max=slot-24h..slot+48h`, 모든 physical
  terminal cursor와 client-side half-open schedule 재검증
- lifecycle: immutable Gamma event ID/canonical slug follow-up; WSS no-message와 wall time으로 상태를
  추정하지 않음
- unknown phase: `DISCOVERED_OPEN` book/ladder/vector는 별도 stratum으로 보존하고
  `PREGAME`/`IN_PLAY`와 합치지 않음
- market: top-level whole-game `moneyline`만
- ladder: `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`
- thresholds: `0.75..0.99`, step `0.01`
- crossing interval: 최대 450초
- liquidity/volume: feature와 strata일 뿐 discovery gate 아님
- fee: optional public observation만; missing fee를 0 또는 임의 rate로 대체하지 않음
- transport: socket read 5초와 별도로 HTTP attempt 전체 15초 wall-clock 경계, 최대 2회 retry

Soccer result-specific Yes/No negRisk와 미국 direct two-team non-negRisk는 별도 structure로 저장한다.
같은 game의 market/outcome/threshold는 독립 사건으로 세지 않고 `event_cluster_id`로 묶는다.
미국 event series는 root ID와 season series ID를 동일시하지 않고 exact semantic root/season shape로
검증한다. Soccer draw는 exact event title을 괄호에 포함하는 production descriptor까지 허용한다.

공식 미국 major-league preseason은 `PRESEASON`으로 허용한다. `PRESEASON`, `REGULAR`,
`POSTSEASON`, `UNKNOWN`을 같은 estimator에 섞지 않는다. minor/G League/AHL/ECHL/NCAA는 exact
major identity를 통과하지 못하므로 제외한다.

## Censoring과 episode

첫 관측이 이미 threshold 위이면 왼쪽 경로를 모르는 `LEFT_CENSORED`다. 직전 full observation이
없거나 450초를 넘긴 뒤 threshold 위이면 `GAP_CENSORED`다. 둘 다 episode가 아니다. 오직 연속
계약 안의 `< X → >= X`만 `UPWARD_CROSSING` episode를 만든다.

book snapshot은 체결이나 quote 보장이 아니다. path의 bid walk도 displayed depth replay이며,
resolution은 public CLOB market이 closed이고 unique one-hot일 때만 `RESOLVED`다. void와 tie는
별도 terminal class로 보존한다.

## 분석 gate

첫 review는 다음만 판정한다.

- family별 cursor completion과 request receipt skew
- positive exact identity와 minor/e-sports rejection
- full-book canonical blob 및 ladder coverage
- threshold vector/censoring/episode uniqueness
- sports clock source coverage
- event-by-ID follow-up, terminal lifecycle, schedule revision, T-24h/T-60m/last-prestart anchor missingness
- 단일 cohort와 unique `SUCCEEDED`·five-family cursor-complete cycle selection
- append-only/create-only DB와 UTC shard handoff
- external APFS storage와 150 GiB/70%/80% guard
- sport family와 season phase별 missing cell

sport 하나라도 evaluable coverage가 없으면 sport-equal macro는 `null`이다. health-only 기간에는
ROI, best threshold, best sport, live promotion을 결론내리지 않는다.

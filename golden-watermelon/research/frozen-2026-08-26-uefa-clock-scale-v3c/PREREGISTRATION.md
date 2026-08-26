# Frozen preregistration — Elite Soccer + UEFA Clock/Scale Evidence v3c

- Frozen decision timestamp: `2026-08-26T14:41:00Z`.
- Entry window: `[2026-08-26T18:30:00Z, 2026-09-02T18:30:00Z)`.
- Resolution follow-up end: `2026-09-09T18:30:00Z`.
- First collection-health review: after `2026-08-27T18:30:00Z`.
- Data contract: `soccer-inplay-elite-competition-match-winner-v3`.
- Schema profile: `golden-watermelon-v3a-schema-v1` (tables unchanged).
- Universe profile: `soccer-elite-leagues-uefa-2026-08-v3c`.
- Classifier: `soccer-elite-competition-identity-v3`.
- Mode: accountless displayed-book counterfactual simulation only.

v3b와 그 이전 DB는 immutable evidence다. v3c는 같은 external workspace에
`watermelon-white-1m-v3c`와 `watermelon-grey-5m-v3c`라는 새 runtime DB를 만들며, 이전 DB를
clean, ALTER, migrate, copy, merge, backfill 또는 delete하지 않는다.

## Amendment rationale

사용자는 elite soccer 범위에 UEFA Champions League와 UEFA Europa League를 추가하고,
향후 실제 손실 사례를 토대로 75/80/85분 이후 진입과 단계적 주문 규모를 검정할 수 있는
증거를 요청했다. 이 amendment는 그 데이터를 수집할 뿐 late-entry 또는 scale-up을 live
정책으로 선택하지 않는다.

보존된 public Gamma market evidence에서 UCL은 tag `100977`, series
`10204/ucl-2025`, UEL은 tag `101787`, series `10209/uel-2025`를 사용했다. 두 대회에는
정규시간 moneyline과 연장전·승부차기까지 포함하는 진출 시장이 함께 있으므로 settlement
description이 정규 90분과 stoppage time만 명시하는 top-level moneyline만 허용한다.

## Frozen universe identity

기존 EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A 여섯 리그는 v3b numeric identity를
그대로 유지한다. 다음 두 cross-league competition을 추가한다.
Frozen domestic codes는 `epl/bun/fl1/lal/mls/sea`다.

| competition | tag id | series id/slug | event slug prefix | resolution host |
|---|---:|---|---|---|
| UEFA Champions League (`ucl`) | 100977 | `10204/ucl-2025` | `ucl-` | `www.uefa.com` |
| UEFA Europa League (`uel`) | 101787 | `10209/uel-2025` | `uel-` | `www.uefa.com` |

공통 event tag `1/100639/100350`, exact competition tag, single exact series relation,
`seriesSlug`, slug prefix, UEFA resolution host와 exactly two teams가 모두 맞아야 한다.
UEFA 대회는 서로 다른 국내 리그 팀이 만나는 구조이므로 team의 domestic league code가
같아야 한다는 domestic-only 조건을 적용하지 않는다. title만으로 대회를 추론하지 않는다.
e-sports와 UEFA Conference League를 포함한 사전 등록 밖 대회는 제외한다.

## Sports clock evidence

Gamma event의 kickoff wall time을 실제 match minute로 치환하지 않는다. Polymarket 공식 public
Sports WebSocket `wss://sports-api.polymarket.com/ws`를 각 bounded cycle에서 최대 10초 관측하고,
대상 event slug와 일치하는 source `period`, `elapsed`, `score`, `live`, `ended`,
`last_update` 원문을 같은 run에 보존한다. 연결 실패·부분 coverage는 숨기지 않고 HIGH
collection gap으로 기록한다. raw `elapsed`와 normalization method를 함께 남기며 하프타임,
source lag, stoppage time 때문에 이를 “정확히 종료까지 남은 분”이라고 부르지 않는다.

사후 replay strata는 source regulation elapsed minute `>=75`, `>=80`, `>=85`다. 각각 정규
90분 기준 마지막 15/10/5분 가설에 대응하지만, 실제 종료까지의 wall-clock remaining time과는
다르다. 각 snapshot의 full-depth ask/bid, 후속 path와 one-hot resolution을 결합해 검정한다.

## Notional capacity evidence

실제 주문은 없고 기본 episode notional은 계속 exact `$5`다. 모든 eligible token의 full
displayed book level을 저장해 다음 frozen ladder를 동일 snapshot에서 재생한다.

`$5, $10, $15, $20, $25, $30, $40, $50, $75, $100, $150, $250, $500`

각 rung에서 full ask depth, ask VWAP/worst ask, `$5` 대비 slippage, 그 shares를 같은 시점 bid로
전량 처분할 수 있는지와 즉시 round-trip haircut을 계산한다. displayed depth는 보장 fill이
아니며, 동일 snapshot bid는 미래 stop depth의 대체물이 아니다.

향후 live scale은 한 번에 한 rung만 올린다. 최소 7일과 current rung의 confirmed live entries
30건 중 더 늦은 시점까지 유지하고, CRITICAL/HIGH 0, PENDING/QUARANTINED 0, fill/fee coverage
100%, 다음 rung의 event-clustered ask 및 실제 path sell-depth coverage가 충분하다는 별도 판정
후에만 진행한다. 이 cohort가 끝나기 전에는 어느 rung도 안전하다고 결론내리지 않는다.

## Frozen treatment and gates

| runtime | arm | cadence |
|---|---|---:|
| `watermelon-white-1m-v3c` | FAST_1M | 1 minute |
| `watermelon-grey-5m-v3c` | CONTROL_5M | 5 minutes |

cadence 외 source/config/grid는 같다. entry threshold는 `0.95/0.96/0.97/0.98/0.99`, stop replay는
hold와 `0.95/0.93/0.90/0.85/0.80/0.70`, primary episode notional은 `$5`다.
Machine-readable review labels는 entry `0.95, 0.96, 0.97, 0.98, 0.99`와
`STOP_0.95/STOP_0.93/STOP_0.90/STOP_0.85/STOP_0.80/STOP_0.70`이다.

첫 24시간에는 cursor/identity/book/clock join/DB/cohort/storage health만 판정한다. 수익성,
late-entry minute, threshold, stop, live notional은 선택하지 않는다. WebSocket clock coverage가
불충분하면 kickoff-time 추정으로 채우지 않고 collector 구조를 먼저 복구한다. 성과 판정은
entry와 follow-up 종료, exact resolution과 충분한 event-clustered 표본 이후에만 한다.

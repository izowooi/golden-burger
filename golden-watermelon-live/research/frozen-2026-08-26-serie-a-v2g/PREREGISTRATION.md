# Golden Watermelon Live Serie A / threshold A-B epoch v2g — 2026-08-26

## Provenance and immutable boundary

- Frozen decision timestamp: `2026-08-26T10:46:46Z`.
- Predecessor: Cat/Dog v2f first-24h health cohort.
- New runtimes: `watermelon-live-cat-96-1m-v2g` and
  `watermelon-live-dog-99-1m-v2g`.
- Entry: `[2026-08-26T15:00:00Z, 2026-09-02T15:00:00Z)`.
- Follow-up cutoff: `2026-09-09T15:00:00Z`.
- Both Jenkins jobs: non-concurrent, `* * * * *`, exact `$5`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.

v2a~v2f DB는 immutable operational evidence다. v2g는 새 runtime DB를 만들고 이전 DB를
clean, rewrite, migrate, copy, merge 또는 delete하지 않는다. 배포는 v2f의 정확한 24시간
half-open health range를 종료 후 sync·검증한 뒤에만 한다.

## Frozen treatment

| arm | Jenkins | runtime | exact `$5` ask VWAP |
|---|---|---|---:|
| A | `polybot-cat` | `watermelon-live-cat-96-1m-v2g` | `[0.96,0.999]` |
| B | `polybot-dog` | `watermelon-live-dog-99-1m-v2g` | `[0.99,0.999]` |

유일한 A/B 차이는 lower entry bound다. 두 arm 모두 best bid `<=0.70` trigger와 signable
full-holding FOK SELL을 사용한다. stop을 arm별로 바꾸지 않는다. 그렇게 하면 threshold
treatment와 exit treatment가 교락된다. shadow의 rare reversal은 1분 사이 0.97에서 약
0.27로 gap-down해 높은 stop도 해당 가격 체결을 보장하지 못했다.

0.96은 최적값 판정이 아니다. v3a White 1분 evidence에서 0.96은 7 resolved event 중
6승 1패, 0.99는 1승 0패였고 표본이 작다. 이 prospective cohort는 0.96의 추가 signal
coverage가 rare loss와 fee를 감당하는지 검정한다.

## Universe and settlement contract

EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A만 허용한다. Serie A exact identity는
`sport.id=12`, code/name `sea/Serie A`, primary tag `100618`, series
`10203/serie-a-2025`, team league `sea`, required event tag `101962`다. 전체 mapping hash는
`fdec6c9f49fff8aae0d8009233cbe0ca0324c385b2c4a49e1486e1cc1cdf7024`, classifier는
`soccer-major-league-identity-v2`다.

- Gamma soccer live keyset cursor complete, max 4 pages; timeout/429는 current cycle fail.
- explicit live and not ended, game age `[0h,4h]`.
- top-level negRisk home/draw/away moneyline YES만 허용.
- description이 정규 90분과 stoppage time만 payout에 포함한다고 명시해야 하며, 다른 절의
  extra-time/penalty 포함 문구와 모순되면 fail closed. `Draw No Bet`은 Draw로 간주하지 않음.
- extra time과 penalty shoot-out은 이 market result에 포함되지 않음.
- `1.000` terminal payout은 tradable quote로 간주하지 않음. market suspension/book removal
  때문에 0.96에서 바로 non-executable/closed로 갈 수 있으므로 매 경기 주문을 강제하지 않음.

## Shared execution and safety contract

- exact `$5` full-depth pre-walk와 fresh pre-submit walk, marketable FOK BUY
- BUY ask-only / stop SELL bid-only book도 실행 가능하며 무관한 반대편 side 부재로 거절하지 않음
- total/event/new-per-cycle capacity `20/1/20`; manual wallet position 미편입
- PENDING/QUARANTINED/unlinked potentially-live BUY를 capacity와 event cap에 포함
- exact condition/token/dynamic-fee agreement; confirmed fill/fee 없이는 HOLDING/COMPLETED 금지
- unknown BUY POST가 발생한 cycle은 남은 BUY를 차단하고, terminal fill의 fee가 불완전하면
  `PENDING_BUY` 유지
- Trade/episode link exception rollback, orphan catalog identity, terminal rejection release
- signable two-decimal SELL shares와 `<0.01` SDK dust 분리
- one-hot exact `0/1` resolution만 terminal; `0.5/0.5` 금지
- no take-profit, trailing stop, time exit, wallet-wide adoption or synthetic redemption

## Health and outcome gates

첫 24시간은 arm별 정확한 first-success half-open range로 cadence, overlap, cursor,
league/scope classification, exact-book funnel, capacity/guard, order/fill/fee lifecycle,
resolution identity, DB quick/FK/cohort/storage와 Jenkins console을 점검한다. 후보·주문이 0이면
threshold opportunity 부재와 운영 차단을 구분하고 수익성으로 해석하지 않는다.

7일 entry 종료 전에는 0.96/0.99 승자를 고르거나 scale-up하지 않는다. follow-up 뒤에도
CRITICAL/HIGH evidence gap, mixed cohort, 수동 position 혼입, incomplete fee/fill이 있으면
성과 판정을 중단한다.

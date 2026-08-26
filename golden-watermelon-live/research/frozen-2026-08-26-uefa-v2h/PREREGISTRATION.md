# Golden Watermelon Live UEFA universe epoch v2h — 2026-08-26

- Frozen decision timestamp: `2026-08-26T14:41:00Z`.
- New runtimes: `watermelon-live-cat-96-1m-v2h` and
  `watermelon-live-dog-99-1m-v2h`.
- Entry: `[2026-08-26T18:30:00Z, 2026-09-02T18:30:00Z)`.
- Follow-up cutoff: `2026-09-09T18:30:00Z`.
- Both Jenkins jobs: non-concurrent, `* * * * *`, exact `$5`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.

v2g와 이전 DB는 immutable operational evidence다. v2h는 새 runtime DB를 만들고 이전 DB를
clean, rewrite, migrate, copy, merge 또는 delete하지 않는다. v2g는 정기 실행을 중지한 뒤
v2h와 합산하지 않는다.

## Frozen treatment

| arm | Jenkins | runtime | exact `$5` ask VWAP |
|---|---|---|---:|
| A | `polybot-cat` | `watermelon-live-cat-96-1m-v2h` | `[0.96,0.999]` |
| B | `polybot-dog` | `watermelon-live-dog-99-1m-v2h` | `[0.99,0.999]` |

두 arm의 유일한 treatment 차이는 lower entry bound다. stop `0.70`, cadence, `$5` notional과
exposure `20/1/20`은 같다. White/Grey의 late-minute와 scale evidence가 모이기 전에는 live
entry time이나 notional을 변경하지 않는다.

## Universe amendment

기존 EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A numeric identity에 다음 두 UEFA
competition을 추가한다.

| competition | tag id | series id/slug | prefix | resolution host |
|---|---:|---|---|---|
| UEFA Champions League (UCL) | 100977 | `10204/ucl-2025` | `ucl-` | `www.uefa.com` |
| UEFA Europa League (UEL) | 101787 | `10209/uel-2025` | `uel-` | `www.uefa.com` |

공통 soccer tags, exact competition tag/series, prefix, UEFA source와 two-team relation이 모두
맞아야 한다. 다른 국내 league code를 가진 두 팀은 UEFA cup에서 정상이다. 반면 정규 90분과
stoppage time만 payout으로 명시하는 top-level negRisk moneyline HOME/DRAW/AWAY YES가 아니면
주문 전에 fail closed한다. advancement, extra-time, penalty-shootout 시장은 제외한다.

## Execution and safety

- exact `$5` full ask-depth prewalk 및 fresh marketable FOK BUY
- confirmed fill과 dynamic fee evidence 전에는 `HOLDING` 전환 금지
- unresolved PENDING/QUARANTINED/orphan BUY가 있으면 신규 진입 fail closed
- event당 1개, total 20개, cycle 20개; manual wallet position 미편입
- best bid `<=0.70` trigger 뒤 signable full shares bid-depth FOK SELL
- exact one-hot `0/1` resolution만 terminal; synthetic wallet mutation 없음
- Gamma cursor incomplete나 identity drift는 해당 cycle 신규 주문 차단

첫 24시간은 collection/execution health만 본다. 7일 entry 종료 전에는 arm winner, late-entry,
scale-up 또는 수익성을 판정하지 않는다. CRITICAL/HIGH evidence gap, fill/fee gap, mixed cohort가
있으면 후속 판단을 중단한다.

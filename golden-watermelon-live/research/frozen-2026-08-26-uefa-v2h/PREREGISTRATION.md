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
current exposure `20/1/5`는 같다. White/Grey의 late-minute와 scale evidence가 모이기 전에는 live
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
- event당 1개, total 20개, cycle 5개; manual wallet position 미편입
- best bid `<=0.70` trigger 뒤 signable full shares bid-depth FOK SELL
- exact one-hot `0/1` resolution만 terminal; synthetic wallet mutation 없음
- Gamma cursor incomplete나 identity drift는 해당 cycle 신규 주문 차단

## Safety amendment — 2026-08-28

Ferencvárosi 승리 market에서 경기 종료 후 남은 `0.001` cleanup bid를 adverse in-play move로
오인해 Cat/Dog가 각각 약 5 shares를 매도한 incident 때문에 다음 execution-only guard를 양 arm에
동일하게 추가한다. threshold, universe, `$5`, cadence와 entry window는 바꾸지 않는다.

- current Gamma live sweep과 exact CLOB condition의 independent OPEN proof
- lifecycle proof 뒤 full book 재조회
- stop `0.70`, maximum slippage 5%p: full-depth VWAP와 worst limit 모두 `>=0.65`
- displayed spread `<=0.10`, projected gross loss `<=35%`
- emergency SELL submission cycle당 1건
- confirmed SELL + proven-resolution 경제손익 `<=-$10`이면 신규 BUY 자동 차단
- live `CONFIRMED` SELL 원장을 Trade 상태보다 우선해 안전 손익을 재계산; legacy RESOLVED
  overwrite는 매도 shares 비율만 settlement에서 제외하고, 모호한 원장 매핑은 신규 BUY 차단
- holding books는 한 번의 batch read로 가져오며 불완전 book을 부분 체결로 보정하지 않음
- Jenkins launcher부터 50초 hard deadline; Python 진입 뒤 42초부터 새 Gamma/CLOB 요청 금지
- deadline이 POST와 겹치면 성공으로 추정하지 않고 execution ledger uncertain-outcome으로 격리
- closed Elderberry의 cycle 폭주 교훈을 반영해 신규 BUY를 cycle당 20개에서 5개로 축소;
  exact `$5` 기준 한 faulty cycle의 신규 요청 원금을 `$100`이 아니라 `$25`로 제한

이는 관측 결과를 보고 arm threshold를 바꾸는 tuning이 아니라 irreversible order의 blast radius를
제한하는 공통 safety correction이다. amendment 전후는 `strategy_source_digest`로 분리하고,
incident 이전 execution evidence를 새 guard의 성과처럼 합산하지 않는다.

첫 24시간은 collection/execution health만 본다. 7일 entry 종료 전에는 arm winner, late-entry,
scale-up 또는 수익성을 판정하지 않는다. CRITICAL/HIGH evidence gap, fill/fee gap, mixed cohort가
있으면 후속 판단을 중단한다.

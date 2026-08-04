# Golden Blueberry 회고 가이드 — Closing Surge

> 먼저 `docs/retro/EVIDENCE_CONTRACT.md`를 읽는다. 이 문서의 예외는 cohort에서 모노레포
> commit 대신 `strategy_source_digest`를 쓰는 점이다. Git commit은 provenance로 계속 남긴다.

## 사전 등록된 실험

- A: first crossing 최소 급등 `+2%p`.
- B: first crossing 최소 급등 `+5%p`.
- 공통: strict binary YES, `[0.85,0.93]`, `(0h,72h]`, sports in-play 포함,
  `$5`, liquidity/volume `$10k`, target/stop `0.97/0.78`.
- arm당 capital `$150`, open notional `$50`, drawdown kill switch `-$30`.

## 1주 checkpoint

1주차에는 P&L 비교, winner 선택, threshold 변경, 증액을 하지 않는다. 다음만 판정한다.

- 두 Jenkins job이 같은 UTC 5분 경계에서 시작했는지, schedule gap과 concurrent build 여부
- live SUCCESS run과 cursor-complete market sweep
- source digest/config가 arm 내 하나인지, arm 간 source digest가 같은지
- snapshot `0<gap<=15m`, first-crossing decision/rejection coverage
- submission/status/CONFIRMED fill/fee/reconciliation coverage
- DB online backup SHA-256, `PRAGMA integrity_check`, restore test
- account/job/runtime job/DB/signature type의 독립성
- drawdown kill switch가 의도대로 신규 BUY만 차단하는지

## 30일 review window

```bash
export REVIEW_START=<YYYY-MM-DD>
export REVIEW_END=<YYYY-MM-DD>          # inclusive UTC date
export REVIEW_AS_OF="$REVIEW_END"
export REVIEW_DAYS=30
export RETRO_OUTPUT="$HOME/polybot-retro/blueberry-$REVIEW_END"
```

daily-rsync catalog에서 각 Jenkins job의 verified online backup을 찾는다. 실행 중 DB를 `cp`하지
않는다.

```bash
cd daily-rsync
uv run daily-rsync locate --strategy golden-blueberry
uv run daily-rsync verify --job <arm-a-jenkins-job> --strategy golden-blueberry
uv run daily-rsync verify --job <arm-b-jenkins-job> --strategy golden-blueberry
cd ..

uv run --project polybot-observability polybot-retro audit \
  --db "$BLUEBERRY_A_DB" --db "$BLUEBERRY_B_DB" \
  --days "$REVIEW_DAYS" --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/audit" --strict
```

`CRITICAL`/`HIGH`, source cutoff 부족, checksum 실패가 하나라도 있으면 수익 비교와 tuning을
중단한다.

## Blueberry 전용 read-only report

```bash
cd golden-blueberry
uv run python scripts/analyze_experiment.py \
  --arm-a "$BLUEBERRY_A_DB" --arm-b "$BLUEBERRY_B_DB" \
  --review-start "$REVIEW_START" --review-end "$REVIEW_END" \
  --output-dir "$RETRO_OUTPUT/ab"
```

도구는 source DB를 immutable/read-only로 열고 전후 SHA-256을 바꾸지 않는다. 출력의
`status`가 `EVALUABLE_NO_AUTOMATIC_WINNER`여도 사람이 event clustering과 uncertainty를
검토해야 한다.

## Cohort gate

각 arm은 정확히 하나의 다음 cohort여야 한다.

```text
config_hash × strategy_source_digest × mode=live × job_name
```

- A의 `entry.min_surge=0.02`, B는 `0.05`.
- source digest는 양쪽이 동일.
- Git commit 차이는 기록하되 unrelated monorepo 변경만으로 cohort를 분할하지 않는다.
- 실제 Blueberry source digest가 달라졌다면 새 cohort로 분리한다.
- simulation/live, carry-in/out, kill-switch 전후는 섞지 않는다.

## Primary evidence

성과 모집단은 BUY와 SELL 양쪽 exact order가 `order_fills.status='CONFIRMED'`이고 size가 대사된
round trip이다. net P&L은 confirmed VWAP×size와 알려진 fee만 사용한다.

반드시 별도로 보고한다.

- first crossings / surge threshold reject / metadata reject(`entry_signal_decisions`)
- fresh-book reject의 sanitization된 로그 사유와 candidate→submitted attrition
- candidate→submitted→confirmed BUY→confirmed closed funnel
- fee-complete round trip n, net P&L, per-position return, win rate, worst loss
- hold time과 stop gap; requested stop 대비 actual exit VWAP
- unresolved/HOLDING/PENDING/QUARANTINED exposure
- resolution settlement assumption(confirmed round-trip P&L에 합산 금지)
- event-clustered 결과와 paired A/B overlap
- 스포츠/비스포츠, pregame/in-play는 exploratory slice로만 표시

## 결정 규칙

| 조건 | 결정 |
|---|---|
| arm당 confirmed closed `<20` | `INCONCLUSIVE` — threshold를 바꾸지 말고 기간 연장 |
| confirmed closed 중 fee gap 존재 | `NOT EVALUABLE` — 대사 복구 |
| 어떤 arm이든 economic P&L `<=-$30` | 신규 진입 중단 유지, `STOP/DIAGNOSE` |
| 양 arm 모두 fee 이후 기대값 `<=0` | `STOP` — 급등 강도 선별 가설 기각 |
| 한 arm만 양수 | cluster CI, 양쪽 time half, worst loss, unresolved exposure를 모두 점검 |
| 결과가 유지되고 evidence 완전 | `CONTINUE $5`; 곧바로 증액하지 않음 |

승자 후보가 생기면 `$10` 승격은 별도 코드 hard-cap 변경과 새 사전 등록 cohort다. `$5` 결과를
선택적으로 재집계해 `$100` 또는 `$1,000`을 정당화하지 않는다.

## 보고서 템플릿

```text
Review UTC range [start, end):
Verified DB paths and SHA-256:
Source/sync cutoffs:
Arm cohorts and source digests:
Strict audit status:
First-crossing funnel by arm:
Confirmed round trips / fee coverage:
Event-clustered net result and uncertainty:
Worst loss / stop gap / hold time:
Unresolved exposure:
Sports and in-play exploratory slice:
Kill-switch status:
Decision: INCONCLUSIVE | NOT EVALUABLE | STOP | CONTINUE $5 | REDESIGN
Next review date:
```

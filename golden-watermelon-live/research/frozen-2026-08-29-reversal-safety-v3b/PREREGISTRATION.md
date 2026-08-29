# Golden Watermelon Live reversal-safety amendment v3b — 2026-08-29

- Safety decision timestamp: `2026-08-29T06:49:31Z`.
- Entry remains `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)`.
- Follow-up cutoff remains `2026-09-12T04:00:00Z`.
- All six Jenkins jobs remain non-concurrent, `* * * * *`, exact `$5`.
- Cohort remains `config_hash × strategy_source_digest × mode × job_name`.

이 문서는 v3a의 universe와 0.96/0.99 entry treatment를 유지하면서 PSG–Lille 실측으로 확인된
두 안전 결함만 교정한다. v3a와 v3b 결과는 `strategy_source_digest`로 분리하며 수익성 집계에서
합치지 않는다. 기존 Cat/Dog DB와 신규 MLB/NHL DB는 bot-owned position을 계속 관리하기 위해
그대로 사용하고 clean, rewrite, migrate, copy, merge, backfill 또는 delete하지 않는다.

## Frozen arms and universe

| family | A | B | exact `$5` ask VWAP |
|---|---|---|---|
| Soccer | `polybot-cat` | `polybot-dog` | `[0.96,0.999]` / `[0.99,0.999]` |
| MLB | `polybot-bear` | `polybot-tiger` | `[0.96,0.999]` / `[0.99,0.999]` |
| NHL | `polybot-lion` | `polybot-wolf` | `[0.96,0.999]` / `[0.99,0.999]` |

Soccer는 EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL의 regulation-time
HOME/DRAW/AWAY YES, MLB/NHL은 exact major-league whole-game direct moneyline만 허용한다.
e-sports, minor/college, child/period/spread/total/prop/future/advancement는 fail closed한다.
Gamma server gate는 cumulative volume `$5,000`, liquidity `$5,000`, 최대 4페이지
cursor-complete이며 fresh exact `$5` full CLOB book이 최종 gate다.

## Amendment 1: proven-no-POST retry contract

한 cycle에서 선택된 모든 episode는 어떤 주문보다 먼저 `QUEUED_NO_POST`로 원자적으로 기록한다.
각 후보는 실제 처리 직전 `SUBMISSION_IN_PROGRESS`가 된다.

- `PreSubmissionContractError`, global/event capacity와 fresh-book 이탈처럼 venue POST가 없었다고
  증명된 결과만 `PRE_SUBMISSION_CONTRACT_ERROR`, `BLOCKED_GUARD` 또는
  `NO_POST_RETRYABLE`로 남기고 다음 fresh in-band snapshot에서 재시도한다.
- 앞 후보의 예외로 처리되지 못한 뒤 후보는 `QUEUED_NO_POST`이므로 다음 cycle에 재시도한다.
- POST 가능성이 생긴 뒤의 예외, unknown submission, venue rejection은 자동 재시도하지 않는다.
  execution ledger가 order absence 또는 exact terminal fill을 증명해야 한다.
- 한 event에서 여러 result가 동시에 threshold를 넘으면 전부 `BLOCKED_GUARD`이며, 이후 정확히
  하나만 in-band일 때만 재검토한다.

signed exact `$5` FOK BUY는 venue tick grid에서 maker `$5`와 four-decimal taker shares를 동시에
만족하는 가장 낮은 limit을 찾는다. limit은 arm 상한 `0.999`를 넘지 않는다. 호환 envelope가
없으면 POST하지 않는다.

## Amendment 2: entry-relative protective stop

effective stop은 모든 family/arm에 공통으로 다음과 같이 고정한다.

`max(0.70, confirmed BUY VWAP - 0.05)`

따라서 confirmed 0.99 진입은 0.94, 0.96 진입은 0.91 부근에서 adverse move를 감지한다. legacy
holding의 저장 stop이 0.70이어도 source v3b 실행 중에는 confirmed BUY VWAP로 같은 effective
stop을 계산한다. 새 BUY는 confirmed fill 대사 시 계산된 stop을 trade row에도 저장한다.

stop은 체결가 보장이 아니다. irreversible SELL 전 current Gamma event와 exact CLOB condition이
독립적으로 OPEN임을 확인하고, 그 뒤 full bid book을 다시 읽는다. spread `<=0.10`, normal
execution floor `effective stop-0.05`, projected gross loss `<=35%`, cycle SELL `1`을 유지한다.
독립 OPEN proof 뒤 stop을 건너뛴 discontinuous gap에서는 floor/loss cap이 손절을 무력화하지
않으며 fresh complete executable book이면 FOK SELL을 허용한다. 종료 후 cleanup bid는 OPEN
proof에서 차단한다.

동일 event에는 한 포지션만 허용한다. 기존 결과의 stop SELL이 exact confirmed fill로 종결되기
전에는 반대 HOME/DRAW/AWAY를 사지 않는다. confirmed 후 다음 cycle에 다른 condition이 fresh
arm 안이면 진입할 수 있다. 이 안전 순서 때문에 최소 한 cadence 지연이 생기며, 1분 사이에
종료되는 reversal은 포착을 보장하지 않는다.

## Unchanged guards and review

- account/event/cycle `20/1/5`, emergency SELL cycle당 `1`, manual wallet position 미편입·미청산.
- unresolved PENDING/QUARANTINED/orphan/fill-fee gap은 신규 BUY를 막는다.
- confirmed SELL + proven resolution 경제손익 `<=-$10`이면 신규 BUY를 막는다.
- DB별 nonblocking run lock과 finite request timeout을 유지한다.
- 첫 24시간은 collection/execution health만, entry 종료 후 family/arm 결과는 v3b source cohort만
  평가한다. CRITICAL/HIGH evidence gap이나 mixed source digest가 있으면 수익성 판단을 중단한다.

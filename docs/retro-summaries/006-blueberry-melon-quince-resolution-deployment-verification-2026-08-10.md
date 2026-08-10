# 006 — Blueberry·Melon·Quince resolution deployment verification — 2026-08-10

작성일: 2026-08-10

Jenkins 관측: `2026-08-10T14:46:08Z` (`23:46:08 KST`)

Build cycle: 약 `2026-08-10T14:36Z`–`14:43Z` (`23:36`–`23:43 KST`)

DB source cutoff: `2026-08-10T14:43:25Z`–`14:43:29Z`

대상: Blueberry 2개, Melon 3개, Quince 3개 live job

선행 기록: [005 — closed-market resolution fix](005-blueberry-melon-quince-closed-market-resolution-fix-2026-08-10.md)

## 0. 결론

closed-market fallback 수정의 운영 배포 검증이 통과했다.

1. 8개 최신 수동 build는 모두 commit `b91e9ac8cdc6`, live, `close_only`, `SUCCESS`였다.
2. 8개 job 모두 TimerTrigger가 비어 있어 자동 신규 cycle은 중지된 상태였다.
3. 수정 전 18개 `HOLDING`이 전부 closed final Gamma payout 증거로 `RESOLVED`가 됐다.
4. 검증 DB 합계는 `PENDING_BUY=0`, `HOLDING=0`, `PENDING_SELL=0`, `RESOLVED=18`이다.
5. 8개 최신 run의 신규 BUY는 모두 0이었다.
6. 8개 DB·bot log·Jenkins console 재동기화와 local verify가 모두 성공했다.

따라서 `004`에서 발견한 resolution lifecycle blocker는 해소됐다. 이 blocker 기준으로는
`active`와 timer 복귀를 진행할 수 있다.

## 1. Jenkins build와 lifecycle

| Jenkins job | Runtime | Build | Commit | Lifecycle | Checked / resolved / bought | Result |
|---|---|---:|---|---|---:|---|
| `polybot-eagle` | `blueberry-live-a-2pp` | 6775 | `b91e9ac8cdc6` | `close_only` | 3 / 3 / 0 | SUCCESS |
| `polybot-fox` | `blueberry-live-b-5pp` | 8570 | `b91e9ac8cdc6` | `close_only` | 1 / 1 / 0 | SUCCESS |
| `polybot-wolf` | `polybot-melon-low` | 7199 | `b91e9ac8cdc6` | `close_only` | 1 / 1 / 0 | SUCCESS |
| `polybot-lime` | `polybot-melon-mid` | 8351 | `b91e9ac8cdc6` | `close_only` | 1 / 1 / 0 | SUCCESS |
| `polybot-fruit` | `polybot-melon-high` | 1585 | `b91e9ac8cdc6` | `close_only` | 0 / 0 / 0 | SUCCESS |
| `polybot-bear` | `polybot-quince-passive` | 6848 | `b91e9ac8cdc6` | `close_only` | 4 / 4 / 0 | SUCCESS |
| `polybot-eco` | `polybot-quince-nearest` | 8738 | `b91e9ac8cdc6` | `close_only` | 4 / 4 / 0 | SUCCESS |
| `polybot-tiger` | `polybot-quince-cross` | 8356 | `b91e9ac8cdc6` | `close_only` | 4 / 4 / 0 | SUCCESS |
| **합계** |  |  |  |  | **18 / 18 / 0** | **8/8 SUCCESS** |

Eagle/Fox의 redacted console과 8개 DB의 최신 `run_audits`를 교차 확인했다. 각 run은
`mode=live`, `lifecycle_mode=close_only`, `git_commit=b91e9ac8cdc6`, `status=SUCCESS`였고
cycle의 `checked_holdings`, `resolved`, `bought`가 위 표와 일치했다.

## 2. daily-rsync와 무결성

정확히 위 8개 job만 `sync-job`으로 다시 가져왔다. simulation·다른 전략은 포함하지 않았다.

| Job | Sync run | DB source cutoff UTC | DB SHA-256 | Verify checked | Result |
|---|---|---|---|---:|---|
| Eagle | `b60c33d330364221bcb645f2edf1d561` | 14:43:28 | `004cd7f7dbbb50bf7ff5edc9563f8ae312adb06d233a5df94ec16084500ee57c` | 1,569 | SUCCESS |
| Fox | `32f73123b6794598864278033310a5ed` | 14:43:29 | `163c9ca38452d31dd818fabbd5ad2e821db215f3c16b605a21ce315a47aa38cc` | 1,570 | SUCCESS |
| Wolf | `e75bceb6d3884669b7a485dfc120710a` | 14:43:25 | `ce9fe1bdcf571b79178126fa5a4b006622a0d6d19fae47dbfbcdd99047e6cf81` | 1,054 | SUCCESS |
| Lime | `3ae2916e689f429d94df22aed2b1eab7` | 14:43:26 | `a0514c4191243af965d4c2512681e057f5c5a539c4baba98b66adf5a200d91ff` | 1,590 | SUCCESS |
| Fruit | `259a419aa49b49b2a48663e9b182d7f3` | 14:43:25 | `064215a58244a98363f7ccbf7b01fb7316959f1045c4a41e2903dab1e7147b9c` | 1,591 | SUCCESS |
| Bear | `e1aac0c5b5de4cf1ae597a469408abe1` | 14:43:27 | `bb3e3df4c02c8aa2e230c8aab72aedb8075702af4ff37a215d0849a9b154668d` | 1,094 | SUCCESS |
| Eco | `f66ba34baa214c4a800c623d502d5a99` | 14:43:28 | `1068659d63448dbe165288f2a9d40619051a1cf5d8458f713f4c902a09a4d512` | 1,629 | SUCCESS |
| Tiger | `97217edccfa840cab46daaef9d13dd43` | 14:43:28 | `d2b2ac4873e4439a67f4a5c0081965696103e8ac6359e4ff0efcf998f1134ddd` | 1,089 | SUCCESS |

동기화 합계:

- 변경 artifact 24개: job당 live DB, 당일 bot log, 최신 Jenkins console
- 기록 bytes: `1,224,666,790` (`1.141 GiB`)
- 전송 실패: 0
- verify checked: 11,186
- verify failure: 0
- `skipped_retention_deleted`: 0
- open artifact conflict: 0
- 8개 모두 `latest_sync_attempt=SUCCESS`, `latest_successful_sync=SUCCESS`,
  `analysis_ready=true`, DB `status=SYNCED`

## 3. 검증된 DB 상태

| Arm | Total | PENDING_BUY | HOLDING | PENDING_SELL | RESOLVED | Resolution assumption |
|---|---:|---:|---:|---:|---:|---:|
| Blueberry A | 3 | 0 | 0 | 0 | 3 | +$2.0307 |
| Blueberry B | 1 | 0 | 0 | 0 | 1 | +$0.4887 |
| Melon low | 1 | 0 | 0 | 0 | 1 | +$0.4887 |
| Melon mid | 1 | 0 | 0 | 0 | 1 | +$0.4887 |
| Melon high | 0 | 0 | 0 | 0 | 0 | $0.0000 |
| Quince passive | 4 | 0 | 0 | 0 | 4 | +$1.7775 |
| Quince nearest | 4 | 0 | 0 | 0 | 4 | +$1.7775 |
| Quince cross | 4 | 0 | 0 | 0 | 4 | +$1.7775 |
| **합계** | **18** | **0** | **0** | **0** | **18** | **+$8.8293** |

18/18 모두 다음 evidence를 갖는다.

- `resolution_outcome='Yes'`
- `resolution_value=1.0`
- `resolution_evidence='gamma_closed_final_outcome_prices+execution_ledger_exact_confirmed_buy'`
- `realized_pnl IS NULL`
- `settlement_assumption_basis='confirmed_buy_fill_gross_fee_unproven'`

따라서 +$8.8293은 exact confirmed BUY와 payout 1을 이용한 **gross settlement assumption**이다.
BUY fee가 입증되지 않았고 실제 redeem transaction도 수집하지 않으므로 realized/net P&L이나
실제 회수 완료 금액으로 표현하지 않는다.

## 4. 운영 결정

- resolution blocker는 해소됐다.
- 운영자가 active를 재개한다면 Eagle/Fox는 같은 wall-clock 관측을 위해 `*/5 * * * *`,
  Melon·Quince 여섯 job은 기존 `H/5 * * * *`를 복구한다.
- 각 shell의 최종 effective lifecycle을 `active`로 한 번만 명확히 두고 중복 export를 정리한다.
- active 복귀 첫 cycle에서도 `bought`, open state와 RunAudit 성공을 확인한다.
- source lineage가 바뀌었으므로 수정 전후 성과 cohort는 합치지 않는다.

별개의 보안 문제는 남아 있다. 8개 config 모두 익명 조회 가능한 plaintext HTTP Jenkins에
private key/funder 값을 inline 할당한 것으로 탐지됐다. 이 lifecycle 검증과 별도로 credential을
교체하고 Jenkins Credentials Binding으로 이전해야 한다.

# golden-nectarine 판정 — 2026-07-30

- 전략: Bottom Fisher
- production deployment: `macmini-m5 / polybot-eagle / golden-nectarine / default / live`
- review window: **[2026-07-12 00:00:00Z, 2026-07-30 00:00:00Z)**
- DB snapshot: 2026-07-30 12:16:51Z
- 판정: **CLOSE**
- 운영 상태: **CLOSED — 운영자 종료 확인 2026-07-30**
- 판정 신뢰도: **중상**(중단 방향), **낮음~중간**(정확한 net 손익과 현재 wallet 노출)
- 성과 원장: BUY·SELL 양쪽의 수량이 일치하는
  `order_fills.status='CONFIRMED'`만 사용

---

## 0. 결론

**현행 live 전략을 종료한다. `POLYBOT_HOLD_HOURS=24`로 live 전환하거나 live A/B를
하지 않는다.**

> **2026-07-30 운영 확인:** 사용자가 이 판정에 따라 Nectarine 전략 운영을 실제로
> 종료했다고 확인했다. 따라서 아래 wind-down 절차는 권고안이 아니라 **실행 이력**으로
> 보존한다. 다만 이 확인만으로 동기화 cutoff 이후 wallet 잔고, redeem 완료, CLOB open
> order 0건이 새 evidence로 증명되는 것은 아니다. 그런 사실은 후속 wallet/CLOB 대사
> artifact가 있을 때만 별도로 확정한다.

완전히 대사된 81건 전체는 fee 전 gross +$6.95지만 이 합계는 견고하지 않다.
단일 event cluster의 6개 market이 +$26.09를 만들었고, 그 cluster를 제외하면
**-$19.14, -4.91%**다. 120시간 calendar exit로 종료됐고 완전 대사 가능한
`max_holding` 부분집합 59건은 **-$14.46, -4.70%**였고 event-cluster bootstrap
95% 구간도 **[-7.61%, -1.75%]**로 0 아래다. 이는 exit reason으로 사후 선택된
기술 통계이지 전체 120h 정책의 alpha를 독립적으로 추정한 값은 아니다.

strict evidence gate는 **4 CRITICAL / 6 HIGH / 2 MEDIUM**으로 실패했다. review
window의 `COMPLETED` 231건 중 실제 BUY·SELL fill을 완전히 대사한 거래는
81건(35.1%)뿐이고 fee도 대부분 불완전하다. 634개 trade row에는
`QUARANTINED` 231건과 `UNFILLED` 40건이 있으며 stale reconciliation이 237건이다.

보유기간을 24·72·120·168·240시간으로 바꾼 optimistic barrier replay도 구제
파라미터를 찾지 못했다. 목표 시점 **이후** 첫 관측만 허용하도록 시점 계약을 바로잡은
결과, 24h 평균은 +1.96%였지만 condition-cluster CI는 **[-1.44%, +5.69%]**였고
다섯 horizon 모두 CI가 0을 포함했다. 이 분석은 모든 submitted signal이 recorded
price에 체결됐다고 가정하고, 현행 20일 rolling-low 진입을 재생하지 못하며,
spread·fee·queue를 무시한다. `UNFILLED` 신호가 +21.63%였던 반면 clean confirmed
subset은 -1.43%였다는 실행 selection도 남는다. 현 데이터는 특정 보유기간을
simulation A/B 후보로 지명할 근거조차 주지 않는다.

따라서 판정은 다음과 같다.

| 항목 | 판정 |
|---|---|
| 현행 120h live | **CLOSE** |
| 24h live 변경 | **금지** |
| 24h vs 120h live A/B | **금지** |
| 특정 horizon 연구 | 현 데이터에서는 **후보 없음**; 복구 후 새 가설의 offline 연구만 허용 |
| `max_positions` 상향·증액 | **금지** |

## 1. 원자료 고정: sync와 verify

과거 문서의 job/account 별칭을 소급 적용하지 않고 `daily-rsync` catalog에서 실제
deployment를 찾았다. 현재 evidence의 Jenkins job은 `polybot-eagle`이고 runtime job은
`default`다. catalog에는 account deployment epoch가 없으므로 이 문서에서 dashboard
slot이나 과거 account alias를 임의 귀속하지 않는다.

| 항목 | 값 |
|---|---|
| local DB | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eagle/strategies/golden-nectarine/runtime/default/databases/latest/trades.db` |
| remote source | `/Users/jongwoopark/.jenkins/workspace/polybot-eagle/golden-nectarine/data/default/trades.db` |
| 크기 | 941,010,944 bytes |
| synchronized artifact SHA-256 | `9ac1442a5ae3697e1646ac29cad2c96017aa832dbe9a863a0a812bdfc4980cae` |
| audit consistent-snapshot SHA-256 | `3a4db89f39cd7b96dbc87ec108cfd3e52b3cc7162374c40b5d0e9f34fd04a753` |
| manifest `quick_check` | `ok` |
| sync | run `7684299172dc452f8ea2549f95bc6594`, plan `9b92e9e2977f7fba`, `SUCCESS` |
| sync 구간 | 2026-07-30 12:15:08Z ~ 12:17:13Z |
| sync 결과 | transferred 3,807 / skipped 0 / failed 0 |
| 재검증 | checked 3,807 / failed 0 / `SUCCESS` |

첫 SHA는 manifest와 current local file bytes가 일치하는 전송 artifact checksum이다.
두 번째는 strict audit가 SQLite online backup으로 고정한 consistent snapshot checksum이다.
서로 다른 대상을 같은 파일의 checksum 불일치로 해석하지 않는다.

함께 동기화된 자료는 Jenkins console 3,786개(build 1~3,788, 3,478·3,479 누락),
bot log 19개(07-12~07-30), 월간 CSV 1개다. 반사실 분석은 DB에 더 들어 있는
2026-07-30 12:11:52Z까지의 forward snapshot을 사용할 수 있지만, 진입·청산 성과
review window는 07-30 00:00Z 미만으로 고정했다.

## 2. strict evidence gate: FAIL

최신 `audit-nectarine/` bundle의 결과다.

### CRITICAL

| issue | 결과 |
|---|---:|
| `completed_trade_fill_gap` | 기간 청산 231건 중 완전 대사 81건, **35.1%** |
| `fill_quantity_overflow` | confirmed fill 합계가 `latest_size_matched`를 초과한 주문 7건 |
| `closed_trade_fill_quantity_mismatch` | 완료 trade의 실제 BUY/SELL 수량 불일치 10건 |
| `uncertain_submission_outcome` | POST 결과 미확정 intent 2건 |

### HIGH / MEDIUM

| severity | issue | 결과 |
|---|---|---:|
| HIGH | `stale_order_reconciliation` | 1시간 초과 미완료 대사 **237건** |
| HIGH | `fill_fee_missing` | confirmed fill의 fee amount 결손 **55.6%** |
| HIGH | `failed_runs` | 28건 |
| HIGH | `stale_running_runs` | 2건 |
| HIGH | `run_schedule_gap` | SUCCESS 최대 공백 21.22시간 |
| HIGH | `archive_window_short` | 요청 window 96.5%, global sweep 68.3% |
| MEDIUM | `legacy_trade_share_mismatch` | reconciled fill과 legacy share 불일치 79건 |
| MEDIUM | `logs_missing` | audit의 DB-relative 자동 탐색 기준 |

`logs_missing`은 실제 로그 부재가 아니다. `daily-rsync` canonical tree의 bot log 19개와
Jenkins console 3,786개를 별도로 검사했다. finding은 audit 결과대로 보존한다.

legacy `trades.realized_pnl`은 요청 price×요청 quantity 기반
`ORDER_ASSUMPTION` 값이다. audit가 보여주는 기간 값 +$13.71과 all-time +$11.04는
성과 주장의 근거가 아니며 아래 표에 사용하지 않았다.

## 3. config와 code cohort

resolved parameter는 두 config에서 동일하고 두 번째에 `lifecycle_mode: active`만
명시적으로 추가됐다.

| config hash | run 구간 | 차이 |
|---|---|---|
| `ed49056811c9fefe3eebc2e67da7317dd850747f99fd40f08fff0789a7016f94` | 07-12 14:55 ~ 07-14 10:57Z | 최초 기록 |
| `6e640d5fd4a3b3398054c12dbb8bf54a07167050091520265ba4be98652f3ba2` | 07-14 11:02 ~ 07-29 23:55Z | `lifecycle_mode: active` 명시 추가 |

공통 운영값:

- $5/position, `max_positions=150`
- 20일 rolling low, 최근 24h 제외
- hold 120h, cooldown 168h
- YES 0.03~0.50, resolution까지 최소 720h
- TP +30%, SL -30%
- liquidity $10k 이상

audit window에 Git commit이 40개다. 주문은 `run_id`로 commit에 연결할 수 있지만
`trades` 자체에는 `run_id`가 없어 81건 결과를 40개 code cohort로 완전 분해할 수 없다.
config가 같다는 이유로 전체 기간을 단일 code cohort로 취급하지 않는다.

## 4. actual confirmed-fill 성과

포함 기준은 live successful submission, confirmed fill, 완료된 reconciliation,
fill 합계와 `latest_size_matched` 일치, 유효한 domain, BUY·SELL 수량 일치다.
이 기준을 통과한 거래는 81/231건이다.

| exit reason | n | events | confirmed cost | gross P&L | gross ROI | win rate |
|---|---:|---:|---:|---:|---:|---:|
| `max_holding` | **59** | **52** | $307.54 | **-$14.46394** | **-4.70%** | 16.9% |
| `stop_loss` | 12 | 11 | $61.27 | **-$25.14099** | **-41.03%** | 0% |
| `take_profit` | 10 | 10 | $52.60 | +$46.55020 | +88.49% | 100% |
| **합계** | **81** | **68** | **$421.41** | **+$6.94527** | **+1.65%** | **24.7%** |

Event-cluster bootstrap 95% interval:

| cohort | 95% interval |
|---|---:|
| 전체 81건 | **[-8.66%, +14.89%]** |
| 대사된 `max_holding` 부분집합 59건 | **[-7.61%, -1.75%]** |
| 최대 양의 event cluster 제외 | [-9.93%, +0.12%] |

### 한 event가 전체 부호를 바꾼다

단일 “presidential-run announcement” event cluster의 6개 market이 **+$26.09**를
만들었다. 이를 제외한 75건/67 events는:

```text
confirmed cost  $389.80
gross P&L       -$19.14
gross ROI       -4.91%
```

따라서 전체 +$6.95는 전형적인 120h mean reversion의 반복 성과가 아니라 한 tail event에
의존한다. 그 payoff까지 포함해도 전체 CI는 0을 넓게 포함한다. 대사된
`max_holding` 부분집합은 event clustering 이후에도 음수지만, 이 사후 부분집합만으로
전체 정책의 alpha를 확정하지 않는다.

confirmed fill은 BUY 410행, SELL 183행으로 총 593행이다. liquidity role은 모두
기록됐지만 fee amount/rate가 완전한 fill은 44.35%, fee-complete round trip은
26/81건뿐이다. **+$6.95는 gross이며 net은 unknown이다.** fee 결손을 0으로 채워
profit으로 확정하지 않는다.

## 5. 실행과 portfolio selection이 전략 검증을 오염시켰다

### 상태와 order flow

snapshot 시점 전체 634 trade rows:

| status | n | 비율 |
|---|---:|---:|
| `COMPLETED` | 246 | 38.8% |
| `HOLDING` | 117 | 18.5% |
| `QUARANTINED` | **231** | **36.4%** |
| `UNFILLED` | 40 | 6.3% |

기간 내 BUY submission 2,606회 중 accepted/matched response는 627회, confirmed fill
order는 299개였다. SELL submission 3,718회 중 response 성공은 231회, confirmed fill
order는 131개였다.

confirmed BUY evidence가 있는 것은 `COMPLETED` 237건과 `HOLDING` 66건뿐이다.
`QUARANTINED` 231건과 `UNFILLED` 40건에는 confirmed BUY fill이 없고, `HOLDING`
51건에도 confirmed BUY fill이 없다. 그러나 fill 결손만으로 zero exposure를 확정할 수
없으므로 wallet/CLOB 대사가 필요하다.

Jenkins console 3,786개와 bot log 19개를 전수 교차한 결과, application 완료 run은
성공 3,757 / 실패 28이고 stale `RUNNING` 2건과 sync 시점 in-flight 1건이 별도로
남았다. 7월 18일 24회 연속 실패의 직접 원인은 429가 아니라 pending CLOB
reconciliation fail-closed였다. `QUARANTINED` 231건 도입 뒤 zero-fill 취소 증명 반복은
4회로 줄었지만, 최신 단면에는 `needs_reconciliation=1`인 submission 246건과 마지막
완료 cycle의 reconciliation error 108건이 남았다. strict audit의 237건은 review
window 종료 시점 기준 stale 수치이고, 246건은 12시간 뒤 synchronized snapshot의 최신
pending 수치라 서로 다른 cutoff다. 성공 cycle도 5분 초과 908회, 최장 88분 20초여서
Jenkins `SUCCESS`를 건강한 주문 lifecycle로 해석할 수 없다.

### `max_positions=150`은 crash breaker가 아니었다

| 지표 | 결과 |
|---|---:|
| active cycles | 3,613 |
| 평균 candidate/cycle | 336.2 |
| cap 발동 cycle | **2,500 / 3,613, 69.2%** |
| cap 발동 active days | 15 / 18 |
| cap skips | **585,874** |
| peak holdings | 150 / 150 |

후보는 Gamma 반환 순서대로 처리됐고 depth·liquidity·expected return ranking이 없다.
따라서 cap이 발동한 대부분의 기간에 `max_positions`는 위험 시에만 작동하는 breaker가
아니라 **무순위 selector**였다.

cap candidate 120h counterfactual도 평균 +1.68%, median 0%, event-cluster 95% interval
[-0.27%, +3.70%]로 명확한 버려진 alpha를 보여주지 않는다. cap을 올리거나 증액할
근거가 없다.

## 6. 현행 20일 rolling-low entry는 replay할 수 없다

좋은 점은 audit 기간의 3,616 sweeps가 모두 cursor-complete이고 snapshot/catalog join이
100%라는 것이다. 그러나 전략 핵심을 재현할 history가 없다.

- archive 전체 span: **17.886일**
- 전략이 요구하는 최소 span: **19일**(20일의 95%)
- live 진입에 사용한 `/prices-history` backfill point: archive에 미보존
- archive만으로 faithful rolling-low signal을 생성할 수 있는 timestamp: **0개**

실제 634개 trade row의 `lookback_days_at_buy`는 모두 19일 gate를 통과했으므로 live
backfill 자체는 진입 시 작동했다. 하지만 그 point가 보존되지 않아 사후에 같은 universe와
rule로 진입 신호를 다시 만들 수 없다.

따라서 아래 counterfactual은 **entry-rule backtest가 아니다.** 이미 recorded order가 있는
signal에 다른 보유기간을 붙인 holding-period sensitivity일 뿐이다. scanner가 보았지만
주문되지 않은 전체 후보, failed BUY 이후 후보, cap과 collateral state를 재현하지 못한다.

## 7. 보유기간 변경으로 구제되는 증거는 없다

### 7-1. corrected post-target replay

현행 TP/SL ±30%를 유지하고 submitted signal을 barrier replay한 결과:

| calendar horizon | n | conditions | mean return | median | condition-cluster 95% CI |
|---:|---:|---:|---:|---:|---:|
| 24h | 193 | 173 | +1.96% | 0.00% | **[-1.44%, +5.69%]** |
| 72h | 185 | 165 | +4.85% | 0.00% | [-0.27%, +10.50%] |
| 현행 120h | 234 | 208 | +3.01% | 0.00% | [-1.88%, +8.28%] |
| 168h | 173 | 145 | +4.81% | 0.00% | [-2.32%, +12.33%] |
| 240h | 171 | 139 | +3.12% | -12.50% | [-4.50%, +11.06%] |

calendar exit은 목표 시점보다 **이전**의 마지막 관측을 쓰지 않고, 목표 시점부터
3시간 안의 첫 관측이 있을 때만 계산했다. barrier가 목표 전에 닿으면 그 최초
관측을 유지했다. 이 정정으로 이전 계산의 24h `n=538`, CI `[+0.12%, +2.97%]`는
무효가 됐다. 표의 모든 CI가 0을 포함하고 horizon별 maturity/coverage 표본도 달라
직접 순위표로 쓸 수 없다. TP/SL을 끄고 24h target 뒤 첫 mark만 보는 별도 replay도
평균 +2.22%, CI **[-4.03%, +10.36%]**로 0을 포함한다.

### 7-2. execution selection과 actual 결과

현행 120h barrier selection screen:

| selection | n | mean markout | condition-cluster 95% CI |
|---|---:|---:|---:|
| `UNFILLED` | 38 | **+21.63%** | **[+6.55%, +38.73%]** |
| recorded `COMPLETED` | 119 | -3.17% | [-9.45%, +4.05%] |
| clean confirmed round-trip path | 37 | -1.43% | [-16.13%, +14.62%] |
| actual confirmed `max_holding` | 59 | **-4.70% gross ROI** | **event CI [-7.61%, -1.75%]** |

좋아 보이는 markout이 실제 체결되지 않은 신호에 집중돼 있다. 모든 signal을
`trades.buy_price`에 체결시키는 가정은 queue·spread·시장 소멸이 중요한 이 데이터에서
낙관적이다.

### 7-3. counterfactual 한계

- 20일 rolling-low entry 자체를 replay하지 못한다.
- submitted order가 있는 signal만 포함해 failed/capped/collateral-break 이후 후보가 빠진다.
- `trades.buy_price` 체결을 가정하고 spread, slippage, fee, queue position을 무시한다.
- forward price는 Gamma outcome price라 CLOB entry와 가격 공간이 다르다.
- target 이후 snapshot이 없는 신호를 제외해 informative censoring이 생긴다.
- `compact-v1` 12h minimum rollup은 barrier 고점을 보존하지 않는다.
- condition cluster는 same-event correlation을 완전히 제거하지 않는다.
- horizon 5개와 여러 filter를 같은 표본에서 본 post-hoc multiple testing이다.
- account deployment epoch와 flow-adjusted NAV가 없어 DB 결과를 계정 수익률로 확장할 수 없다.

### 7-4. 허용되는 후속 연구

현재 screen에서 특정 horizon을 고르면 같은 표본을 본 뒤 고르는 post-hoc
최적화가 된다. 따라서 24h를 포함해 어느 보유기간도 A/B 후보로 지명하지 않는다.
완전히 새로운 가설을 연구하려면 다음 조건을 먼저 충족한다.

1. strict audit의 CRITICAL/HIGH를 먼저 0으로 만든다.
2. 최소 20일 이상 uncompacted entry-time history 또는 재현 가능한 backfill을 보존한다.
3. 현재 grid와 독립적인 unseen 기간·보유기간·효과 기준을 결과 전에 사전 등록한다.
4. 별도 simulation job/DB를 쓰며 real order를 제출하지 않는다.
5. 비교 arm을 둔다면 같은 신호를 동시에 배정하고 event 단위 paired 결과를 낸다.
6. bid/ask, spread, slippage와 fee sensitivity를 포함한 net proxy를 쓴다.
7. 최소 event 수, 승격·중단 기준을 결과를 보기 전에 고정한다.

이는 현재 live를 계속할 허가도, 24h를 우선 검정하라는 권고도 아니다. 새 가설이
simulation에서 통과하더라도 별도의 promotion gate 없이는 live로 전환하지 않는다.

## 8. 최종 판정 근거

1. 대사된 120h `max_holding` 부분집합이 -4.70%이고 event CI도 0 아래다.
2. 전체 +1.65%는 단일 6-market event에 의존하며 그 event를 빼면 -4.91%다.
3. 완료 거래의 실제 fill coverage가 35.1%이고 fee 결손 때문에 net은 unknown이다.
4. `QUARANTINED` 231건과 stale reconciliation 237건은 live validation을 방해한다.
5. cap이 69.2% cycle에서 상시 발동했지만 ranking이 없어 portfolio 구성이 비결정적이다.
6. rolling-low entry replay가 불가능해 어느 horizon markout도 entry alpha를 검증하지 못한다.
7. apparent positive markout이 `UNFILLED` selection에 집중돼 actual fills로 확인되지 않는다.

이 수치만으로 전체 신호의 음의 alpha가 확정된다는 뜻은 아니다. 낮은 fill coverage,
fee 결손, 대규모 quarantine/stale reconciliation과 cap 오염 때문에 안전하고 식별 가능한
live 검증을 계속할 수 없다는 운영·evidence 근거가 CLOSE의 주근거다. 현 데이터에서는
24h를 포함한 특정 보유기간도 후속 실험군으로 선택하지 않는다.

## 9. 운영 후속 조치 — 전략 종료 완료

전략의 신규 진입과 운영 종료는 2026-07-30 운영자 확인으로 완료됐다. 아래 명령과
게이트는 당시 종료 절차를 재현하거나 잔여 evidence를 감사할 때만 사용한다.

### 즉시: 신규 진입 동결

Jenkins `polybot-eagle`에 다음 값을 넣고 첫 run의
`lifecycle_mode=close_only`, `buy_candidates=0`, `bought=0`을 확인한다.

```bash
# 전환 직전 live evidence를 online backup으로 고정
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"

export POLYBOT_LIFECYCLE_MODE=close_only
```

`close_only`는 기존 GTC BUY를 자동 취소하지 않는다. live host의 credential-bound
shell에서 먼저 dry-run한 뒤 BUY만 취소한다.

```bash
uv run tools/wind_down.py status
uv run tools/wind_down.py cancel
uv run tools/wind_down.py cancel --side BUY --yes
```

### 대사와 자연 청산

1. `HOLDING` 117건, `QUARANTINED` 231건, `UNFILLED` 40건을 wallet/CLOB 기준으로
   분류한다.
2. stale reconciliation 237건과 uncertain intent 2건을 거래소 evidence로 확정한다.
3. 요청 share나 `trades.realized_pnl`로 exposure·손익을 계산하지 않는다.
4. 기본 max holding 120h에 실행·대사 buffer를 더한 grace 동안 자연 청산을 유지한다.
5. fill overflow 7건, closed quantity mismatch 10건, legacy share mismatch 79건을
   correction evidence와 함께 분리한다.
6. cap을 올리거나 24h를 live env에 넣지 않는다.

intent 해제는 열린 주문과 대조하는 read-only 실행부터 한다. `<LIVE_DB>`는
`daily-rsync` evidence copy가 아니라 Jenkins live DB다.

```bash
uv run --script tools/resolve_stuck_intents.py \
  --db "<LIVE_DB>" --strategy golden-nectarine --side ALL

uv run tools/reconcile_positions.py \
  --db "<LIVE_DB>" --funder "$POLYMARKET_FUNDER_ADDRESS"
```

### 종료 게이트

다음을 모두 만족한 뒤에만 `archive_only` 또는 Jenkins disable로 넘어간다.

- Data API live position 0 또는 redeem/dust/no-book로 명시 분류
- CLOB open BUY/SELL 0
- pending/unknown intent와 reconciliation error 0
- `HOLDING`/`QUARANTINED`가 wallet/order/fill evidence와 대사됨
- 마지막 `close_only` run 성공
- online backup, SHA manifest, 로그 고정

Honeydew와 Nectarine은 중앙 market archive 역할도 한다. 두 job을 동시에 끄기 전에
다른 collector가 같은 cursor-complete sweep, catalog와 cadence 계약을 충족하는지 strict
audit로 증명해야 한다. 그렇지 않으면 포지션 정리 뒤 최소 60일간 `archive_only` 책임을
유지한다. DB·로그·코드는 삭제하지 않는다.

## 10. 재현 명령

분석은 local synchronized copy를 read-only로 사용한다.

```bash
cd /Users/izowooi/git/t1

export WORK=/Users/izowooi/.Codex/_workspace/retro-2026-07-30
export DB="$PWD/daily-rsync/data/sources/macmini-m5/jobs/polybot-eagle/strategies/golden-nectarine/runtime/default/databases/latest/trades.db"

cd daily-rsync
uv run daily-rsync locate --job polybot-eagle --strategy golden-nectarine
uv run daily-rsync verify --job polybot-eagle --strategy golden-nectarine
cd ..

shasum -a 256 "$DB"
sqlite3 -readonly "$DB" "PRAGMA quick_check;"

set +e
uv run --project polybot-observability polybot-retro audit \
  --db "$DB" \
  --days 18 \
  --as-of 2026-07-29 \
  --output-dir "$WORK/audit-nectarine" \
  --strict
audit_status=$?
set -e
test "$audit_status" -eq 1
# 이 historical snapshot은 evidence gap 때문에 정확히 exit 1이어야 한다.

python "$WORK/analyze_markouts.py" \
  --database "$DB" \
  --strategy nectarine \
  --period-start 2026-07-12T00:00:00 \
  --period-end 2026-07-30T00:00:00 \
  --output "$WORK/nectarine-markouts-post-target.json"

python "$WORK/nectarine-analysis.py" "$DB" \
  > "$WORK/nectarine-analysis-output.json"
```

보존 artifact:

- `ARTIFACT_MANIFEST.{md,sha256}`
- `audit-nectarine/retro-audit.{md,json}`
- `nectarine-markouts-post-target.json`
- `nectarine-report.md`
- `nectarine-analysis.py`
- `analyze_markouts.py`

관련 계약: [Evidence Contract](EVIDENCE_CONTRACT.md),
[전략별 회고 가이드](golden-nectarine.md),
[퇴역 플레이북](../strategy-wind-down-playbook.md).

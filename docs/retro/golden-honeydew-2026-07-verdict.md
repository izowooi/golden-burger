# golden-honeydew 판정 — 2026-07-30

- 전략: Night Watch
- production deployment: `macmini-m5 / polybot-bear / golden-honeydew / default / live`
- review window: **[2026-07-13 00:00:00Z, 2026-07-30 00:00:00Z)**
- 판정: **CLOSE**
- 판정 신뢰도: **중상**(중단 방향), **낮음~중간**(정확한 net 손익과 현재 wallet 노출)
- 성과 원장: BUY·SELL 양쪽의 수량이 일치하는
  `order_fills.status='CONFIRMED'`만 사용

---

## 0. 결론

**현행 live 전략을 종료한다. 실자금 A/B도 하지 않는다.**

완전히 대사된 실제 왕복 체결 316건의 결과는 fee 차감 전부터
**-$55.917340, -3.54197%**였다. 양수/0/음수 거래는 96/15/205건이고,
중앙 거래 수익률은 -6.43%다. 편차, 진입가, 평일 quiet window와 주말 어느 주요
slice도 gross 기준 양수가 아니었다.

더 큰 deviation만 고르면 회생하는 것처럼 보이는 markout cell이 있다. 그러나 그
markout은 모든 submitted signal이 `trades.buy_price`에 체결됐다고 가정한다. 실제
confirmed fill만 같은 threshold로 자르면 부호가 반대로 음수다. 특히
`|deviation| >= 10pp`는 optimistic markout **+7.41%**와 달리 actual fill
**-4.30%**였다. `UNFILLED` 신호의 markout이 유난히 좋았다는 사실도 이 괴리를
설명한다. 실행되지 않은 승자를 실현 가능한 alpha로 취급할 수 없다.

동시에 strict evidence gate는 **4 CRITICAL / 6 HIGH / 2 MEDIUM**으로 실패했다.
SELL submission 17,549회 중 17,173회가 실패했고, 한 token에는 실패가 1,351회
반복됐다. 이 상태에서 live A/B는 전략을 검증하기보다 실행·대사 위험을 더 누적시킨다.

따라서 이번 판정은 다음과 같다.

| 항목 | 판정 |
|---|---|
| 현행 live | **CLOSE** |
| live parameter 변경 | **금지** |
| 실자금 A/B | **금지** |
| 증액·promotion | **금지** |
| 후속 연구 | evidence 복구 후 offline/simulation-only, 사전 등록 OOS만 허용 |

## 1. 원자료 고정: sync와 verify

경로는 job 이름을 추측하지 않고 `daily-rsync locate`로 찾았다. `default`는 Jenkins
job명이 아니라 runtime job이다.

| 항목 | 값 |
|---|---|
| local DB | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-honeydew/runtime/default/databases/latest/trades.db` |
| remote source | `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-honeydew/data/default/trades.db` |
| 크기 | 958,570,496 bytes |
| synchronized artifact SHA-256 | `f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df` |
| audit consistent-snapshot SHA-256 | `f9add252196a6619c54b98c095de48766d9b85fa049d993fe1f299856d57e6ae` |
| manifest `quick_check` | `ok` |
| sync | run `2fe82d55ad8a43d0a3ffa87583d929f8`, plan `e0ecd14369895db0`, `SUCCESS` |
| sync 구간 | 2026-07-30 12:12:06Z ~ 12:14:27Z |
| sync 결과 | transferred 3,816 / skipped 0 / failed 0 |
| 재검증 | checked 3,816 / failed 0 / `SUCCESS` |

두 SHA는 서로 다른 대상을 뜻한다. 첫 번째는 manifest와 현재 local file bytes가
일치하는 전송 artifact checksum이다. 두 번째는 strict audit가 같은 DB를 SQLite online
backup으로 다시 고정한 consistent snapshot checksum이다. 둘을 같은 파일의 불일치로
해석하지 않는다.

함께 동기화된 자료는 Jenkins console 3,796개, bot log 18개, 월간 CSV 1개다.
DB와 Jenkins의 마지막 complete cycle은 2026-07-28 15:42Z 부근이고
`market_snapshots`의 마지막 시각은 2026-07-28 15:41:33Z다. 따라서 review window는
07-30 00:00Z까지로 고정했지만, **07-28 마지막 관측 이후의 거래소·wallet 상태는 이
bundle로 주장하지 않는다.**

## 2. strict evidence gate: FAIL

최신 `audit-honeydew/` bundle의 결과다.

### CRITICAL

| issue | 결과 |
|---|---:|
| `completed_trade_fill_gap` | 완료 375건 중 양면 confirmed fill 완전 대사 316건, **84.3%** |
| `fill_quantity_overflow` | confirmed fill 합계가 `latest_size_matched`를 초과한 주문 4건 |
| `closed_trade_fill_quantity_mismatch` | 완료 trade의 실제 BUY/SELL 수량 불일치 24건 |
| `uncertain_submission_outcome` | POST 결과 미확정 intent 3건 |

### HIGH / MEDIUM

| severity | issue | 결과 |
|---|---|---:|
| HIGH | `stale_order_reconciliation` | 1시간 초과 미완료 대사 35건 |
| HIGH | `fill_fee_missing` | confirmed fill의 fee amount 결손 **77.3%** |
| HIGH | `failed_runs` | 31건 |
| HIGH | `run_schedule_gap` | SUCCESS 최대 공백 32.32시간 |
| HIGH | `archive_window_short` | 요청 window 92.1%, compact cadence 45.2% |
| HIGH | `market_sweep_attestation_missing` | 완전한 membership denominator 계약 미충족 |
| MEDIUM | `legacy_trade_share_mismatch` | reconciled fill과 legacy share 불일치 312건 |
| MEDIUM | `logs_missing` | audit의 DB-relative 자동 탐색 기준 |

`logs_missing`은 실제 로그 부재가 아니다. 로그는 `daily-rsync`의 별도 canonical tree에서
발견해 교차 점검했다. 다만 audit가 자동 결합하지 못했으므로 finding은 삭제하지 않는다.

이 gate에서는 파라미터 교정·증액·promotion을 할 수 없다. 또한 legacy
`trades.realized_pnl=-$46.21`은 요청 가격×요청 수량 기반
`ORDER_ASSUMPTION` 값이므로 아래 성과에 사용하지 않았다.

## 3. config와 code cohort

audit window에는 resolved config가 2개다.

| config hash | run 구간 | 차이 |
|---|---|---|
| `5b13d7905a0d1fd42de635637b51105adb634e58f17c32fbf90cc553cb6f856a` | 07-13 00:01 ~ 07-14 10:57Z | 최초 기록 |
| `30be03ee6262cee4d16595da53ec23de76b86b2a16992a0e1576afc0fe439852` | 07-14 11:02 ~ 07-28 15:40Z | `lifecycle_mode: active` 명시 추가 |

매매 수치는 동일하다.

- $5/position, `max_positions=-1`
- 24h median 대비 deviation 5pp 이상
- volume spike ratio 1.5 미만
- token probability 0.30~0.90
- 평일 06:00~13:00 UTC와 주말
- TP +6%, SL -6%, max holding 24h

audit window에 Git commit이 33개이므로 단일 code cohort는 아니다. `trades`에는
`config_hash`나 `run_id`가 직접 연결되지 않아 모든 거래를 code cohort별로 완전히 분해할
수도 없다. 이 혼합은 정확한 효과 크기의 신뢰도를 낮추며, 여러 cohort aggregate를 근거로
새 수치를 고르지 않는 이유다.

## 4. actual confirmed-fill 성과

포함 기준은 live successful submission, confirmed fill, 완료된 reconciliation,
fill 합계와 `latest_size_matched` 일치, 유효한 fill domain, BUY·SELL 수량 일치다.

### 전체

| 지표 | 결과 |
|---|---:|
| 완전 대사 round trips | **316 / 375** |
| event clusters | 135 |
| confirmed BUY notional | **$1,578.707710** |
| fee 차감 전 gross P&L | **-$55.917340** |
| gross ROI | **-3.54197%** |
| win / flat / loss | 96 / 15 / 205 |
| win rate | 30.38% |
| median trade return | **-6.4288%** |
| fee-complete trades | **45 / 316** |

평균 양수 거래는 +8.93%, 평균 음수 거래는 -9.64%였다. flat을 제외한 손익분기
win rate는 약 51.9%인데 실제 win 비율은 31.9%였다.

### exit reason

| exit | n | gross P&L | gross ROI | win rate |
|---|---:|---:|---:|---:|
| `stop_loss` | 194 | **-$97.07819** | **-10.023%** | 0% |
| `take_profit` | 86 | +$41.38973 | +9.621% | 100% |
| `max_holding` | 36 | -$0.22888 | -0.127% | 27.78% |

설정된 stop은 -6%지만 실제 confirmed fill의 평균 손실 폭은 -10.02%였다. 요청 가격을
쓰는 `realized_pnl`은 이 실행 차이를 보여주지 못한다.

주요 slice도 모두 gross 음수였다.

- deviation 5~7pp: -2.49%, 7~10pp: -5.75%, 10pp 이상: -4.64%
- weekday quiet: -3.74%, weekend: -2.76%
- entry 0.30~0.45: -4.08%, 0.45~0.60: -5.24%,
  0.60~0.75: -3.03%, 0.75~0.90: -1.60%

confirmed fill 930행의 liquidity role은 모두 있지만 fee가 완전한 fill은 211행뿐이고,
fee-complete round trip은 45건뿐이다. **-$55.92는 gross 손실이며 net은 unknown이다.**
fee 결손을 0으로 채워 net 손익을 만들지 않는다.

## 5. 실행 장애: SELL retry loop

### 상태와 order flow

| 항목 | 결과 |
|---|---:|
| trade rows | 596 |
| `COMPLETED` / `UNFILLED` | 375 / **179** |
| `HOLDING` / `EXPIRED` | 32 / 10 |
| BUY submissions | 656회, 성공 596, 실패 60 |
| SELL submissions | **17,549회, 성공 376, 실패 17,173** |
| confirmed BUY fills | 503행 / 402 orders / $1,995.231255 |
| confirmed SELL fills | 427행 / 347 orders / $1,660.577751 |

SELL 실패 17,173회는 독립 position 수가 아니라 반복 attempt 수다. 그래도 같은
token/side를 매 cycle 다시 제출하는 운영 결함은 분명하다.

- 영향 token 121개
- token당 평균 실패 141.9회
- 단일 token 최대 실패 1,351회
- 잔고 부족/불완전 해석 14,030회
- zero balance 3,102회

Jenkins console 3,796개와 bot log 18개를 전수 교차한 결과도 같은 결론이다.
application run은 성공 3,762 / 실패 31이었지만, 7월 18일 25회 연속 실패의 직접 원인은
429가 아니라 pending CLOB reconciliation fail-closed였다. zero-fill BUY의 취소를
증명하지 못한 경고는 14개 trade에서 2,923회, 주문 취소 실패는 2,917회 반복됐다.
마지막 완료 cycle은 7월 28일 15:42:05Z이며 동기화 scan cutoff까지 **44시간 44분**
후속 실행이 없다. 이 공백이 의도적 중단인지 Jenkins 장애인지는 운영 설정에서 별도로
확인해야 하며, 이 bundle로 현재 정상 가동을 주장할 수 없다.

snapshot 시점 `HOLDING` 32건 중 confirmed BUY fill이 있는 것은 18건($77.682261)뿐이다.
나머지 14건은 status만으로 실보유를 입증할 수 없다. 반대로 fill이 있는 18건은
wallet/CLOB 대사 전까지 노출이 없다고 가정해서도 안 된다. `EXPIRED` 10건은 모두
`resolved_unredeemed`이며 confirmed BUY notional은 $49.660156이다.

## 6. markout이 보인 “회생”은 actual fill과 모순된다

### 6-1. 동일 threshold 비교

아래 optimistic markout은 현행 TP/SL ±6%, 24h barrier를 sparse archive에서 재생한
균등가중 평균 수익률이다. actual 열은 같은 deviation 하한을 통과한 완전 대사
confirmed round trip의 균등가중 평균 수익률이다. CI는 condition ID cluster bootstrap이며
같은 event의 여러 condition을 완전히 묶은 CI가 아니다.

| `abs(deviation)` 하한 | optimistic markout | n | actual confirmed fill | n |
|---:|---:|---:|---:|---:|
| 5pp | -0.64% `[-3.77%, +2.68%]` | 429 | **-3.54% `[-4.75%, -2.21%]`** | 316 |
| 7pp | +1.02% `[-3.71%, +5.75%]` | 190 | **-5.05% `[-6.79%, -3.36%]`** | 126 |
| 10pp | **+7.41% `[+0.99%, +13.75%]`** | 97 | **-4.30% `[-7.77%, -1.31%]`** | 56 |
| 15pp | +10.76% `[-4.07%, +27.05%]` | 35 | -4.44% `[-11.65%, +1.70%]` | 22 |
| 20pp | +16.83% `[-19.92%, +49.72%]` | 14 | -11.48% `[-23.38%, +0.27%]` | 9 |

10pp cell만 보면 “deviation을 높여 live A/B”가 그럴듯해 보인다. 그러나 실제 체결은
반대 부호이고 actual CI도 0 아래다. threshold를 더 높여도 actual 결과가 좋아지는
단조 관계가 없다.

selection screen도 같은 경고를 준다.

- 현행 barrier의 `UNFILLED` 117건 markout: 평균 **+6.72%**, win rate 80.3%
- clean confirmed round-trip path 250건 markout: 평균 **-2.95%**
- actual strict 316건: gross ROI **-3.54%**

즉 optimistic return이 실제로 체결되지 않은 신호에 집중돼 있다. queue position,
spread와 시장 소멸 때문에 체결 여부가 informative할 수 있으므로, 이를 “놓친 수익”이나
live alpha로 환산하면 안 된다.

### 6-2. 더 실행 가능한 hot-window replay

compaction되지 않은 마지막 24시간만 사용하고 BUY ask→SELL bid proxy로 계산한 별도
replay도 구제 증거를 주지 않았다.

| horizon | executable proxy mean | event-cluster 95% CI | n |
|---:|---:|---:|---:|
| 1h | -7.71% | [-17.60%, +0.73%] | 23 |
| 3h | **-2.72%** | **[-4.43%, -1.55%]** | 13 |
| 6h | **-3.82%** | **[-7.57%, -0.81%]** | 13 |

이 replay도 한 평일뿐이고 actual signal overlap은 62.5%라 단독 폐쇄 근거는 아니다.
다만 confirmed live fill과 같은 방향이며 optimistic screen의 반증이다.

### 6-3. counterfactual 한계

- 모든 submitted signal이 recorded `trades.buy_price`에 체결됐다고 가정한다.
- second token은 저장된 YES의 `1-YES`로 근사한다.
- spread, fee, latency, queue position, market disappearance를 무시한다.
- `compact-v1`의 12h cold rollup 때문에 6% TP/SL 선후관계 재생이 불완전하다.
- target 이후 3시간 이내 관측이 있는 신호만 남아 informative censoring이 생긴다.
- single-axis, in-sample, multiple-testing 미보정이다.
- condition cluster는 same-event correlation을 완전히 제거하지 않는다.
- 기존 holdings, 24h cooldown, portfolio state를 완전히 재현하지 않는다.

따라서 apparent optimistic rescue를 live parameter로 승격할 수 없다.
**Honeydew의 live A/B는 금지한다.**

## 7. 최종 판정 근거

1. 실제 양면 confirmed fill 316건이 fee 전부터 -3.54%다.
2. 주요 실제 체결 slice가 모두 음수이고 더 큰 deviation도 actual fill을 구제하지 못했다.
3. optimistic cell은 체결 가정과 `UNFILLED` selection에 민감하며 actual 결과와 부호가
   반대다.
4. SELL retry loop, 35 stale reconciliation, 3 uncertain intent 때문에 신규 노출을
   안전하게 검증할 수 없다.
5. fee coverage가 불완전하므로 알려진 gross 손실보다 net이 좋아졌다고 주장할 근거도 없다.
6. strict gate가 FAIL이므로 Evidence Contract상 tuning·promotion 자체가 금지된다.

판정은 **CLOSE**다. “정확한 손실 규모를 모두 확정할 수 없다”는 evidence 한계가
“계속 운용할 근거가 있다”로 뒤집히지는 않는다.

## 8. 운영 후속 조치

### 즉시: 신규 진입 동결

Jenkins `polybot-bear`에 다음 값을 넣고 첫 run의
`lifecycle_mode=close_only`, `buy_candidates=0`, `bought=0`을 확인한다.

```bash
# 전환 직전 live evidence를 online backup으로 고정
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"

export POLYBOT_LIFECYCLE_MODE=close_only
```

`close_only`는 기존 GTC BUY를 자동 취소하지 않는다. live host의 credential-bound
shell에서 먼저 dry-run하고 BUY만 취소한다. 아래 `<LIVE_DB>`는 synchronized evidence
copy가 아니라 Jenkins의 live DB다.

```bash
uv run tools/wind_down.py status
uv run tools/wind_down.py cancel
uv run tools/wind_down.py cancel --side BUY --yes
```

### 대사와 자연 청산

1. wallet/CLOB 기준으로 `HOLDING` 32건을 재분류한다.
2. stale reconciliation 35건과 uncertain intent 3건을 거래소 evidence로 확정한다.
3. SELL loop를 무작정 재시도하지 말고 실제 가용 잔고와 open order를 먼저 대사한다.
4. `EXPIRED` 10건은 resolution/redeemable/실제 redemption을 분리해 회수한다.
5. 기본 max holding 24h에 실행·대사 buffer를 더한 grace 동안 자연 청산을 유지한다.
6. fill overflow 4건, closed quantity mismatch 24건, legacy share mismatch 312건을
   correction evidence와 함께 분리한다.

intent 해제는 열린 주문과 대조하는 기본 read-only 실행부터 한다. 출력이 요구한 확인
문구를 검토하기 전에는 `--execute`를 붙이지 않는다.

```bash
uv run --script tools/resolve_stuck_intents.py \
  --db "<LIVE_DB>" --strategy golden-honeydew --side ALL

uv run tools/reconcile_positions.py \
  --db "<LIVE_DB>" --funder "$POLYMARKET_FUNDER_ADDRESS"
```

### 종료 게이트

다음을 모두 만족한 뒤에만 `archive_only` 또는 Jenkins disable로 넘어간다.

- Data API live position 0 또는 redeem/dust/no-book로 명시 분류
- CLOB open BUY/SELL 0
- pending/unknown intent와 reconciliation error 0
- DB `HOLDING`이 wallet/order/fill evidence와 대사됨
- 마지막 `close_only` run 성공
- online backup, SHA manifest, 로그 고정

Honeydew와 Nectarine은 중앙 market archive 역할도 한다. 두 job을 동시에 끄기 전에
다른 collector가 같은 cursor-complete sweep, catalog와 cadence 계약을 충족하는지 strict
audit로 증명해야 한다. 그렇지 않으면 포지션 정리 뒤 최소 60일간 `archive_only` 책임을
유지한다. DB·로그·코드는 삭제하지 않는다.

## 9. 재현 명령

분석은 local synchronized copy를 read-only로 사용한다.

```bash
cd /Users/izowooi/git/t1

export WORK=/Users/izowooi/.Codex/_workspace/retro-2026-07-30
export DB="$PWD/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-honeydew/runtime/default/databases/latest/trades.db"

cd daily-rsync
uv run daily-rsync locate --job polybot-bear --strategy golden-honeydew
uv run daily-rsync verify --job polybot-bear --strategy golden-honeydew
cd ..

shasum -a 256 "$DB"
sqlite3 -readonly "$DB" "PRAGMA quick_check;"

set +e
uv run --project polybot-observability polybot-retro audit \
  --db "$DB" \
  --days 17 \
  --as-of 2026-07-29 \
  --output-dir "$WORK/audit-honeydew" \
  --strict
audit_status=$?
set -e
test "$audit_status" -eq 1
# 이 historical snapshot은 evidence gap 때문에 정확히 exit 1이어야 한다.

python "$WORK/analyze_markouts.py" \
  --database "$DB" \
  --strategy honeydew \
  --period-start 2026-07-13T00:00:00 \
  --period-end 2026-07-30T00:00:00 \
  --output "$WORK/honeydew-markouts-post-target.json"

python "$WORK/honeydew_backtest.py" "$DB" \
  > "$WORK/honeydew-backtest-output.json"
```

보존 artifact:

- `ARTIFACT_MANIFEST.{md,sha256}`
- `audit-honeydew/retro-audit.{md,json}`
- `honeydew-markouts-post-target.json`
- `honeydew-report.md`
- `honeydew_backtest.py`
- `analyze_markouts.py`

관련 계약: [Evidence Contract](EVIDENCE_CONTRACT.md),
[전략별 회고 가이드](golden-honeydew.md),
[퇴역 플레이북](../strategy-wind-down-playbook.md).

# golden-melon 회고 가이드 — Resolution Sprint

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽는다.
> UTC half-open range `[REVIEW_START, REVIEW_END_EXCLUSIVE)`를 고정하고,
> `polybot-retro audit --strict`의 `CRITICAL`/`HIGH` issue가 0이 되기 전에는
> 결론·tuning·증액을 만들지 않는다.

전략은 **Resolution Sprint**다. 해결까지 `(0h, 72h]` 남은 표준 이진 YES가 처음으로
`[0.85, 0.93]` 밴드에 상향 진입하고 24시간 거래량 gate를 통과하면 매수하고,
`0.97` 목표 / `0.78` 절대 손절로 관리한다. trailing stop과 time exit은 없다.

**A/B/C 처치축은 `min_volume_24h` 하나다.** 진입 밴드·시계·배리어·금액은 전 팔 동일하다.

## 0. 이 전략이 왜 이렇게 생겼는가 — golden-cherry 진단

melon은 `golden-cherry`(Resolution Momentum)의 재설계다. cherry의 실측 진단이 설계를
결정했으므로 회고 때 같은 지표를 본다.

| cherry 실측 (671건, 2026-03-30~07-22) | 값 |
|---|---|
| 건당 수익률 (요청가 기준) | **−3.1%** — 전 금액대, 전 월에서 음수 |
| 승률 | 31~36% (TP+10/SL−8의 손익분기 44.4%) |
| `stop_loss` 실현폭 | 설정 −8%인데 **−24.78%** (52건) |
| 그 중 **보유 30분 이내** | **31건(60%), 평균 −24.6%, 최악 −99.3%** |
| `trailing_stop` | 122건 −3.94% |
| CONFIRMED BUY 비율 | $200 미만 73% → $1,500 이상 **18%** |
| 주문/유동성 중앙값 | 최대 구간 **21.7%** |
| 종결률 | 247/671 = 37% (HOLDING 246, QUARANTINED 76) |

**손실의 정체는 진입이 아니라 청산이었다.** `stop_loss` 52건이 −1,289를 만들어
`take_profit` 70건의 +1,006을 통째로 지웠다. 그리고 그 60%가 **매수 후 30분 안에**
발생했다 — 5분 폴링으로는 잡을 수 없는 가격 점프다.

→ 그래서 melon은 (a) trailing stop을 없애고, (b) 절대가 손절만 두고,
(c) **꼬리를 파라미터가 아니라 금액과 포지션 수로 통제**한다. $3,000짜리 −99%는
−$2,970이지만 $5짜리 −99%는 −$4.95다.

> **한계**: 위 진단은 `golden-cherry/data/default/trades.db`(작업트리 사본, mtime
> 2026-07-28) 하나에 근거한다. daily-rsync catalog에 없어 `verify`·`local_sha256`
> 증빙이 없고, catalog가 가리키는 `polybot-yellow`·`polybot-orange`의 DB는 동기화되지
> 않았다. 운영자가 기억하는 "월간 10%" 구간은 이 사본에 없다(4월 −0.46%, 5월 −9.93%,
> 3월은 1건). **melon의 설계 근거로는 쓰되, cherry의 최종 판정으로 인용하지 않는다.**

## 1. 30일 판정 요약

| endpoint | 근거 | 사전 등록 규칙 |
|---|---|---|
| 건당 수익률 | 엄격 왕복(양쪽 CONFIRMED + 수량 일치) | 손익분기 **58.0%** 승률 대비 |
| 승률 | 같은 모집단 | 밴드 중앙 0.89 기준 martingale 기대치 57.9% |
| CONFIRMED BUY n | exact order ID | 팔당 **30건 미만이면 판정 불가** |
| 30분 이내 손절 비중 | `stop_loss` × 보유시간 | cherry 60% 대비 감소했는가 |
| 최악 단건 손실 | 실체결 기준 | $5 × −100% = −$5로 유계인가 |
| 주문/유동성 | `buy_amount / liquidity_at_buy` | 0.1% 상한이 지켜졌는가 |
| CONFIRMED BUY 비율 | accepted 대비 | cherry의 금액별 붕괴(73→18%)가 없는가 |

**배리어 산술은 사전 등록됐다.** 진입 밴드 중앙 0.89에서 목표 0.97 = +9.0%,
손절 0.78 = −12.4% → 손익분기 승률 **58.0%**. 캘리브레이션이 맞다면 martingale
도달확률이 **57.9%** 이므로 **배리어 배치로는 edge가 생기지 않는다.** edge가 있다면
거래량 gate의 진입 선별에서만 온다. 이것이 A/B/C가 검정하는 것이다.

## 2. 복붙용 회고 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md와 docs/retro/golden-melon.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END_EXCLUSIVE=<YYYY-MM-DD UTC>

1) UTC half-open range와 일치하는 REVIEW_DAYS와 --as-of 포함 종료일을 계산한다.
2) daily-rsync catalog에서 세 팔 DB를 발견하고 verify한 절대 경로만 쓴다.
3) 세 DB를 반복 지정해 polybot-retro audit --strict를 실행한다.
4) CRITICAL/HIGH 또는 evidence gap이 있으면 비교·tuning·증액을 중단한다.
5) config_hash × git_commit × mode × job_name cohort를 분리한다.
6) 세 팔이 $5, 진입 [0.85,0.93], 72h, 0.97/0.78, 동일 commit/cadence인지 검증하고
   min_volume_24h 만 다른지 resolved config로 확인한다.
7) exact order ID의 CONFIRMED fill만 성과에 사용한다. trades.realized_pnl 금지.
8) stop_loss 건의 보유시간 분포를 반드시 보고한다 (cherry는 60%가 30분 이내였다).
9) event_id × 교차 시각 창으로 pair/cluster하고 같은 event를 독립 n으로 세지 않는다.
10) 승률·건당 수익률을 손익분기 58.0%와 비교해 보고한다.
11) 거절된 후보(skipped_markets)의 volume 분포도 함께 뽑아 오프라인 반사실을 만든다.
```

## 3. 기간과 evidence discovery

```bash
export REVIEW_START=2026-08-05
export REVIEW_END_EXCLUSIVE=2026-09-04
export REVIEW_AS_OF=2026-09-03
export REVIEW_DAYS=30
export RETRO_OUTPUT="$HOME/polybot-retro/melon-$REVIEW_END_EXCLUSIVE"
```

```bash
cd daily-rsync
uv run daily-rsync locate --strategy golden-melon
uv run daily-rsync verify --job <jenkins-job> --strategy golden-melon
cd ..

uv run --project polybot-observability polybot-retro audit \
  --db "$MELON_LOW_DB" --db "$MELON_MID_DB" --db "$MELON_HIGH_DB" \
  --days "$REVIEW_DAYS" --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT" --strict
```

`verify`를 통과한 catalog 절대 경로만 쓴다. 디렉터리명으로 성공을 추정하지 않는다.

## 4. 고정값 검증

| 항목 | A (low) | B (mid) | C (high) |
|---|---:|---:|---:|
| `min_volume_24h` | **$20,000** | **$50,000** | **$150,000** |
| 진입 밴드 | 0.85~0.93 | 동일 | 동일 |
| 시계 | (0h, 72h] | 동일 | 동일 |
| 목표 / 손절 | 0.97 / 0.78 | 동일 | 동일 |
| 주문액 | $5 | $5 | $5 |
| `min_liquidity` | $20,000 | 동일 | 동일 |
| `max_positions` | 20 | 동일 | 동일 |
| `execution_mode` | nearest | 동일 | 동일 |
| cadence | `H/5 * * * *` | 동일 | 동일 |

표와 다른 config hash, commit, mode, job, amount는 별도 cohort로 분리한다.

## 5. Evidence gate

반드시 보고할 coverage:

- run SUCCESS/FAILED/RUNNING, schedule gap, unknown Git commit
- cursor-complete market sweep과 membership digest
- current/prior snapshot ID join, `0 < gap <= 15분`, 이전 0.85 이상 관측 여부
- BUY/SELL submission → status → CONFIRMED fill coverage
- partial fill, uncertain intent, stale reconciliation, terminal zero-fill
- **`stop_loss` 건의 보유시간 분포** (cherry 재현 여부)
- 주문/유동성 실측 비율 분포
- resolution evidence coverage와 redeem 미수집 한계

하나라도 `CRITICAL`/`HIGH`면 아래 집계는 진단용으로만 실행하고 결론을 만들지 않는다.

## 6. 1차 성과 endpoint

```text
arm = config_hash × git_commit × mode × job_name
strict_roundtrip = BUY/SELL 양쪽 CONFIRMED + 두 size 상대오차 <= 0.5%
entry_vwap = exact confirmed BUY fills only
exit_vwap  = exact confirmed SELL fills only
per_trade_return = exit_vwap / entry_vwap - 1
```

팔별로 n, 평균 수익률, 승률, 손익분기(58.0%) 대비 차이, 최악 단건, 30분내 손절 비중을 낸다.

## 7. 2차 관측값

- 미청산(HOLDING)·해결(EXPIRED)의 mark-to-market — **생존편향 점검용으로 반드시 함께 본다.**
  elderberry에서 실현분만 보면 −0.28%였던 것이 EXPIRED 편입 시 달라졌다.
- 거절된 후보의 volume 분포 — **A/B에서 돌리지 않은 threshold의 오프라인 반사실**을 만든다.
- 스포츠 vs 비스포츠 (cherry에서는 차이가 없었다: −2.96% vs −3.27%)

## 8. 30일 결정

| 조건 | 결정 |
|---|---|
| 상시 경제손익 ≤ **−$20** | kill switch 확인, 신규 진입 중단, 전후 cohort 분리 |
| 팔당 CONFIRMED BUY < 30 | **INCONCLUSIVE** — 표본 부족. 연장하거나 새 cohort로 재시작 |
| 전 팔 승률 < 58.0% 이고 CI가 58%를 포함하지 않음 | **STOP** — cherry와 같은 결론 |
| 특정 volume 팔만 58% 초과, 순서가 단조 | **CONTINUE** — 그 팔로 좁혀 증액 검토 |
| 순서가 비단조 | **DIAGNOSE** — 거래량 gate는 선별 축이 아니다 |
| 30분내 손절 비중이 cherry(60%) 수준 | **재설계** — 진입 시점 자체를 바꿔야 한다 |

결론 형식:

```text
Decision: INCONCLUSIVE | STOP | CONTINUE | DIAGNOSE | REDESIGN
Evidence window [start, end):
Verified DB SHA-256:
Cohorts:
CONFIRMED BUY n by arm:
Per-trade return by arm (vs 58.0% breakeven):
Win rate by arm:
Worst single loss by arm:
<30min stop_loss share by arm:
Order/liquidity ratio distribution:
Unrealized mark-to-market (survivorship check):
Rejected-candidate volume distribution (offline counterfactual):
Kill-switch status:
Primary limitation:
Next review date:
```

## 9. 보존과 secret

DB와 로그는 commit하지 않는다. 실행 중 DB를 `cp`하지 말고 online backup과 SHA-256
manifest를 workspace 밖 durable storage에 보관한다. private key, funder 실값,
credential identifier를 보고서·로그·명령 history에 남기지 않는다.

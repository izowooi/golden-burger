# 031 — Golden Pomegranate 스포츠 고확률 진입 grid — 2026-08-19

작성일: 2026-08-19 KST

## 0. 결론

사용자 가설은 다음처럼 해석하고 검정했다.

> 스포츠 이진 시장에서 Gamma `endDate`까지 6시간 이내에 가격이 0.75~0.97의 각
> 1-cent threshold를 처음 상향 교차하면 매수한다. 이후 더 높은 목표가에서 팔거나
> one-hot resolution의 0/1까지 보유한다.

12일 DB는 시장 가격 경로의 **retrospective proxy 검정**에는 쓸 수 있지만 실제 체결 가능한
전략을 확정하기에는 부족하다. 사전에 고정한 최소 표본 gate를 만족하는 train 조합이 하나도
없어 primary winner는 `NONE`이다.

- 사용자 anchor `0.80→0.90`: 전체 point estimate는 fee-net `+8.01%`, fee+1¢ adverse
  `+5.92%`지만 train은 `-0.10%`, validation은 `+12.13%`로 불안정하고 전체 95% CI가
  `[-2.24%, +12.91%]`다.
- `0.85→0.95`: 양 기간 모두 손실이며 전체 fee+1¢ ROI `-15.95%`다. 기각한다.
- `0.90→resolution`: 양 기간 모두 손실이며 전체 fee+1¢ ROI `-4.18%`다. 기각한다.
- `0.95→resolution`: train `+3.74%`, validation `-0.24%`다. 한 번의 패배가 얇은 이익을
  지웠으므로 기각한다.
- 사후 안정성 screen의 최고값은 `0.94→resolution`이다. 확인된 60건이 60승이라 fee-net
  `+5.76%`, 1¢ adverse `+4.68%`였지만 검증 통과가 아니다. 85개 신호 중 25개가 평가되지
  않았고 그중 24개는 이미 endDate가 cutoff보다 6시간 이상 과거인데 resolution label이 없다.
  실제 CLOB 진입 ask도 85건 중 1건뿐이다.

`0.94→resolution`의 60/60 승률 Wilson 95% 하한은 `93.98%`이고 fee 포함 손익분기 승률은
`94.55%`다. 따라서 관측된 전승에도 보수적 하한 edge는 `-0.57pp`다. 검증되지 않은
resolution 누락 25건을 모두 패배로 두면 signal-equal ROI는 `-25.34%`다.

현재 가장 좋은 판단은 **실거래 또는 월 5% 달성 주장 금지**, `0.94→resolution`을 새
전향적 simulation의 A군으로만 사용하는 것이다.

## 1. Evidence provenance

- review range: `[2026-08-07T00:00:00Z, 2026-08-19T00:00:00Z)`
- timezone: UTC; 표시 해석은 KST 병기
- source/job/runtime: `macmini-m5 × golden-pomegranate × pomegranate-15m-v2`
- mode/contract: `sim × research-full-v1`
- remote DB root:
  `/Volumes/t7/jenkins/golden-pomegranate/golden-pomegranate/data/pomegranate-15m-v2`
- verified local DB root:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/golden-pomegranate/strategies/golden-pomegranate/runtime/pomegranate-15m-v2/databases/research/2026/08`
- latest successful sync: `3555ab97ae7644aaa66ddd067acdc3a0`, finished
  `2026-08-19T11:08:24.286658Z`
- latest requested source cutoff: `2026-08-19T00:03:02.840977Z`
- `daily-rsync verify`: 12 checked, 0 failed, missing/unavailable/conflict 0, archive coverage
  12/12 complete
- shard별 절대경로·SHA-256·source/sync cutoff는 선행
  [030 보고서](030-golden-pomegranate-health-and-strategy-discovery-2026-08-19.md#2-evidence-provenance)의
  동일 12개 immutable input 표를 따른다.
- protocol freeze:
  `golden-pomegranate/research/2026-08-19-sports-favorite-grid-preregistration.md`
- analyzer:
  `golden-pomegranate/scripts/evaluate_sports_favorite_grid.py`
- analyzer SHA-256:
  `6876f85171919259ccceb93d668a66a1cc6e9da5e27db3053bd4f3501fc6f51f`
- generated result SHA-256:
  `4060625c0ee4077f5875570efec91ab715b91ee10ecffa8552e1539549138ef8`

원자료는 앞선 underdog 연구에서 이미 열어봤다. 이번 규칙은 계산 전에 고정했지만 뒤 6일을
`untouched holdout`으로 부르지 않고 `temporal validation`으로만 표기한다.

## 2. 검정 계약

### 2.1 시장과 clock

- sports tag, active/open/orderbook/accepting-orders, 표준 2-outcome
- outcome probability 합 `[0.98,1.02]`, spread `<=3¢`
- Pomegranate source envelope: cumulative volume `>=2,000`, liquidity `>=10,000`
- 연속 관측이 `>6h`에서 `(0h,6h]`로 들어오고 receipt gap 5~30분, endDate 불변
- outcome 0 proxy: `ask=best_ask`, `bid=best_bid`
- outcome 1 proxy: `ask=1-best_bid`, `bid=1-best_ask`
- 직전 ask가 threshold 미만이고 현재 ask가 threshold 이상인 첫 상향 교차
- polling overshoot는 `<=1¢`, condition/outcome/entry당 한 번

중요한 의미 제한이 있다. DB의 `endDate`는 실제 경기 종료 시각과 같다는 보장이 없다.
1,988개 counterfactual signal 중 424개만 `endDate`와 `gameStartTime`이 1분 이내로 같고,
1,564개는 다르다. 따라서 이번 결과는 문자 그대로의 “실제 경기 종료 전 6시간”이 아니라
**Gamma endDate 전 6시간** 검정이다.

### 2.2 grid와 비용

- entry: `0.75..0.97`, 1¢ 간격 23개
- target: entry보다 최소 2¢ 높은 값부터 `0.99`, 1¢ 간격
- 별도 arm: resolution까지 보유
- target 도달은 진입 후 관측된 proxy bid가 target 이상일 때만 인정
- target 미도달은 cutoff 전 one-hot resolution이 있는 경우만 0/1 정산
- target도 label도 없으면 손익을 만들지 않고 censored 처리
- 모든 매수·목표가 매도를 taker로 가정
- `fees_enabled=1/null`은 sports rate 0.03, `0`은 fee 0
- primary sensitivity: 진입 ask `+1¢`, 목표 청산 bid `-1¢`
- 보조 sensitivity: 양쪽 `2¢`
- 가상 notional: 신호당 `$5`

공식 fee 식은 `shares × feeRate × p × (1-p)`다. resolution winner는 `$1`, loser는 `$0`으로
정산했다. maker rebate, holding reward, queue fill과 latency price move는 넣지 않았다.

## 3. 표본과 데이터 한계

| 지표 | 결과 |
|---|---:|
| eligible sports quote observations | 471,429 |
| 6h boundary | 2,082 |
| counterfactual signals | 1,988 |
| condition / event | 653 / 464 |
| train / validation signals | 857 / 1,129 |
| validation event-purge | 2 signals |
| same-cycle exact CLOB entry quote | 34 / 1,988 = 1.71% |
| label conflicts | 0 |

Gamma proxy path는 넓지만 exact CLOB 표본은 너무 작다. 특히 anchor의 exact evaluable 수는
`0.80→0.90` 1건, `0.80→resolution` 2건, `0.85`·`0.90` 0건,
`0.95→resolution` 1건뿐이다. proxy에서 target을 보았다는 사실은 실제 `$5` depth와 체결을
증명하지 않는다.

## 4. 사용자가 지정한 anchor

아래 ROI는 event마다 같은 가중치를 주고 sports taker fee와 1¢ adverse execution을 적용했다.

| 전략 | train n/events | train ROI | validation n/events | validation ROI | 전체 판정 |
|---|---:|---:|---:|---:|---|
| 0.80→0.90 | 36/34 | **-0.10%** | 35/33 | **+12.13%** | 기간 불안정, CI 0 포함 |
| 0.80→resolution | 32/30 | +3.73% | 31/29 | +11.80% | 양수지만 CI·label gate 실패 |
| 0.85→0.95 | 26/25 | **-29.61%** | 37/36 | **-6.46%** | 기각 |
| 0.85→resolution | 25/24 | **-30.47%** | 33/33 | **-9.08%** | 기각 |
| 0.90→resolution | 34/32 | **-6.00%** | 39/37 | **-2.61%** | 기각 |
| 0.95→resolution | 27/25 | +3.74% | 29/27 | **-0.24%** | validation 실패 |

전체 기간의 추가 진단:

| 전략 | evaluable/censored | fee-net | fee+1¢ | 95% CI fee+1¢ | all-censored-loss |
|---|---:|---:|---:|---:|---:|
| 0.80→0.90 | 71/12 | +8.01% | +5.92% | [-2.24%, +12.91%] | -8.40% |
| 0.80→resolution | 63/20 | +9.01% | +7.70% | [-2.66%, +16.08%] | -18.04% |
| 0.85→0.95 | 63/15 | -14.51% | -15.95% | [-28.69%, -4.58%] | -30.93% |
| 0.90→resolution | 73/15 | -3.15% | -4.18% | [-12.91%, +3.65%] | -19.66% |
| 0.95→resolution | 56/15 | +2.71% | +1.68% | [-2.40%, +3.77%] | -18.87% |

`0.80→0.90`은 validation만 보면 좋아 보이지만 train에서 1¢ 비용 후 음수다. 반대로
`0.80→resolution`은 두 기간 point estimate가 양수지만 전체 63건 중 resolution 승리는
55건이고 Wilson 하한 승률 `76.89%`가 fee 포함 손익분기 `80.85%`보다 낮다.

## 5. resolution-only 23개 진입가

표의 ROI는 `fee + 1¢ adverse`, `n/events`는 evaluable 표본이다. 서로 다른 기간에서 부호가
바뀌는 값이 많아 1-cent 최적화가 매우 불안정함을 보여준다.

| entry | train n/events | train ROI | validation n/events | validation ROI |
|---:|---:|---:|---:|---:|
| 0.75 | 30 / 30 | -4.64% | 50 / 47 | +1.03% |
| 0.76 | 29 / 26 | -1.28% | 47 / 44 | -12.44% |
| 0.77 | 28 / 25 | +8.97% | 37 / 36 | -12.01% |
| 0.78 | 30 / 28 | -10.45% | 31 / 30 | +4.39% |
| 0.79 | 25 / 23 | -13.95% | 31 / 30 | +4.84% |
| 0.80 | 32 / 30 | +3.73% | 31 / 29 | +11.80% |
| 0.81 | 35 / 34 | -7.60% | 23 / 21 | +3.29% |
| 0.82 | 30 / 28 | -8.30% | 36 / 34 | -1.79% |
| 0.83 | 27 / 25 | -3.18% | 42 / 38 | +4.49% |
| 0.84 | 27 / 25 | -27.68% | 45 / 41 | -7.65% |
| 0.85 | 25 / 24 | -30.47% | 33 / 33 | -9.08% |
| 0.86 | 29 / 26 | -16.82% | 37 / 37 | +1.40% |
| 0.87 | 26 / 24 | -10.65% | 42 / 39 | +7.04% |
| 0.88 | 15 / 14 | -24.15% | 40 / 35 | -2.89% |
| 0.89 | 35 / 33 | -18.42% | 43 / 39 | -7.97% |
| 0.90 | 34 / 32 | -6.00% | 39 / 37 | -2.61% |
| 0.91 | 13 / 13 | +7.91% | 37 / 37 | +2.15% |
| 0.92 | 19 / 19 | +6.74% | 36 / 34 | +3.67% |
| 0.93 | 32 / 30 | +2.26% | 41 / 38 | +3.00% |
| 0.94 | 29 / 26 | **+4.60%** | 31 / 31 | **+4.75%** |
| 0.95 | 27 / 25 | +3.74% | 29 / 27 | -0.24% |
| 0.96 | 22 / 20 | +2.62% | 34 / 33 | -0.65% |
| 0.97 | 28 / 24 | +1.37% | 38 / 36 | -1.25% |

## 6. 대표 target 조합

| 전략 | train n | train fee+1¢ | validation n | validation fee+1¢ |
|---|---:|---:|---:|---:|
| 0.80 → 0.85 | 37 | +1.82% | 38 | +6.53% |
| 0.80 → 0.90 | 36 | -0.10% | 35 | +12.13% |
| 0.80 → 0.95 | 35 | +2.86% | 33 | +10.68% |
| 0.80 → 0.99 | 34 | +4.40% | 31 | +11.55% |
| 0.85 → 0.90 | 29 | -23.67% | 39 | -7.84% |
| 0.85 → 0.95 | 26 | -29.61% | 37 | -6.46% |
| 0.85 → 0.99 | 26 | -28.74% | 33 | -9.26% |
| 0.90 → 0.95 | 37 | -6.60% | 43 | -1.49% |
| 0.90 → 0.99 | 36 | -5.32% | 40 | -2.63% |
| 0.95 → 0.99 | 31 | +3.14% | 32 | -0.08% |

0.80은 목표가를 높일수록 이번 표본의 point estimate가 좋아졌지만 사실상 resolution 보유와
비슷해진다. 0.85와 0.90은 어느 목표가도 양 기간을 구하지 못했다. 0.95는 작은 손실 한 번의
영향이 목표 이익 전체보다 크다.

## 7. 사후 후보 0.94→resolution

| 지표 | train | validation | 전체 descriptive |
|---|---:|---:|---:|
| signals / evaluable / events | 40 / 29 / 26 | 45 / 31 / 31 | 85 / 60 / 57 |
| wins | 29 / 29 | 31 / 31 | 60 / 60 |
| fee-net ROI | +5.68% | +5.82% | +5.76% |
| fee+1¢ ROI | +4.60% | +4.75% | +4.68% |
| fee+2¢ ROI | +3.55% | +3.69% | +3.62% |
| `$5` 총 P&L | +$8.27 | +$9.03 | +$17.30 |
| censored / matured missing | 11 / 10 | 14 / 13 | 25 / 24 |
| exact executable | 1 | 0 | 1 |
| hold p50 / p95 | 4.24h / 6.10h | 3.75h / 7.87h | 3.76h / 7.09h |

겉보기로는 사용자의 “작은 수익도 좋다”는 조건에 가장 잘 맞는다. 그러나 이 값을 곧바로
월 5%라고 읽으면 안 된다.

1. `+4.68%`는 평가된 거래 1건당 event-equal ROI이지 월간 계좌 수익률이 아니다.
2. 25개 미평가 신호 중 24개는 단순 기간 끝 right-censor가 아니라 resolution evidence gap이다.
3. 경험적 60/60만 resample한 bootstrap CI는 보지 못한 패배를 만들지 못해 과도하게 좁다.
   Wilson 하한과 break-even을 비교하면 아직 edge가 입증되지 않는다.
4. 23개 entry와 수백 target 중 결과를 본 뒤 고른 post-hoc 값이다.
5. exact CLOB ask/depth가 1건이라 실제 `$5` 체결 가능성을 검증하지 못했다.

## 8. 권고

### 현재 판정

- `0.85→0.95`: `STOP`
- `0.90→resolution`: `STOP`
- `0.95→resolution`: `STOP` 또는 negative control
- `0.80→0.90`: `RESEARCH`, 재현 전까지 live 금지
- `0.80→resolution`: `RESEARCH`, 재현 전까지 live 금지
- `0.94→resolution`: 가장 좋은 **prospective simulation candidate**, live 금지

### 다음 paired simulation

하나의 accountless collector에서 같은 시각과 같은 CLOB batch로 세 arm을 함께 기록한다.

| arm | 규칙 | 역할 |
|---|---|---|
| A | exact ask가 0.94를 상향 교차, resolution 보유 | 사후 최고 후보 |
| B | exact ask가 0.80를 상향 교차, 0.90 target 후 미도달 시 resolution | 사용자 원가설 |
| C | exact ask가 0.90를 상향 교차, resolution 보유 | 이번 표본의 negative control |

공통 계약은 sports only, Gamma endDate 6h window, 5분 cadence, `$5` virtual taker,
condition당 1회다. 모든 후보의 두 token exact book, `$5` depth, `feesEnabled`와 fee schedule,
`gameStartTime/endDate`, target path, one-hot resolution을 100% 저장한다. Pomegranate v2의
resolution watcher 결함을 그대로 재사용하지 않는다.

첫 24시간은 collection health만, 7일은 표본·누락만 확인한다. 최초 수익성 판정은 30일 후
하고 A/B/C나 수치를 중간에 바꾸지 않는다. 최소 gate는 arm당 evaluable 300, event 200,
resolution coverage 90%, exact quote/depth/fee 100%, event-cluster CI 하한 양수다.

## 9. 재현

```bash
cd /Users/izowooi/git/t1/daily-rsync
uv run daily-rsync verify \
  --job golden-pomegranate \
  --strategy golden-pomegranate \
  --from-date 2026-08-07 \
  --to-date 2026-08-18

cd /Users/izowooi/git/t1
pomegranate_db_args=()
for pomegranate_db in daily-rsync/data/sources/macmini-m5/jobs/golden-pomegranate/strategies/golden-pomegranate/runtime/pomegranate-15m-v2/databases/research/2026/08/*/trades_sim_*.db; do
  pomegranate_db_args+=(--db "$pomegranate_db")
done

uv run --project golden-pomegranate python \
  golden-pomegranate/scripts/evaluate_sports_favorite_grid.py \
  "${pomegranate_db_args[@]}" \
  --train-start 2026-08-07T00:00:00Z \
  --train-end 2026-08-13T00:00:00Z \
  --validation-start 2026-08-13T00:00:00Z \
  --validation-end 2026-08-19T00:00:00Z \
  --skip-quick-check \
  --output /tmp/pomegranate-sports-favorite-grid.json
```

검정 정의는
[protocol freeze](../../golden-pomegranate/research/2026-08-19-sports-favorite-grid-preregistration.md),
재현 코드는
[analyzer](../../golden-pomegranate/scripts/evaluate_sports_favorite_grid.py)에 있다.

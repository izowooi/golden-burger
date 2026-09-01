# Golden Watermelon Live · Golden Peach A/B 중간 교정 — 2026-09-02

## 결론

- **실거래 파라미터는 지금 바꾸지 않는다.** Watermelon은 확인용 독립 구간이 아직 0건이고,
  Peach는 동일 경기 20건뿐이다. 이 시점의 최댓값으로 갈아타면 진행 중인 사전 등록 실험을
  깨고 탐색 결과에 과적합한다.
- Watermelon의 `0.94` 하한은 현재 frozen grid에 없으므로 성과를 추정하지 않는다. 수집된
  `0.95`조차 해결 보유의 수수료 반영 평균 수익률이 `-0.45%`이고 95% 구간이
  `[-5.01%, +3.08%]`여서 하한을 낮출 근거가 없다.
- Peach Grey에서는 simulation 주문에 원래 없는 거래소 fill/fee 원장을 live 증거 공백으로
  오판해 후보 22건 중 5건을 막았다. **live 방어는 유지하고 simulation에서만 이 전역 차단을
  제외한다.** 이 수정이 이번에 배포하는 유일한 실행 로직 변경이다.
- `polybot-gold`의 현재 epoch는 Golden Plum이고, 과거 자료는 Golden Coconut이다. 현재
  Watermelon/Peach와 다른 모집단·계약이므로 이번 A/B 성과에 섞지 않았다.

## 증거 경계

모든 DB는 `daily-rsync` 최신 성공 동기화 후 `verify`와 SQLite `quick_check`를 통과한 절대
경로만 사용했다. 공통 분석 cutoff는 `2026-09-01T14:20:00Z`다.

| Jenkins | runtime | SHA-256 | source cutoff |
|---|---|---|---|
| Cat | `watermelon-live-cat-96-1m-v2h` | `6b055ac67ef33dcc90ff35a1312487d607c43a9c5e21a055bd198fc2591c284f` | `2026-09-01T14:21:18Z` |
| Dog | `watermelon-live-dog-99-1m-v2h` | `98bf02705265b3a1212d88b0bb93a16a99472fb532ad1ad2a1447ac2b5fd5c40` | `2026-09-01T14:25:18Z` |
| Bear | `watermelon-live-bear-mlb-96-1m-v3a` | `3fa0e410fdae65ef962a6161ed831b884a005f03a76ca61561e0b7fd0d1ad260` | `2026-09-01T14:30:29Z` |
| Tiger | `watermelon-live-tiger-mlb-99-1m-v3a` | `5d2e61508766cd390e08269c51bb635757723a12cbcc0871f6b84705d521d9e8` | `2026-09-01T14:34:32Z` |
| Eco | `peach-live-eco-3pp-1m-v1` | `e65c9a1eca21aa12d26880f5c3699c09bbb6be7f0c781d807ef94d8815965a68` | `2026-09-01T14:39:22Z` |
| Fruit | `peach-live-fruit-5pp-1m-v1` | `372122ea30a9d6cf35b02cc2aa4990264097a96c4fb7408402b7bbb86b446827` | `2026-09-01T14:44:22Z` |
| White | `watermelon-white-1m-v4a` | `6a20d45d41b0f603620825cd6bfe967c7c099789cd6bbabaca10df724b4e6360` | `2026-09-01T14:56:04Z` |
| Grey | `peach-shadow-1m-v1` | `affd81a16405ed4dd9764a5e150c668c4fe94d83c45ad6fea1c1b52e6e9f2c0d` | `2026-09-01T15:19:24Z` |

엄격 검증은 Watermelon `[2026-08-29T00:00Z, 2026-09-01T00:00Z)`, Peach
`[2026-08-30T00:00Z, 2026-09-01T00:00Z)`로 수행했다. 체결·수수료의 CRITICAL/HIGH는 없었다.
다만 여러 배포 cohort와 초기 중단 시간을 함께 포함해 failed run과 schedule gap HIGH가 남았다.
최신 cohort만 보면 Watermelon 성공 간격 최대 약 2.01분, Peach 약 1.46분이고 최근 실패는 없다.
이 때문에 전체 기간을 하나의 확정 수익 표본으로 합치지 않고 아래 결과를 중간 기술 통계로만
사용한다.

## Watermelon 실거래

최신 안전 코드 cohort(`2026-08-30T06:11Z` 이후)의 확정 BUY/SELL 또는 증명된 resolution만
집계했다. accepted order나 요청 가격 기반 `realized_pnl`은 사용하지 않았다.

| family / arm | DB 행 | 확정 경제 결과 | zero-fill | open/불명확 | fee-net P&L |
|---|---:|---:|---:|---:|---:|
| Soccer Cat 0.96 | 21 | 15 | 6 | 0 | `+$1.06` |
| Soccer Dog 0.99 | 15 | 12 | 3 | 0 | `+$0.50` |
| MLB Bear 0.96 | 21 | 21 | 0 | 0 | `-$0.69` |
| MLB Tiger 0.99 | 18 | 18 | 0 | 0 | `+$0.71` |

Cat의 stop 2건은 이후 실제 승리한 Chelsea와 Rennes였으므로 손절이 각각 약 `-$0.44`,
`-$0.77`의 불필요한 손실을 만들었다. Bear의 stop 7건 중 White에서 해결 결과까지 연결되는
6건도 모두 최종 승리였다. 반면 Padres 선택처럼 최종 패배한 경기에서는 stop이 약 `-$5`의
전손을 줄였다. 따라서 단순히 stop을 없애는 것은 꼬리 손실을 다시 키울 수 있어, 이 짧은
live 구간만으로 제거하지 않는다.

## White 반사실 시뮬레이션

White는 실제 체결이 아니라 exact `$5` 표시 호가와 sports taker fee를 재생한 결과다. 92개
독립 경기까지 수집됐지만 모두 calibration(교정 탐색) 구간이고 confirmation(독립 확인) 구간은
아직 0건이다.

| entry | events | win rate | 해결 보유 fee-net 평균 수익률 | bootstrap 95% 구간 |
|---:|---:|---:|---:|---:|
| 0.95 | 92 | 94.68% | -0.45% | [-5.01%, +3.08%] |
| 0.96 | 81 | 97.53% | +0.005% | [-3.87%, +2.61%] |
| 0.97 | 81 | 97.53% | -0.02% | [-3.86%, +2.59%] |
| 0.98 | 71 | 97.18% | -1.36% | [-5.67%, +1.56%] |
| 0.99 | 60 | 100.00% | +0.83% | [+0.76%, +0.89%] |

`0.99`의 60전 전승은 현재 표본의 기술 결과이지 희귀 패배가 없다는 보장이 아니다. 고정 stop
`0.70/0.80/0.85/0.90/0.93/0.95`는 모든 entry threshold에서 해결 보유보다 낮았다. 예를 들어
0.96은 보유 `+0.005%` 대비 stop 0.90 `-4.02%`, stop 0.93 `-3.36%`; 0.99는 보유
`+0.83%` 대비 stop 0.93 `-3.22%`, stop 0.95 `-3.31%`다. 현재 live의 entry−5%p stop은
이 구간 사이에 있지만 동일 정책을 정확히 독립 검증한 것은 아니므로 다음 epoch의 처치 후보로만
남긴다.

수집 품질에는 결과 3종 완전성 공백 28건, source minute 공백 468건이 HIGH로 남았다. 평가된
episode의 resolution coverage는 100%지만, 이 공백과 독립 확인 0건 때문에 지금 threshold나
stop을 승격하지 않는다.

## Peach 실거래와 직접 6호가 재생

Eco/Fruit의 신호 파라미터는 배포 중 안전 코드 digest만 바뀌었고 TP `+3%p/+5%p`, 공통
SL `-10%p`는 유지됐다. 서로 같은 20개 event를 event 단위로 합쳤다.

| arm | confirmed buys | fee-net P&L | 양수 event |
|---|---:|---:|---:|
| Eco +3%p | 19 | `+$1.43` | 16/20 |
| Fruit +5%p | 19 | `+$4.48` | 16/20 |

Fruit가 6 event에서 더 좋고 Eco가 1 event에서 더 좋으며 13 event는 동률이다. 차이는
`+$3.06`이지만 비동률 표본은 7개뿐이다.

Grey의 direct HOME/DRAW/AWAY × YES/NO 원시 book을 새 read-only 분석기로 재생했다. 최초
유효 event 20개(기존 trade 17 + 잘못 막힌 고유 event 3), 모든 선택은 NO였다. 매수·매도에
`shares × 0.05 × price × (1-price)` fee를 적용했다.

| TP / SL | evaluated | fee-net 평균 수익률 | 95% 구간 | 최악 |
|---|---:|---:|---:|---:|
| +3%p / -10%p | 20/20 | +1.92% | [-4.25%, +7.02%] | -37.31% |
| +5%p / -10%p | 20/20 | +3.23% | [-3.11%, +8.39%] | -37.31% |
| +7%p / -10%p | 20/20 | +5.38% | [-1.36%, +10.75%] | -37.31% |

`+7%p/-10%p`가 30개 탐색 조합 중 최고였지만 구간이 0을 포함하고, 모두 NO이며, 별도 fresh
execution book은 DB에 보존되지 않아 표시 호가 재생과 실제 FOK가 다를 수 있다. 따라서
`+7%p`를 지금 live에 넣지 않는다.

## 이번 변경과 다음 판정

1. `golden-peach`에 direct six-book TP/SL grid 분석기를 추가한다. DB는 read-only로 열고
   quick-check·SHA-256·fee·full-depth·검열 event를 결과에 남긴다.
2. Grey simulation에서만 live fill/fee 부재에 따른 전역 BUY 차단을 제외한다. Eco/Fruit의
   live fail-closed guard는 그대로다.
3. Cat/Dog/Bear/Tiger/Eco/Fruit의 threshold, TP, SL, notional `$5`, 1분 cadence는 변경하지 않는다.
4. Watermelon은 entry 종료 `2026-09-05T04:00Z`, follow-up `2026-09-12T04:00Z` 후 현재 cohort와
   독립 확인 구간을 나눠 재검토한다. 다음 후보는 `0.96/0.99` 하한 변경보다 **현재 동적 stop과
   resolution hold의 동일 threshold 비교**다.
5. Peach는 entry 종료 `2026-09-13T00:00Z`, follow-up `2026-09-20T00:00Z`까지 +3/+5를 유지한다.
   100개 독립 경기와 YES/NO 양쪽 표본 전에는 +7을 승격하지 않는다.

이 결정은 “현재 값이 최적”이라는 판정이 아니라, 지금 수치를 바꿀 통계적 근거가 아직 없다는
판정이다. 자료 누락 결함만 즉시 고치고 live 처치를 고정하는 것이 A/B의 정보가치를 가장 크게
보존한다.

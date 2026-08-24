# 034 — Golden Papaya·Queen·Melon 지속가능성 검토 — 2026-08-24

작성일: 2026-08-24 KST

## 0. 결론

사용자가 정한 지속가능성 기준은 계좌 전체 자금 대비 **주 +2% 또는 월 +10%**다.
세 전략 모두 현재 배포와 `$5` 주문 규모로는 이 기준을 충족하지 못한다.

| 전략 | 운영 판정 | 핵심 이유 | 권고 |
|---|---|---|---|
| Golden Papaya | **폐쇄 확정** | 완결 3건은 모두 손실, 종료된 시장 20건이 `HOLDING`에 남고, 전부 승리로 보는 상한도 목표 미달 | Cat·Dog timer 제거 완료. 잔여 포지션은 운영자가 수동 종료 |
| Golden Queen | **폐쇄 확정** | 현재 `$5` cohort가 양쪽 모두 음수이고 `0.85` stop이 실제 `0.41~0.69`에 체결됨 | Queen·King timer 제거 완료. 잔여 포지션은 운영자가 수동 종료 |
| Golden Melon High | **폐쇄 확정** | 19일에 체결 1건뿐이고 `$150k` volume arm은 표본이 생성되지 않음 | Fruit timer 제거 완료 |
| Golden Melon Mid·Low | **폐쇄 확정** | 전 거래가 이익이지만 독립 event 5개뿐이고 계좌 주간 환산은 목표에 크게 미달 | Lime·Wolf timer 제거 완료 |

이는 “전략 가설이 통계적으로 완전히 기각됐다”는 뜻과는 다르다. Papaya·Queen은
first-crossing archive와 운영 evidence가 불완전하고, Melon은 손실 표본이 한 건도 없어
tail risk가 검증되지 않았다. 다만 **현재 live 배포를 수익원으로 계속 운영하거나 금액을
늘릴 근거는 없다.** 연구와 수익 운용을 분리해야 한다.

이번 검토에서는 code·Jenkins 설정을 변경하지 않았다. 결과를 보고 parameter를 사후 선택하면
기존 cohort가 오염되며, 현재 evidence는 live 변경을 정당화하지 못한다.
이후 사용자가 세 전략 모두 폐쇄를 확정해 아래 7절의 Jenkins timer 중단을 별도 실행했다.

## 1. Evidence 경계

- Papaya·Queen: `[2026-08-12T00:00:00Z, 2026-08-24T00:00:00Z)`
- Melon: `[2026-08-05T00:00:00Z, 2026-08-24T00:00:00Z)`
- timezone: 수익 집계는 UTC half-open range, Jenkins 표시 설명은 KST
- 성과: `order_fills.status='CONFIRMED'`의 실제 size·price·fee만 확정 체결로 사용
- `RESOLVED`는 one-hot Gamma payout과 exact BUY fill이 함께 있는 경우에도
  `settlement_pnl_assumption`으로 분리했으며 실제 SELL/REDEEM 현금 체결로 부르지 않음
- 사용자 제공 7일 손익은 계좌 전체 snapshot이므로 bot 전략 손익과 구분

작업 시 MacBook은 약 211GiB, Mac Mini 내부 디스크는 약 61GiB가 남아 있었다.
7개 job의 scan 예상량은 합계 약 2.05GiB였고 모두 동기화할 수 있었다. 실제 sync는
failure 0건이었고, 각 DB는 `daily-rsync verify`와 SQLite integrity를 통과했다.

| Jenkins | verified local DB | SHA-256 | source cutoff / sync finished UTC |
|---|---|---|---|
| `polybot-cat` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-papaya/runtime/papaya/databases/latest/trades.db` | `d8dfe2f9b53579cf8565958efcadbe96756d387969c7e433b14a6ac6dbbb91a9` | `2026-08-24T10:21:53.296126Z` / `2026-08-24T10:24:16.997Z` |
| `polybot-dog` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-papaya/runtime/papaya/databases/latest/trades.db` | `a7c874fd6d78bc558e12d34b68ad6885bc55bd88041c86e77bc51e47f7d2377f` | `2026-08-24T10:16:50.652Z` / `2026-08-24T10:25:01.690Z` |
| `polybot-queen` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-queen/strategies/golden-queen/runtime/queen-live-24h/databases/latest/trades.db` | `1614f59c5dda2f3bb2cf83cda55050b971722b00cd0814d8e1273f6880ca3cde` | `2026-08-24T10:22:39.989Z` / `2026-08-24T10:26:27.747Z` |
| `polybot-king` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-king/strategies/golden-queen/runtime/queen-live-12h/databases/latest/trades.db` | `2fd834149db0562dec351e2b49516a3f2e44fd0dc5f98d862576a17b56363878` | `2026-08-24T10:24:40.382Z` / `2026-08-24T10:30:06.259Z` |
| `polybot-fruit` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-fruit/strategies/golden-melon/runtime/polybot-melon-high/databases/latest/trades.db` | `016535c32500be24f9022fd39ad35ab9c244fcd94a169050e45ae2d92617bddb` | `2026-08-24T10:28:43.140Z` / `2026-08-24T10:33:49.566Z` |
| `polybot-lime` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-lime/strategies/golden-melon/runtime/polybot-melon-mid/databases/latest/trades.db` | `c3f53645b64f8a7185fe99d8e7f3c4a4cfd3f08fa9d32954edb069d6049c31c4` | `2026-08-24T10:35:42.124Z` / `2026-08-24T10:37:47.732Z` |
| `polybot-wolf` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-wolf/strategies/golden-melon/runtime/polybot-melon-low/databases/latest/trades.db` | `c0b82eaa9269c475202016763a2077bc7a906154c3ce7290ffe97f7d0c6db4cf` | `2026-08-24T10:37:17.527Z` / `2026-08-24T10:39:51.421Z` |

Wolf의 과거 Jenkins console 17개는 retention 삭제로 명시적으로 skip됐다. 현재 DB와 bot log,
8월 21일 이후 collection-health 판단에는 필요한 coverage가 있으나 과거 console 완전성은 없다.

## 2. Jenkins와 공통 운영 상태

| Jenkins | 주기 | 최신 build | 최신 관측 runtime | Clean / concurrent |
|---|---|---|---:|---|
| `polybot-cat` | `H/10` | SUCCESS | 약 85초 | 없음 / 차단 |
| `polybot-dog` | `H/10` | SUCCESS | 약 21초 | 없음 / 차단 |
| `polybot-queen` | `H/5` | SUCCESS | 약 27초 | 없음 / 차단 |
| `polybot-king` | `H/5` | SUCCESS | 약 9초 | 없음 / 차단 |
| `polybot-fruit` | `H/5` | SUCCESS | 약 11초 | 없음 / 차단 |
| `polybot-lime` | `H/5` | SUCCESS | 약 40초 | 없음 / 차단 |
| `polybot-wolf` | `H/5` | SUCCESS | 약 7초 | 없음 / 차단 |

전 job의 global `max_positions`가 가득 차서 거래하지 못한 것은 아니다. 검토 구간의 최대
open count는 Cat 8, Dog 14, Queen 2, King 1, Melon 각 1 이하였다. Queen·Melon의 저빈도는
주로 신호 희소성, spread와 fresh-price 재검증 때문이다. Papaya는 global cap 전에도 종료된
동일 event 행이 남아 `max_event_positions=1`을 막는 사례가 실제 로그에서 확인됐다.

2026-08-20에는 Gamma API가 HTTP 451을 반환해 전 job이 공통으로 약 7시간 45분 동안
실패했다. Cat/Dog은 각 47건, Queen/King은 93/92건, Melon은 각 92~93건의 451 실패가
남았다. 이 구간을 전략별 code 문제로 보지는 않지만 first-crossing 관측 공백이므로 해당
교차를 완전한 prospective evidence로 사용할 수 없다. 8월 21일 이후 Melon strict
collection audit은 CRITICAL/HIGH 없이 통과했다. Papaya·Queen은 60일 archive 계약 때문에
여전히 HIGH다.

## 3. Golden Papaya — Cat 24h / Dog 72h

### 3.1 실제 구성

두 arm은 `$5`, YES `0.95~0.97` 첫 상향교차, absolute stop `0.90`, 최소 유동성
`$10k`, 24시간 거래량 `$2k`, position/event cap `20/1`이 같고 종료시간 범위만
24h/72h로 다르다. 60일 archive는 2026-08-05 12:36 UTC부터 약 19일만 축적돼
retention requirement의 약 30.8%다.

### 3.2 확인된 거래와 손익

| 항목 | Cat 24h | Dog 72h |
|---|---:|---:|
| 후보 → BUY | 22 → 9 | 51 → 16 |
| 완결 roundtrip | 1 | 2 |
| TP / stop | 0 / 1 | 0 / 2 |
| CONFIRMED 완결 순손익 | **-$0.35949** | **-$0.76074** |
| 검토 종료 시 `HOLDING` | 8 | 14 |
| 그중 market end가 지난 행 | 7 | 13 |
| 검토기간 나머지가 모두 YES=1인 손익 상한 | **+$1.38686** | **+$1.91582** |
| 위 상한의 `$300` 계좌 주간 환산 | **+0.27%** | **+0.37%** |

마지막 행은 이미 난 stop 손실을 포함하고 미해결 포지션이 모두 1로 payout된다고 가정한
낙관적 상한이다. 그 상한도 목표 +2%/주에 크게 미달한다. 최근 며칠의 더 빠른 진입 속도만
선택해 모든 미래 거래가 승리한다고 놓아도 약 0.8%/주 수준으로 목표에 못 미친다.

사용자가 제공한 계좌 7일 손익 Cat +$1.57, Dog +$1.93은 각각 약 +0.52%, +0.64%다.
이 값에는 미해결 mark, payout, 계좌 전체 변동이 섞이며 봇의 strict fill P&L과 일치하지
않는다. 어느 쪽 기준으로 보아도 +2%에는 미달한다.

### 3.3 가장 큰 결함

- Cat의 종료된 7건, Dog의 종료된 13건이 Gamma final payout을 확보하지 못해 계속
  `HOLDING`이다.
- 최신 cycle마다 CLOB midpoint는 없고 `closed+final payout 증거 없음` 경고가 반복된다.
- catalog는 마지막 active snapshot의 `proposed` 또는 미확정 상태에서 멈췄다.
- 실제 신규 후보가 생겨도 과거 종료 행과 같은 event이면 `event 포지션 한도 도달`로
  거절된다.
- partial fill, 현재 `PENDING_BUY/PENDING_SELL`, fee 누락은 없었다. 문제는 체결이 아니라
  **resolution lifecycle과 cap 해제**다.

따라서 `max_positions`를 올리면 결함을 가릴 뿐이고, `min_volume_24h`나 liquidity를
낮추면 stale row가 더 빨리 쌓인다. H/10을 H/5로 바꿔도 resolution 증거가 생기지 않는다.

### 3.4 판정과 다음 조건

1. Cat·Dog의 수익 목적 신규 진입을 중지한다.
2. closed market을 condition ID로 재조회해 one-hot final payout을 적재하고, wallet/redeem과
   대사해 `HOLDING`을 `RESOLVED` 또는 증거 기반 terminal 상태로 해제한다.
3. 과거 20건을 backfill한 뒤 exact strategy P&L을 다시 계산한다.
4. 60일 archive가 성숙하는 2026-10-04 전후까지 first-crossing 우열이나 threshold를
   변경하지 않는다.
5. 연구를 계속한다면 `$5`, 현재 band·liquidity·volume·cap을 그대로 둔다.

운영 결함을 고쳐도 현재 진입 속도와 payout 폭으로 목표 수익률을 만들기 어렵다. 그러므로
**현재 배포는 수익 전략으로 폐쇄**하고, 가설 자체가 중요할 때만 별도 연구 cohort로 남기는
것이 타당하다.

## 4. Golden Queen — Queen 24h / King 12h

### 4.1 cohort를 나눠야 하는 이유

Queen 24h의 현재 `$5` config는 `2026-08-20T10:32:21Z`, King 12h는
`2026-08-20T10:29:19Z`에 처음 나타났다. 8월 12일부터 현재 설정으로 계속 실행된 것이
아니다. 그 전 Queen에는 `$100` 거래가 있었다.

| 항목 | Queen 24h | King 12h |
|---|---:|---:|
| 전체 후보 → BUY | 11 → 4 | 6 → 1 |
| 전체 완결 TP / stop | 2 / 2 | 0 / 1 |
| 전체 CONFIRMED 순손익 | **+$4.35984** | **-$2.44260** |
| 현재 `$5` cohort 완결 | 3 | 1 |
| 현재 `$5` cohort 순손익 | **-$3.69210** | **-$2.44260** |

Queen 전체 양수는 `$100` 한 건의 +$8.05194가 만든 결과다. 현재 `$5` cohort는 Queen
1승 2패, King 0승 1패였다. 고정 review end 뒤 최신 cutoff까지 Queen은 stop 손실
-$0.58410과 open 1건, King은 stop 손실 -$0.53100과 open 1건이 추가됐다.

Queen의 전체 +$4.35984도 `$3,000` 계좌에서 12일 +0.145%, 주간 약 +0.085%뿐이다.
목표는 주 +$60이다. 현재 신호 수와 `$5` 주문으로는 모든 거래가 TP여도 이를 만들 수 없다.

### 4.2 거래가 적은 이유와 stop 위험

- global position/open-notional cap에 막힌 cycle은 없었다.
- 24h의 미주문 후보 7건은 spread 초과 4, 주문 직전 가격 이탈 3건이었다.
- 12h의 미주문 후보 5건은 spread 초과 4, 가격 이탈 1건이었다.
- 거절 spread는 대부분 5.0~13.2%였고, 재검증 가격은 `0.898`, `0.891`, `0.855`,
  `0.810`까지 이미 떨어진 사례가 있었다.
- 따라서 `max_spread=0.02`나 entry band를 완화하면 급락 중이거나 체결 품질이 나쁜
  시장을 사게 된다. 이 방어는 유지해야 한다.

더 큰 문제는 stop trigger `0.85`가 손실 상한이 아니라는 점이다. 실제 stop SELL VWAP은
Queen `0.69`, `0.41`, King `0.47`이었다. 약 `0.93` 진입 후 `0.41` 매도 한 건은
정상 TP 한 건의 이익을 열 번 이상 지운다. 5분 cadence를 더 짧게 해도 event gap과
order-book depth 부족을 제거할 수 없다.

### 4.3 evidence gap과 판정

- BUY/SELL confirmed fill과 fee coverage는 100%이고 partial/PENDING/수량 불일치는 없다.
- 반면 60일 first-crossing archive는 약 30.8%뿐이고 451 공백이 있다.
- 두 arm의 실행 시각도 2~3분 달라 동일 event에서 spread와 fresh price가 달랐다.
- 24h만 잡은 14~16시간대 승리 2건이 있지만 독립 표본이 너무 작아 24h 우위를
  주장할 수 없다.

따라서 현재 live에서는 `entry`, `stop`, `max_spread`, `min_volume_24h`, 주문액을 변경하지
않는다. 특히 `$100`으로 되돌리거나 spread를 완화하지 않는다.

수익 운영은 중단하는 것이 맞다. 추가 가설을 검증하고 싶다면 live parameter tuning이
아니라 별도 simulation에서 24h를 고정하고 volume gate 하나만 사전등록해 비교해야 한다.
이번 승패를 보고 특정 volume 값을 고르면 사후 과적합이므로 이 문서에서는 live 권장값을
제시하지 않는다.

## 5. Golden Melon — High / Mid / Low

### 5.1 실제 A/B/C 축

세 arm은 `$5`, 진입 `0.85~0.93`, `(0h,72h]`, TP `0.97`, stop `0.78`, 최소
유동성 `$20k`, experiment capital `$100`이 같다. 차이는 24시간 거래량 gate뿐이다.

- `polybot-fruit`: High `$150k`
- `polybot-lime`: Mid `$50k`
- `polybot-wolf`: Low `$20k`

### 5.2 성과

| Arm | CONFIRMED BUY / SELL | 엄격 roundtrip | resolution assumption | SELL 실현손익 | resolution 포함 경제손익 | `$100` 기준 주간 환산 | 30일 환산 |
|---|---:|---:|---:|---:|---:|---:|---:|
| High `$150k` | 1 / 1 | 1 | 0 | +$0.75372 | **+$0.75372** | +0.278% | +1.19% |
| Mid `$50k` | 5 / 3 | 3 | 2 | +$1.93431 | **+$2.99054** | +1.102% | +4.72% |
| Low `$20k` | 5 / 3 | 3 | 2 | +$1.94976 | **+$3.08246** | +1.136% | +4.87% |

사용자 계좌 전체를 분모로 하면 주간 환산은 High 약 0.09%, Mid 약 0.37%, Low 약
0.15%로 더 낮다. `$100` experiment capital 기준으로도 +2%/주에 미달한다.

11개 arm별 행은 6개 unique market, 5개 unique event에 불과하다. Mid와 Low는 4개
condition을 공유하고 전체 경제손익 차이는 `$0.09192`뿐이다. 전 거래가 이익이라 겉보기에는
고무적이지만, stop은 세 arm 모두 0건이다. 이 전략이 막으려던 급락 tail을 한 번도
검증하지 못했으므로 주문액을 늘릴 수 없다.

### 5.3 거래가 적은 이유

| Arm | 후보 → BUY | 주요 미체결 원인 |
|---|---:|---|
| High | 2 → 1 | volume gate 자체가 희소 |
| Mid | 7 → 5 | spread 초과 |
| Low | 10 → 5 | fresh-price 이탈, spread, cycle 1건 제한 |

global max position이나 open-notional cap에 막힌 사례는 없었다. 같은 관측군의 volume-gate
교차 19건은 `<$20k` 12건, `$20~50k` 3건, `$50~150k` 4건, `≥$150k` 0건이었다.
중앙값은 약 `$16.4k`, p90 약 `$91.7k`, 최대 약 `$110.2k`다. High `$150k`는
수익성 이전에 prospective sample을 만들지 못한다.

### 5.4 판정과 선택지

- High는 폐쇄한다. `$150k`를 사후에 낮추면 기존 arm의 연장이 아니라 새 cohort다.
- Mid·Low는 수익 전략으로는 중단한다.
- 학습 목적이라면 `$5`와 기존 parameter를 고정한 채 각각 independent confirmed BUY
  30건까지 제한 연장할 수 있다.
- 현재 19일 5건 속도라면 arm당 30건에는 시작일부터 약 114일, 지금부터도 약 95일이
  더 필요하다. 이 기간을 기다릴 의사가 없다면 Mid·Low도 폐쇄하는 것이 합리적이다.
- order amount 증액, TP/SL 변경, 새 volume threshold 선택은 금지한다. 현재 데이터에는
  손실 tail과 threshold 우열의 근거가 없다.

## 6. 권장 실행 순서

1. `polybot-cat`, `polybot-dog`, `polybot-queen`, `polybot-king`의 신규 진입을 멈춘다.
2. Papaya의 종료된 20개 `HOLDING`을 wallet·Gamma final payout과 대사하고 lifecycle을
   복구한다. DB clean은 하지 않는다.
3. `polybot-fruit` High arm은 종료한다.
4. `polybot-lime`, `polybot-wolf`는 수익 운용을 종료하고, 연구를 계속할 때만 `$5` 그대로
   기간과 30-event gate를 명시해 유지한다.
5. 어떤 arm도 증액하거나 필터를 완화하지 않는다.
6. 후속 연구는 새 runtime/config hash와 고정된 시작·종료일로 분리하고, 중간 결과를 보고
   parameter를 바꾸지 않는다.

현재 수익 목표를 우선한다면 가장 단순한 결론은 **Papaya·Queen·Melon High를 폐쇄하고,
Melon Mid·Low도 연구 가치가 비용보다 크다고 판단할 때만 소액으로 남기는 것**이다.

## 7. 사용자 폐쇄 결정과 Jenkins timer 중단 — 2026-08-24

사용자가 Papaya·Queen·Melon 세 전략의 종료를 확정했다. Jenkins job 자체를 disable하거나
workspace를 지우지 않고, 신규 자동 cycle만 막기 위해 각 config에서 `TimerTrigger` 하나만
제거했다. shell, SCM, credential reference, build retention, DB·log·workspace는 변경하지 않았다.

| 전략 | Jenkins job | 기존 주기 | 변경 후 | 마지막 확인 build | 변경 후 config SHA-256 |
|---|---|---|---|---|---|
| Papaya | `polybot-cat` | `H/10 * * * *` | timer 없음 | `#5154 SUCCESS` | `4e30998ef6be3778d97e1839b7a274dc8ea7cc4237c3e5f0c0ded627db00eadf` |
| Papaya | `polybot-dog` | `H/10 * * * *` | timer 없음 | `#5049 SUCCESS` | `71e22cc539a67b3ad5bd9fc030de51168478c7359bba35ff65467d9f2ade53c2` |
| Queen | `polybot-queen` | `H/5 * * * *` | timer 없음 | `#5032 SUCCESS` | `579cc6306fbb4736fd34aaf360651e1cbbd06381ed17a52360c8f19d2f5b6441` |
| Queen | `polybot-king` | `H/5 * * * *` | timer 없음 | `#5033 SUCCESS` | `a2fabf4c9b71382850f7a499ac246c659a98f597dde36ccc05ae73e1710bcdf6` |
| Melon | `polybot-fruit` | `H/5 * * * *` | timer 없음 | `#5419 SUCCESS` | `8ca3f56e75bfef7cb48fcaef6348babd1d29c126a8ed672158d77fb0704adf41` |
| Melon | `polybot-lime` | `H/5 * * * *` | timer 없음 | `#12185 SUCCESS` | `0a1d91ef1a6c279dec8fb9c674823199be059282a309d677c25c3c644fb8c7b1` |
| Melon | `polybot-wolf` | `H/5 * * * *` | timer 없음 | `#11034 SUCCESS` | `66a66af8870329773df83000c9058ac29feb028509a9270a006775d040f422b7` |

변경 직전에 이미 실행 중이던 Dog `#5049`와 Wolf `#11034`는 강제 중단하지 않았고 둘 다
자연스럽게 `SUCCESS`로 끝났다. 2026-08-24 21:41 KST 재조회에서 7개 모두
`disabled=false`, `buildable=true`, `concurrent=false`, `TimerTrigger=0`, queue 0,
running build 0이었다. 따라서 예약 실행은 더 생기지 않지만 사용자가 수동으로 Build Now를
누르면 여전히 실행된다. Jenkins description에 남은 과거 cron 문구는 표시용 텍스트일 뿐
실제 trigger가 아니다.

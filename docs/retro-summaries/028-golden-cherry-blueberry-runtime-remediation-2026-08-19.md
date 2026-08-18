# 028 — Golden Cherry·Blueberry 해석 정정, lifecycle·수집 성능 복구 — 2026-08-19

작성일: 2026-08-19 KST

대상:

- Golden Cherry live: `polybot-yellow`, `polybot-orange`
- Golden Blueberry live/shadow: `polybot-eagle`, `polybot-fox`, `polybot-shadow`
- 동일 Gamma universe를 중복 조회하던 live job:
  `polybot-cat`, `polybot-dog`, `polybot-king`, `polybot-queen`, `polybot-bear`,
  `polybot-eco`, `polybot-tiger`, `polybot-fruit`, `polybot-wolf`, `polybot-lime`

## 0. 결론

```text
Orange 고가 진입 15건: 15패 resolution이 아님. 해결 전 매도 손익 15건이 모두 음수.
최종 outcome: 11건은 1, 4건은 0.
상대 TP entry×1.10: entry > 0.90909에서는 구조적으로 도달 불가능.
0.95/0.99/해결 보유 절대 목표: 타당한 새 가설이나 현재 DB에는 가격 경로가 없어 즉시 live 배포하지 않음.

과거 Orange open=100: Polymarket UI의 실보유 100건이 아니라 stale DB lifecycle 95건 포함.
wallet reconciliation: Yellow 7건, Orange 95건 안전 종결. DB clean은 하지 않음.
현재 wallet/DB 대사: Yellow bot 3건, Orange bot 7건 실재. PENDING_SELL=0.
현재 PENDING_BUY: Yellow 2, Orange 3 — 모두 LIVE, matched=0인 미체결 주문이며 partial fill 아님.

historical partial fill: $5 주문에서도 실제 발생. 상태명 MATCHED만 믿던 결함을 코드로 수정.
배포 commit: 9ec24bb. 두 live job 최신 자연 build와 RunAudit SUCCESS.

Blueberry cumulative volume >= $5,000: 세 job 모두 적용. 333 page/약 3.3만 → 63~64 page/약 6.3천.
Shadow 최근 실패 원인 제거, workspace ambiguity 제거, sync/verify SUCCESS.

나머지 10개 broad-universe job: 동일 sweep 공유 cache로 중복 10회를 1회로 축소.
cache-hit cycle은 약 10~36초. 단, 새 333-page sweep의 선두 1개는 여전히 장시간 소요.
live 실험 universe를 조용히 바꾸지 않기 위해 이 10개에는 volume 5k를 일괄 적용하지 않음.
```

## 1. `0.9091 초과 15건 전부 손실`의 정확한 의미

분석 구간은 이전 회고와 같은 단일 cohort다.

- UTC half-open range:
  `[2026-08-16T11:30:00Z, 2026-08-18T10:15:00Z)`
- Git commit: `ce039e70f544c022bda07be073f7018b28248ffe`
- Orange config hash:
  `c078cf0e8a390ece9c9f828705d87127b01de67de2a02c0f3a401c5e91b3ba33`
- 대상: 양쪽 `CONFIRMED` fill 수량·가격을 정확히 대사할 수 있는 Orange closed trade 중
  entry price가 `1 / 1.10 = 0.90909…`보다 높은 15건

15건 모두 **해결 전에** 매도됐다.

| 조기 exit | 건수 | confirmed-fill net |
|---|---:|---:|
| stop loss | 6 | 약 `-$3.9552` |
| trailing stop | 9 | 약 `-$2.5885` |
| take profit | 0 | `$0` |
| 합계 | 15 | 약 **`-$6.5437`** |

공식 Gamma closed-market outcome을 condition/outcome identity로 다시 조회한 결과는 다음과
같다.

| 최종 결과 | 건수 | 실제 조기 exit net | 해결까지 보유한 반사실 P&L |
|---|---:|---:|---:|
| 매수 outcome = 1 | 11 | 약 `-$3.7287` | 약 `+$4.3796` |
| 매수 outcome = 0 | 4 | 약 `-$2.8150` | 약 `-$19.9837` |
| 합계 | 15 | 약 **`-$6.5437`** | 약 **`-$15.6041`** |

따라서 두 문장이 동시에 참이다.

1. 15건의 봇 조기 매도 손익은 모두 음수였다.
2. 그중 11건은 결국 매수 outcome이 1로 해결됐다.

조기 exit는 11개 승자의 이익 약 `$8.11`을 놓쳤지만, 4개 패자의 손실을 약 `$17.17`
줄여 전체로는 해결 보유보다 약 `$9.06` 나았다. 즉 현재 표본은 “무조건 해결까지
보유”도 지지하지 않는다. 문제는 모든 고가 진입에 동일한 상대 TP·stop·trailing을 적용해
승자와 패자를 구분하지 못했다는 것이다.

Polymarket 가격은 결과의 보장이 아니라 시장 참여자가 형성한 **implied probability**다.
공식 문서도 표시 가격이 bid/ask midpoint이고 spread가 큰 경우 last trade일 수 있으며,
실제 매수는 ask·매도는 bid에서 이뤄질 수 있다고 설명한다.
([Prices & Orderbook](https://docs.polymarket.com/concepts/prices-orderbook))
독립이고 정확히 90%인 사건 15개조차 모두 적중할 확률은 `0.9^15 ≈ 20.6%`뿐이다.
실제 시장들은 상관돼 있고 entry ask가 완전히 calibration된 확률이라는 보장도 없으므로
“0.9이면 15건 모두 1이어야 한다”는 결론은 성립하지 않는다.

## 2. 절대 목표가 제안에 대한 판단

사용자 지적대로 `entry × 1.10`은 고가 확률 시장에 맞지 않는다.

- entry `0.9091`이면 TP가 거의 `1.00`이다.
- entry가 이를 넘으면 TP가 `1.00`보다 커져 절대 체결될 수 없다.
- entry `0.95`에서 10% 상대 수익을 요구하면 목표가 `1.045`다.

따라서 `0.95`, `0.99`, resolution처럼 **절대 가격으로 exit를 정의하는 설계**가 훨씬
직관적이다. 다만 이번에 이를 live로 즉시 바꾸지는 않았다.

- 두 DB의 `market_snapshots`가 모두 0행이라 각 거래가 진입 후 `0.95`, `0.99`, stop 중
  어디를 먼저 통과했는지 재생할 수 없다.
- entry `0.949`에서 `0.95` 매도는 spread·slippage를 감안하면 경제적 이익이 아닐 수 있다.
- Orange의 현재 entry 상한은 `0.95`이므로 “`0.98` 이상에서 매수 후 1까지 보유”는 현재
  전략이 실제로 관측하지 않는 별도 entry arm이다.
- 이번에 Cherry 코드와 lifecycle cohort가 이미 바뀌었다. 같은 시점에 exit까지 바꾸면
  원인을 분리할 수 없다.

다음 exit 실험은 동일 candidate를 주문 없이 기록하면서 `TP 0.95 / 0.97 / 0.99 /
resolution hold`를 공통 stop과 함께 path replay하는 shadow cohort가 적절하다. 목표가는
최소한 `entry + 실제 왕복비용`보다 높아야 한다. 현재 근거로 특정 절대값의 수익성을
확정하지 않는다.

## 3. `max_positions=100`과 낮은 liquidity의 의미

### 3.1 100은 지갑 포지션 수가 아니라 과거 설정 상한이었다

이전 Orange 구성은 `max_positions=100`, cycle 신규 상한 5였다. 봇은 안전상 아래 DB
상태를 모두 open slot으로 센다.

- `PENDING_BUY`: 주문을 냈지만 full fill을 아직 확정하지 못함
- `HOLDING`: 실제 보유로 관리 중
- `PENDING_SELL`: 매도를 냈지만 full fill을 아직 확정하지 못함
- `QUARANTINED`: 증거가 불충분해 자동 종결하지 못함

이전 source cutoff에서 Orange DB는 `HOLDING 96 + PENDING_BUY 2 + PENDING_SELL 2 = 100`이었지만,
공개 wallet API에는 bot과 매핑되는 실보유가 5건뿐이었다. 따라서 “계좌가 100개를
보유”한 것이 아니라, 매도·해결·미체결 뒤 종결되지 않은 DB 행 95건이 상한을 점유했다.

`tools/reconcile_positions.py`를 먼저 dry-run한 뒤 public wallet evidence가 없는 행만
종결했다. DB clean은 하지 않았고, 수동 미매핑 포지션도 건드리지 않았다.

| Job | 종결한 stale open | backup | 종결 직후 DB open |
|---|---:|---|---:|
| Yellow | 7 | `trades.20260818T155305Z.pre-reconcile.db` | 3 |
| Orange | 95 | `trades.20260818T155318Z.pre-reconcile.db` | 5 |

재발 시 한 cycle에 수십 건을 다시 예약하지 않도록 공통 안전 설정도 맞췄다.

| 설정 | Yellow | Orange |
|---|---:|---:|
| buy amount | `$5` | `$5` |
| max positions | 10 | 10 |
| max new positions/cycle | 1 | 1 |
| cadence | `H/5` | `H/5` |

이는 순수 A/B 완성이 아니다. 진입 band와 최소 liquidity는 아직 다르다. 다만 과거
`100 vs 10`, `5 vs 1`이라는 노출·burst 차이는 제거했다.

### 3.2 현재 wallet/UI와 DB는 서로 맞는다

최종 sync DB에 대해 공개 wallet API로 다시 dry-run 대사했다.

| Job | wallet 유효 보유 | bot DB와 매핑 | 수동/미매핑 | DB open |
|---|---:|---:|---:|---:|
| Yellow | 5 | 3 | 2 | `HOLDING 3 + PENDING_BUY 2` |
| Orange | 8 | 7 | 1 | `HOLDING 7 + PENDING_BUY 3` |

사용자가 UI를 확인한 뒤 Orange에 bot fill 2건이 더 생겨 현재 bot 보유가 7건이 됐다.
나머지 5개 `PENDING_BUY`는 모두 venue `LIVE`, `size_matched=0`인 미체결 GTC 주문이라
wallet position으로 보이지 않는다. `PENDING_SELL`은 양쪽 모두 0이다.

현재 `max positions 10 도달` 로그는 Orange의 `실보유 7 + 미체결 예약 3 = 10`을 뜻한다.
이 미체결 주문은 기본 30분 TTL 뒤 재조회·취소되며 slot을 돌려준다. 따라서 현재 방어는
stale 95건 때문에 전체 전략을 잘못 막는 상태가 아니라, 동일 자금을 여러 주문에 중복
예약하지 않기 위한 정상적인 보수 계산이다.

### 3.3 `min_liquidity`는 계좌 잔액이 아니다

Orange `$30,000`, Yellow `$125,000`은 해당 시장의 Gamma metadata liquidity 하한이다.
Orange가 “낮은 liquidity”라는 말은 계좌에 돈이 적다는 뜻이 아니라 더 얕은 시장까지
후보로 포함한다는 뜻이다. 최신 sweep은 Orange가 41 page/4,064 market, Yellow는 대략
9 page/수백 market이었다. 이 차이도 두 job을 entry band 하나만의 A/B로 볼 수 없는
이유다.

## 4. `$5`인데도 partial fill이 가능한 이유와 근거

`POLYBOT_BUY_AMOUNT=5`는 5주가 아니라 **주문 원금 5달러**다. 예를 들어 가격 0.91이면
요청 수량은 약 `5 / 0.91 = 5.49주`다. GTC limit order는 반대편 주문 수량이 일부만
있으면 일부 체결되고 나머지는 book에 남거나 취소될 수 있다. 공식 order lifecycle도
부분 체결된 부분은 취소할 수 없고 미체결 잔량만 취소된다고 설명한다.
([Order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle),
[CLOB error codes](https://docs.polymarket.com/resources/error-codes))

과거 DB의 실제 evidence는 다음과 같다.

| Job | side | requested shares | confirmed shares | venue terminal/status |
|---|---|---:|---:|---|
| Orange | BUY | 5.780347 | 4.720000 | `CANCELED_MARKET_RESOLVED` |
| Orange | BUY | 5.494505 | 5.000000 | terminal partial |
| Orange | SELL | 5.710000 | 4.337347 | `INVALID` |
| Orange | SELL | 5.810000 | 1.392404 | `CANCELED_MARKET_RESOLVED` |
| Orange | BUY→SELL 사례 | 5.780000 | 1.820000 | order status `MATCHED` |

마지막 사례는 3.96주가 남았는데도 기존 코드가 `MATCHED`라는 문자열과 확인된 fill 합이
서로 같다는 이유로 trade를 `COMPLETED` 처리했다. `$5` 소액이라는 사실은 order book의
잔량 분할, tick/수량 정밀도, 경기 지연 체결을 없애지 않는다.

## 5. Golden Cherry lifecycle 수정과 배포

수정 순서는 다음과 같다.

1. 두 live job timer를 잠시 비활성화했다.
2. terminal partial BUY는 확인된 실제 수량만 `HOLDING`으로 활성화하고, terminal partial
   SELL은 실제 잔량을 `HOLDING`으로 되돌리도록 수정했다.
3. `MATCHED` 상태명 대신 `order_status_events.original_size`와 submission의 실제 token
   amount를 authoritative requested size로 사용하도록 수정했다.
4. confirmed fill 합이 authoritative size와 일치할 때만 full fill로 종결한다.
5. 실제 wallet balance와 confirmed SELL을 함께 사용해 residual을 계산하고, 0.010001주
   이하의 quantization dust만 자동 종결한다.
6. 테스트·배포 후 timer와 `active`를 복원하고 수동 및 자연 build를 확인했다.

관련 commit:

- `1074b7f` — Cherry 기본 burst/position 안전 상한 정리
- `a85f72e` — terminal partial BUY/SELL lifecycle 처리
- `9ec24bb` — venue original size 기준 full-fill 판정과 residual 보존

Golden Cherry 전체 테스트는 96개 통과했고, 저장소 전략 계약 검증은 21개 프로젝트 모두
PASS했다. 문제 order를 read-only replay했을 때 `submitted=5.78`, `matched=1.82`,
`full_fill=false`, `terminal_partial=true`로 판정됐다.

최종 Jenkins 상태:

| Job | config | latest verified build | lifecycle/timer |
|---|---|---|---|
| Yellow | `570c1d7f87ad…` | `#50322 SUCCESS`, 54.906s | `active`, `H/5` |
| Orange | `e4b3b2440bed…` | `#55570 SUCCESS`, 19.543s | `active`, `H/5` |

두 최신 DB의 RunAudit도 commit `9ec24bb`, 각 resolved config hash에서 SUCCESS다.
Yellow 로그의 legacy uncertain intent 경고는 같은 token/side만 격리하며 cycle 전체를
중단하지 않는다. 이번에 사용자가 직접 보유한 대형 position/order evidence는 삭제하지
않았다.

## 6. 최종 Golden Cherry sync/verify

### Yellow

- Sync run: `37707ed6839e406bb939eb27cb91f812`
- Plan: `6718cfa7ad72218b`
- Finished: `2026-08-18T16:56:30.749896Z`
- Result: SUCCESS, transferred 3, skipped 2,051, failed 0
- Verify: SUCCESS, checked 5,538, retention skip 0, conflict 0
- Source cutoff: `2026-08-18T16:54:18.319566Z`
- Local DB SHA-256:
  `3afc88018b70bccfe49bddfb93d897b7150594e99c84e4419073ecb0b0193be5`
- Verified DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db`

### Orange

- Sync run: `c008562c958042e08faad7c571d98b18`
- Plan: `b88ec4ddf4ce286f`
- Finished: `2026-08-18T16:56:29.665947Z`
- Result: SUCCESS, transferred 3, skipped 2,054, failed 0
- Verify: SUCCESS, checked 4,373, retention skip 0, conflict 0
- Source cutoff: `2026-08-18T16:55:45.619909Z`
- Local DB SHA-256:
  `8484f254fae6c1b648aa04c45549940f8c2a45d363897ade057357f204fcf9d0`
- Verified DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db`

## 7. Golden Blueberry cumulative volume 5,000

Blueberry의 archive/membership filter에 아래를 추가했다.

```text
POLYBOT_ARCHIVE_MIN_CUMULATIVE_VOLUME=5000
```

Gamma 공식 keyset endpoint는 `volume_num_min`, `liquidity_num_min`과 cursor pagination을
지원한다. 이 필터는 API가 먼저 market membership을 줄인 후 page를 내려주므로 3.3만 건을
모두 받은 뒤 로컬에서 버리는 것과 다르다.
([Gamma keyset markets](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination))

배포 후 완주 sweep 결과:

| 항목 | 이전 | 현재 |
|---|---:|---:|
| page | 약 333 | **63~64** |
| raw/qualified market | 약 33,000 | **6,253~6,310** |
| 목표 | 기준 | **1/5 이하 달성** |

필터는 다음 세 개에 동일하게 적용했다.

- `polybot-eagle`: live +2%p arm
- `polybot-fox`: live +5%p arm
- `polybot-shadow`: 주문 없는 2×2 research grid

`누적 volume 5,000`, `24h volume 10,000`, `liquidity 10,000`은 서로 다른 값이다.

| 값 | 의미 | 사용 시점 |
|---|---|---|
| cumulative volume | 시장 개설 이후 누적 거래량 | Gamma server-side universe membership |
| 24h volume | 최근 24시간 활동량 | 실제 entry activity gate |
| liquidity | 현재 시장의 유동성 metadata | entry market-quality gate |
| CLOB spread/depth | 지금 실제로 체결 가능한 호가 상태 | 주문 직전 execution gate |

이제 first crossing은 “시장 전체 역사”가 아니라 **누적 거래량 5,000 membership에 들어온
뒤 최초 관측 교차**다. 이전 자료와 섞을 수 없는 새 source/config cohort다.

관련 commit:

- `accc271` — Blueberry server volume filter와 공용 Gamma cache 도입
- `32cebbe` — 직전 5분의 검증된 sweep 재사용
- `b1e6410` — shared sweep leader page interval 단축

Blueberry 전체 테스트 355개와 전략 계약 검증을 통과했다.

## 8. `polybot-shadow` 실패와 동기화

최근 실패는 과거 333-page scan의 shared-cache lock 대기 timeout에서 발생했다. 5,000
필터로 producer sweep을 64 page로 줄이고 cache hit을 확인했다.

- refresh leader sample `#3744`: SUCCESS, 64 page/6,310 market, 208.978s
- cache-hit sample `#3745`: SUCCESS, 7.004s
- 최신 refresh `#3752`: SUCCESS, 63 page/6,253 market, cursor-complete, 210.849s,
  RunAudit SUCCESS
- Jenkins config: enabled, `H/5`, `shadow_only`, credential 없음
- config SHA-256:
  `bce54567f13c00c5233bdc2f94bad409392bf43e1bfa270b8d83248b2e9f65c8`

`daily-rsync`가 과거 internal/external workspace 후보를 동시에 발견해 scan이 모호했던 문제는
현재 실제 경로를 Jenkins `customWorkspace`로 명시해 해결했다.

```text
/Users/jongwoopark/.jenkins/workspace/polybot-shadow
```

기존 DB를 이동하거나 삭제하지 않았다. 올바른 source에서 sync run
`ba007dae5ef64acb858a0fd01598cd6e`가 1,943 artifact, 366,575,673 byte를 실패 0으로
동기화했고 verify는 3,759건 모두 SUCCESS다. 최신 확인 DB SHA-256은
`bd41311c488f2fd93b0b65444bdd888581db981418fe369973bbeaefc7d9316d`다.

Eagle과 Fox도 각각 sync/verify를 통과했다.

- Eagle: run `9e85be55daa24a718ac3e035ce1f6cb7`, verify 3,726 SUCCESS
- Fox: run `9d41962d72694bf98af2211459def1b5`, verify 3,725 SUCCESS

## 9. 나머지 10개 job이 오래 걸린 원인과 수정

Red/Cherry/Orange가 빠른 이유는 Gamma server filter가 강해 page 수가 적기 때문이다.
반면 Melon·Quince·Papaya·Queen 계열은 strategy archive를 위해 대략
`liquidity >= 1,000, cumulative volume >= 0`의 넓은 동일 universe를 각 job이 따로
333 page/약 33,221 market씩 조회했다. 10개 job이 같은 public snapshot을 반복해서 받아
각각 12~16분이 걸렸다.

다음 4개 코드베이스에 owner-only `GammaSweepCache`를 연결하고 10개 Jenkins job이 같은
절대 cache 경로를 보게 했다.

```text
/Users/jongwoopark/.cache/polybot/gamma-sweeps-v1
```

cache는 filter identity가 완전히 같고 cursor-complete, membership digest, market set,
qualified count가 일치할 때만 재사용한다. 공유되는 것은 public Gamma 응답뿐이며 각
전략의 DB, archive, first-crossing, 주문 상태는 독립이다.

변경 후 전체 전략 테스트는 Melon 334, Quince 346, Papaya 256, Queen 288개가 각각
통과했고 cache 전용 관측성 테스트 4개도 통과했다. `polybot-observability` 전체 suite는
200개 중 196개 통과, 4개 실패였다. 실패 4개는 현재 날짜 2026-08-19에 만든 audit row를
고정된 과거 `as-of=2026-07-31` 검증에 섞는 기존 date-sensitive fixture이며 이번 cache
경로를 실행하지 않는다. 이를 cache 회귀 통과로 숨겨 표현하지 않는다.

확인한 cache-hit 자연 build 표본:

| Job | 전략 계열 | build | duration |
|---|---|---:|---:|
| `polybot-cat` | Papaya | #4320 | 15.764s |
| `polybot-dog` | Papaya | #4213 | 36.599s |
| `polybot-king` | Queen | #3369 | 16.566s |
| `polybot-queen` | Queen | #3367 | 10.936s |
| `polybot-bear` | Quince | #9015 | 18.559s |
| `polybot-eco` | Quince | #10904 | 21.923s |
| `polybot-tiger` | Quince | #10521 | 38.668s |
| `polybot-fruit` | Melon | #3755 | 43.530s |
| `polybot-wolf` | Melon | #9370 | 21.065s |
| `polybot-lime` | Melon | #10520 | 12.195s |

12개 job 모두 shell fail-fast와 `uv sync --frozen`도 확인했다. Blueberry의 직접 cache-hit
표본은 Eagle `#8929` 8.517s, Fox `#10723` 8.060s였다. Shadow가 새 63-page snapshot을
발행하던 다음 주기에도 Eagle `#8930` 84.414s, Fox `#10724` 78.990s로 둘 다 SUCCESS였다.

### 남은 한계

이 수정은 host 전체의 동일 333-page 네트워크 sweep을 10번에서 1번으로 줄였지만,
새 cache를 만드는 **선두 job 1개** 자체는 여전히 broad universe 전체를 순차 조회한다.
그 cycle에 함께 시작한 follower는 결과를 기다리므로 해당 batch는 길다. 모든 job을 항상
5분 안에 끝내려면 다음 중 하나가 더 필요하다.

실제 refresh batch에서도 Lime `#10518` 791.161s, Dog `#4212` 758.560s, Eco `#10902`
741.824s, Tiger `#10520` 634.984s, Fruit `#3754` 627.615s, Cat `#4319` 465.206s가
모두 SUCCESS로 끝났다. 그 snapshot이 발행된 직후 위 표의 다음 자연 주기들이 14~45초로
끝난 것을 확인했다. 즉 긴 시간은 실패·deadlock이 아니라 현재 broad refresh 비용이고,
중복 제거 효과와 잔여 한계가 둘 다 재현됐다.

1. 각 전략의 archive membership에 server-side cumulative volume/liquidity 하한을 추가하고
   모두 새 cohort로 다시 시작한다.
2. 별도 market snapshot collector를 운영하고 live 전략이 그 완주 snapshot을 읽도록
   architecture를 분리한다.

Papaya·Queen은 현재 24h volume gate가 1,000, Quince는 2,000이라 Blueberry의 cumulative
5,000을 무조건 복제하면 실제 후보 universe를 바꾼다. 이번 작업에서는 여러 live 전략의
실험 정의를 조용히 바꾸지 않고, 먼저 의미가 보존되는 중복 제거까지만 배포했다.

## 10. 평가 시계와 다음 판단

Blueberry는 5,000 filter 배포 시점부터 새 cohort다.

- 24시간: cadence, cursor completeness, cache hit/leader, DB 무결성만 확인
- 7일: candidate → order → confirmed fill funnel과 arm별 표본 수만 확인
- 14일: **2026-09-02 KST 전후** 중간 점검은 가능하지만 수익성 확정은 하지 않음
- 30일: **2026-09-18 KST 전후**, arm당 fee-complete confirmed closed 20건 이상일 때
  +2%p/+5%p 비교
- 30일에도 표본 미달이면 파라미터를 중간 변경하지 말고 기간 연장

Cherry도 lifecycle commit `9ec24bb` 이전/이후을 섞지 않는다. 절대 exit 가설을 검증하려면
현재 live P&L과 섞지 않는 별도 shadow path evidence부터 수집한다.

## 11. 운영 보안 별도 경고

Yellow와 Orange는 여전히 익명 `config.xml` 조회가 가능한 plaintext HTTP Jenkins에 signer
관련 값을 inline으로 둔다. 본 문서와 로그에는 값을 남기지 않았다. 수익성·lifecycle과는
별개로 Credentials Binding, Jenkins 인증, HTTPS 전환이 필요하다.

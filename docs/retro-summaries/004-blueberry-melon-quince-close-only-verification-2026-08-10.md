# 004 — Blueberry·Melon·Quince close-only verification — 2026-08-10

작성일: 2026-08-10

Jenkins 관측: `2026-08-10T14:06Z` 이후

DB source cutoff: `2026-08-10T14:01:28Z`–`14:01:30Z`

대상: Blueberry 2개, Melon 3개, Quince 3개 live job

## 0. 결론

`003`의 재가동 절차 4번과 5번을 확인했다.

1. 8개 수동 build는 모두 commit `e86769afd8f1`, `close_only`, `SUCCESS`였다.
2. 로그에서 기존 `PENDING_BUY` 18건 전부가 exact full BUY fill로 `HOLDING` 승격됐다.
3. 같은 cycle에서 18건 모두 청산 검사를 받았고 신규 BUY는 0건이었다.
4. 8개 DB·bot log·Jenkins console log 재동기화와 verify는 모두 성공했다.
5. 검증된 DB의 최종 합계는 `PENDING_BUY=0`, `HOLDING=18`, `PENDING_SELL=0`이다.

그러나 절차 6번인 `active`/자동 trigger 복귀는 아직 권하지 않는다. 네 개의 실제 시장은
Gamma에서 이미 `closed=true`, `resolved`, YES payout 1로 확인되지만, 봇의
condition-ID 조회가 closed market을 제외해 18건을 `RESOLVED`로 종결하지 못했다.

> 후속 조치: 이 조회 결함은
> [005-blueberry-melon-quince-closed-market-resolution-fix-2026-08-10.md](005-blueberry-melon-quince-closed-market-resolution-fix-2026-08-10.md)에서
> 세 전략 모두 수정했다. 본 문서의 DB·Jenkins 수치는 수정 배포 전 snapshot으로 유지한다.

## 1. Jenkins build와 lifecycle 로그

모든 job의 `TimerTrigger`는 비어 있었고, latest build는 운영자의 수동 실행이었다.
`disabled=false`라 수동 build는 가능하지만 자동 실행은 중지된 상태다.

| Jenkins job | Runtime | Build | Result | Pending checked → activated | Holdings checked | Sold / resolved |
|---|---|---:|---|---:|---:|---:|
| `polybot-eagle` | `blueberry-live-a-2pp` | 6774 | SUCCESS | 3 → 3 | 3 | 0 / 0 |
| `polybot-fox` | `blueberry-live-b-5pp` | 8569 | SUCCESS | 1 → 1 | 1 | 0 / 0 |
| `polybot-wolf` | `polybot-melon-low` | 7198 | SUCCESS | 1 → 1 | 1 | 0 / 0 |
| `polybot-lime` | `polybot-melon-mid` | 8350 | SUCCESS | 1 → 1 | 1 | 0 / 0 |
| `polybot-fruit` | `polybot-melon-high` | 1584 | SUCCESS | 0 → 0 | 0 | 0 / 0 |
| `polybot-bear` | `polybot-quince-passive` | 6847 | SUCCESS | 4 → 4 | 4 | 0 / 0 |
| `polybot-eco` | `polybot-quince-nearest` | 8737 | SUCCESS | 4 → 4 | 4 | 0 / 0 |
| `polybot-tiger` | `polybot-quince-cross` | 8355 | SUCCESS | 4 → 4 | 4 | 0 / 0 |
| **합계** |  |  | **8/8 SUCCESS** | **18 → 18** | **18** | **0 / 0** |

각 로그에는 `exact full BUY fill로 HOLDING 활성화`가 기대 수만큼 있었고, cycle summary의
`pending_buys_activated`와 일치했다. `close_only: 신규 진입을 건너뜁니다`와 `bought=0`도
8개 모두 확인했다.

## 2. daily-rsync와 무결성

8개 job만 개별 `sync-job`으로 다시 가져왔다. simulation·Date·그 밖의 job은 범위에
포함하지 않았다.

| Job | Sync run | Finished UTC | DB SHA-256 | Verify checked | Result |
|---|---|---|---|---:|---|
| Eagle | `8fcc3be374f6473484ad6325264949ac` | 14:08:04 | `c0c00e6731a48492773addf1369eb4191805cb05561f98ec43f9c7471cc29972` | 1,568 | SUCCESS |
| Fox | `23777184d9cf49559b5f9a846d683c64` | 14:09:27 | `331ca721e2511b14af138cb6c39785533b961cafeaa8fae800ceca7166d261c8` | 1,569 | SUCCESS |
| Wolf | `3166d09a18444e9f8db35fca99d1474d` | 14:10:26 | `0024f0266df3c4f746ad87c3c8b1269fc7c906183e729b44c42c2df30d2df339` | 1,053 | SUCCESS |
| Lime | `5de4585ce3974f80be3f5c64617a4dfc` | 14:11:36 | `2f364a9b1f879e20d58303377151380aae0ddafc5e958b5421c65a79aa7a8f0b` | 1,589 | SUCCESS |
| Fruit | `760b9bb69584468a97a49bc094ea186a` | 14:12:40 | `ede5cba99d4186d2b03c46d98ef066852dad01eaa913780a4acd4e3c8e18ec2c` | 1,590 | SUCCESS |
| Bear | `111bfbae43c3400bbf4a35198630a7d9` | 14:13:29 | `9ba230be09f24df4fae6866c35ceb54f887e6996bde6f8c9451a88bc0f87b2db` | 1,093 | SUCCESS |
| Eco | `c5f7e251993d477b9e1f80bca7edbd8a` | 14:14:56 | `3a52868b6205b6eb74cb1625822383dd8a99de0aead19b5ceedd6597d90c10f5` | 1,628 | SUCCESS |
| Tiger | `0da158b3519740de9685b92271bd52bf` | 14:15:46 | `bb7052cf870e7416dd217b038349436a32d05b61ffc4c285819863f434314fc2` | 1,088 | SUCCESS |

합계 185개 artifact를 새로 전송했고 약 1.23GB를 기록했다. verify는 11,178개를 검사했으며
실패 0, retention skip 0, open provenance conflict 0이었다. 모든 match에서
`latest_sync_attempt=SUCCESS`, `latest_successful_sync=SUCCESS`, `analysis_ready=true`였다.

## 3. 검증된 DB 상태

| Arm | Total | PENDING_BUY | HOLDING | PENDING_SELL | Completed / resolved | Exact size+VWAP holding |
|---|---:|---:|---:|---:|---:|---:|
| Blueberry A | 3 | 0 | 3 | 0 | 0 | 3 |
| Blueberry B | 1 | 0 | 1 | 0 | 0 | 1 |
| Melon low | 1 | 0 | 1 | 0 | 0 | 1 |
| Melon mid | 1 | 0 | 1 | 0 | 0 | 1 |
| Melon high | 0 | 0 | 0 | 0 | 0 | 0 |
| Quince passive | 4 | 0 | 4 | 0 | 0 | 4 |
| Quince nearest | 4 | 0 | 4 | 0 | 0 | 4 |
| Quince cross | 4 | 0 | 4 | 0 | 0 | 4 |
| **합계** | **18** | **0** | **18** | **0** | **0** | **18** |

따라서 수량 반올림과 `PENDING_BUY` lifecycle 수정은 운영 DB에서도 성공했다.

## 4. 새로 발견한 closed-market resolution 조회 결함

18개 포지션은 네 개의 unique YES market에 걸쳐 있다. 최신 public Gamma 개별 market
응답은 네 개 모두 다음 계약을 만족한다.

- `closed=true`
- `acceptingOrders=false`
- `outcomePrices=["1", "0"]`
- `umaResolutionStatus="resolved"`

market ID는 `3202424`, `3223294`, `3241076`, `3290728`이다. 앞의 BTC 세 시장은 수동
build 전에 이미 resolved였다. 마지막 ETH 시장은 Gamma `updatedAt`이
`2026-08-10T14:16:17Z`라 이번 build cutoff인 `14:01Z` 뒤에 해결됐다.

현재 `GammaClient.get_market_by_condition_id()`는 다음 query만 보낸다.

```text
/markets?condition_ids=<condition-id>&limit=1
```

Gamma의 현재 동작에서는 이 query가 closed market에 빈 배열을 반환한다. 같은 query에
`closed=true`를 넣으면 해당 market이 반환되고, `/markets/<market-id>`도 정상이다. 그래서
midpoint가 사라진 뒤의 fallback이 `market=None`으로 끝나며 로그에
`closed+final payout 증거 없음`을 남겼다.

이는 수량 반올림 수정과 별개의 lifecycle 결함이다. 현재 18건의 payout=1 기준 gross
settlement 가정은 합계 **+$8.8293**이지만 BUY fee 증거가 0/18이고 실제 redeem도 추적하지
않으므로 realized/net P&L로 기록하면 안 된다.

## 5. 다음 결정

- 4번과 5번은 **통과**했다.
- 8개 job은 지금처럼 timer 없이 `close_only`로 둔다.
- `active`와 자동 trigger 복귀 전에 closed condition 조회를 `closed=true` fallback 또는
  known market ID 조회로 복구하고, 네 시장이 18건 모두 `RESOLVED`로 전환되는지 다시
  한 번의 `close_only` build로 확인해야 한다.
- 이 문서는 진단만 기록한다. closed-market 조회 코드는 이번 확인 작업에서 변경하지 않았다.

# 023 — Yellow·Orange active 복귀와 MAKER fee 누락 복구

- 작업일: 2026-08-14 KST
- 대상: `polybot-yellow`, `polybot-orange` (`golden-cherry/default`, live)
- 결과: 두 잡 모두 `active + H/5 * * * *`로 복귀했고 자연 build와 신규 주문 대사가
  성공했다.
- 제외: `polybot-red`는 요청대로 timer 없는 `close_only` 대기 상태를 유지했다.

## 최종 판정

| Job | 최종 Jenkins | DB/wallet gate | 자연/최종 검증 build |
|---|---|---|---|
| `polybot-yellow` | lifecycle `active`, `H/5` | Lee 수동 position 2건 guard 등록, Erdoğan 없음, 복귀 전 `PENDING_BUY=0`, `PENDING_SELL=0` | 첫 자연 `#49115`, 최신 확인 `#49117` SUCCESS |
| `polybot-orange` | lifecycle `active`, `H/5` | 기존 MAKER SELL `COMPLETED`, 복귀 전과 최신 확인 모두 `PENDING_SELL=0`, 해당 BO3 wallet position 없음 | 첫 자연 `#54364`, 최신 확인 `#54365` SUCCESS |

최종 config SHA-256은 Yellow
`b74293dcb2d0ef3c5ca0230c8d18e39b178eb8c0bcf2e2832be28e46831d22cf`, Orange
`d768bd67d06e1dc2b07b3f9685d54b3e4b9d2da57c5ad34962c0e61efa653fe4`다.
두 config 모두 `concurrentBuild=false`를 유지한다.

## 사전 안전 조치와 백업

코드·DB를 바꾸기 전에 두 TimerTrigger를 제거하고 `close_only`를 유지했다. 원격 DB는
SQLite online backup으로 다음 위치에 보존했다.

```text
/Users/jongwoopark/polybot-db-backups/yellow-orange-reactivation-20260814T1125Z/
```

두 backup 모두 `PRAGMA quick_check=ok`다. backup SHA-256은 Yellow
`fd52f01a67991e81a957fb8553d5e8bdfaee165e3633d25225510fea09165672`, Orange
`3e7a0473ee908d041bba2a810482bf7552b78c8e4ac9e62ba835b95650d39eef`다.

## Yellow guard와 수동 청산 확인

운영자가 Erdoğan `No` position을 수동 청산한 뒤 공개 position API를 재조회했다.
Erdoğan position은 사라졌고 다음 두 `No` position만 운영자 수동 보유분으로 남았다.

- `Lee Jae-myung arrested before 2027?`
- `Lee Jae-myung out as president of South Korea in 2026?`

두 condition은 Yellow DB의 `skipped_markets`에
`operator_wallet_guard_preexisting_untracked_position` reason으로 원자적으로 등록했다.
두 condition에 기존 bot trade가 없는 것을 먼저 확인했고, 등록 후 DB
`quick_check=ok`와 row 2개를 다시 검증했다.

close-only `#49112`는 commit `4395ee8`로 성공했고 open 상태는
`PENDING_BUY=0`, `HOLDING=1`, `PENDING_SELL=0`이었다. 이후 active `#49113`에서 새 $5 BUY
하나를 체결 전 `PENDING_BUY`로 기록했고, 대사 전용 `#49114`에서 exact confirmed fill을
확인해 `HOLDING`으로 전이했다. 첫 자연 timer `#49115`도 active cycle로 성공했다.

이어진 자연 timer `#49116`은 신규 BUY를 `HOLDING`으로 대사한 뒤 실제 take-profit SELL을
제출했고, `#49117`은 그 SELL의 exact confirmed fill을 확인해 `COMPLETED`로 전이했다.
실제 전이 증거는 `MATCHED`, confirmed size `6.446362`, maker role, fee rate/amount NULL,
known fee `$0`, `needs_reconciliation=0`이다. 이는 이번 MAKER fee 누락 규칙이 신규 실거래의
BUY→HOLDING→SELL→COMPLETED 전 구간에서 작동한다는 end-to-end 확인이다.

`#49117` 같은 cycle에서 다음 take-profit SELL과 BUY도 새로 제출됐으므로 최신 원격 DB는
`COMPLETED=975`, `HOLDING=1`, `PENDING_BUY=1`, `PENDING_SELL=1`, `UNFILLED=399`다. 이
`PENDING_SELL=1`은 과거 고착분이 아니라 방금 생성된 in-flight 주문이며 다음 H/5 대사
대상이다.

## Orange MAKER fee metadata 누락 수정

고착됐던 SELL은 요청·matched·confirmed size가 모두 `5.68`, terminal `MATCHED`, exact fill
합계도 `5.68`이었다. 두 fill의 `liquidity_role`은 `MAKER`였지만
`fee_rate_bps`와 `fee_amount_usdc`가 모두 NULL이라 기존 reader가 fee evidence gap으로
판정했다. 공개 wallet에는 해당 BO3 position이 없어 실제 전량 매도와도 일치했다.

[Polymarket fee 정책](https://docs.polymarket.com/trading/fees)은 platform maker fee가 0임을
명시하고, Golden Cherry에는 builder-fee 주문 경로가 없다. 따라서 commit `4395ee8`은 다음
좁은 규칙만 추가했다.

- explicit `fee_rate_bps=0`: 기존과 같이 known zero
- `CONFIRMED + liquidity_role=MAKER + rate NULL + amount NULL`: known zero
- `TAKER`, role 불명, builder-fee 가능 경로, 0이 아닌 rate의 amount 누락: 계속 fail closed

회귀 테스트는 MAKER NULL/NULL 성공, TAKER·role 불명 실패, nonzero rate 실패를 각각
고정한다. Golden Cherry 전체 `89 passed`, fill reader 대상 `11 passed`, 모노레포
`strategy contract: PASS (20 strategies)`를 통과했다. 동기화 DB 사본에 실제 Orange 주문을
재생한 결과도 `confirmed`, `full=true`, `fee_complete=true`, known fee `$0`이었다.

close-only `#54361`은 해당 row를 `COMPLETED`로 전이했고 다음을 기록했다.

```text
sell_confirmed_size = 5.68
sell_confirmed_vwap = 0.98
sell_confirmed_fee_usdc = 0.0
pnl_basis = exact_reconciled_buy_sell_confirmed_fills_net_known_fees
```

복귀 전 DB 합계는 `COMPLETED=89`, `HOLDING=4`, `UNFILLED=60`,
`PENDING_BUY=0`, `PENDING_SELL=0`이었다. active `#54362`가 새 BUY 4건을 pending으로
기록했고, 대사 전용 `#54363`에서 3건을 `HOLDING`으로 전이했다. 첫 자연 timer
`#54364`는 이전 남은 BUY를 `HOLDING`으로 전이하고 새 live BUY 한 건만 정상
`PENDING_BUY`로 남긴 채 `PENDING_SELL=0`으로 성공했다. 다음 자연 timer `#54365`도
SUCCESS였고 최신 원격 DB는 `COMPLETED=89`, `HOLDING=8`, `PENDING_BUY=2`,
`PENDING_SELL=0`, `UNFILLED=60`이다.

## Daily Rsync evidence

active 전환 gate에 사용한 post-guard/post-fix 동기화는 다음과 같다.

| Job | Sync run | Source cutoff (UTC) | Local DB SHA-256 | Verify |
|---|---|---|---|---|
| Yellow | `dcc1fdaba0d248438d0c2e014e2f68f1` | `2026-08-14T11:29:50.221252Z` | `e4e68c2a0e5fae21385366a0d5ca3045820f8b7838415fd61ba1119fcbc0bfe1` | SUCCESS, 4,324 checked |
| Orange | `2ba3fe044d14413f8e37560a080a6ebb` | `2026-08-14T11:29:00.830438Z` | `c842951eb72f2a2f913f1813cc1772575298800298173263790f5bb137e8fa37` | SUCCESS, 3,160 checked |

두 sync 모두 failed 0, retention skip 0, open artifact conflict 0이다. canonical DB는 각각
다음 절대 경로다.

```text
/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db
/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db
```

active 자연 build는 이 source cutoff 이후이므로 Jenkins 원본 redacted console과 원격 DB
read-only 조회로 별도 검증했다. 작업 중 로컬 여유 공간이 38 GiB에서 16 GiB까지 내려가
APFS 사용률이 99%가 됐고, 기본 `daily-rsync` 50 GiB safety floor가 후속 pull을 차단했다.
복귀 gate에 필요한 약 112 MiB는 일회성 임시 config로 실제 전송량보다 최소 수 GiB를
남기며 동기화했지만,
자연 build 이후 추가 pull은 안전선을 더 낮추지 않고 중단했다. 원래
`config.local.toml`의 50 GiB 설정은 변경하지 않았다.

다음 `daily-rsync` 작업 전에는 로컬 여유 공간을 최소 50 GiB 이상, 운영 여유를 포함하면
60 GiB 이상으로 회복해야 한다.

## 남은 알려진 한계

- Yellow의 과거 intent 오류 16건과 Orange 1건은 7월 legacy closed row에 국소 격리돼 있고
  current open token과 겹치지 않는다. 현재 cycle 전체를 막지는 않지만 historical fill
  coverage 한계로 계속 분리한다.
- natural active cycle이 만든 `PENDING_BUY`와 새 `PENDING_SELL`은 체결 증거가 나오기 전
  상태를 정직하게 기록한 것이다. 다음 H/5 cycle이 full fill을 `HOLDING`으로 전이하거나,
  exact zero-fill이 30분 TTL을 넘으면 authoritative cancellation 후 `UNFILLED`로 종결한다.
- 이 작업은 active 복귀와 lifecycle 정확성만 판정했다. 과거 수익성이나 진입 파라미터는
  변경하지 않았다.

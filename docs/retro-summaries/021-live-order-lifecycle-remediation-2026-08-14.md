# 021 — live order lifecycle 긴급 복구와 재가동 게이트

- 작업일: 2026-08-14 KST
- 출발점: `020-live-account-fleet-audit-2026-08-14.md`의 다섯 긴급 항목
- 원칙: 실거래 코드 배포 전 live timer 중지, remote SQLite online backup, clean build 금지,
  공개 wallet·CLOB exact evidence 기반 mutation, `close_only` 검증 후 제한적 timer 복원
- 성과 판단 범위: 이 문서는 운영 복구 보고서다. 수익성·파라미터 최적화는 판단하지 않는다.

## 결론

즉시 신규 손실을 만들 수 있던 blocker는 차단했다. Cherry, Yellow, Orange는 신규 BUY가
없는 `close_only` 상태로 5분마다 기존 노출만 관리한다. Red는 `close_only`이면서 timer도
없다. Red의 invalid-token 반복 청산은 검증 build에서 재발하지 않았고, Orange의 가짜
$44,600 open notional과 modern bot의 zero-fee `PENDING_SELL` 고착도 해소했다.

다만 **과거 원장 증거가 복구된 것은 아니다.** Yellow·Cherry·Red에는 오래된 CLOB intent
대사 오류가 남고, Red·Yellow·Orange 계좌에는 current DB에 매핑되지 않은 wallet position이
있다. 이들은 어떤 전략의 포지션인지 또는 보유/청산할지 운영자 결정을 받기 전 자동으로
매도하지 않았다. 따라서 세 close-only job을 지금 active로 되돌리거나, legacy 성과를
근거로 파라미터를 조정하면 안 된다.

## 최종 운영 상태

| Jenkins job/계열 | 최종 mode와 timer | 검증된 DB 상태 | 이번 조치 | 남은 위험 |
|---|---|---|---|---|
| `polybot-red` | `close_only`, timer 없음 | `COMPLETED 592 / EXPIRED 426 / UNFILLED 184 / open 0` | invalid-token 행 1개를 wallet/CLOB 증거로 `UNFILLED` 종결. 수동 build `#54390`에서 보유·BUY·SELL 0, invalid-token 재발 0 | DB 밖 account position 약 70개와 historical intent 오류 16건 |
| `polybot-yellow` | `close_only`, `H/5` | `COMPLETED 974 / HOLDING 1($5) / UNFILLED 399`, pending 0 | 지갑에 없는 open 4행 중 3개 `UNFILLED`, off-ledger terminal 1개 `COMPLETED`; exact lifecycle 배포 | legacy exact coverage 81%, 대사 오류 16건, DB 밖 wallet position 3개 |
| `polybot-cherry` | `close_only`, `H/5` | `COMPLETED 240 / EXPIRED 60 / HOLDING 5 / UNFILLED 69`, pending 0 | 신규 BUY 차단, strict gap intent 6건 operator-gap 격리, wallet에 없는 7행 종결, 막혔던 청산 2건 처리 | 최신 cycle의 historical reconciliation 오류 21/33과 expired redeem 60건 |
| `polybot-orange` | `close_only`, `H/5` | `COMPLETED 88 / HOLDING 5($25) / UNFILLED 60`, pending 0 | stale open 63행을 49 `UNFILLED` + 14 `COMPLETED`로 대사. 재발한 zero-fill ghost BUY 2건도 새 TTL 경로로 종결 | current DB 밖 guarded wallet position 1개와 historical ledger 오류 1건 |
| Blueberry·Melon·Papaya | 원래 live timer 복원 | Eagle·Dog·Fruit·Lime·Wolf의 `PENDING_SELL=0`; 확인된 terminal trade는 `COMPLETED` | explicit `fee_rate_bps=0`을 known zero fee로 인정하고 exact fill을 재대사 | Cat과 다른 Papaya exit는 실제 첫 사례 때 재확인 필요 |
| Queen·Quince | 원래 live timer 복원 | 현재 pending sell 0 | 같은 latent zero-fee fix 배포; Papaya·Queen은 venue-quantized MATCHED도 exact confirmed 합과 같을 때만 허용 | 아직 해당 코드 경로의 live exit 표본이 적음 |

`close_only` job의 timer는 신규 매수를 만들기 위한 것이 아니다. 이미 존재하는 HOLDING의
가격·해결 상태를 확인하고, exact fill·SELL lifecycle을 계속 대사하기 위해 유지한다. Red는
current DB open이 0이고 account-wide disposition이 미정이므로 timer를 복원하지 않았다.

## 코드 수정

### Explicit zero fee

Blueberry, Melon, Papaya, Queen, Quince의 fill reader가 `fee_amount_usdc`만 보던 결함을
수정했다. 이제 exact fill의 `fee_rate_bps=0`이 명시되어 있고 fee amount가 비어 있으면
수수료 0으로 확정한다. 비영(0이 아닌) fee rate인데 amount가 없거나 fill coverage가
불완전하면 이전처럼 fail closed한다.

### Venue-quantized MATCHED

Papaya와 Queen은 venue가 MATCHED size를 소수점 단위로 정규화한 경우, exact confirmed fill
합과 MATCHED amount가 정확히 같고 reconciliation이 terminal일 때만 완료로 인정한다.
단순 근사 비교나 요청 수량 기준 반올림으로 상태를 넘기지 않는다.

### Golden Cherry exact lifecycle

Golden Cherry는 accepted 주문을 체결로 보던 legacy 동작을 제거했다.

- BUY accepted → `PENDING_BUY`; exact confirmed full fill만 `HOLDING`
- SELL accepted → `PENDING_SELL`; exact confirmed full fill + known fee만 `COMPLETED`
- terminal zero-fill BUY → `UNFILLED`; terminal zero-fill SELL → `HOLDING`
- exact LIVE + matched 0 BUY는 기본 30분 TTL 후 authoritative cancel이 확인돼야 `UNFILLED`
- legacy HOLDING인데 exact LIVE BUY fill이 부족한 행은 좁은 migration으로 `PENDING_BUY`
- BUY 원가 evidence가 없는 legacy row는 exact SELL로 운영상 닫을 수 있지만 P&L은 `NULL`
- Gamma가 명시적으로 closed/inactive/non-accepting이면 dead-book midpoint로 SELL하지 않음

추가된 lifecycle 필드는 confirmed size/VWAP/fee, SELL matched 시각, pending remainder,
P&L basis다. migration은 idempotent `ALTER TABLE`이며 simulation의 가상 체결 의미는
바꾸지 않았다.

## DB·Jenkins 조치 순서

1. live 16개 job의 timer를 중지하고 실행 중 build가 끝난 뒤 조치를 시작했다.
2. 9개 mutation 대상 DB를 SQLite online backup하고 SHA-256 manifest와 `quick_check`를
   검증했다. backup은 Mac Mini의 workspace 밖 durable 경로에 보존한다.
3. wallet·CLOB open order·exact fill evidence를 dry-run 대사하고, 확인 토큰이 일치하는
   stale row만 공식 reconciliation 도구로 종결했다. DB를 clean하지 않았다.
4. zero-fee·quantization fix를 배포하고 modern affected job을 한 번씩 `close_only`로
   실행해 `PENDING_SELL → COMPLETED`를 확인했다.
5. Golden Cherry fix를 배포했다. Orange의 실제 DB online-backup 복사본으로 migration과
   TTL dry-run을 먼저 검증한 뒤 remote에 적용했다.
6. Cherry·Yellow·Orange는 `close_only H/5`, Red는 `close_only` + timer 없음으로 고정했다.
   나머지 live job은 기존 active timer로 복원했다.
7. 수동 build와 최소 한 번의 자연 timer build, redacted console의 `RUN_AUDIT SUCCESS`,
   DB 재동기화·verify를 교차 확인했다. Red는 DB mutation 뒤 별도 수동 build를 추가해
   invalid-token 재발이 없음을 확인했다.

## Evidence cutoff

아래는 canonical `daily-rsync` DB이며 local/remote SHA가 일치하고 verify가 SUCCESS인
snapshot이다. 시각은 UTC다.

| Job | Source cutoff | Sync finish | Verified DB | SHA-256 |
|---|---|---|---|---|
| Red | `2026-08-14T10:25:47Z` | `10:27:34Z` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-red/strategies/golden-date/runtime/default/databases/latest/trades.db` | `1f19a7437a2674a7388a05c43e9addf608dcbbd0b16ee898c2a166562b5b5277` |
| Yellow | `2026-08-14T10:18:40Z` | `10:19:31Z` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db` | `25acb7850a36a6246d0a4fb4b1827ecbb4d82a32d2ccce7d16f2523f85155936` |
| Cherry | `2026-08-14T10:30:25Z` | `10:30:42Z` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-cherry/strategies/golden-elderberry/runtime/default/databases/latest/trades.db` | `cc348a73bbf9f7c492068c13f28d8cc6488b6a46c19f1bb767ae93a02c3eb416` |
| Orange | `2026-08-14T10:25:24Z` | `10:30:06Z` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db` | `946de7a972dfbcccc4b3adfdf67305843c55c660e118616899f2f91cecd2705b` |

Verify 결과는 Red 3,082 / Yellow 4,312 / Cherry 7,359 / Orange 3,149 artifact checked,
failed 0, retention skip 0, conflict 0이다. remediation DB 복사본 strict audit은
`CRITICAL 0 / HIGH 8 / MEDIUM 5`다. 남은 HIGH는 주로 historical failed-run/archive 및
legacy fill evidence gap이며 현재 zero-fee completion 결함이 아니다. 이 gap을 추정값으로
채우지 않는다.

## 운영자 액션 플랜

### P0 — 지금 유지할 안전 상태

추가 작업 없이도 즉시 지켜야 하는 상태다.

1. Red는 timer 없음 + `close_only`를 유지한다.
2. Yellow·Cherry·Orange는 `close_only H/5`를 유지한다.
3. 네 job 모두 clean build, DB 삭제, 과거 row 일괄 `COMPLETED` 변경을 금지한다.
4. historical HIGH가 없어질 때까지 이 구간으로 수익성·파라미터를 평가하지 않는다.

### P1 — 운영자 결정을 받아야 하는 계좌 단위 position

자동 처리하지 않은 이유는 매도가 실제 자산 처분이기 때문이다.

1. **Red:** current Date DB 밖 position 약 70개를 `현재 전략 보유 / 과거 전략 잔여 /
   만기·redeem / 즉시 청산`으로 분류한다. 전량 wind-down을 원하면 account-wide dry-run과
   예상 체결가·slippage를 먼저 출력하고 별도 확인 토큰으로 실행한다.
2. **Yellow:** current DB에 없는 wallet position 3개의 생성 epoch를 과거
   Golden Banana/Cherry DB와 confirmed fill로 역추적한다. 현재 Golden Cherry가 관리해야
   한다면 명시적으로 adopt하고, 아니면 account-wide wind-down 대상으로 분리한다.
3. **Orange:** DB 밖 guarded position 1개는 약 $550 규모라 자동 무시하거나 매도하지
   않는다. `adopt / hold outside bot / close` 중 하나를 정한다.
4. 결정을 주면 각 계좌마다 `dry-run → online backup → exact 확인 토큰 → 1회 실행 →
   wallet/DB 재대사 → daily-rsync verify` 순서로 처리한다.

### P1 — active 재가동 gate

Yellow·Cherry·Orange를 한꺼번에 active로 바꾸지 않는다. 각 job별로 다음 조건을 모두
만족할 때 한 개씩 재가동한다.

1. current wallet position과 DB open row가 일대일로 매핑되거나, 예외 position의
   disposition이 문서화되어 있다.
2. `PENDING_BUY=0`, `PENDING_SELL=0`이고 current token/side를 막는 unresolved intent가 0이다.
3. `close_only` 자연 build 3회 연속 `SUCCESS`, 신규 BUY 0, 같은 lifecycle 오류 재발 0이다.
4. active 전환 시 기존 파라미터를 동시에 바꾸지 않고 새 cohort 경계를 기록한다.
5. 첫 active build 뒤 15분 동안 3회 자연 실행을 확인하고 즉시 다시 동기화한다. accepted
   주문은 HOLDING이 아니라 PENDING으로 기록되고 exact fill 뒤에만 승격되어야 한다.
6. 위 gate가 깨지면 timer를 먼저 끄고 `close_only`로 되돌린다. clean은 하지 않는다.

### P2 — 24시간 후 확인

다음 요청으로 재점검할 수 있다.

> Red·Yellow·Cherry·Orange와 zero-fee 수리 대상 job을 daily-rsync로 다시 동기화해서,
> remediation 이후 24시간의 pending 상태, exact fill/fee coverage, wallet↔DB mismatch,
> unresolved current intent, 자연 build cadence를 확인해줘. 수익성이나 파라미터는 아직
> 평가하지 말고 021의 active 재가동 gate만 판정해줘.

### P2 — Jenkins 보안

16개 live config 모두 `set +x`를 secret 참조 전에 두어 xtrace 노출은 막았다. 그러나
private credential이 job config에 inline이고, anonymous 사용자가 `config.xml`을 읽을 수
있으며 Jenkins가 HTTP다. 이는 LAN이어도 별도 위험이다.

1. Mac Mini Jenkins에 Credentials Binding이 없으면 설치한다.
2. private key를 Jenkins credential로 새로 만들고 Freestyle의 secret-text binding으로
   환경 변수에 주입한다. inline `export`는 제거한다.
3. 과거 console/config 접근 가능성을 고려해 private key rotation을 계획한다.
4. anonymous `Extended Read/Configure` 권한을 제거하고 로그인과 least privilege를 켠다.
5. 가능하면 Caddy/Nginx 등의 reverse proxy로 HTTPS를 적용한다.

이 보안 migration은 credential ID와 실제 키 교체가 필요하므로 이번 거래 복구와 묶어
자동 수행하지 않았다.

## 검증과 변경 이력

- `c9e1a07` — explicit zero-fee fill reconciliation
- `a4672fd` — venue-quantized matched fill 판정
- `c2cc639` — Golden Cherry exact BUY/SELL lifecycle와 TTL
- zero-fee 대상 5개 프로젝트 회귀 suite: **1,574 passed**
- Golden Cherry: **86 passed**, warning 0
- strategy contract verifier: **20/20 PASS**
- observability suite: **193 passed**; 현재 시각과 고정된 과거 audit end가 충돌하는 기존
  time-window test 4개는 실패했으며 observability source는 변경하지 않았다.
- controlled/natural Jenkins build는 대상 job에서 SUCCESS, `RUN_AUDIT SUCCESS`
- 민감한 key/address/token/order ID는 이 문서와 commit에 포함하지 않았다.

# 027 — Golden Cherry Orange·Yellow 후속 parameter/lifecycle 회고 — 2026-08-18

> **2026-08-19 정정:** 아래의 `entry > 0.90909` 15건이 "전부 손실"이라는 표현은
> 15개 outcome이 모두 0으로 해결됐다는 뜻이 아니라, 봇의 **해결 전 매도 손익이 15건
> 모두 음수**였다는 뜻이다. 공식 Gamma closed-market 결과를 다시 조회한 결과 매수 outcome은
> 11건이 1, 4건이 0으로 해결됐다. 해석·lifecycle 복구·재배포 결과는
> [028 회고](028-golden-cherry-blueberry-runtime-remediation-2026-08-19.md)가 이 문서를
> 정정·보완한다.

작성일: 2026-08-18 KST

대상:

- `polybot-orange` → `golden-cherry/data/default/trades.db`
- `polybot-yellow` → `golden-cherry/data/default/trades.db`

## 0. 결론

```text
Sync / verify: Orange·Yellow 모두 SUCCESS
Orange 0.95 상단 확대: 유지 근거 없음, 현재 exit 구조에서는 REJECT
수익성 최종 판정: NOT READY
가장 긴급한 문제: shared partial-fill lifecycle 결함이 2026-08-16 지적 후에도 미수정
권고: 두 job close_only → lifecycle 수정·대사 → 새 cohort에서 한 축만 비교
이번 작업의 code/Jenkins 변경: 없음 (분석 요청에 따른 read-only 회고)
```

Orange의 `POLYBOT_SELL_THRESHOLD=0.95`는 매도 가격이 아니라 **진입 가격 상한**이다.
현재 take-profit은 매수가 대비 `+10%`이므로 진입가가
`1 / 1.10 = 0.90909…`보다 높으면 가격이 1.00이어도 take-profit에 도달할 수 없다.
최신 동일 commit cohort에서 이 고가 구간의 exact-closed 15건은 전부 stop-loss 또는
trailing-stop으로 끝났고 take-profit은 0건이었다.

Orange와 Yellow는 상단 하나만 다른 A/B가 아니다. 현재 주요 차이는 다음 네 축 이상이다.

| 항목 | Orange | Yellow |
|---|---:|---:|
| 진입 확률 | 0.85–0.95 | 0.75–0.88 |
| 최소 유동성 | $30,000 | $125,000 |
| max positions | 100 | 10 |
| cycle 신규 상한 | 5 | 1 |

따라서 두 계정의 손익 차이를 `0.95` 하나의 효과라고 해석할 수 없다. 최신 로그에서
Orange는 이미 open 100개로 포지션 상한에 꽉 차 후보 34개가 있어도 신규 BUY를 하지
못했다. 넓힌 universe가 더 좋은 표본을 만든 것이 아니라 노출을 먼저 10배 채운 뒤
scanner를 막는 구조가 됐다.

## 1. 동기화와 evidence cutoff

작업 시작 시 MacBook 여유 공간은 353 GiB, Mac mini 내부 여유는 약 70.1 GiB로
`daily-rsync`의 50 GiB safety floor를 통과했다.

| 항목 | Orange | Yellow |
|---|---|---|
| Scan current strategy/runtime | `golden-cherry/default` | `golden-cherry/default` |
| Plan | `8bed7cb9bfdbaa56` | `60ca5901389ff8a4` |
| Sync run | `86a4849626c04b749028bf161fe41b77` | `f0685cb692e84c4c932bb82ef1624cb2` |
| Result | SUCCESS · 574 transfer · 실패 0 | SUCCESS · 575 transfer · 실패 0 |
| Finished UTC | `2026-08-18T10:22:37.629209Z` | `2026-08-18T10:25:48.034482Z` |
| Verify | SUCCESS · 4,302 checked | SUCCESS · 5,466 checked |
| retention skip / conflict / failure | 0 / 0 / 0 | 0 / 0 / 0 |

### Orange evidence

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-orange/golden-cherry/data/default/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- local/remote SHA-256:
  `9b40560cb47080fde9b265012ba4da2c0ed4cfdf355c7aec3c864d2ec739de68`
- DB `synced_at`: `2026-08-18T10:19:59.291685Z`
- Source cutoff: `2026-08-18T10:17:34.898605Z`
- Bot log: 69개, Jenkins console: 4,227개 available

### Yellow evidence

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-yellow/golden-cherry/data/default/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- local/remote SHA-256:
  `72138f85177c5966368ef49ff546ee91027535b31f58fc968ad4e1c610847237`
- DB `synced_at`: `2026-08-18T10:22:56.506846Z`
- Source cutoff: `2026-08-18T10:18:47.830199Z`
- Bot log: 73개, Jenkins console: 5,387개 available

DB 이후에도 5분 timer가 계속 실행되므로 보고서 수치의 권위 있는 cutoff는 위 source
cutoff다. 별도로 10:33:58Z에 Jenkins를 재조회했을 때 최신 완료 build Orange `#55502`,
Yellow `#50254`는 모두 SUCCESS였다.

## 2. 실제 Jenkins 구성

두 job 모두 enabled, non-concurrent, `H/5 * * * *`, live/active이며 clean extension이나
shell clean 명령은 없다. config SHA-256은 Orange
`d768bd67d06e1dc2b07b3f9685d54b3e4b9d2da57c5ad34962c0e61efa653fe4`, Yellow
`b74293dcb2d0ef3c5ca0230c8d18e39b178eb8c0bcf2e2832be28e46831d22cf`로
2026-08-16 회고와 같다.

공통 동작은 `$5`, `(0h,120h]`, `exit_hours=0`, stop `-8%`, TP `+10%`, trailing `5%`,
sports in-play 허용이다. Orange의 Jenkins description은 아직 `24h~720h / exit 12h`로
적혀 있어 실제 shell/DB resolved config와 다르다. 두 shell의 `uv sync`도 다음 정비 때
`uv sync --frozen`으로 맞추는 편이 재현성에 안전하다.

`inspect-jenkins-job`은 두 config 모두 anonymous read 가능한 HTTP에서 signer 관련 값을
inline으로 보유한다고 판정했다. 값은 기록하지 않았다. 이는 전략 수익성 판정과 별개지만
운영 보안상 Credentials Binding과 인증된 HTTPS가 권고된다.

## 3. Primary comparison cohort

Evidence Contract에 따라 config와 commit을 섞지 않았다.

- UTC half-open range:
  `[2026-08-16T11:30:00Z, 2026-08-18T10:15:00Z)`
- KST:
  `[2026-08-16 20:30, 2026-08-18 19:15)`
- Git commit: `ce039e70f544c022bda07be073f7018b28248ffe`
- Orange config hash:
  `c078cf0e8a390ece9c9f828705d87127b01de67de2a02c0f3a401c5e91b3ba33`
- Yellow config hash:
  `7dfa94598a04797714e942fb6478dc989fac948bb99b637d76a35c5a97baf242`

이 commit은 026 회고 문서만 바꾼 documentation-only commit이다. 그래도 공통 계약대로
다른 commit의 run과 합치지 않았다.

### Cadence와 scanner

| 지표 | Orange | Yellow |
|---|---:|---:|
| RunAudit | 561 | 560 |
| SUCCESS / FAILED | 561 / 0 | 560 / 0 |
| 평균 / 최대 runtime | 50.01s / 156.63s | 29.59s / 69.82s |
| 최대 start gap | 8.884m | 9.914m |
| cursor-complete sweep | 561 / 561 | 560 / 560 |
| 평균 pages | 35.91 | 7.96 |
| 평균 unique markets | 3,539.67 | 743.66 |
| candidates | 13,888 | 2,664 |
| BUY submitted / activated | 127 / 103 | 140 / 121 |
| SELL submitted | 86 | 117 |
| reconciliation errors | 562 | 8,960 |

실행 수는 해당 46시간 45분의 5분 cadence 기대치와 맞고 runtime도 다음 정기 실행을
지속적으로 침범하지 않았다. 주기를 줄이거나 늘릴 근거는 없다. 최대 gap은 manual/SCM
경계가 섞인 일부 run 간격이며 전체 sweep 누락으로 이어지지 않았다.

Orange는 Yellow보다 평균 시장 4.76배, candidate 5.21배를 보지만 activated BUY는 오히려
적다. 최신 console에서 `open=100 (PENDING_BUY=2, HOLDING=96, PENDING_SELL=2)`와
`최대 포지션 수 (100) 도달`이 반복됐다. Yellow는 `open=9`였다.

### Exact confirmed-fill 기술 통계

성과에는 `trades.realized_pnl`을 사용하지 않았다. BUY/SELL의
`order_fills.status='CONFIRMED'` 실제 size/price를 사용하고, Golden Cherry 계약상
`MAKER + fee fields omitted` 또는 명시적 `fee_rate_bps=0`만 known-zero fee로 인정했다.

| 지표 | Orange | Yellow |
|---|---:|---:|
| BUY trade / confirmed entry | 127 / 103 | 140 / 120 |
| exact closed | 65 | 92 |
| quantity-mismatch COMPLETED | 8 | 20 |
| 아직 open | 30 | 8 |
| exact-closed net | **-$8.43253** | **-$4.13184** |
| 양수 exact trade | 22/65 (33.8%) | 45/92 (48.9%) |
| primary cohort open confirmed 원가 | 약 $149.97 | 약 $39.94 |

현재 DB 전체 open confirmed 원가는 Orange 약 `$498.54`, Yellow 약 `$49.22`다. Orange의
동시 노출이 약 10배인 상태에서 closed-only 손익을 비교하면 Orange의 아직 끝나지 않은
30건이 빠지는 selection bias도 생긴다. 따라서 위 음수는 경고 신호이지만 최종 기대값은
아니다.

같은 condition을 두 계정이 모두 거래했고 양쪽 모두 exact closed인 paired subset은
13건뿐이다.

| paired subset | Orange | Yellow |
|---|---:|---:|
| 평균 entry signal | 0.883038 | 0.823846 |
| exact net | -$0.332510 | +$1.699760 |
| 양수 trade | 7/13 | 7/13 |

같은 시장에서도 Orange가 평균 5.92%p 비싸게 들어가 pair당 `$0.1563` 낮았다. 표본이
13이고 시간·시장 cluster도 독립이 아니므로 유의한 인과 결과로 주장하지 않는다. 다만
026의 32-pair 결과도 Orange가 낮았다는 방향과 일치한다.

## 4. Strict evidence gate

`polybot-retro audit --strict` 범위는 완결된 UTC 3일
`[2026-08-15T00:00:00Z, 2026-08-18T00:00:00Z)`로 고정했다. 산출물은 local-only다.

- `daily-rsync/data/analysis/golden-cherry-orange-yellow-20260818/retro-audit.json`
- `daily-rsync/data/analysis/golden-cherry-orange-yellow-20260818/retro-audit.md`

strict audit는 exit 1이다.

| Issue | Orange | Yellow | 판정 |
|---|---:|---:|---|
| COMPLETED exact fill coverage | 85.9% | 82.2% | CRITICAL |
| closed BUY/SELL quantity mismatch | 37 | 37 | CRITICAL |
| uncertain submission outcome | 0 | 1 | Yellow CRITICAL |
| generic fee amount missing | 63.7% | 59.7% | generic HIGH, 아래 보정 |

동일 3일의 confirmed fill을 전략 계약으로 다시 분류하면 Orange 659건, Yellow 491건 중
true unknown fee는 양쪽 모두 0건이다. Orange는 maker omission 420 + explicit zero 239,
Yellow는 293 + 198이다. 따라서 fee HIGH는 generic auditor가 전략별 known-zero 계약을
모르는 false positive다. 반면 quantity mismatch와 uncertain intent는 실제 blocker다.

DB 경로에서 log를 자동 연결하지 못했다는 generic MEDIUM도 있었지만 daily-rsync catalog의
bot/console log는 모두 available이고 최신 redacted console을 별도로 대조했다.

## 5. 부분체결 lifecycle 결함은 아직 활성 상태다

`golden-cherry/src/polybot/db/fill_evidence.py`는 confirmed fill 합과
`latest_size_matched`가 같으면 `latest_order_status == 'MATCHED'`라는 이유만으로
`requested_size`보다 얼마가 부족하든 full fill로 인정한다. 2026-08-16의 026 회고에서
지적한 분기가 현재 source에 그대로 남아 있다.

Primary cohort에서 잘못 `COMPLETED`된 mismatch는 다음과 같다.

| 지표 | Orange | Yellow |
|---|---:|---:|
| mismatch COMPLETED | 8 | 20 |
| BUY−SELL 잔량 합 | 4.022510주 | 0.101974주 |
| 최대 단건 잔량 | **3.960000주** | 0.014104주 |
| 마지막 SELL가 표시 잔량 가치 | 약 $3.2250 | 약 $0.0845 |

Yellow의 최신 mismatch는 주로 venue quantization dust지만 Orange에는 BUY 5.78주 중
SELL 1.82주만 체결되고 3.96주가 남았는데도 `COMPLETED`된 명백한 partial fill이 있다.
상태명만으로 full fill을 허용하면 quantization과 실제 partial을 구분할 수 없다.

source cutoff 현재 terminal partial 고착도 그대로다.

| Job | state | 수량 | venue state | age |
|---|---|---|---|---:|
| Orange | PENDING_BUY | 5.780347 requested / 4.72 confirmed | CANCELED_MARKET_RESOLVED | 93.2h |
| Orange | PENDING_BUY | 5.494505 / 5.00 | CANCELED_MARKET_RESOLVED | 81.9h |
| Orange | PENDING_SELL | 5.71 / 4.337347 | INVALID | 93.5h |
| Orange | PENDING_SELL | 5.81 / 1.392404 | CANCELED_MARKET_RESOLVED | 67.0h |
| Yellow | PENDING_BUY | 6.172840 / 5.263156 | CANCELED_MARKET_RESOLVED | 80.6h |

최신 remote console에서도 Yellow는 이 BUY를 `full=False`로 매 cycle 대기했고 Orange는
PENDING 4건을 포함한 open 100개로 포지션 상한을 계속 소모했다. 이는 과거 snapshot의
잔재가 아니라 현재 운영을 계속 제한하는 결함이다.

Yellow에는 2026-08-16T00:03:46Z의 `SUBMIT_OUTCOME_UNKNOWN`, order ID 없음, 자동 대사
대상 아님인 BUY intent도 1건 남아 있다. 최신 cycle의 legacy 대사 오류 16건과 함께
해당 token/side 주문을 격리할 수 있다.

## 6. “다른 값이면 어땠나” sensitivity

`market_snapshots`는 두 DB 모두 0행이다. 후보 전체의 확률 경로, ranking, position-cap
충돌을 재생할 수 없으므로 진짜 counterfactual은 불가능하다. 아래는 Orange가 실제로
진입한 거래를 사후 제외한 sensitivity일 뿐이다.

### 진입 상단

| 사후 upper cap | confirmed | open | exact closed | mismatch | exact-closed net |
|---:|---:|---:|---:|---:|---:|
| 0.88 | 44 | 4 | 33 | 7 | -$1.71523 |
| 0.90 | 62 | 5 | 49 | 8 | **-$1.06083** |
| 0.90909 | 63 | 5 | 50 | 8 | -$1.88883 |
| 0.92 | 79 | 14 | 57 | 8 | -$5.66133 |
| 0.94 | 96 | 25 | 63 | 8 | -$7.32163 |
| 0.95 current | 103 | 30 | 65 | 8 | **-$8.43253** |

`>0.90909`의 exact closed 15건은 stop-loss 6건 `-$3.9552`, trailing-stop 9건
`-$2.5885`, take-profit 0건이다. 아직 open 25건이 있어 최종 손익은 아니지만 TP가
불가능하다는 산술과 실제 exit 분포가 같은 방향이다.

026의 이전 독립 commit cohort에서도 Orange current cap은 `-$12.04459`, 0.90 사후 cap은
`-$0.85157`, `>0.90909` 구간은 `-$11.34206`이었다. 두 cohort를 합치지는 않았지만
0.95 확대가 나빴다는 방향은 반복됐다.

### 최소 유동성

| 사후 liquidity floor | confirmed | open | exact closed | mismatch | exact-closed net |
|---:|---:|---:|---:|---:|---:|
| $30k current | 103 | 30 | 65 | 8 | -$8.43253 |
| $50k | 64 | 17 | 42 | 5 | -$4.89687 |
| $75k | 49 | 14 | 32 | 3 | -$1.16879 |
| $100k | 38 | 12 | 23 | 3 | -$0.90059 |
| $125k | 32 | 9 | 20 | 3 | +$3.40951 |
| $250k | 6 | 0 | 5 | 1 | +$0.64878 |

이 표만 보고 `$125k`가 수익성 최적값이라고 결론 내리면 안 된다. 026 cohort의 `$125k`
사후 subset은 반대로 `-$6.19302`였고, 실제 floor를 높였으면 빈 slot에 다른 후보가 들어와
결과도 달라진다. 다만 `$30k`로 universe와 동시 노출을 함께 넓힌 현재 설계가 더 안전한
방향이라는 증거는 없다.

## 7. 권고안

### P0 — parameter보다 신규 노출 보호

1. 두 job을 `close_only`로 전환해 신규 BUY를 먼저 멈춘다. 특히 Orange는 현재 open 100,
   confirmed 원가 약 `$498.54`이며 결함 상태에서 빈 slot이 생기는 즉시 다시 채울 수 있다.
2. DB를 clean하지 않는다. terminal partial, residual, wallet 대사의 유일한 증거다.
3. 이번 작업은 분석 요청이므로 Jenkins를 변경하지 않았다.

### P1 — shared lifecycle 수정

1. `MATCHED` 무조건 full-fill 예외를 제거한다.
2. raw pre-quantization request가 아니라 signed order의 maker/taker amount 또는 venue
   `original_size`에서 **authoritative submitted size**를 구해 confirmed fill과 비교한다.
   이 방식이면 5.847953→5.84 같은 정상 quantization은 허용하고 5.78→1.82는 거부한다.
3. terminal partial BUY는 실제 confirmed size를 관리 가능한 HOLDING 또는
   `RESOLVED_CLAIMABLE/QUARANTINED`로 전이한다. 무기한 PENDING_BUY를 금지한다.
4. SELL 완료는 cumulative confirmed BUY−SELL로 계산한다. sellable residual은 HOLDING,
   최소 주문 미만은 provenance를 가진 DUST/RESIDUAL로 보존한다.
5. Yellow의 2026-08-16 unknown intent는 거래소 open order/trade와 probe한 뒤
   `polybot-retro resolve-intent`로 증거 기반 해제한다.
6. generic retro auditor에 Golden Cherry maker/explicit-zero fee adapter를 반영한다.

배포 후 `close_only` 자연 build와 재동기화에서 terminal pending 0, wallet과 DB 잔량 일치,
새 COMPLETED exact quantity coverage 100%, 새 cohort CRITICAL/HIGH 0을 확인해야 active로
복귀할 수 있다.

### P2 — 다음 parameter cohort

현재 `0.95`는 유지하지 않는다.

- 안전 rollback이면 Orange upper를 **0.90 이하**로 낮춘다. 0.90은 +10% TP에 0.99의
  실행 여유를 남긴다. 0.90909는 이론 경계라 spread/rounding 여유가 없다.
- `$5`는 유지한다.
- 위험 제한과 비교 가능성을 위해 `max_positions=10`, `new/cycle=1`로 맞춘다.
- min liquidity는 수익 최적값으로 확정할 수 없다. 안전 rollback은 `$125k`, 실험이면
  다른 모든 값을 고정하고 `$75k` 대 `$125k` 한 축만 비교한다.
- upper를 검정하려면 두 arm 모두 lower `0.85`, liquidity, position cap, 신규 상한을
  같게 두고 `0.88` 대 `0.90`만 비교한다.
- `0.95`를 꼭 연구하려면 +10% TP형 Golden Cherry가 아니라 resolution까지 보유하는
  별도 last-mile 가설로 사전등록한다.

5분 cadence는 유지한다. lifecycle 수정 후 새 cohort는 최소 7일 관찰하되 최대 entry
window가 120시간이므로 review 시 마지막 120시간 entry를 mature 성과에서 제외한다.
안정적인 parameter 판단은 30일이 더 적절하다.

### P3 — 다음에는 진짜 반사실을 저장한다

각 cycle의 전체 candidate에 probability, liquidity, 기준시각, ranking, cap/중복/주문 차단
사유를 append-only로 저장한다. 현재처럼 `market_snapshots=0`이면 “0.90이었다면 빈 slot에
무엇을 샀는가”를 재생할 수 없다. parameter 최적화 전에 이 evidence를 추가해야 한다.

## 8. 재현 명령

```bash
cd daily-rsync
uv run daily-rsync scan --job polybot-orange
uv run daily-rsync plan --job polybot-orange --strategy golden-cherry --days 7
uv run daily-rsync sync --plan 8bed7cb9bfdbaa56
uv run daily-rsync verify --job polybot-orange --strategy golden-cherry

uv run daily-rsync scan --job polybot-yellow
uv run daily-rsync plan --job polybot-yellow --strategy golden-cherry --days 7
uv run daily-rsync sync --plan 60ca5901389ff8a4
uv run daily-rsync verify --job polybot-yellow --strategy golden-cherry
```

Jenkins config와 console은 `inspect-jenkins-job`의 redacted read-only 조회로 확인했다.
credential 값은 출력하거나 기록하지 않았다.

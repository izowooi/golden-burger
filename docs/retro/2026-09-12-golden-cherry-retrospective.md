# Golden Cherry 장기 회고 — 2026-09-12

## 판정

| 항목 | 판정 |
|---|---|
| 현재 전략 수익성 | **기각** — exact `$5` 고유 포지션 1,247건, 확인된 경제손익 `-$142.417508` |
| `$10` 이상 증액 | **금지** — confirmed 매수원가 대비 `-2.30%`, 주요 진입대와 종목이 모두 음수 |
| 파라미터 즉시 교정 | **보류** — strict evidence audit가 CRITICAL 4 / HIGH 5이고 prospective shadow가 미완료 |
| 현재 live | **유지** — `polybot-yellow`, active, `H/5`, `$5`; 사용자가 중단을 판단할 때까지 scheduler를 끄지 않음 |
| 수동 `Lee Jae-myung…` 포지션 | Cherry DB 0건. 전략 성과·capacity·청산 대상에서 제외하고 운영자 소유로 유지 |

분석 cutoff는 `2026-09-12T13:29:32Z`다. `daily-rsync`로 `polybot-yellow × golden-cherry ×
default`를 scan, plan, sync, verify, pin했으며 pin SHA-256은
`0e643d391e291a83e0efd2efdc6ac8ae261a6cca3568db889415b58f66b5bd99`다. 실제 성과는
`order_fills.status='CONFIRMED'`와 exact one-hot resolution만 사용했다. legacy
`trades.realized_pnl` 894행은 합산하지 않았다.

## 운영 상태

- Jenkins build `#57196`은 SUCCESS였고 job은 enabled다.
- 현재 운영 override는 진입 `0.75–0.88`, `$5`, 유동성 `$125,000`, 최대 포지션 10,
  cycle당 신규 1건, TP `+10%`, SL `-8%`, trailing `5%`, 신규 진입 floor `-$200`이다.
- 최근 24시간 run은 288/288 SUCCESS, 간격 p50 5.00분, p95 5.15분, 최대 5.47분이다.
- 최근 7일은 2,014/2,014 SUCCESS이며 10분 초과 gap은 1회다.
- 현재 Cherry 관리 포지션은 5건, 원금 `$25.0638`이다. 수동 wallet 포지션은 DB에
  가져오지 않으므로 이 수치에 들어가지 않는다.
- 역사적 unknown BUY 7건은 2026-09-06 사용자 지시에 따른 immutable
  `OPERATOR_HANDLED_ASSUMPTION`이다. venue fill/zero-fill 증거로 바꾸지 않으며 capacity에서는
  제외하고 같은 token에 대한 봇 주문만 금지한다.

## 확인된 성과

확정 SELL은 1,217개 성분 `-$38.745204`, exact resolution settlement는 68개 성분
`-$103.672304`다. 부분 SELL 뒤 resolution된 38개 포지션은 두 성분이 있으므로 성분 수
1,285와 고유 포지션 수 1,247은 서로 다르다.

| 구분 | 건수 | 손익 | 요청 원금 대비 |
|---|---:|---:|---:|
| take profit | 524 | `+$387.724350` | `+14.82%` |
| stop loss | 343 | `-$359.032775` | `-20.96%` |
| trailing stop | 312 | `-$69.717375` | `-4.48%` |
| resolution 포함 성분 | 106 | `-$101.391708` | `-19.93%` |
| **종합** | **1,285 성분 / 1,247 포지션** | **`-$142.417508`** | **고유 포지션 confirmed 원가 `$6,201.374032` 대비 `-2.30%`** |

익절은 작동했지만 하방 청산과 최종 패배의 합을 상쇄하지 못했다. `-8%` SL이 실제로는
평균 `-20.96%`에 체결된 것은 5분 cadence 사이의 가격 gap과 시장 충격 때문이다. 따라서
표시 threshold를 조금 바꾸는 것만으로 이 꼬리가 사라지지 않는다.

| 진입 VWAP | 성분 수 | 손익 | ROI |
|---|---:|---:|---:|
| `.75–.78` | 228 | `-$25.416072` | `-2.24%` |
| `.78–.82` | 434 | `-$51.804958` | `-2.40%` |
| `.82–.86` | 320 | `-$56.907126` | `-3.57%` |
| `.86–.88` | 265 | `-$8.289352` | `-0.63%` |

`.86–.88`이 덜 나빴지만 양수가 아니며 사후 선택이다. 이를 새 최적 구간으로 간주하지 않는다.

현재 exact 고유 포지션 기준 sports phase도 in-play `-$114.624184` (`-2.19%`), pregame
`-$27.793324` (`-2.83%`)으로 둘 다 음수다. 태그 기반 family 집계에서도 UFC 6건
`+$3.2904` 외에 축구 `-$50.4592`, 테니스 `-$24.4575`, e-sports `-$23.1525`, 농구
`-$18.0094`, 야구 `-$12.5102`, 미식축구 `-$11.1451`이었다. UFC 6건은 확장 근거가 아니다.

주간 손익은 9월 5일 00:00 UTC 이후 `+$2.476782`로 최근 화면의 양호한 방향과 일치한다.
그러나 그 전 세 주가 각각 `-$40.497408`, `-$51.850204`, `-$52.390250`이었다. 최근 한 주만
보면 전략의 누적 손실을 놓친다. 수동 포지션을 제외해도 Cherry 자체의 월간 방향은 음수다.

## 대형 주문기와 체결 문제

DB는 2026-03-30부터 2026-09-12까지 166일을 보존한다. 사용자가 기억한 3개월 이상 운영은
맞다. 다만 exact lifecycle 계약으로 완전히 감사 가능한 구간은 2026-08-08 `$5` 복귀 이후다.

과거에는 `$1,000`뿐 아니라 `$2,000`, `$3,000` 설정도 있었다. 요청액별 UNFILLED 비율은
`$1,000` 262건 중 171건(65.3%), `$2,000` 62건 중 46건(74.2%), `$3,000` 53건 중
42건(79.2%)이었다. 주문을 키울수록 full fill이 어려워지고 legacy 유령 포지션과 원장 공백이
늘었다. 현재 `$5`를 증액하지 않는 이유는 수익성뿐 아니라 이 실행 이력에도 있다.

## Shadow 비교

`polybot-cherry-shadow`는 실제로 존재하며 5분 cadence로 enabled 상태다. 이 잡은
0.76–0.78, 0.80–0.82, 0.84–0.86과 7개 청산 정책을 paired displayed-book으로 기록한다.
따라서 live의 전체 0.75–0.88을 그대로 복제하는 단일 backtest가 아니다.

9월 5일 23:52 UTC까지의 검증된 pin에서 현행 TP10/SL08/trailing05 정책은 완결 29건 합계
약 `-$1.473139`였다. low band `+$0.929421`, middle `+$0.317483`, high `-$2.720043`으로
표본도 작고 결과도 안정적이지 않다. 오늘 remote DB는 6.16 GiB까지 커졌으며 local free-space
50 GiB floor를 위반해 새 plan을 실행하지 않았다. Jenkins 최신 run은 cursor complete,
FAILED 0, 신규 path와 resolution 관측이 계속되는 것을 확인했다. 정식 entry 종료일은
2026-10-04, follow-up 종료일은 2026-11-03이다.

## Evidence audit와 변경 결정

strict audit는 CRITICAL 4 / HIGH 5로 FAIL이다. 주된 항목은 legacy COMPLETED fill coverage
69.4%, 수량 overflow 2건, BUY/SELL 수량 불일치 576건, stale reconciliation 23건,
historical unknown intent 7건, legacy catalog gap 66건이다. 이 결함은 exact analyzer가 인정한
1,247개 포지션의 손익을 다시 legacy 추정치로 바꾸지는 않지만, 과거 전체 경로를 재생해
TP/SL/trailing의 새 최적값을 고르는 것을 금지한다.

따라서 이번 회고에서는 live 파라미터와 universe를 바꾸지 않았다. 모든 주요 entry band가
음수이고 shadow prospective cohort가 끝나지 않은 상태에서 `.86–.88`이나 특정 종목만 고르면
사후 과적합이 된다. `$5` live와 shadow 수집은 유지하고 증액은 거부한다.

코드에서는 `analyze_exact_history.py`가 운영자 처리 가정 7건을 active 미추적 원금
`$7,041.52`로 잘못 표시하던 보고 버그를 수정했다. 이제 runtime과 같은 의미로 active
미추적 BUY는 0건/`$0`, 운영자 처리 가정 7건은 별도 표시된다. 이는 주문이나 DB 원장을
변경하지 않는다.

다음 파라미터 판정은 shadow entry가 끝난 2026-10-04 이후, follow-up에서 각 band의 완결률이
충분할 때 한다. 그 전에도 exact 누적 손익이 `-$200` floor에 닿으면 신규 BUY guard가
자동으로 막고 기존 5개 포지션의 대사·청산은 계속한다. scheduler 정지는 사용자의 별도 판단을
따른다.

## 별도 운영 위험

Jenkins config가 private key와 funder address를 inline export하고 anonymous config read도
허용해 inspector가 CRITICAL을 보고했다. 이번 회고에서는 secret 값을 읽거나 옮기지 않았다.
Credentials Binding으로 이전하고 anonymous `config.xml` 읽기를 막아야 한다. 이 보안 변경은
전략 파라미터와 독립적으로 수행한다.

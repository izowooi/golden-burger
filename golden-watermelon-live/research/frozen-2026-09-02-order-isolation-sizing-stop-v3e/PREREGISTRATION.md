# Golden Watermelon Live 주문 격리·증액·손절 보정 v3e — 2026-09-02

- 기존 진입 구간 `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)`과 후속 관찰 종료
  `2026-09-12T04:00:00Z`는 유지한다.
- Soccer/MLB/NHL의 0.96 대 0.99 진입 하한, 1분 cadence, 종목별 모집단은 유지한다.
- 배포 시점 이후는 새 `config_hash × strategy_source_digest × mode × job_name` 묶음으로
  분석하고, 이전 묶음과 합쳐 손익 또는 A/B 우승자를 계산하지 않는다.
- 기존 DB는 additive migration만 수행하며 clean, rewrite, backfill 또는 merge하지 않는다.

| Jenkins/runtime | family | 진입 범위 |
|---|---|---|
| `polybot-cat/watermelon-live-cat-96-1m-v2h` | Soccer | `[0.96,0.999]` |
| `polybot-dog/watermelon-live-dog-99-1m-v2h` | Soccer | `[0.99,0.999]` |
| `polybot-bear/watermelon-live-bear-mlb-96-1m-v3a` | MLB | `[0.96,0.999]` |
| `polybot-tiger/watermelon-live-tiger-mlb-99-1m-v3a` | MLB | `[0.99,0.999]` |
| `polybot-lion/watermelon-live-lion-nhl-96-1m-v3a` | NHL | `[0.96,0.999]` |
| `polybot-wolf/watermelon-live-wolf-nhl-99-1m-v3a` | NHL | `[0.99,0.999]` |

## 변경 근거

1. Bear의 Seattle 주문은 거래소가 `post_only_mode`로, Tiger의 Minnesota 주문은
   `trading is disabled`로 명시적으로 거절했다. 주문 ID와 연결된 trade가 없는데도 일반 HTTP
   503처럼 “제출 결과 불명”으로 저장되어 각각 이후 8개·1개 다른 경기 후보를 전역 차단했다.
2. 최근 live에서 SEA와 WSH는 최종 승자였지만 진입가보다 5%p 낮은 0.95/0.94에서 손절되어
   합계 약 `-$0.4809`를 확정했다. 앞선 Bear HOU도 최종 승자였으나 0.893에서 손절되어 약
   `-$0.3832`였다.
3. 검증된 White 표시 호가 재생에서는 고정 0.70/0.80/0.85/0.90/0.93/0.95 손절이 모든 진입
   하한에서 해결까지 보유보다 나빴다. 따라서 이 보정은 수익 최적 손절을 주장하지 않고,
   잦은 일시 하락 손절을 제거하면서 0.70만 재난 손실 방어선으로 남긴다.

## 명시적 미주문 거절과 실패 격리

- `PolyApiException`의 정확한 `post_only_mode` 응답 또는 정확한 `trading is disabled` 응답만
  “거래소가 주문을 만들지 않음”으로 판정한다. 일반 5xx, timeout, connection error는 계속
  제출 결과 불명으로 둔다.
- 과거 행도 order ID 없음, 연결 trade ID 없음, 기존 수동 판정 없음, 위 정확한 응답 원문이
  모두 맞을 때만 `NO_ORDER_CREATED`로 자동 해제한다.
- 결과 불명 BUY, 미대사 BUY, orphan BUY는 한 capacity를 예약하고 같은 token/event 재주문을
  막지만, account/event/cycle 여유 안에서 다른 경기는 계속 처리한다.
- 주문 제출 전 정밀도·fee 계약 오류는 해당 후보만 `PRE_SUBMISSION_CONTRACT_ERROR`로 남기고
  뒤 후보를 계속 처리한다. 방향을 알 수 없는 대사 오류, open BUY fill/fee 공백, 일반
  quarantine, 경제손익 증거 공백은 계속 전역 차단한다.

## 손절 보정

- effective stop은 신규·기존 open trade 모두 `0.70`이다. 코드 표현은
  `max(0.70, confirmed entry VWAP-0.30)`이며 현재 진입 범위에서는 0.70이 항상 지배한다.
- 과거 DB의 0.94/0.95 등 저장값은 당시 증거로 보존하되 새 실행 정책을 강제하지 않는다.
- 0.70은 최적값으로 선택한 것이 아니라 재난 손실 제한이다. 1분 snapshot 사이에 0.97에서
  0.06으로 건너뛰는 가격 gap은 어떤 고정 polling 주기로도 체결가를 보장하지 않는다.
- 손절은 실제 보유 전량을 하나의 FOK로 제출한다. 전량 호가 깊이가 없으면 일부를 성공으로
  기록하지 않고 기존 180분 국소 격리·대사 절차를 따른다.

## 증액 시 원자적 금액 축소

- 신호와 모집단 비교는 항상 정확한 `$5` 표시 ask VWAP으로 유지한다.
- 운영자가 `POLYBOT_BUY_AMOUNT`를 `$5`보다 크게 명시한 새 묶음에서는 같은 fresh book으로
  목표 금액을 검사한 뒤, 가격 상한 안에서 전량 체결 가능한 가장 큰 금액 하나를 선택한다.
- 허용 사다리는 `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000`과
  사용자가 지정한 정확한 목표값이다. 목표 `$1,000` 중 `$214`만 안전하면 `$200` FOK 한 건을
  내며, `$5`도 불가능하면 주문하지 않는다.
- 이는 거래소의 부분 체결 주문이 아니다. 선택된 금액은 전량 체결 아니면 0체결인 FOK라서
  잔여 수량을 완료로 오인하지 않는다. 익절·손절은 확인된 실제 보유량 전량을 관리한다.
- 현재 Jenkins 목표값은 계속 `$5`다. 금액 변경은 별도 운영 결정이며 반드시 새 config hash로
  분리하고 한 단계씩만 올린다.

## 종목별 증거와 실행시간

- catalog, snapshot, trade에 `sport_family`, `league_code`, `league_name`, 원본 tag JSON을
  저장한다.
- trade에는 `target_buy_amount_usdc`, `selected_buy_amount_usdc`, 가격 상한 내 표시 호가 최대
  금액, 축소 사유를 저장한다.
- 기존 fresh book을 재사용해 계산하며 추가 Gamma/CLOB 요청을 만들지 않는다.
- 모든 관련 job은 1분보다 짧아야 한다. 배포 build와 자연 build에서 runtime을 측정하고 50초
  경고 또는 overlap이 발생하면 timer를 복구하지 않는다.

이 변경은 현재 0.96/0.99 A/B 우열, 종목 우열 또는 증액을 판정하지 않는다. Bear/Tiger의 이전
24시간은 기회 집합이 달랐으므로 무효이며, v3e 배포 뒤 공통 후보와 완전한 주문·체결·fee 증거를
새로 수집한다.

# Watermelon Live·Plum 주문 격리·증액·종목 증거 보정 — 2026-09-02

## 검토 범위

- MLB A/B 반개구간: `[2026-09-01T10:02:55Z, 2026-09-02T10:02:55Z)`
  (KST `[2026-09-01 19:02:55, 2026-09-02 19:02:55)`).
- Bear DB: `daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-watermelon-live/runtime/watermelon-live-bear-mlb-96-1m-v3a/databases/latest/trades.db`,
  SHA-256 `c0edcc25d622863b14e5d1e8470dc9a75c75b8bcb1f7200bcc84abc7bb70a444`.
- Tiger DB: `daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-watermelon-live/runtime/watermelon-live-tiger-mlb-99-1m-v3a/databases/latest/trades.db`,
  SHA-256 `294d2cfebfb63d4ea2dab50cd6761db7534324f7c7e1f41d970d27d48379dc47`.
- White displayed-book DB: `daily-rsync/data/sources/macmini-m5/jobs/polybot-white/strategies/golden-watermelon/runtime/watermelon-white-1m-v4a/databases/latest/trades_sim.db`,
  SHA-256 `3ad4501d9bb010334ba4fa55465b385484140adaec82796aff9b9aac390cb31f`,
  source cutoff `2026-09-02T11:11:22.149530Z`, sync finished
  `2026-09-02T11:38:04.884823Z`.
- White 동기화는 `daily-rsync` run `bca0b3b00e2143b4a61e28d116d6e244`가
  SUCCESS였고 verify 1,121개 artifact 모두 통과했다.

## “차단”의 실제 의미

- Bear의 첫 SEA 주문은 거래소가 HTTP 503과 함께
  `post_only_mode: only post-only orders and cancels are allowed`를 명시적으로 반환했다.
  주문이 생성되지 않았는데 기존 공통 분류기가 모든 5xx를 제출 결과 불명으로 취급했다.
- 그 결과 실제 open position은 0이고 20개 중 19개 capacity가 남았음에도
  `untracked_buy_exposure`와 `unresolved_buy_outcome` 전역 방어가 켜졌다. 이 버그가
  SF–PIT, NYM–TB, TOR–CLE, ATL–WSH, MIA–KC, DET–MIN, SD–CIN, STL–LAD의 뒤 후보를 막았다.
- Tiger의 DET–MIN도 거래소의 정확한 `trading is disabled` 거절을 결과 불명으로 잘못
  분류했고, 그 뒤 STL–LAD를 같은 방식으로 막았다.
- 수정 후 위 두 정확한 응답만 확정 미주문(`NO_ORDER_CREATED`)으로 해제한다. 일반 5xx,
  timeout, 연결 단절은 여전히 불명확하므로 보수적으로 격리한다.
- 결과 불명 BUY·미대사 BUY는 한 event/token과 한 capacity만 격리한다. 방향을 모르는
  대사 오류, open BUY fill/fee 공백, 일반 quarantine은 계속 전역 차단한다.

## 하한 미달과 CLOB 미수집

- “최고 `$5` VWAP 0.985, 하한 미달”은 Tiger arm의 진입 범위가
  `[0.99,0.999]`인데 PIT의 관측 가능한 `$5` 전량 매수 VWAP 최고가 0.985였다는 뜻이다.
  98.5%는 99%보다 낮으므로 Tiger 미진입은 정상이다. Bear의 0.96 arm에서는 진입
  대상이었으나 위 전역 차단 버그 때문에 놓쳤다.
- “최고 0.90/0.88/0.64”는 경기 시작 1분의 가격이 아니라, 수집된 경기 전체에서
  완전한 `$5` ask가 있었던 snapshot 중 최고 실행가격이다. 시장은 해결값 1이 DB에
  기록되기 전에 CLOB 호가가 닫히거나 사라질 수 있다. terminal payout 1과 마지막
  거래가능 ask 1은 같은 개념이 아니다.
- “Gamma에는 있으나 완전한 `$5` CLOB 호가 없음”은 종목 목록·메타데이터에는 경기가
  있었지만, 해당 cycle에 선택 token을 `$5` 전량 살 수 있는 유효 ask 깊이가 없었다는
  뜻이다. 부분 책을 임의 가격으로 채우지 않아 snapshot을 보수적으로 제외한 것이다.

## 손절 자료와 운영 보정

- Tiger의 SEA와 WSH는 최종 승자를 샀지만 0.95/0.94에 팔려 합계
  `-$0.480927`을 확정했다. 앞선 Bear HOU도 최종 승자였으나 0.893 손절로 약
  `-$0.383224`였다.
- White의 해결 완료 표시 호가 재생은 0.95 진입 103건, 0.96 진입 90건을 포함했다.
  0.70/0.80/0.85/0.90/0.93/0.95의 모든 고정 손절이 같은 진입군의 해결까지 보유보다
  나빴다. 예를 들어 0.96 진입의 event-equal fee-net ROI는 보유 `+0.2221%`,
  0.70 손절 `-1.9583%`, 0.90 손절 `-3.6686%`였다.
- 다만 White에는 결과 3종 호가와 경기시계 수집 공백 HIGH 경고가 남아 있어 이 자료로
  “통계적으로 최적인 손절값”을 고르지는 않는다. 확인된 live 오판 손절을 제거하는 안전
  보정으로 상대 5%p 손절만 없애고, 절대 0.70을 재난 방어선으로 남긴다.
- 1분 snapshot 사이에 가격이 0.97에서 0.06으로 건너뛰면 어떤 고정 주기도 중간 가격
  체결을 보장하지 않는다.

## 증액과 종목별 DB 계약

- 신호 비교는 계속 baseline exact `$5`다. 운영 목표 금액을 올리면 같은 fresh book에서
  가격 상한 내 전량 가능한 가장 큰 사다리 금액 하나를 FOK로 제출한다.
- 사다리: `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000`.
  목표 `$1,000`에 안전한 깊이가 `$214`이면 `$200` FOK 한 건만 내며, `$5`도 안 되면
  주문하지 않는다. 선택 금액은 전량체결 또는 0체결이므로 불명확한 부분 체결을 허용하는
  설계가 아니다.
- 익절·손절은 confirmed 실제 보유량 전량을 FOK SELL한다. 일부만 팔린 상태를 완료로
  표시하지 않는다.
- Watermelon Live와 Plum의 catalog/snapshot/trade에 `sport_family`, `league_code`,
  `league_name`, 원본 tag JSON을 저장한다. trade에는 목표액·선택액·가격 상한 내 최대
  표시 가능액·축소 사유를 저장한다.
- White는 이미 같은 종목·리그·tag 및 `$5`~`$1,000` 표시 깊이 계약을 보유해 로직 변경이
  필요 없었다. Silver/Gold는 새 필드를 추가하되 기존 CLOB 응답을 재사용해 API 호출과
  실행시간을 늘리지 않는다.

## 전략 역할 정정

- King/Queen은 Golden Plum 축구 live A/B다. King은 익절 0.90, Queen은 0.95이고 나머지
  진입·손절 조건은 같다.
- Silver는 Plum 축구 simulation, Gold는 Plum MLB/NFL/NBA simulation이다. NHL은 코드
  profile만 있고 아직 Jenkins 수집이나 live가 아니다.
- Cat/Dog는 Watermelon Live 축구, Bear/Tiger는 Watermelon Live MLB,
  Lion/Wolf는 Watermelon Live NHL A/B다. White는 별도 Golden Watermelon 표시 호가
  simulation이다.

## 검증·배포 기준

- 수정 대상 테스트: Watermelon Live 206, Plum 279, 공통 실행대장 227개 통과.
- 28개 전략 계약 검사 통과. 공통 실행대장을 참조하는 나머지 전략도 프로젝트별로
  회귀 테스트했으며, Golden Coconut만 이번 변경과 무관한 기존 `../../AGENTS.md` 고정
  해시 불일치로 실패했다.
- 배포 뒤 각 Jenkins DB에서 과거 Bear/Tiger 불명확 intent가 정확한 거절 증거로만
  해제됐는지, unresolved BUY/PENDING BUY가 0인지, 새 DB 열과 새 source digest가 있는지,
  각 build가 60초 미만인지 확인한다.

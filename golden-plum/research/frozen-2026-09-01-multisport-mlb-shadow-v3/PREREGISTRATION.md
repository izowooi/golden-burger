# Golden Plum 사전 등록 — multisport-mlb-shadow-v3

## 변경 목적과 코호트 경계

Golden Plum의 축구 실거래 계약은 그대로 유지하면서, 같은 “직접 호가 선두의 상승 확인”
가설을 종목별로 분기할 수 있는 구조를 만든다. `polybot-silver`는 축구, 새
`polybot-gold/plum-shadow-gold-mlb-1m-v1`은 MLB 원자료와 표시 호가 기반 반사실을
각각 1분마다 수집한다. NBA·NFL·NHL은 분류·호가·재생 코드만 준비하며 이번 배포에서는
Jenkins runtime이나 실거래를 허용하지 않는다.

기존 v1·v2 사전 등록과 DB는 수정하거나 합치지 않는다. v3는
`config_hash × strategy_source_digest × mode × job_name`과 각 job의 첫 성공 실행 시각으로
분리한다. 과거 `polybot-gold`의 Golden Coconut DB도 새 Golden Plum runtime DB와 섞지
않는다.

runtime의 종목·mode·protocol·사전 등록 파일·DB 경로·외장 workspace·주기 제한은 하나의
불변 `RuntimeSpec`에서 원자적으로 결정한다. 공유 YAML이나 개별 환경변수로 이 조합의 일부만
바꿀 수 없으며, resolved config에는 runtime spec version과 sport profile hash를 함께 남긴다.

## 현재 배포 계약

| Jenkins/runtime | 종목 | mode | 역할 |
|---|---|---|---|
| `polybot-king/plum-live-king-90-1m-v1` | 축구 | live | TP 0.90 A |
| `polybot-queen/plum-live-queen-95-1m-v1` | 축구 | live | TP 0.95 B |
| `polybot-silver/plum-shadow-silver-1m-v1` | 축구 | simulation | 6개 직접 호가·증액 자료 |
| `polybot-gold/plum-shadow-gold-mlb-1m-v1` | MLB | simulation | 2개 팀 직접 호가·증액 자료 |

실거래 runtime은 축구만 허용한다. MLB·NBA·NFL·NHL family를 live job에 주입하면
네트워크나 DB를 열기 전에 실패해야 한다. simulation runtime에는 private key, funder
address, signature type을 주입하지 않는다.

## 종목별 모집단

### 축구

- EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL
- regular-time HOME/DRAW/AWAY 세 명제의 direct YES/NO, 경기당 정확히 6 token
- source minute 0부터 source가 `ended=true`로 바뀔 때까지 관측
- source minute 및 wall-clock age 상한 없음
- 시간 강제 청산 없음; TP·SL·검증된 resolution만 사용

### MLB

- exact MLB identity: sport id 8, tag 100381, root series 3, team league `mlb`
- whole-game 최상위 two-team moneyline 한 개, 팀명이 붙은 정확히 2 token
- child event, prop, future, period/inning, spread/run-line, total, minor league,
  esports를 제외
- Gamma `live=true`, `ended=false`인 경기 시작부터 종료까지 관측하며 extra innings도
  시간 상한으로 자르지 않음
- 이닝·초/말을 축구 minute로 변환하지 않음. `source_elapsed_minutes`는 NULL로 두고
  snapshot UTC cadence와 명시적 live/ended lifecycle을 사용

NBA·NFL·NHL도 각 공식 sport/tag/root-series/team-league identity와 같은 direct
two-team moneyline 계약을 코드로 보유한다. 데이터 수집과 live 승격은 별도 사전 등록
전에는 시작하지 않는다.

## 공통 서버·실행 필터

- Gamma keyset: family tag 하나, `live=true`, 누적 거래량 5,000 이상, 유동성 5,000 이상
- 축구 최대 4페이지, direct sport 최대 2페이지. cap을 넘으면 일부 모집단을 저장하지
  않고 cycle 전체를 실패 처리
- CLOB full book은 token별 단건 조회가 아니라 최대 250 token batch로 한 번에 읽음
- primary exact `$5` ask/bid depth와 spread를 저장
- `$5/$10/$25/$50/$100/$250/$500` 증액 자료는 simulation에서 같은 cached full book을
  로컬 계산하므로 추가 네트워크 요청을 만들지 않음
- 한 cycle의 목표 runtime은 50초 미만이며 1분 timer가 겹치면 job lock이 두 번째 실행을
  건너뛴 사실을 로그와 run audit에 남김

## 종목별 파라미터 구조

각 sport family는 별도 versioned profile을 가진다. 축구의 현재 live primary는
`[0.75,0.78]` first crossing, 3회, 누적 +0.02, pullback 0.01, SL -0.15다. MLB·NBA·NFL·NHL은
아직 보정되지 않은 collection profile이며 같은 primary를 “최적값”으로 주장하지 않는다.

simulation은 다음 paired grid를 저장·재생한다.

- entry: 0.55, 0.60, 0.65, 0.70, 0.75, 0.80
- target: 0.85, 0.90, 0.95
- stop delta: 0.05, 0.10, 0.15, 0.20
- trend observations: 2, 3, 5
- minimum cumulative move: 0.01, 0.02, 0.03, 0.05
- displayed notional: 5, 10, 25, 50, 100, 250, 500 USDC

여러 grid cell은 같은 경기의 반복 측정이므로 독립 거래로 세지 않는다. displayed book
결과는 actual fill이나 realized P&L이 아니며 수수료 전 반사실로 표시한다.

## MLB primary simulation lifecycle

Gold의 primary simulation episode는 두 팀 token 중 midpoint가 유일하게 가장 높고, 같은
token의 최근 3개 exact `$5` ask VWAP이 90초 이하 간격으로 누적 +0.02 이상 상승하면서
처음 `[0.75,0.78]`에 들어오면 생성한다. target 0.95, stop은 entry -0.15이며
시간 청산은 없음으로 고정한다. 이 primary는 수집 상태와 resolution 연결을 검증하기 위한 사전 등록 경로이지
MLB 최적 파라미터가 아니다.

## 수집 완전성과 종료 추적

- 발견한 eligible event마다 cycle별 expected token 수, 실제 저장한 token 수, batch book
  응답 수, 누락 token과 완료 여부를 별도 evidence로 남긴다. 일부 token만 읽힌 cycle은
  진입·재생 표본으로 사용하지 않는다.
- event가 live discovery에서 사라져도 마지막 관측만으로 승패를 추정하지 않는다. 명시적인
  one-hot terminal outcome을 찾거나 `2026-10-08T00:00:00Z` follow-up 종료까지 bounded
  재조회하고, 끝내 확인하지 못하면 right-censored(오른쪽 잘림)로 기록한다.
- terminal follow-up은 primary episode나 가상 주문이 생성되지 않은 eligible event에도
  적용한다. 그래야 진입 조건을 만족하지 않은 경기까지 포함한 모집단 해결률을 측정할 수 있다.
- sport identity, market condition, token label과 outcome, source live/ended, snapshot/event
  timestamp, request/page lineage를 저장한다. 축구의 source minute와 MLB의 NULL source minute를
  서로 변환하거나 보간하지 않는다.

## 주문 규모 확대 자료

각 complete snapshot의 같은 cached full book에 대해 `$5/$10/$25/$50/$100/$250/$500` 전
구간을 독립적으로 재생한다. 각 금액마다 ask/bid fillable notional, shares, VWAP, 최악 가격,
사용 level 수, 부분 체결 잔여량, spread와 왕복 표시 손익을 저장한다. 이후 path replay에서도
각 notional의 진입과 청산 가능량을 별도로 계산하며 `$5` 결과를 선형 배수로 확대하지 않는다.
이는 표시 호가(displayed book) 기준 반사실이며 queue position, 실제 체결, 수수료·slippage가
확정된 P&L이 아니다. 이 추가 계산은 simulation runtime에만 존재하고 live King/Queen의
1분 cycle에는 포함하지 않는다.

## 실행 시간과 외장 저장소 계약

- Gold/Silver는 승인된 `/Volumes/t7/jenkins/<job>` exact workspace, volume UUID pin, sentinel,
  job별 marker, 최소 50 GiB 여유 공간과 DB containment를 네트워크·DB 접근 전에 검증한다.
- Gold cycle에는 50초 hard deadline을 실제로 적용한다. deadline 초과는 성공으로 숨기지 않고
  run audit에 실패로 남기며 다음 minute와 겹치지 않게 한다.
- process lock 때문에 실행을 건너뛰면 별도 run audit에 `LOCK_SKIPPED`로 기록한다. 반복
  skip은 cadence 정상으로 세지 않으며 수집 건강 상태 실패다.
- Gold 수집 구간은 `[2026-09-01T00:00:00Z, 2026-10-01T00:00:00Z)`, terminal follow-up은
  `2026-10-08T00:00:00Z`까지다.

## 판정 gate

- 첫 24시간: cadence, runtime, cursor completeness, exact two-book coverage, NULL source
  minute semantics, event-cycle completeness, capacity JSON, DB quick-check, run/cohort와
  저장공간 증가량만 판정
- MLB eligible event 20개 전: 파라미터 방향 판단 금지
- 해결까지 관측된 MLB event 100개 전: 최적 entry/target/stop 또는 live 승격 결론 금지
- 종목별 parameter는 event-paired, fee/slippage sensitivity와 표시 depth coverage를 함께
  보고 별도로 동결
- CRITICAL/HIGH evidence gap, incomplete cursor, family identity drift, 1분 주기에서 반복
  lock skip가 있으면 수익성 판단을 중단하고 수집 경로부터 복구

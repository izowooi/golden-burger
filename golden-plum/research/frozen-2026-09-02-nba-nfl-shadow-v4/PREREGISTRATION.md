# Golden Plum 사전 등록 — NBA·NFL shadow v4

## 목적과 변경 경계

Golden Plum의 축구 실거래 King/Queen과 기존 Silver 축구·Gold MLB 자료 수집 조건은
바꾸지 않는다. 이번 변경은 계정과 주문이 없는 `polybot-gold`에 NBA와 NFL 자료 수집
runtime 두 개를 추가한다. 세 Gold runtime은 같은 외장 workspace에서 서로 다른 SQLite
DB를 사용하고 한 Jenkins build 안에서 병렬로 실행한다.

| Jenkins/runtime | 종목 | mode | DB 역할 |
|---|---|---|---|
| `polybot-gold/plum-shadow-gold-mlb-1m-v1` | MLB | simulation | 기존 MLB 직접 2호가 |
| `polybot-gold/plum-shadow-gold-nfl-1m-v1` | NFL | simulation | NFL 직접 2호가·증액 깊이 |
| `polybot-gold/plum-shadow-gold-nba-1m-v1` | NBA | simulation | NBA 직접 2호가·증액 깊이 |

새 runtime은 private key, funder address, signature type과 `--live`를 시작 전에 거부한다.
표시 호가 기반 가상 결과는 실제 주문·체결·실현 손익이 아니다. 기존 DB를 지우거나
합치지 않고 `config_hash × strategy_source_digest × mode × job_name`으로 분리한다.
공유 source digest가 바뀌는 기존 runtime도 배포 전후 코호트를 분리해서 분석한다.

## 검증 가설

경기 시작부터 종료까지 최상위 리그 두 팀의 직접 moneyline 양쪽 호가와 실제 주문 규모별
깊이를 보존하면, 종목별 진입·익절·손절 후보와 향후 주문 규모를 같은 경기 단위로 검증할
수 있다. 현재 `[0.75,0.78]`, 3회 상승, 누적 `+0.02`, 목표 `0.95`, 손절 `-0.15`는
수집 경로를 검증하기 위한 초기 가상 경로일 뿐 최적 파라미터라는 가정은 하지 않는다.

## NFL 모집단

- Gamma sport id `10`, primary tag `450`, root series `10187`, team league `nfl`
- 정규시즌, NFL 플레이오프와 Super Bowl의 실제 두 팀 경기
- root series 또는 `<family>-YYYY` 공식 시즌 series 하나와 NFL 소속 두 팀이 모두 확인된
  whole-game 최상위 two-team moneyline만 포함
- 대학 미식축구, 하위리그, futures, conference/division/championship winner, player prop,
  spread, total, quarter/half market, child event와 esports 제외

## NBA 모집단

- Gamma sport id `34`, primary tag `745`, root series `10345`, team league `nba`
- 정규시즌, NBA Cup의 실제 두 팀 경기, play-in, 플레이오프와 NBA Finals
- root series 또는 `<family>-YYYY` 공식 시즌 series 하나와 NBA 소속 두 팀이 모두 확인된
  whole-game 최상위 two-team moneyline만 포함
- 대학농구, G League, Summer League, futures, conference/championship winner, player prop,
  spread, total, quarter/half market, child event와 esports 제외

대회명 문자열만으로 포함시키지 않는다. 공식 종목 식별자, 시즌 series, 정확히 두 개의
최상위 리그 팀, 직접 moneyline 구조가 함께 맞아야 한다. 따라서 1군 팀이 참가하는 주요
대회의 실제 경기는 포함하고 대회 우승자 예측이나 소속이 다른 팀 경기는 제외한다.

## 수집·실행 계약

- Gamma keyset은 종목별 tag, `live=true`, `closed=false`, `related_tags=false`, 누적 거래량
  5,000 이상, 유동성 5,000 이상을 서버에서 먼저 적용한다.
- 종목별 최대 2페이지를 cursor 끝까지 읽는다. cap을 넘거나 cursor가 반복되면 일부 결과를
  성공으로 저장하지 않고 해당 cycle을 실패한다.
- 경기 시작부터 Gamma `ended=true` 전까지 두 팀 token의 exact `$5` full-depth ask/bid를
  같은 cycle에 저장한다. 한쪽 호가가 없으면 완전한 event set으로 세지 않는다.
- 농구 quarter와 미식축구 quarter를 축구 minute로 변환하지 않는다.
  `source_elapsed_minutes`는 NULL이고 UTC snapshot 간격과 명시적 live/ended 상태를 쓴다.
- 같은 cached full book으로 `$5/$10/$25/$50/$100/$250/$500`의 매수·매도 전량 가능 여부,
  VWAP, 최악 가격, 사용 level 수와 잔여량을 계산한다. 추가 CLOB 요청은 만들지 않는다.
- cadence는 각 runtime 60초, hard deadline은 50초다. MLB·NFL·NBA를 병렬 실행하여 세
  runtime의 합 때문에 다음 분 build와 겹치지 않게 한다. runtime별 nonblocking lock은
  독립적이다.
- 외장 workspace는 `/Volumes/t7/jenkins/polybot-gold` exact 경로, APFS UUID pin,
  sentinel, marker와 50 GiB 여유 공간을 네트워크·DB 접근 전에 검증한다.

## 가상 경로와 탐색 격자

- entry: `0.55/0.60/0.65/0.70/0.75/0.80`
- target: `0.85/0.90/0.95`
- stop delta: `0.05/0.10/0.15/0.20`
- trend observations: `2/3/5`
- minimum cumulative move: `0.01/0.02/0.03/0.05`
- displayed notional: `5/10/25/50/100/250/500 USDC`

같은 경기의 여러 격자 행은 독립 표본이 아니다. primary episode는 유일한 선두가 3회의
90초 이내 snapshot에서 누적 0.02 이상 상승하고 `[0.75,0.78]`을 처음 위로 통과할 때만
생성한다. 목표 0.95, 손절 entry-0.15, 시간 강제 청산 없음으로 기록하되 이 값을 선택하는
성과 판단은 종목별 100경기 전까지 하지 않는다.

## 기간과 사후 추적

- 신규 가상 episode 기간: `[2026-09-02T10:30:00Z, 2026-12-01T10:30:00Z)`
- terminal follow-up 종료: `2026-12-08T10:30:00Z`
- 실제 수집 시작은 배포 후 runtime별 첫 성공 run이며, 그 이전 구간을 정상 cadence로
  간주하지 않는다.
- live discovery에서 사라진 event는 one-hot terminal 결과까지 bounded follow-up한다.
  끝까지 해결 증거가 없으면 오른쪽 잘림(right-censored)으로 남기고 결과를 추정하지 않는다.

## 판정 기준

- 첫 24시간: runtime별 cadence, cursor 완료, 완전한 2호가 event set, family/season/team
  identity, 증액 깊이 JSON, 경로·해결 추적, DB 무결성, 코호트와 저장공간 증가만 판정
- 종목별 eligible event 20개 전: 파라미터 방향 판단 금지
- 종목별 해결 event 100개 전: 최적 진입·목표·손절이나 live 승격 결론 금지
- 페이지 미완료, family identity drift, 반복 lock skip, event set 누락, DB 무결성 오류가
  있으면 성과 분석을 중단하고 수집 경로부터 복구
- 향후 금액 확대는 displayed depth와 실제 소액 체결 자료를 함께 확인하며 `$5` 결과를
  단순 선형 배수로 환산하지 않음

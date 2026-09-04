# Golden Cherry·Blueberry·Tangerine 회고와 스포츠 런타임 안전성 보강

- 작성일: 2026-09-05 KST
- 코드 배포 기준: `61139bdcbce3f728fa850b944939d8217ff90f74`
- 성과 원칙: `docs/retro/EVIDENCE_CONTRACT.md`
- 핵심 단위: 요청 주문이 아니라 정확히 대사된 `CONFIRMED` 체결·수수료·정확한 token 해결 증거

## 1. 집행 결론

| 전략 | Jenkins | 현재 판정 | 파라미터 결론 | 운영 결론 |
|---|---|---|---|---|
| Golden Cherry | `polybot-yellow` | 현재 전략 기각, 새 가설만 다시 검증 | 전체 0.75–0.88은 개선 근거 없음. 0.80–0.82+해결까지 보유는 후보 | 신규 매수 금지. 기존 보유분 관리와 계정 대사만 별도 결정 |
| Golden Blueberry | `polybot-eagle` A, `polybot-cherry` B | 판정 불가 | +2%p / +5%p A/B를 유지. 표본이 현저히 부족 | 각 $5 유지, 증액 금지. 새 계측 cohort로 적립 |
| Golden Tangerine | `polybot-orange` A, `polybot-fox` B | 0.92가 0.94보다 유망하지만 검증 미완료 | 0.92/0.94를 검증 구간에서 유지 | 각 $5, 이벤트당 1건, 계정당 3건 유지. A/B 시각 정렬 필수 |

단위 증액을 권고할 전략은 현재 하나도 없다. Cherry는 실제 손실과 미해결 주문 증거 때문에 신규 진입을 멈추는 것이 맞고, Blueberry는 표본이 너무 적으며, Tangerine은 후향적 자료에서 0.92가 좋았지만 아직 독립 검증 자료가 0건이다.

## 2. 자료 확보와 무결성

`daily-rsync scan → plan → sync → verify`를 Jenkins job×strategy×runtime 경계로 분리해 수행했다. 모두 원격 SQLite 작성자와 경쟁하지 않은 시점의 검증된 복제본이다.

| Jenkins | 전략/runtime | 원격 크기 | `daily-rsync verify` |
|---|---|---:|---:|
| `polybot-yellow` | Cherry `default` | 1.50 GiB | SUCCESS, 8,355건 확인, 실패 0 |
| `polybot-eagle` | Blueberry `blueberry-live-a-2pp` | 1.36 GiB | SUCCESS, 7,158건 확인, 실패 0 |
| `polybot-cherry` | Blueberry `blueberry-live-b-5pp` | 1.92 GiB | SUCCESS, 583건 확인, 실패 0 |
| `polybot-orange` | Tangerine `tangerine-live-a-94` | 568.8 MiB | SUCCESS, 1,464건 확인, 실패 0 |
| `polybot-fox` | Tangerine `tangerine-live-b-92` | 455.4 MiB | SUCCESS, 1,466건 확인, 실패 0 |
| `polybot-shadow` | Blueberry shadow | 525.4 MiB | SUCCESS, 4,586건 확인, 실패 0 |
| `polybot-black` | Tangerine 대조 shadow | 13.42 GiB | SUCCESS, 884건 확인, 실패 0 |

Black은 첫 복제 중 원격 DB가 변경되어 검증을 반드시 다시 시작했다. 수집을 잠시 멈춘 뒤 새 plan으로 14,340,266,030 byte를 다시 검증했고, 최종 실패·충돌은 0이다. 부분 동기화를 성과 근거로 사용하지 않았다.

### 증거 해석의 경계

- GTC `accepted` 또는 `orderID`는 체결이 아니다.
- 실현 손익은 정확한 BUY·SELL `CONFIRMED` 수량/VWAP·수수료만 사용한다.
- 해결 보유 손익은 Gamma의 `closed=true`, 정확한 condition/token 정렬, 유일한 0/1 결과를 모두 만족한 건만 사용한다.
- 과거 `trades.realized_pnl`은 요청 가격×요청 수량을 기준으로 한 구간이 있어 주 지표에서 제외했다.
- 과거 결과로 수치를 고른 분석은 탐색 근거일 뿐이며, 앞으로 모은 독립 검증 자료를 대체하지 않는다.

## 3. Golden Cherry / `polybot-yellow`

### 3.1 현재 계정·DB 판정

| 항목 | 결과 |
|---|---:|
| 정확한 confirmed SELL 순손익 | **-$44.847137** |
| 정확한 token 해결 가정 손익 | **-$100.314853** |
| 겹치지 않는 정확 경제적 합계 | **-$145.161990** |
| 출처를 확정하지 못한 BUY 예약 | 14건, 보수적 예약액 $10,184.893276 |
| 현재 관리되는 오픈 포지션 | 4건, 약 $20.1056 |

다음 두 가지 조건 중 하나라도 있으면 새 BUY를 차단하도록 코드를 바꿖었다.

1. 정확한 누적 경제적 손익이 기본 `-$30` 아래인 경우
2. 실제 POST·체결 여부를 입증하지 못한 BUY intent가 남아 있는 경우

따라서 Yellow를 `active`로 실행해도 현재 DB에서는 새 매수가 발생하지 않는다. 다만 보유분 확인·해결은 실행되므로, 자동 실행을 다시 켜는 행위는 현실 자산을 변경할 수 있는 별도 운영 결정이다.

### 3.2 정확한 해결까지 보유했을 때

검토 구간은 `[2026-08-14T00:00:00Z, 분석 시점)`이다. confirmed BUY 1,102건을 조회했고, 정확한 해결이 있는 1,099건을 사용했다.

| 구간 | n | 승 | 패 | 해결까지 보유 ROI |
|---|---:|---:|---:|---:|
| 전체 진입 0.75–0.88 | 1,099 | 883 | 216 | **-1.1966%** |
| 경기 전 | 173 | 146 | 27 | +4.4112% |
| 경기 중 | 926 | 737 | 189 | **-2.2527%** |
| 0.76–0.78 | 203 | 159 | 44 | +1.5485% |
| **0.80–0.82** | **190** | **165** | **25** | **+6.7507%** |
| 0.84–0.86 | 159 | — | — | -1.9877% |

0.80–0.82의 단순 이벤트 bootstrap 95% 구간은 약 `+0.45%∼+12.56%`였다. 첫 시간 반은 +9.75%, 뒤 시간 반은 +4.38%로 둘 다 양수였다. 하지만 이는 여러 구간을 후향적으로 비교한 후보이고, 분석 단위도 아예 진입하지 않은 시장을 포함한 무작위 모집단이 아니다. 즉시 실거래 수치로 쓰면 안 된다.

현재 청산 방식의 문제는 더 명확했다. 0.80–0.82 영역의 성숙한 실제 청산 196건은 ROI **-2.742%**였다. 정확히 비교 가능한 188건에서 실제 청산 손익은 `-$29.298`, 해결까지 보유했을 때는 `+$67.345`로 차이가 `+$96.643`이었다. 결국 승리한 포지션 82건을 stop/trailing이 중간에 끊어 실제로는 `-$49.916`, 해결 보유 가정으로는 `+$93.756`이다.

### 3.3 새로 배포한 Cherry 가상 실험

`polybot-cherry-shadow` / `cherry-shadow-resolution-v2`를 새로 만들었다.

- 운영: 계정·서명·주문·POST가 없는 public GET-only
- 저장소: `/Volumes/t7/jenkins/polybot-cherry-shadow`
- 주기: `H/5 * * * *`
- 진입 수집: `[2026-09-04T16:00:00Z, 2026-10-04T16:00:00Z)` = `[2026-09-05 01:00, 2026-10-05 01:00)` KST
- 추가 해결 추적: `[2026-10-04T16:00:00Z, 2026-11-03T16:00:00Z)`
- 진입 cell: 0.76–0.78 / **0.80–0.82** / 0.84–0.86의 정확한 $5 ask VWAP
- 청산: 해결까지 보유, 현재 TP/SL/trailing, 한 조건씩 바꾼 민감도 조합을 같은 path에서 동시 비교
- 다른 DB와 합치지 않는 append-only `trades_sim.db`
- 식별자: config `1a7a361d...`, source `9234ca4e...`, preregistration `72d87684...`

첫 구성 시 입력기 중복으로 build #1이 shell syntax 실패했다. 즉시 구성을 교정했고 #2는 6.699초에 SUCCESS였다. #2는 사전 등록 시작 전이라 네트워크/DB를 만지지 않고 정상적으로 `before_preregistered_start`를 기록했다.

시작 경계 직후 실행한 #7은 6.530초에 SUCCESS였다. 4.860초 동안 7 page의 raw market 669건을 terminal cursor로 완주했고, 후보 3건의 exact $5 book, 9건의 band decision, episode/path 3건을 저장했다. 제한에 걸린 후보·실패 run은 0이고 `quick_check=ok`였다. `daily-rsync` run `ffde9bdb214d44258080485e21e73944`로 DB·console 8건/3,431,917 byte를 동기화했고, 후속 verify는 확인 7건·실패/충돌 0으로 SUCCESS였다. 동기 DB를 read-only analyzer로 다시 열었을 때 cohort 1개, cursor-complete 1/1, 관찰 book 3/3, 종료 전 censor 3건, DB SHA-256 `cbc720a0c6a7…`를 확인했고 승자는 선택하지 않았다.

다음 자연 timer build #8도 5.297초에 SUCCESS였고 collector 본체는 2.301초였다. 동일한 7 page/669 market/3 book을 완주했으며 기존 episode 3건의 path만 추가하고 새 episode는 0건이어서 최초 교차 유일성도 유지됐다. 두 active cycle 누적은 sweep 2, market observation 1,338, book/path 6, decision 18, episode 3, failed 0, `quick_check=ok`이다.

## 4. Golden Blueberry / `polybot-eagle`·`polybot-cherry`·`polybot-shadow`

### 4.1 성과 판정

가장 최근의 동일 source cohort에서 A(+2%p)는 정확한 종결 2건, 순손익 `+$0.1148`이고 B(+5%p)는 0건이다. 2026-08-31 이후로 조금 넓혀도 A 3건 `+$0.6035`, B 1건 `+$0.4887`이다. 이런 표본은 승자나 수치를 고를 수 없다.

과거 shadow DB에는 한 condition이 예상보다 네 번 저장된 정확 중복과, 새 구간 104행의 cohort 식별자 누락이 있었다. 따라서 과거 shadow 성과는 `NOT_EVALUABLE_EVIDENCE_CONTRACT`(증거 계약상 평가 불가)로 두었다. 과거 DB를 고쳐 정상인 척하지 않고, 새 코드부터 정확한 source digest·config·mode·job 식별자와 유일성을 저장한다.

### 4.2 수정한 안전장치

- 동일 DB 프로세스 잠금: 이전 실행이 안 끝나면 다음 실행은 비차단으로 건너뛴다.
- Gamma 전체 sweep 최대 100 page, 225초 예산, 각 요청 timeout·일시 403/429/5xx 유한 재시도.
- 새 BUY 전에 미확정 BUY intent·pending·quarantine를 capacity에 예약.
- 10분을 넘긴 GTC BUY는 취소·대사되기 전까지 새 노출을 허용하지 않음.
- terminal partial BUY는 실제 confirmed 수량만 HOLDING으로 관리.
- SELL은 정확한 BUY/SELL full fill, 수량 일치, 수수료 완전성 후에만 COMPLETED.
- 0.01 share 미만의 확인된 먼지 잔량은 `RESIDUAL`로 분리하고 가짜 종결하지 않음.

### 4.3 실행 시간

최근 평균은 약 26–27초였지만, 과거에는 300초를 넘긴 건과 shadow 최대 1,704초가 있었다. 배포 후 `polybot-shadow` #8595의 첫 전체 Gamma sweep은 65 page/6,458 market, 100.194초가 걸렸다. 같은 검증된 5분 cache를 사용한 자연 실행 #8596은 Jenkins 9.494초, 봇 본체 5.072초였다. 두 build 모두 `61139bd`와 cursor-complete sweep으로 SUCCESS였다.

### 4.4 권고

- +2%p/+5%p와 $5를 그대로 둔다.
- arm당 최소 100건의 정확한 독립 종결, 해결 coverage 95% 이상, 실행 편차 허용 범위를 채우기 전에 수치·금액을 바꾸지 않는다.

## 5. Golden Tangerine / `polybot-orange`·`polybot-fox`·`polybot-black`

### 5.1 실거래 성과

| arm | 해결 건수 | 승률 | 평균 진입 | 정확 순손익 | ROI |
|---|---:|---:|---:|---:|---:|
| Orange A 0.94–0.95 | 87 | 91.95% | 0.9446 | **-$11.418854** | **-2.625%** |
| Fox B 0.92–0.93 | 141 | 92.20% | 0.9229 | **-$0.115866** | **-0.016%** |

같은 condition/outcome으로 둘 다 해결된 paired 20건은 A `+$0.6731`, B `+$2.5012`였다. 그러나 A-only 67건이 `-$12.09`, B-only 121건이 `-$2.617`이어서 arm 출발 시각이 다른 상태에서의 단순 총액 비교는 무작위 A/B가 아니다.

기존 `H/5`에서 두 arm의 최근 sweep 시간 차이는 중앙값 65.45초, 95% 지점 80.49초, 최대 813.10초였다. 즉 서로 다른 호가를 봤다. 재개할 때는 Orange/Fox 모두 `*/5 * * * *`로 맞춰야 한다.

Orange의 PENDING_BUY 1건은 요청 5.2631 share와 confirmed 5.26852 share가 `$0.005094` 정도 차이 나는 정상적 FOK 종단 체결이었다. 과거 코드가 이를 수량 불일치로 오판했다. 새 코드는 인증된 주문/거래 catalog·정확 token·실제 경제 차이 1 cent 이하를 모두 입증해 HOLDING으로 복구한다. 0.5/0.5로 해결된 void 1건도 유일한 `umaResolutionStatus=resolved`일 때만 0 손익으로 종결한다.

### 5.2 Black 계정 없는 대조 수집기

Black은 4,269회 cursor-complete sweep, 2,418,737 market observation을 생성했고 DB `quick_check=ok`, 수집 문제 0이었다.

| 진입 arm | episode / 해결 | 이벤트 | 승률 | event-equal fee-net ROI | +1 cent 비용 ROI |
|---|---:|---:|---:|---:|---:|
| **0.92** | 354 / 349 | 285 | 95.42% | **+2.5117%** (95% CI -0.04∼+4.76) | +1.4710% |
| 0.94 | 612 / 603 | 433 | 93.37% | **-0.7805%** (95% CI -3.02∼+1.26) | -1.7708% |

0.92 arm의 stop은 해결 보유 +2.51%보다 모두 나쁘다: stop 0.60 `-6.06%`, 0.70 `-6.26%`, 0.80 `-7.43%`. 0.94 arm도 해결 보유 `-0.78%`에 비해 stop 0.60 `-8.05%`, 0.70 `-8.85%`, 0.80 `-9.65%`였다. 편향이 최대화된 후향 train 구간 결과이므로 지금 0.92로 합치지 않는다.

Black의 독립 validation은 `2026-09-05T00:00:00Z`(09:00 KST)에 시작한다. 이 경계 전인 2026-09-05 00:22 KST에 `H/5` 수집을 다시 켰고, 직전 14.34GB DB를 완전히 검증했다. 9월 19일 진입 수집 종료·10월 19일 후속 해결 종료 전에 승자를 확정하지 않는다.

### 5.3 새 실거래 방어선

- exact $5를 코드로 고정하고 임의 증액 override를 거부.
- 총 3건 / event 1건 / cycle 1건의 exposure 한도와 $15 open notional을 강제.
- 정확한 누적 손실 $15에서 신규 BUY를 자동 중지.
- POST 전 후보를 DB에 예약해 중간 crash·알 수 없는 응답을 capacity에 포함.
- 과거 부분 체결·fee 0 오판·반올림 차이를 정확 증거로만 복구.
- 수동 wallet 포지션을 bot trade로 편입하거나 청산하지 않음.
- e-sports는 즉시 제외하지 않음. B에서는 e-sports가 양수였고 Black validation과 universe를 달리하면 비교가 무효화되기 때문이다. 대신 종목 strata를 분리 보고한다.

## 6. Watermelon → Peach → Plum 안전장치 역상 점검

최신 Plum에만 있던 방어선이 Peach/Watermelon에 빠지지 않도록 다음을 적용했다. 신호 수치·A/B 파라미터·$5는 바꾸지 않았다.

| 방어선 | Watermelon research | Watermelon Live | Peach | Plum |
|---|---|---|---|---|
| 하나의 주문 오류가 다른 후보를 중단하지 않음 | 해당 없음 | 적용 | 적용 | 적용 |
| pending/unknown BUY를 max-position에 예약 | 해당 없음 | 적용 | 적용 | 적용 |
| SELL 접수를 체결로 오인하지 않음 | 해당 없음 | 적용 | 적용 | 적용 |
| 주문 실패를 event-local로 격리 | 해당 없음 | 적용 | 적용 | 적용 |
| fail-closed, transaction 단위 schema migration | 적용 | 적용 | 적용 | 적용 |
| `closed=true`의 0.5/0.5 void를 권위적 해결 상태일 때만 인정 | 적용 | 적용 | 적용 | 적용 |
| active API에서 사라진 시장을 condition-ID closed fallback으로 후속 | 적용 | 적용 | 적용 | 적용 |
| 1분 job의 network 42초 / hard 50초 예산 | 적용 | 적용 | 적용 | 적용 |
| 동일 DB 중복 작성 잠금 | 적용 | 적용 | 적용 | 적용 |
| 시뮬레이션 행은 venue fill 칼럼이 없어도 capacity를 가짜로 차단하지 않음 | 적용 | 해당 없음 | 적용 | 적용 |

Watermelon research는 축구·MLB·NBA·NFL·NHL family를 독립 worker로 병렬 수집하고, 한 family의 느린 API가 전체 1분 cycle을 잡아먹지 않게 했다. NBA G League·Summer League처럼 1군 정규 모집단이 아닌 태그는 fail-closed로 제외한다.

## 7. 배포·실행 검증

### 7.1 로컬 검증

| 프로젝트 | 시험 |
|---|---:|
| Golden Cherry | 143 passed + sdist/wheel build |
| Golden Blueberry | 376 passed + sdist/wheel build |
| Golden Tangerine | 101 passed + sdist/wheel build |
| Golden Peach | 254 passed + sdist/wheel build |
| Golden Plum | 306 passed + sdist/wheel build |
| Golden Watermelon | 129 passed + sdist/wheel build |
| Golden Watermelon Live | 225 passed + sdist/wheel build |
| 모노레포 전략 계약 | **28 strategies PASS** |

`git diff --check`, 스테이징 민감정보 탐지, `.env*`·`*.key`·`*.pem` ignore 규칙을 통과했다. 사용자의 기존 추적/미추적 파일은 스테이징하지 않았다.

### 7.2 계정 없는 Jenkins 검증

| Jenkins | commit | 최종 자연 실행 | 결과 / 시간 |
|---|---|---:|---|
| `polybot-black` | 기존 Golden Black | #4269 | SUCCESS, 117.542s; `H/5` 복구 |
| `polybot-shadow` | `61139bd` | #8596 | SUCCESS, 9.494s; bot 5.072s |
| `polybot-white` | `61139bd` | #17309 | SUCCESS, 4.078s; bot 3.211s |
| `polybot-grey` | `61139bd` | #9580 | SUCCESS, 14.544s |
| `polybot-gold` | `61139bd` | #5503 | SUCCESS, 16.138s |
| `polybot-silver` | `61139bd` | #5738 | SUCCESS, 11.130s; bot 3.098s |
| `polybot-cherry-shadow` | `61139bd` | #8 | SUCCESS, 5.297s; natural collection 2.301s |

Black/Cherry/Blueberry는 5분, White/Grey/Gold/Silver는 1분 timer를 복구했다. White/Grey/Gold/Silver DB는 외장 T7에 계속 있으며, 코드만 정확히 `61139bd`로 갱신했다. 로그의 `Failed BUY/SELL containment`는 실패 발생 보고가 아니라, “실패해도 다른 event를 계속 처리한다”는 config 설명이다.

### 7.3 실거래 배포 상태

`polybot-yellow/eagle/cherry/orange/fox/cat/dog/bear/tiger/lion/wolf/eco/fruit` workspace의 코드는 자동 build를 멈춘 상태에서 `61139bd`로 안전하게 배치했다. 실거래 cycle은 실행하지 않았다. `polybot-king/queen`은 1분 timer가 켜져 있어 실행 중 코드 교체를 하지 않았다. 이 두 job은 사용자의 실거래 재개/일시 정지 확인 후 교체해야 한다.

실거래 timer를 다시 켜면 주문이 발생할 수 있으므로, 자동 재개는 보고서 작성 시점에 수행하지 않았다. 재개할 때는 Yellow는 신규 진입 차단을 로그로 확인하고, Orange의 기존 PENDING_BUY/void를 정상화한 뒤, 모든 job에서 첫 1회+자연 2회를 검증해야 한다.

## 8. 2026–27 시즌 기간과 주요 대회

날짜는 각 리그의 공식 현지 일자를 기준으로 한다. 개별 경기의 한국 시간은 일광절약시간·세부 편성에 따라 바뀌므로 진입 로직은 공식 source clock을 사용해야 한다.

### 유럽 축구 5대 리그

| 리그 | 2026–27 시즌 | 비고 |
|---|---|---|
| Premier League | **2026-08-21 ∼ 2027-05-30** | 공식 개막전은 8/21 20:00 BST |
| LaLiga | **2026-08-15 ∼ 2027-05-30** | 1라운드 8/15–27, 38라운드 5/30 |
| Serie A | **2026-08-22/23 ∼ 2027-05-30** | 공식 발표는 8/23 weekend |
| Bundesliga | **2026-08-28 ∼ 2027-05-22** | Matchday 1은 8/28–30 |
| Ligue 1 | **2026-08-21 ∼ 2027-05-29** | 마지막 34라운드 5/29 |

공식 출처: [Premier League](https://www.premierleague.com/en/news/4468487/dates-for-202627-premier-league-season-confirmed), [LaLiga](https://www.laliga.com/calendar-2026-2027/laliga-easports), [Serie A](https://www.legaseriea.it/serie-a/news/le-date-della-stagione-2026-2027), [Bundesliga](https://www.bundesliga.com/en/bundesliga/news/calendar-for-2026-27-season-world-cup-34676), [Ligue 1](https://www.lfp.fr/article/ligue-1-mc-donald-s-le-calendrier-de-la-saison-2026-2027)

### 미국 4대 종목

| 종목 | 정규 시즌 | 포스트시즌·주요 이벤트 |
|---|---|---|
| MLB | **2026-03-25 ∼ 09-27** | 포스트시즌 9/29∼10/31, World Series 10/23∼10/31(최대 7차전) |
| NFL | **2026-09-09 ∼ 2027-01-10** | Wild Card 1/16–18, Divisional 1/23–24, Conference Final 1/31, Super Bowl LXI 2/14 현지(2/15 KST) |
| NBA | **2026-10-20 ∼ 2027-04-11** | NBA Cup 10/30∼12/11; playoff/Finals 세부 일자는 본 공식 발표에서 미정 |
| NHL | **2026-09-29 ∼ 2027-04-10** | 84경기 체제; Stanley Cup playoff/Final 세부 일자 미정 |

공식 출처: [MLB 정규 시즌](https://www.mlb.com/news/mlb-2026-schedule-released), [MLB 포스트시즌·World Series](https://www.mlb.com/news/2026-mlb-playoff-and-world-series-schedule), [NFL 주요 일정](https://operations.nfl.com/calendar-events/nfl-important-dates), [NBA](https://www.nba.com/news/2026-27-nba-regular-season-schedule), [NHL](https://www.nhl.com/news/nhl-announces-2026-27-regular-season-schedule)

### UEFA 주요 대회

| 대회 | 리그 phase | 토너먼트 마지막 구간 | 결승 |
|---|---|---|---|
| Champions League | **2026-09-08 ∼ 2027-01-27** | knockout playoff 2/16–24, 16강 3/9–17, 8강 4/6–14, 4강 4/27–5/5 | **2027-06-05 Madrid** (6/6 KST) |
| Europa League | **2026-09-16 ∼ 2027-01-28** | knockout playoff 2/18–25, 16강 3/11–18, 8강 4/8–15, 4강 4/29–5/6 | **2027-05-26 Frankfurt** (5/27 KST) |

공식 출처: [UEFA Champions League](https://www.uefa.com/uefachampionsleague/news/02a6-20d57cfcd03e-407c22a7f465-1000--2026-27-champions-league-teams-dates-draws-format-final/), [UEFA Europa League](https://www.uefa.com/uefaeuropaleague/news/02a6-20d57d095740-e1e0b3de85df-1000--2026-27-europa-league-teams-dates-draws-format-final/)

### “주요 경기도 수집하는가?”에 대한 정확한 답

그렇다. MLB postseason·World Series, NFL playoff·Super Bowl, NBA playoff/Finals, NHL playoff/Stanley Cup Final의 **개별 1군 경기**가 해당 공식 family tag 안에서 정확한 two-team whole-game moneyline으로 등록되면 수집한다. Champions League·Europa League의 개별 경기도 정확한 competition identity와 승/무/패 whole-match 조건을 만족하면 포함한다.

반면 “월드시리즈 우승팀”, “Super Bowl 우승팀”, “디비전 우승”처럼 시즌 전체를 걸고 베팅하는 futures/prop은 제외한다. 이는 누락이 아니라, 현재 가설이 “진행 중인 하나의 경기”의 path를 검증하기 때문이다.

## 9. 다음 검토 일정

| 시점 | 확인할 내용 | 파라미터/수익 판정 |
|---|---|---|
| 2026-09-06 01:00 KST 이후 | Cherry shadow 첫 24시간: cadence, cursor, membership, book, path, DB, 용량 | 하지 않음 |
| 2026-09-12 01:00 KST 이후 | Cherry shadow 7일 수집 coverage·이벤트 clustering·해결 후속 | 하지 않음 |
| 2026-09-19 이후 | Tangerine Black validation 진입 구간 종료 건강성 | 아직 승자 확정 금지 |
| 2026-10-05 01:00 KST 이후 | Cherry 30일 진입 종료, 새 episode 수집 중지 확인 | 중간 수익 확정 금지 |
| 2026-10-19 이후 | Tangerine Black 후속 해결 종료 후 0.92/0.94 최종 비교 | 가능 |
| 2026-11-04 01:00 KST 이후 | Cherry shadow 후속 해결 종료, event-clustered 최종 비교 | 가능 |
| Blueberry arm당 정확 종결 100건 이상 | +2%p/+5%p 비교, fill/resolution coverage, 시간 편차 | 가능 |

Cherry 24시간 점검 예시:

> `polybot-cherry-shadow를 daily-rsync로 동기화하고 cherry-shadow-resolution-v2의 [2026-09-04T16:00:00Z, 2026-09-05T16:00:00Z) 첫 24시간 collection health를 검증해줘. 수익성이나 승자는 판단하지 말고 cadence, terminal cursor, membership, exact $5 book, entry/path/resolution, cohort, DB 무결성과 저장공간 증가량만 확인해줘.`

## 10. 남은 운영 판단

1. **Yellow**: 현재 자동 실행을 계속 멈춘다. 4건의 보유분과 14건 unknown BUY 증거를 운영자가 어떻게 다룰지 결정한 후 close-management만 일시 켤 수 있다.
2. **Blueberry A/B**: 새 증거 계약으로 +2/+5, $5를 다시 수집한다. 현재 자료로는 파라미터를 변경하지 않는다.
3. **Tangerine A/B**: 0.92/0.94, $5를 유지하되 두 timer를 같은 `*/5`로 맞춘다. Black validation이 완료되기 전에 0.92로 합치지 않는다.
4. **스포츠 live**: 새 코드를 배치했지만 timer는 다시 켜지 않았다. 실제 $5 주문이 발생할 수 있는 재개는 명시적 운영 확인 후 수행한다.

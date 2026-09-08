# 2026-09-08 고정 스포츠 후보 후속 검증

새 축구 4경기·MLB 11경기로 전일 후보를 후속 검증했다. 전일 좋아 보였던 Plum .74 추세 후보는 손실로 바뀌었다. 추세 없는 .70 가격대 후보와 일부 Peach 대안은 양수였지만, 높은 미래 순이익 확률이나 실거래 승격을 입증한 표본은 아니다. White의 실제 수집 실패와 Guava 감사의 산술 오류도 수정했다.

## 자료와 검증 범위

- 고정 금융 검증 구간: UTC `[2026-09-07T15:10:00Z, 2026-09-08T10:00:00Z)`.
- 연구 운영 24시간 점검: `[2026-09-07T10:04:00Z, 2026-09-08T10:04:00Z)`. 두 구간을 혼동하지 않는다.
- Gold 4·Silver 1·Grey 5·Guava 4 DB와 White parent/sidecar 2 DB를 각각 표준 scan/plan/sync/verify/pin으로 확보했다. White 두 파일은 하나의 paired view이며 두 거래가 아니다.
- 새 고유 경기는 **15개**다. P/P와 Guava, White의 같은 경기를 더해 45개로 부르지 않는다. 세 분석의 source/cohort/event 단위는 합계 60개이며 독립 경기 수가 아니다.
- 전일 코드에 이미 있던 74개 focus 사양과 26개 named 대조 사양을 새 가격·손익을 읽기 전에 고정했다. 문서 작성 자체는 관측 구간이 지난 뒤이므로 문서 전체가 사전 등록됐다고 주장하지 않는다. 원 manifest SHA는 `52efb553a0d7b93716a71cb1a9506586a297013fcdc9db5b185136fa486e015d`다.
- 주비교 500개 사양, 사전에 정한 stop 민감도 포함 3,830개 사양이다. 실제 주결과 표는 444개이며 Guava C에 축구 표본이 없는 56개를 삭제해 다중 비교 분모를 줄이지 않았다. 새 결과에 맞춰 entry/TP/stop/fee를 다시 선택하지 않았다. P/P 19,140행, Guava 21,300행, White 2,160행을 각각 한 번 재생했다. 기존 655만 조합을 덮어쓰지 않았다.

모든 손익은 실제 체결이 아닌 **표시 full-book 반사실**이다. $5 BUY notional에 실제 관측 fee를 추가하고, SELL 수량 0.01주 내림과 dust 0 회수를 적용한다. 목표가를 보았다는 이유만으로 그 가격에 팔았다고 하지 않는다. 같은 시각 매수·매도, 실제 매수가 이하 target, 없는 중간 가격은 거절한다. FAILED·90초 초과 공백·선택 token의 누락은 검열하며 0손익으로 채우지 않는다.

## Plum: 좁은 목표가만으로 수익이 보장되지 않는다

주 수집원 Gold MLB와 Silver 축구의 **기본 stop·관측 fee** 결과다. 각 source의 동일 경기 모집단 안에서 대안 정책을 비교한 것이며, 아래 네 줄을 독립 거래로 합산하지 않는다.

| 고정 연구 정책 | 종목 / 모집단 | 진입·완결 | 양수 | 순손익 USDC |
|---|---|---:|---:|---:|
| .74 추세 확인 / 실제 BUY+.01 | MLB / 11경기 | 7 / 7 | 3 | −3.40906 |
| .74 추세 확인 / 실제 BUY+.01 | 축구 / 4경기 | 2 / 2 | 1 | −2.01132 |
| 추세 없는 .70..73 가격대 / 명목 TP .72 | MLB / 11경기 | 7 / 7 | 6 | +0.69972 |
| 추세 없는 .70..73 가격대 / 명목 TP .72 | 축구 / 4경기 | 2 / 2 | 2 | +1.00653 |

MLB 추세 후보는 전일의 고정 `5회/+ .01`, 축구는 `3회/+ .02`다. Gold의 실제 collection 설정 `.75..78/3회+.02/TP .95/SL .15`와 같다고 부르지 않는다. 위 MLB 연구 stop은 BUY−.12, 축구는 BUY−.15다. 수집 설정과 연구 반사실 정책은 서로 다른 축이다.

.70 정책은 상향 돌파를 추가로 요구하지 않는 **가격대 조건**이다. 실제 BUY≥.72이면 그 event의 첫 후보를 거절한다. TP도 전량 bid와 비용 후 $0.001 초과를 함께 요구하므로, .72에 무조건 파는 정책이 아니다.

| 경기 | 모형이 선택한 결과 | BUY 호가 VWAP | SELL 호가 VWAP | 종료 | fee-net USDC |
|---|---|---:|---:|---|---:|
| Atlanta–Philadelphia | Philadelphia Phillies | .71000 | .59000 | SL | −1.00405 |
| Mets–Marlins | New York Mets | .70000 | .78000 | TP | +.43294 |
| Angels–Boston | Boston Red Sox | .71000 | .74000 | TP | +.06938 |
| Minnesota–Detroit | Detroit Tigers | .71000 | .73710 | TP | +.04850 |
| Washington–San Diego | San Diego Padres | .70000 | .77000 | TP | +.35958 |
| Cincinnati–Dodgers | Los Angeles Dodgers | .70000 | .81000 | TP | +.65346 |
| Toronto–Athletics | Athletics | .70000 | .74000 | TP | +.13991 |
| Udinese–Lazio | Udinese 승리 명제의 NO | .70000 | .74000 | TP | +.13991 |
| Getafe–Celta | Celta 승리 명제의 NO | .71000 | .85000 | TP | +.86662 |

TP 8건의 실제 bid VWAP는 .73710~.85000이었다. Boston은 비용 후 비양수인 TP 관측을 1회, Udinese NO는 2회 보류했다. 다른 TP는 첫 충족 bid가 이미 더 높았다. 명목 .72의 가상 체결 수익이 아니다. 유일한 SL인 Philadelphia는 BUY fee .07250와 SELL fee .08515를 포함해 −1.00405였다. 정확한 token/condition/UTC/수량/dust/fee 및 비진입·거절 6건도 별첨에 보존했다.

이 결과로 .74 추세 후보의 안정성이 재현됐다고 말할 수 없다. .70 가격대 후보 역시 7건·2건 표본이므로 새로운 최적값이나 확정 수익으로 승격하지 않는다.

## Peach: 순위와 진입 구간을 분리해서 본다

Grey 축구의 고정 `.60..63 / 실제 BUY+.02 / SL−.10`에서 가격대 조건은 **3위만 2진입·2완결·2양수, +.67058 USDC**였다. 다른 순위 1·2·4·5·6은 무진입이다. 직전 관측 아래→위 crossing을 요구하면 여섯 순위 모두 무진입이었다.

이 좁은 구간의 1위 무진입을 원래 최고값 전략의 반증으로 해석하면 안 된다. 원래 넓은 `.60..94` 1위 대조는 A(+.03)·B(+.05) 모두 **4진입·4완결·3양수·1SL, +.94163 USDC**였다. 3위의 2/2만으로 원래 1위보다 우월하다고 판단하지 않는다.

넓은 구간 A/B는 이번 네 경기에서 같은 호가에 청산됐다. 아래 A/B 공통 네 줄은 여덟 독립 거래가 아니다.

| 고정 Peach 대안 | 경기 | 모형이 선택한 명제 | BUY→SELL 호가 VWAP | 종료 | fee-net USDC |
|---|---|---|---|---|---:|
| 1위 A/B | Cagliari–Lecce | Lecce 승리 NO | .75→.89 | TP | +.83230 |
| 1위 A/B | Udinese–Lazio | 무승부 NO | .69→.59 | SL | −.89347 |
| 1위 A/B | Getafe–Celta | Celta 승리 NO | .71→.84 | TP | +.79379 |
| 1위 A/B | Elche–Real Sociedad | 무승부 NO | .73→.78 | TP | +.20901 |
| 3위 +.02 | Udinese–Lazio | Lazio 승리 NO | .63→.66 | TP | +.05233 |
| 3위 +.02 | Getafe–Celta | 무승부 NO | .63→.73 | TP | +.61825 |

MLB는 native kickoff clock이 검증되지 않아 해당 정책은 무진입이다. 별도 scheduled-start-age 진단에서 좁은 구간 1위 crossing은 2/2 양수 +.10162, 가격대 조건은 3건 중 2양수지만 −1.83549였다. 넓은 구간의 A(+.07)·B(+.10)는 각각 7완결·5양수이며 순손익은 −.77417 / +.71905였다. scheduled age를 실제 inning clock이나 라이브 Peach의 검증 결과로 바꾸지 않는다.

## Watermelon: 실패한 원본을 정상 가격 경로로 바꾸지 않았다

White r5 paired view의 287,187개 expected-slot 행에는 FULL 4,849개, 유효 가격 행 1,110개와 IDENTITY_MISSING 273,800개가 있다. .95/.97→.99 및 .96→.97 focus는 증거 조건을 통과한 진입이 없었다. 이것은 실제 가격 기회가 없었다는 뜻이 아니다. Named .92→.96 대조의 축구 1·MLB 1진입은 모두 FAILED/path gap으로 검열됐으며 손익을 0으로 채우지 않았다.

독립 Guava 호가로 같은 고정 Watermelon 정책을 재생했을 때, A의 **Elche–Real Sociedad / Real Sociedad 승리 YES**에서 .95→.99는 −2.01516, .97→.99는 −2.07284였다. .95 진입은 20:48:55 UTC, .97 진입은 20:52:54 UTC이고, 두 대안 모두 20:58:03 UTC의 full bid VWAP .582에서 SL이었다. stop 기준 .70에 팔았다고 가정하지 않았다. 각 진입 후 stop 전 최고 full-depth bid의 worst price는 .98/.891여서 .99 TP가 가능했다는 증거가 없다. 관측 사이 순간 가격까지 없었다고 주장하지는 않는다. MLB의 작은 양수 사례는 source별 1~2건 수준이었다. 따라서 .99 목표만으로 높은 확률의 이익이 입증됐다고 하지 않는다.

별도의 **사후 단일 사례 설명**으로 Elche의 .95 진입을 그대로 두고 TP만 .97로 바꾸면, 20:51:59.783528 UTC의 full bid .98에서 5.26주를 청산하는 반사실이 가능했고 fee-net은 **+.13715 USDC**였다(BUY fee .01250, SELL fee .00515). 손실을 본 뒤 추가한 설명이므로 원래 고정 500개 주비교나 새로운 우승 정책으로 합산하지 않았다. .97 진입은 이 .98 관측보다 늦었고, TP .97은 실제 진입가 이하라 거절된다. 이전의 좋은 가격에 나중 진입을 소급해서 팔 수 없다.

이 비교의 stop은 현재 source 규약인 `max(.70, actual BUY−.30)`이다. 전체 live runtime의 독립 Gamma+CLOB preflight, FOK 거절·지연·포트폴리오 노출까지 재현한 것은 아니다.

## Guava H1 산술 정정과 현재 상태

기존 local 감사가 Decimal의 1e−28 수준 잔여를 `gross_gap>0`으로 받아 일부 가격상 차이 0인 후보를 선택했다. 원본 decimal 가격·size를 Fraction으로 재계산해 정확한 부호를 판정했다. 임의 epsilon이나 새 threshold는 추가하지 않았다.

| 구간 / 판정 | 신호·관측 | gross 양수 / 0손익 | gross USDC | .05 fee 가정 후 USDC |
|---|---:|---:|---:|---:|
| 과거 원래 기계적 분기 | 12 / 12 | 2 / 4 | −3.12254 | −3.97649 |
| 과거 정확 양수 조건 | 9 / 9 | 1 / 3 | −.27934 | −.67607 |
| 새 24h 원래 기계적 분기 | 4 / 4 | 0 / 1 | −.47057 | −.91647 |
| 새 24h 정확 양수 조건 | 3 / 3 | 0 / 1 | −.32564 | −.57944 |

이 감사의 horizon은 60초 이후 첫 가용 관측이며 실제 보유 시간이 정확히 60초인 체결이 아니다. 원래 script와 모든 기존 결과는 보존했고 활성 local 감사 분기만 `guava-h1-exact-rational-v2`로 교정했다. runtime H1의 applicable은 complete six-book 비교 가능성을 뜻하며 양수 신호나 실거래 실행이 아니다.

새 24h Guava 4개 DB에는 고유 15경기, book 시도 7,720개와 원본 dict 7,314개/MISSING 406개가 있다. 빈 bid와 빈 ask는 각각 538개이며 서로 겹칠 수 있다. phase30 적용 후 중복·window 오류·skip은 0이었다. 새 경기 종료 후 follow-up 346개도 다음 :30을 기록했으며 연속 run gap의 최대는 81.207초였다. H2 score 변화는 59행/9경기, H3는 1,399행/10경기, H5 실행 가능 신호는 0이다. 이 행 수를 독립 거래 수로 세지 않는다.

## 수집 오류 수정과 배포

White r5는 league 분류를 통과한 하위 시장도 지속 추적에 넣었다. registry 157개 중 142개가 하프타임·정확한 스코어 등의 child였고, 20개 후속 조회 한도를 잠식했다. 24h 부모 run 1,437개 중 r5 실패는 937개였으며, raw PUBLISHED 뒤 부모 persistence가 실패한 사례도 있었다.

`73a756e`의 White r6는 child를 DISCOVERY_ONLY로 분리한다. 기존 child는 첫 불변 관측의 parentEventId로 증명한 경우만 working registry 상태를 교정하고, 원래 raw 행은 수정하지 않는다. 정상 15개 anchor와 일시적인 top-level metadata 누락은 유지한다. 종료도 raw publication과 부모 성공을 대사한 뒤 확정하여 실패 회차가 영구 조회 중지로 이어지지 않게 했다. 조회 한도 20개와 network42초/cycle50초 예산은 유지한다.

r6 수동 #22114는 SUCCESS 18.653초, 자연 #22115/#22116은 1.697/1.558초였다. 실제 142개 재분류와 정상 15경기의 후속 조회·terminal 확인을 복구했다. 종료된 시장의 book 부재 34개는 MISSING으로 보존했다. 별도 parent/sidecar pin에서 새 6개 cycle의 부모 성공과 raw publication을 대사했다. 과거 raw 439,203행과 부모 FAILED 1,566행의 변경·누락 0, 두 schema의 동일성을 확인했다. 원 config SHA와 1분 예약도 최종 확인했으며, 운영 별첨에 증거를 보관한다.

Guava는 동일한 정상 object와 JSON-wrapped string이 다르게 거절되는 오류를 수정했다. URL과 다른 필드의 @가 합쳐져 가짜 인증정보처럼 판정됐기 때문이다. 실제 인증정보·encoded secret key·plain/malformed 문자열 차단은 유지한다. 특정 D 실패의 원문은 기록 전 거절되어 없어, 그 사건의 원인이 이 재현 사례였다고 단정하지 않는다.

Guava에는 기존 운영 baseline의 evidence.py만 적용해 source `670407bd…`로 분리했다. 네 job의 수동 1회·자연 2회가 실제 저장 실행으로 확인됐고, 새 DB run 35회 모두 성공했다. phase30·원 config SHA·schema는 유지하고, 이전 분석 pin의 불변 행 388,232개에서 변경·누락 0을 확인했다.

## 재현과 남은 범위

- 코드: `tools/conservative_sports_prospective.py`, `tools/white_raw_grid.py`, `tools/watermelon_raw_sidecar.py`, `tools/guava_h1_exact_math.py`.
- 독립 시간·source·수수료·stop 및 White pair의 미래 시각/terminal/누락 회귀를 포함한 관련 100개 테스트 통과. White 전체 212개와 build, Guava 전체 854개 및 운영 baseline overlay 536개 테스트/build도 통과했다. Guava subtest는 별도 기록한다.
- 원본·fixed manifest·세 결과 디렉터리·경기별 경로·SHA·배포 검증은 local-only `docs/local/conservative-sports-20260907/resumed1/`에 있다. 기존 동결 결과를 덮지 않았다.
- 새 가격 차트에는 15경기·30 source/cohort·13,046개 관측을 생략 없이 포함했다. 종목·경기·source와 직접 YES/NO 가격을 비교할 수 있다. NBA/NFL/NHL은 이번 구간 관측 0, UFC/복싱은 미등록으로 구분한다.
- Lion/Wolf는 config-only/예약 OFF다. 현재 NFL 1분 수익 표본은 없고, 이전 Coconut 5분 NFL 시계 형식 검증은 별도의 역사 자료다. 새 높은 확률의 순이익 파라미터 확정·실거래 전환은 아직 완료되지 않았다.

# Raspberry·Strawberry 연구 종료 검토 — 2026-09-08 UTC

현재 실행 코드를 확인하면 `polybot-do/re/mi`는 Raspberry의 조건 해시 3-shard이고, `polybot-shadow-one`이 Strawberry다. 네 잡은 주문 없는 연구 수집기였다. 이번 검토 결과 현 epoch를 종료했고 DB·로그·미해결 사례를 보존했다. 같은 가설의 새 epoch나 실거래를 자동으로 시작하지 않았다.

| 대상 | 실제 검토 자료 | 판정·운영 조치 |
|---|---|---|
| Raspberry DO/RE/MI | 독립 v3 DB 3개, 15.67일 진입·최초 60~75분 후속 호가 | 등록 건강성 gate 회복 불가, 비용을 견디는 edge 근거 부족. 9/8 14:25 UTC까지 세 job 종료 확인 |
| Strawberry shadow-one | 동결 v1의 7일 신규 진입 + 별도 v2a 후속 추적 | primary의 미완결 회복 상한도 음수, 장기 시장 위주의 모집단과 추적 기간 불일치. 9/8 15:04 UTC 종료 확인 |

이는 실제 계좌 손실 확정이나 심리 가설 전체의 통계적 기각을 뜻하지 않는다. 아래 수익은 표시 ask/bid·정산 지급을 이용한 반사실이다. 10.4/72.5bps는 고정 비용 스트레스이며 실제 fee 계측값이 아니다.

## Raspberry: 기다려도 현재 30일 gate를 통과할 수 없다

정식 v3 기간은 `[2026-08-23T20:00Z,2026-09-22T20:00Z)`다. 이번 진입 구간은 `[2026-08-23T20:00Z,2026-09-08T12:00Z)`의 15.6667일이다. 과거 무효 v2와 합쳐 한 달로 세지 않았다. 13:55~13:58 source 기록까지 담은 pin으로 각 진입 +60~75분의 첫 독립 요청을 대사했으며, 해당 결과가 12:00 당시 모두 알려졌다는 뜻은 아니다.

세 job은 서로 다른 전략 arm이 아니다. 각 shard가 DO·RE·MI를 모두 계산하며 **MI만 primary**다.

| Shard | 성공 / 기대 slot | 현재 coverage | 남은 모든 slot 성공 시 30일 coverage 상한 |
|---|---:|---:|---:|
| DO | 3,763 / 4,512 | 83.40% | 91.33% |
| RE | 3,570 / 4,512 | 79.12% | 89.10% |
| MI | 3,724 / 4,512 | 82.54% | 90.88% |

30일은 shard마다 8,640 slot이다. 이미 누락된 749/942/788 slot을 뒤늦게 복원하지 않으므로 등록된 95% 건강성 기준은 수학적으로 회복 불가능하다. 공식 analyzer는 `COLLECTION_HEALTH_FAIL`이다.

| 고정 arm | qualified / quote-complete | 완결 부분 평균 gross | 72.5bps 차감 | neutral match |
|---|---:|---:|---:|---:|
| DO | 566 / 530 | −1.3588% | −2.0838% | 0.1887% |
| RE | 438 / 417 | −1.7784% | −2.5034% | 0.4796% |
| MI primary | 356 / 343 | −0.9493% | −1.6743% | 0.2915% |

MI의 초반/후반 severe 평균도 각각 −2.9802%/−0.3759%다. MI−DO paired 비교는 180/343만 성립했고 95% 하한이 −47.04bps여서 지속 관측의 추가 가치도 확인되지 않았다. Neutral match는 MI에서 1/343으로 등록 80% 기준에 크게 못 미친다.

원 analyzer의 MI cluster 88은 `shard×event` 수이며 실제 서로 다른 event_id는 74개다. 같은 event를 shard 사이에서도 묶은 descriptive bootstrap에서 MI severe의 95% 양측 구간은 [−292.29, −4.40]bps이고, 더 높은 98.33% 일측 상한은 +11.73bps다. 따라서 확실한 미래 손실이나 가설 전체 기각으로 과장하지 않는다. 건강성 회복 불가와 현재 비용 후 edge 근거 부재가 종료 근거다.

전체 normalized token coverage는 99.86~100%, same-request/raw linkage는 100%였다. 하지만 특정 run의 normalized coverage HIGH가 29회이고 RE에는 terminal 없는 STARTED 1회가 있다. 정상 late skip은 HTTP 0건으로 동작했다. 이를 중복/늦은 HTTP 전송이라고 설명하지 않는다.

운영 비용의 구체 후보도 있다. 매 5분 `run→status→health`에서 health가 status를 다시 호출하고 전체 DB `PRAGMA quick_check`를 수행했다. 세 DB 약4.3GB를 하루288회 읽으면 논리 읽기 약1.24TB/day다(실제 디스크 I/O는 cache에 따라 다름). Core p95 17~19초와 긴 outer build의 차이를 설명할 후보이나 단독 원인으로 확정하지 않았다. 새 실험 전에 이 실행 경로와 거의 없는 neutral 매칭을 설명·검증해야 하며, 같은 값을 다시 30일 돌리는 것은 권하지 않는다.

세 job의 disable 후 기존 build가 끝났고, 별도 post-stop standard scan/sync/verify/pin까지 완료했다. 마지막 미완료는 4 SIGNAL+4 OPPOSITE 대조군이다. 현재 CLI에는 신규 진입만 끄는 follow-up-only 기능이 없으므로 마지막 게시 증거에서 행정적 우측 검열로 보존했다. DB를 완료나 0손익으로 수정하지 않았다.

## Strawberry: 신규 진입은 한 달이 아니라 동결된 7일이다

v1 신규 진입은 `[2026-08-15T04:00Z,2026-08-22T04:00Z)`이고 이후 v2a는 같은 표본의 후속 추적이다. 모집단은 CLOB `/sampling-markets`이며 스포츠와 비스포츠를 모두 포함한다.

v1 성공 slot은 646/1,008=64.09%, FAILED47회였다. 실행 p95는1,394.15초, 최대1,829.96초로 10분보다 긴 실행이 많았다. 19,846 crossing 중17,230 executable episode를 만들었으며 gap-censored crossing26,373/left-censored4,783도 그대로 남겼다.

고정 primary는 sampling .95 crossing 후 $5 displayed ask 진입, .85 bid stop, 나머지 terminal 보유다. .95는 실제 매수가격이 아니다. 실제 ask VWAP 범위는 .7692308~.999였다.

| 같은 .95 crossing 정책 | 완결 / 전체 | 검열 | 완결 부분 평균 gross | 72.5bps 차감 |
|---|---:|---:|---:|---:|
| .85 stop, target 없음 — primary | 1,888 / 2,633 | 745 | −24.4673% | −25.1923% |
| .85 stop, .98 target | 2,081 / 2,633 | 552 | −21.0099% | −21.7349% |
| .85 stop, .99 target | 2,005 / 2,633 | 628 | −22.4973% | −23.2223% |
| stop·target 없음 | 945 / 2,633 | 1,688 | −1.5944% | −2.3194% |
| stop 없음, .99 target | 1,340 / 2,633 | 1,293 | −1.2071% | −1.9321% |

정책마다 완결 분모가 달라 이 표만으로 stop 제거 효과를 인과적으로 단정하지 않는다. 사전 등록한 45개 유효 cell 모두 완결 부분 평균 gross가 음수였고, 가장 덜 음수인 값을 새 primary로 고르지 않았다. .85 stop은 .85 체결가 보장이 아니다.

Primary의 알려진 완료 gross 합은 −$2,309.7161이다. 검열된745건만이 아니라 **이미 완료된 것까지 전체2,633건에 payout1의 최대 추가 이익**을 중복해서 주는 느슨한 계산도 +$297.9223뿐이다. 이 과도하게 유리한 상한조차 **−$2,011.7938**이다. 기록된 완료 판정·고정 진입을 유지하고 주당 매도/지급이1USDC를 넘지 않는다는 가정이다. 실제 실현 P&L이 아니며 수수료를 무시한 상한이다.

## 후속 자료의 품질과 남은 정보

별도 v1/v2a pin으로 source anchor·seed를 검증했다. 후속 audit 범위의 compact book **4,151,258개**는 모두 gzip/hash/token 검증을 통과했고 unique one-hot resolution1,365개도 통과했다. raw request linkage 오류·terminal 이후 재요청·금지된 신규 sampling은0이다. 다만 전체 audit 성공 slot은826/2262=36.52%, FAILED1180/미종결 STARTED1이었다. 성공 게시 source/config는2개, 실패한 maintenance까지 포함한 run source/config는6개여서 한 건강한 확인 코호트로 합치지 않았다.

두 분석기 표현 문제를 분리했다. 첫 실제 자연 slot은19:17이나 analyzer의 clock-alignment heuristic은 수동 확인 run을 포함한19:07을 시작으로 삼는다. 또한 경계의 FULL_SEED를 cycle-completed 범위에 넣고 STARTED 범위에서 빼 atomic 경고1건을 냈다. 전체 DB exact-run join에서는839cycle 모두 SUCCESS와 연결됐고 반대 방향 누락도0이었다. 이 경고를 실제 원자적 게시 손상으로 보고하지 않는다. 원 analyzer output과 독립 보완 근거를 함께 보존했다.

v1 handoff에서 이미 terminal이었던6,377 episode에 v2a가2,872개를 더해 총9,249/17,230=53.68%가 terminal 확인됐다. 미해결은7,981 episode/4,386 token/4,385 condition이다. stop/TP 뒤에도 terminal 연구는 계속될 수 있으므로 실제 open trade 수가 아니다.

미해결 중7,056(88.4%)은 과거 Gamma end_date가 등록 추적 종료일9/21보다 뒤이고, 종료일까지 도달하는 것은360, 이미 날짜가 지난 경우86, 미상479다. 이 날짜를 실제 경기 종료·closed·payout으로 간주하지 않았다. 최근9/6·9/7·9/8(14시까지)에 새 terminal은20/28/3 condition인데 book은 합계1,558,030행이었다. Primary가 회복되지 않고 대부분 시장의 horizon이 추적 기간과 맞지 않아 현 10분 full-book 연구를 종료했다. 저장공간 부족 때문에 내린 결정은 아니다.

최종 #2789의 structured log는 cycle845, unresolved7,981, 새 resolved0, seed healthy였다. 실제 commit 로그는15:02:48 UTC, job 종료 확인은15:04:50 UTC다. 분석 pin cycle839 이후6cycle은 log counter로만 운영 closeout을 확인했고 그 가격을 pin에서 읽거나 재생했다고 주장하지 않는다. 미해결은 행정적 우측 검열로 보존한다.

이 자료는 폐기하지 않는다. 이후 새 Last Mile을 제안하려면 단기 horizon·시장 상태·직접 full-depth·수수료 관측을 갖춘 별도 모집단과 건강성 검정을 먼저 사전 등록해야 한다. 지금은 새 bot 자동 배포보다 사용 중인 단기 스포츠 raw 자료의 품질에 집중한다.

## 근거

모든 원 DB·pin·로그·상세 결과는 local-only `docs/local/sports-workbench-20260908/strawberry-review/`에 보존했다. 핵심 파일은 `raspberry-review.md`, `raspberry-v3-current.json`, `closeout/FINAL.json`, `strawberry-review.md`, `strawberry-v1-frozen-pilot.json`, `strawberry-v2a-current.json`, `strawberry-atomic-boundary-check.json`, `strawberry-closeout.json`이다. 해당 폴더의 최종 manifest가 분석 코드·결과·pin provenance를 고정한다.

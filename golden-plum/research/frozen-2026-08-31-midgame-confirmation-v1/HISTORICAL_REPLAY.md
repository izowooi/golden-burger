# Golden Peach 직접 여섯 호가 탐색 재생

## 증거 경계

- Jenkins/source: `polybot-grey` / `golden-peach` / `peach-shadow-1m-v1`
- 검증 DB: `daily-rsync/data/sources/macmini-m5/jobs/polybot-grey/strategies/golden-peach/runtime/peach-shadow-1m-v1/databases/latest/trades_sim.db`
- remote path: `/Volumes/t7/jenkins/polybot-grey/golden-peach/data/peach-shadow-1m-v1/trades_sim.db`
- local/remote SHA-256: `08fba89294769afb264562a3e0703ad207fed2cfff1cd81c4430bd5c170b801b`
- sync 완료: `2026-08-31T11:39:57.134861Z`
- DB copy cutoff: `2026-08-31T11:31:15.285641Z`
- 마지막 snapshot: `2026-08-31T01:20:13.442986Z`
- `daily-rsync verify`: `SUCCESS`, checked 1,612, failed 0
- SQLite `quick_check`: `ok`
- 적재량: 직접 full-depth snapshot 10,499행, event 17개

이 재생은 표시된 호가 전체 깊이(displayed full-depth)를 이용한 반사실이며 수수료가
빠져 있다. 실제 주문, 실제 체결, 실현 손익 증거가 아니다.

## 사전 후보와 엄격 계약 결과

엄격 계약은 event의 현재 여섯 호가 중 유일한 선두, 같은 token의 3회 관측, 각 간격
90초 이하, 누적 +0.02 이상, pullback 최대 0.01, 직전값 미만에서 현재 진입 구간으로
올라온 최초 교차를 모두 요구한다.

| 진입 | 익절 | 손절폭 | 거래 수 | 양수 | 표시호가 손익 |
|---:|---:|---:|---:|---:|---:|
| 0.60 | 0.90 | 0.15 | 0 | 0 | $0.00 |
| 0.60 | 0.95 | 0.15 | 0 | 0 | $0.00 |
| 0.75 | 0.90 | 0.15 | 3 | 2 | -$0.11 |
| 0.75 | 0.95 | 0.15 | 3 | 2 | +$0.54 |

0.60은 단순히 표본이 적어서가 아니라 여섯 호가 최고값의 구조와 맞지 않는다. 세 결과
중 하나의 YES가 1/3 이하이면 그 결과의 NO는 약 2/3 이상이므로 최고값은 정상적인
보완 호가에서 이미 약 0.67 이상이다.

전체 사전 격자의 어느 셀도 거래가 5건을 넘지 않았고, 10건 이상인 셀은 0개였다.
따라서 최고 소표본 셀을 골라 live 설정으로 쓰지 않는다. 보고서 후보 0.75를 공통
진입으로 고정하고, King 0.90 대 Queen 0.95 target만 앞으로 비교한다.

## 판정

- 과거 재생만으로 수익성을 주장할 수 없다.
- 이번 live A/B는 최소금액 `$5`의 앞으로 수집하는 반증 실험이다.
- 공통 event 20개 전에는 target 우열을 말하지 않는다.
- arm당 확정 종료 50개·공통 event 30개·중대 증거 공백 0 전에는 금액을 늘리지 않는다.
- Silver 100경기 전에는 전체 격자를 보고 live 파라미터를 바꾸지 않는다.

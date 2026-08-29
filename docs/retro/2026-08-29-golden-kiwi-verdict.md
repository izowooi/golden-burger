# Golden Kiwi Micro-Cascade 최종 판정 — 2026-08-29

## 결론

**⛔ `STOP / UNRESEARCHABLE`, 실험 폐쇄.**

Golden Kiwi A/B/C/D는 SQLite 파일과 follow-up 자체는 보존 가능한 연구 자료지만,
사전등록한 30일 confirmatory experiment로는 더 이상 유효하지 않다. 같은 job과 DB를
더 오래 실행해도 이미 발생한 cadence 이탈과 source/config cohort 분할을 사후에 복구할
수 없다. 따라서 2026-08-29에 Jenkins TimerTrigger를 네 job에서 모두 제거했다.

이 판정은 수익성이 음수라는 뜻이 아니다. **수익성 또는 최적 파라미터를 판단할 수 있는
실험 계약이 성립하지 않았다는 뜻**이다. A/C/D를 사후 승자로 고르거나 threshold를
완화하지 않는다.

## 고정 검토 범위와 증거

- UTC half-open range: `[2026-08-13T00:00:00Z, 2026-08-29T02:25:00Z)`
- A: `polybot-kiwi-a / kiwi-sim-a-3x1`
- B (primary): `polybot-kiwi-b / kiwi-sim-b-3x2`
- C: `polybot-kiwi-c / kiwi-sim-c-5x1`
- D: `polybot-kiwi-d / kiwi-sim-d-5x2`
- 네 canonical `trades_sim.db` 모두 `daily-rsync verify=SUCCESS`,
  SQLite `quick_check=ok`, foreign-key issue 0
- latest sync run:
  - A `cdfd511de15d402b8b55eea6d41b82d1`
  - B `450bba49c9a6440391db1a8e5d4fbeff`
  - C `6167967038184bbcb5034aa380b54c09`
  - D `6319b718c2584f908dd776d54fda7d92`

## 사전등록 gate 결과

| Arm | cadence coverage | off-schedule SUCCESS | p95 cycle | source/config cohort | quote-complete raw signal | distinct event |
|---|---:|---:|---:|---:|---:|---:|
| A (3×1%p) | 75.85% | 737 | 333.667s | 13 | 26 | 9 |
| B (3×2%p, primary) | 72.05% | 916 | 345.581s | 13 | 16 | 8 |
| C (5×1%p) | 74.01% | 830 | 337.600s | 13 | 1 | 1 |
| D (5×2%p) | 76.13% | 738 | 336.828s | 13 | 0 | 0 |

사전등록은 arm별 cadence coverage 90% 이상, off-schedule SUCCESS 0, 단일
`config_hash × strategy_source_digest × mode × job_name` cohort를 요구한다. Primary B는
quote-complete raw signal 50개와 event 30개 이상도 요구한다. 실제 결과는 세 조건을 모두
실패했다.

## 무엇은 정상적으로 수집됐는가

- 모든 arm의 experiment contract row는 고정된 arm/job/window/offset을 보존했다.
- 검토 범위의 sweep은 cursor-complete였고 최대 27페이지·2,662 market으로 53페이지·
  5,330 market·120초 상한 안이었다.
- mature raw-selected signal에 생성된 follow-up quote는 A 26/26, B 16/16, C 1/1로
  완결됐다. D는 signal이 없었다.
- filtered-universe 구조는 과거 267페이지·26,654 market 전수조사 문제를 줄였다.

즉, DB 손상이나 follow-up 누락 때문에 폐쇄한 것이 아니다. 가장 중요한 primary signal이
희소했고, 동시에 Jenkins 실행시간이 5분 cadence를 자주 넘겨 slot과 cohort 계약이 깨진
것이 폐쇄 이유다.

## 운영 조치

- `polybot-kiwi-a`, `polybot-kiwi-b`, `polybot-kiwi-c`, `polybot-kiwi-d` TimerTrigger 제거
- 마지막 build는 각각 `#6616`, `#6614`, `#6624`, `#6625` SUCCESS
- 실제 credential과 live 주문은 source-level hard block 때문에 사용되지 않았다.
- DB와 로그는 삭제·clean·merge하지 않고 historical research evidence로 보존한다.

향후 Micro-Cascade를 다시 검정하려면 현재 DB를 이어 쓰지 않는다. 새로운 가설,
새 preregistration, 새 canonical job/DB, 안정적으로 5분 안에 끝나는 수집 구조를 먼저 만든
뒤 독립 실험으로 시작해야 한다.

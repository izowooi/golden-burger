# Golden Kiwi research artifacts

이 디렉터리는 Micro-Cascade의 immutable 연구 protocol, benchmark와 결과를 보존한다.
현재 실행 계약은 [`frozen-2026-08-13/`](frozen-2026-08-13/)이고,
[`frozen-2026-07-30/`](frozen-2026-07-30/)은 최초 연구의 역사 기록이다.

## 가장 먼저 읽을 순서

1. [`frozen-2026-08-13/PREREGISTRATION.md`](frozen-2026-08-13/PREREGISTRATION.md) —
   filtered request envelope, 고정 UTC window와 새 analyzer 계약
2. [`frozen-2026-08-13/GAMMA_FILTER_BENCHMARK.json`](frozen-2026-08-13/GAMMA_FILTER_BENCHMARK.json) —
   threshold grid, exact API sweep, strict 후보 parity와 선택 근거
3. [`2026-07-30-cohort-correction.md`](2026-07-30-cohort-correction.md) — 독립
   재검토에서 확인한 cross-commit C 신호와 point-in-time catalog 증거 부족
4. [`PREREGISTRATION.md`](frozen-2026-07-30/PREREGISTRATION.md) — arm 결과를 보기 전에
   고정한 universe, 네 팔, outcome, 통계와 promotion gate
5. [`RESEARCH_REPORT.md`](frozen-2026-07-30/RESEARCH_REPORT.md) — evidence 발견, 분석
   범위, OOS 결과와 해석
6. [`RESULTS.md`](frozen-2026-07-30/RESULTS.md) — 기계 생성 결과 요약
7. [`signals.csv`](frozen-2026-07-30/signals.csv) /
   [`results.json`](frozen-2026-07-30/results.json) — 행 단위 및 구조화 결과
8. [`micro_cascade_analysis.py`](frozen-2026-07-30/micro_cascade_analysis.py) — 사용한
   분석 코드

## 무결성 확인

```bash
cd golden-kiwi/research/frozen-2026-07-30
shasum -a 256 -c MANIFEST.sha256
cd ../frozen-2026-08-13
shasum -a 256 -c MANIFEST.sha256
```

각 manifest의 모든 파일이 `OK`여야 한다. `MANIFEST.sha256` 자체는 manifest가 자신을
재귀적으로 서명하지 않기 때문에 검증 목록에 포함하지 않는다.

현재 filtered-universe preregistration SHA-256:

```text
65e33146e018ff9b01495af515fd059ba5be33de15758ad438584427ea02223c
```

최초 연구 preregistration SHA-256:

```text
0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006
```

Primary source DB SHA-256:

```text
f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df
```

## 재현 범위

분석 script에는 연구 당시의 절대 local DB 경로가 고정돼 있다. 이는 사용한 evidence
identity를 감추지 않기 위한 immutable historical artifact이지 어느 컴퓨터에서도 바로
실행되는 portable CLI가 아니다.

다시 실행하려면:

1. `daily-rsync locate`/`verify`로 동일 source deployment와 SHA-256 DB를 확인한다.
2. script의 입력 경로를 바꿔야 한다면 frozen 파일을 덮어쓰지 말고 새 날짜 디렉터리와 새
   preregistration/manifest를 만든다.
3. 기존 OOS 구간은 이미 결과를 본 데이터이므로 새 threshold 선택이나 새 test로
   재사용하지 않는다.

분석 당시 Python 3.11.4, SQLite 3.41.2였고 `PRAGMA quick_check=ok`였다. 대상
full-cadence 범위는 compact-v1 hot window 안에 한정했다. 이전 12시간 rollup row를 5분
snapshot으로 오인하지 않았다.

## 이 결과가 말하는 것

- 네 arm 모두 frozen promotion gate를 실패했다.
- Primary B는 strict event-purged OOS에서 1 signal / 1 event뿐이다.
- B의 +13.55bps는 단일 event라 CI를 계산할 수 없다.
- C의 +52.63bps는 서로 다른 Git commit의 snapshot을 이은 계약 위반 수치라 promotion
  해석에서 철회됐다.
- 과거 DB에는 snapshot-level strict-binary/`negRisk` 증거가 없어 A/B/C/D 전체가 현재
  기준의 promotion evidence로는 불완전하다.
- 일반 OOS의 A/B는 각각 -1.0234%, -1.8072%였다.
- top-of-book counterfactual이며 actual fill/fee P&L이 아니다.

따라서 이 artifact를 수익성 홍보나 live 승격 근거로 사용하지 않는다. 다음 판단은
`kiwi-sim-a-3x1`부터 `kiwi-sim-d-5x2`까지 네 독립 DB의 새로운 30일 구간에서만 한다.

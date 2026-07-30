# L3 AGENTS.md — Golden Kiwi

이 문서는 `golden-kiwi/` 전체에 적용되는 작업 지침이다. 상위
`../AGENTS.md`의 공통 운영 규칙을 상속하며, Golden Kiwi 고유 계약은 이 문서를
우선한다.

## 저장소 목적

Golden Kiwi / Micro-Cascade는 사람들의 정보 반영이 3~5회의 작은 연속 YES 상승으로
이어지는지를 검정하는 5분 cadence 연구 봇이다. 실제 주문은 source-level hard block으로
금지하며 simulation과 raw counterfactual evidence만 수집한다.

주요 결과는 fresh execution을 통과한 가상 position이 아니다. append-only raw signal
전체에서 사전 고정 순서로 뽑은 `raw_selected`의 point-in-time best ask와, 신호
+60~75분 사이 독립 Gamma condition 조회에서 처음 얻은 SUCCESS-run valid best bid를
비교한다. simulation trade exit은 position cap·cooldown·depth 영향을 받는 별도 runtime
diagnostic이다.

## 구조

- `src/polybot/`: archive, signal, ranking, follow-up, simulation과 live hard block
- `src/polybot/db/`: job별 SQLite와 append-only experiment evidence
- `scripts/analyze_experiment.py`: 네 DB를 read-only로 평가하는 analyzer v2
- `tests/`: arm, lineage, funnel, follow-up, collection, drawdown, live 차단 계약
- `research/frozen-2026-07-30/`: checksum으로 고정한 최초 연구 산출물
- `research/2026-07-30-cohort-correction.md`: cross-Git 양수 결과 철회 기록
- `data/<job>/trades_sim.db`: ignored canonical runtime DB
- `README.md`: 실행·Jenkins·분석 절차
- `STRATEGY.md`: 가설·반증·promotion 계약

## 고정 실험군

네 arm에서 바꿀 수 있는 처치축은 `confirmation_steps`와
`min_cumulative_move`뿐이다. Primary B가 실패해도 A/C/D의 사후 승자로 구조하지 않는다.

| Arm | 양의 step | 누적 YES 변화 | Canonical job | UTC offset |
|---|---:|---:|---|---:|
| A | 3 | `0.01` | `kiwi-sim-a-3x1` | 0 |
| B | 3 | `0.02` | `kiwi-sim-b-3x2` | 1 |
| C | 5 | `0.01` | `kiwi-sim-c-5x1` | 2 |
| D | 5 | `0.02` | `kiwi-sim-d-5x2` | 3 |

각 job은 같은 Git commit과 서로 다른 절대 경로 DB를 사용한다. lineage 전체는 같은
`config_hash × git_commit × mode × job_name`이어야 하고, 현재 RUNNING run의 마지막
snapshot과 이전 SUCCESS·cursor-complete run의 row만 3~10분 gap으로 연결한다.
backfill, forward-fill, rollup 또는 다른 cohort 결합은 금지한다.

## 표본 선택과 evidence

- 표준 이진 YES, `negRisk=false`, 6시간 이상, YES 0.20~0.80만 진입 후보로 삼는다.
- liquidity $20,000, volume24h $10,000, spread 0.02를 고정한다.
- sports, games, esports와 짧은 crypto 계열 exact tag를 고정 제외한다.
- 같은 event에서는 liquidity 내림차순·`condition_id` 오름차순으로 하나를 고른다.
- event 승자를 같은 전역 순서로 정렬하고 fresh gate를 처음 통과한 최대 1개만 진입한다.
- fresh entry는 한 CLOB book에서 bid, ask, spread와 depth를 함께 고정한다.
- `micro_cascade_signal_decisions`에는 raw 후보, sibling/event/global rank, cooldown,
  portfolio/drawdown, fresh attempt와 탈락 사유를 append-only로 남긴다.
- `micro_cascade_followup_observations`에는 `raw_selected`별 +60~75분 condition 조회의
  quote, 부재와 오류를 append-only로 남긴다.
- FAILED source/observer run은 row를 삭제하지 않고 분석에서 제외한다.
- quote가 없으면 0, 마지막 가격 또는 추정값으로 채우지 않고 censor한다.
- point-in-time catalog가 붙은 실제 5분 snapshot을 60일 보존하고 cold rollup하지 않는다.

## Promotion collection 계약

`POLYBOT_EXPERIMENT_START_UTC`, `POLYBOT_EXPERIMENT_END_UTC`,
`POLYBOT_CADENCE_OFFSET_MINUTE`는 all-or-none이다. 없으면 smoke/archive mode이며
`collection_eligible=0`이다. 일부만 있거나 `[start,end)`가 정확히 30일이 아니면
시작하지 않는다. 정확한 최초 UTC 구간은 사용자가 첫 run 전에 확정해야 하며 README의
날짜는 예시일 뿐이다.

`micro_cascade_experiment_contracts`는 arm/job, window, cadence offset,
preregistration hash와 analyzer version을 DB에 최초 한 번 불변 저장한다. 같은 DB에서
계약을 바꾸지 않는다. Jenkins는 숨은 `H/5` 대신 다음 trigger를 쓴다.

```text
A: 0-59/5 * * * *
B: 1-59/5 * * * *
C: 2-59/5 * * * *
D: 3-59/5 * * * *
```

각 job의 `POLYBOT_CADENCE_OFFSET_MINUTE`는 각각 0, 1, 2, 3이어야 한다. concurrent
build를 끄고 p95 cycle runtime이 5분을 넘으면 gap을 완화하지 말고 수집 구조를 고친다.
off-schedule 또는 duplicate-slot SUCCESS run은 primary signal/follow-up에서 제외하고
promotion 전체를 fail-closed한다.

## Risk와 drawdown

simulation exposure는 $5, 최대 3 position, open notional $15, event당 1 position,
신규 1 position/cycle, event cooldown 6시간이다.

drawdown은 같은 cohort의 SUCCESS entry/terminal run에 연결된 finite economic P&L을
시간순으로 합산한 최초 -$20 crossing만 사용한다. 감지 RUNNING run에는 pending row만
stage하고 그 detector가 SUCCESS가 된 뒤 영구 latch로 finalize한다. FAILED 또는 stale
detector의 pending은 discard한다. startup은 미완료 pending을 먼저 reconcile한다.
latch는 회복·재시작으로 해제하지 않고 손상 상태는 fail closed한다. 새 실험은 새
preregistration·job·DB·검토가 필요하다.

## 작업 전 확인

1. 상위 `../AGENTS.md`
2. `README.md`, `STRATEGY.md`, `config.yaml`, `.env.example`
3. frozen preregistration·report·result·correction과 checksum
4. 성과 작업이면 `../docs/retro/EVIDENCE_CONTRACT.md`와
   `../docs/retro/golden-kiwi.md`
5. 변경 source와 대응 test

## 실행과 검증

dependency와 frozen artifact부터 확인한다.

```bash
uv sync --frozen --extra dev
cd research/frozen-2026-07-30
shasum -a 256 -c MANIFEST.sha256
cd ../..
uv run pytest
uv run pytest --cov=polybot --cov-report=term-missing
uv build
```

collection env가 없는 아래 명령은 smoke/archive 확인용이다.

```bash
uv run polybot config --job kiwi-sim-b-3x2
uv run polybot run --simulate --job kiwi-sim-b-3x2
uv run polybot status --job kiwi-sim-b-3x2
```

`--live`, `simulation_mode=False`, non-simulation bot/Trader와 authenticated submission
진입점이 모두 거부되는지 유지한다. routine simulation은 public Gamma/CLOB read를
사용하지만 test에서는 network mock과 임시 SQLite를 쓴다.

30일 뒤에는 네 DB와 각 DB만 대상으로 만든 exact-window strict audit JSON을 함께 준다.
audit의 `database_sha256`은 분석할 immutable DB 실제 바이트와 정확히 일치해야 한다.

```bash
uv run python scripts/analyze_experiment.py \
  --start <UTC_START> --end <UTC_END> \
  --db A=<A_DB> --db B=<B_DB> --db C=<C_DB> --db D=<D_DB> \
  --strict-audit A=<A_JSON> --strict-audit B=<B_JSON> \
  --strict-audit C=<C_JSON> --strict-audit D=<D_JSON> \
  --output <RESULT_JSON>
```

analyzer v2는 raw denominator와 +60~75분 outcome을 재구성한다. 외부 strict audit,
cadence 또는 immutable contract가 없으면 `NOT_EVALUABLE_FAIL_CLOSED`다. legacy
recorded-trade subset은 diagnostic/fallback일 뿐 promotion denominator가 아니다.

## Promotion gate

Primary B에 대해 quote-complete raw signal 50개, event 30개, event-cluster 98.75%
lower CI와 10.4bps stress lower CI가 모두 양수, 전반/후반 양수, target/quote 및 cadence
coverage 각각 90% 이상, 단일 shared Git cohort, strict audit CRITICAL/HIGH 0을 모두
요구한다. 실패나 표본 부족은 `STOP / UNRESEARCHABLE`이며 threshold를 완화하지 않는다.
통과도 `SHADOW_REVIEW_ONLY`일 뿐 live 승인이 아니다.

## 변경 금지와 주의사항

- frozen control, arm mapping, selection 순서와 promotion gate를 관측 결과에 맞춰 바꾸지 않는다.
- private key나 funder를 Kiwi job에 주입하지 않는다.
- drawdown latch나 append-only experiment row를 수정·삭제하지 않는다.
- resolution, redeemable, redeem transaction, CLOB intent와 confirmed fill을 섞지 않는다.
- 과거 C `+0.5263%`는 cross-Git lineage이므로 양수 evidence로 재사용하지 않는다.
- live hard block 제거에는 별도 사용자 승인, shadow evidence, 새 risk budget,
  reviewed source change와 새 preregistration이 모두 필요하다.

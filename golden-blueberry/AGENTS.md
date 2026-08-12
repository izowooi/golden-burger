# L3 AGENTS.md — golden-blueberry

이 문서는 `golden-blueberry/` 전체에 적용되는 작업 지침이다.
상위 `../AGENTS.md`와 `/Users/izowooi/git/AGENTS.md`를 상속하며, Blueberry 고유 계약은 이 문서를 우선한다.
confirmed-fill evidence, Jenkins secret 처리와 signature type은 상위 `../AGENTS.md`의 공통 계약을 그대로 적용하고 여기서 반복하지 않는다.

## 저장소 목적

Golden Blueberry / Closing Surge는 해결까지 72시간 이내인 표준 이진 시장에서 YES의 최초 급등을 추종하는 Polymarket 전략 봇이다.
기본은 simulation이며, 실제 거래 가능한 소액 A/B 검증 단계로 운영한다.
총 실험 자금은 $300이고 서로 독립된 두 arm에 $150씩 배정한다.
실험 목적은 다른 조건을 고정한 채 `min_surge` 2pp와 5pp만 비교하는 것이다.

## 구조

- `src/polybot/`: config, archive, signal, scanner, execution, lifecycle과 SQLite 구현
- `scripts/backtest.py`: immutable CSV를 입력으로 쓰는 offline backtest
- `scripts/analyze_experiment.py`: 두 arm DB를 read-only로 비교하는 A/B analyzer
- `scripts/test_api_key.py`: API credential 연결 확인 도구
- `tests/`: config, mode, lifecycle, signal, fill evidence, trader와 backtest 계약
- `data/<job>/`: ignored runtime 경로이며 simulation은 `trades_sim.db`, live는 `trades.db` 사용
- `config.yaml`, `.env.example`: 기본 전략값과 environment override 계약
- `Jenkinsfile`: arm 선택, 5분 cadence와 simulation/live 실행 pipeline
- `README.md`, `STRATEGY.md`: 실행 절차와 변경 불가 전략 계약
- `../docs/retro/golden-blueberry.md`: 회고 계획과 판정 기록

## 공통 작업 원칙

- 유일한 A/B 처치축은 `POLYBOT_MIN_SURGE`다.
- A는 `BLUEBERRY_ARM=A_2PP`와 `POLYBOT_MIN_SURGE=0.02`, B는 `BLUEBERRY_ARM=B_5PP`와 `POLYBOT_MIN_SURGE=0.05`를 사용한다.
- 두 arm의 `execution_mode=nearest`, 진입·청산 threshold, 시계, 주문액, liquidity·volume, cadence, risk와 sports/in-play 조건은 동일하게 유지한다.
- 두 arm은 서로 다른 stable `job_name`, mode별 DB, wallet/account/funder와 Jenkins credential binding을 사용한다.
- cohort는 `config_hash × strategy_source_digest × mode × job_name`으로 식별한다.
- Git commit은 provenance로만 기록하며 cohort 경계로 사용하지 않는다.
- source digest나 config가 바뀌면 새 cohort로 시작하고, simulation과 live evidence를 섞지 않는다.

### 변경 불가 전략 계약

- outcomes가 정확히 `[Yes, No]`이고 `negRisk=false`인 표준 이진 시장만 허용하며 방향은 YES-only다.
- 유효한 직전 YES가 `0.85` 미만이고 현재 YES가 `[0.85, 0.93]`이며 `0 < snapshot gap <= 15분`인 first observed crossing만 진입 후보로 삼는다.
- 보존 이력에 YES `0.85` 이상 관측이 있으면 dip/re-cross를 새 후보로 만들지 않는다.
- scheduled와 pregame 진입 시계는 `(0h, 72h]`이며 sports를 기본 포함한다.
- in-play sports는 kickoff 후 최대 360분까지만 허용하고 pregame과 별도 cohort로 평가한다.
- 미해결 익절은 signal과 fresh executable bid가 모두 `0.97` 이상일 때만 시도하고, signal이 `0.78` 이하이면 절대가격 손절을 시도한다.
- trailing stop과 time exit은 추가하지 않으며 resolution, redeemable, redeem transaction과 SELL fill을 구분한다.
- 초기 A/B Jenkins preset은 `buy_amount_usdc=$5`이며, code invariant는 `buy_amount_usdc <= $5`다.
- $1은 5-share 최소 주문을 충족하지 못하므로 사용할 수 없고, $5 hard cap은 회고를 거친 code change 없이는 올리지 않는다.
- open-notional cap은 `buy_amount_usdc × max_open_notional_multiple`로 파생하며, 현재 `max_open_notional_multiple=10`이므로 `$5 × 10 = $50`이다.
- 최대 position은 10개, event당 1개, cycle당 신규 진입은 1개로 제한한다.
- arm의 economic P&L이 `-$30`에 도달하면 신규 진입 kill switch를 발동하되 기존 position 관리는 계속한다.

## 작업 전 확인

1. 상위 `../AGENTS.md`
2. `README.md`, `STRATEGY.md`, `config.yaml`, `.env.example`
3. `Jenkinsfile`과 변경 source에 대응하는 `tests/`
4. 성과 작업이면 `../docs/retro/EVIDENCE_CONTRACT.md`와 `../docs/retro/golden-blueberry.md`
5. A/B 작업이면 두 arm의 resolved config, source digest, mode, job과 DB 격리 상태

## 공통 명령어

프로젝트 루트에서 일상 simulation을 다음과 같이 실행한다.

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job <arm-job>
uv run polybot status --simulate --job <arm-job>
uv run polybot run --simulate --job <arm-job>
uv run python scripts/backtest.py <immutable.csv> --output-dir <artifact-dir> --review-start <YYYY-MM-DD> --review-end <YYYY-MM-DD>
uv run python scripts/analyze_experiment.py --arm-a <a-db> --arm-b <b-db> --review-start <YYYY-MM-DD> --review-end <YYYY-MM-DD> --output-dir <artifact-dir>
```

상위 보안 계약과 승인 절차를 만족한 arm job에서만 live config와 DB를 명시적으로 선택한다.

```bash
uv run polybot config --live --job <arm-job>
uv run polybot status --live --job <arm-job>
uv run polybot run --live --job <arm-job>
```

저장소 공통 전략 계약도 함께 검증한다.

```bash
cd ..
uv run tools/verify_strategy_contracts.py
```

## 검증 기준

- code나 config를 바꾸면 전체 test와 build를 실행한다.
- A/B 설정을 바꾸면 resolved config diff에서 `min_surge` 외 실험 조건이 동일한지 확인한다.
- test는 network와 주문 제출을 mock하고 SQLite는 임시 경로를 사용한다.
- offline backtest는 provenance와 checksum이 고정된 immutable CSV만 사용하고 결과를 hypothetical fill로 표시한다.
- A/B analyzer는 DB를 수정하지 않으며 exact review range와 단일 cohort만 평가한다.
- 1주차는 운영·수집 health checkpoint로만 사용하고 승자 판정이나 parameter tuning을 하지 않는다.
- 30일차에도 arm당 `CONFIRMED` closed position이 20건 미만이면 표본 부족으로 실험을 연장한다.
- 30일 회고는 read-only analyzer와 strict retro audit를 함께 사용한다.
- source digest 또는 config cohort가 섞이거나 exact fill·fee evidence가 비면 승격하지 않는다.
- 증액은 별도 회고 후 $5 code hard cap을 바꾼 새 cohort에서만 검토한다.

## 루트 설정 변경 기준

- 전략 수치를 바꾸면 `config.yaml`, `src/polybot/config.py`, 관련 signal·timing·risk test, `STRATEGY.md`, `README.md`와 retro를 함께 갱신한다.
- A/B mapping을 바꾸면 `Jenkinsfile`, config validation, analyzer와 문서가 `min_surge` 단일 축을 유지하는지 확인한다.
- `pyproject.toml` dependency를 바꾸면 `uv.lock`을 동기화하고 기존 path dependency 영향을 확인한다.
- 다른 `golden-*` 폴더와 shared observability를 바꾸는 작업은 이 프로젝트 범위 밖의 별도 영향 검토로 분리한다.

## 주의사항

- `simulation_mode=true`와 명시적 `--simulate`를 안전 기본값으로 유지한다.
- CLI는 명시적 `--live`, Jenkins는 명시적 `BLUEBERRY_LIVE=true` 없이는 실주문을 내지 않는다.
- simulation과 live는 같은 `job_name`에서도 각각 `trades_sim.db`와 `trades.db`를 선택하며 status/config 확인에도 mode flag를 생략하지 않는다.
- arm별 wallet, DB 또는 job을 공유하면 A/B 격리가 아니므로 실험을 시작하지 않는다.
- Jenkins는 두 arm의 관측 시각을 맞추는 `*/5 * * * *`, `disableConcurrentBuilds()`와 15분 timeout을 유지한다. job별 offset이 달라지는 `H/5`로 바꾸지 않는다.
- 같은 Mac의 A/B job은 owner-private 절대경로인 `POLYBOT_GAMMA_SHARED_CACHE_DIR`를 동일하게 설정해 같은 5분 bucket의 cursor-complete sweep만 공유한다. cache 검증·lock fail-closed를 우회하거나 이 기능을 universe 축소로 바꾸지 않는다.
- 새 DB는 자동 `compact-v1`이며 sweep 상세는 24시간 checkpoint, telemetry와 bot 일일 로그는 60일 보존한다. entry/shadow decision snapshot 참조는 정리하지 않는다. clean build는 새 cohort 시작 시 한 번만 허용한다.
- arm별 실제 infrastructure identifier는 저장소 문서에 고정하지 않고 Jenkins에서 관리한다.
- 다른 `golden-*` 폴더는 read-only로 취급한다.

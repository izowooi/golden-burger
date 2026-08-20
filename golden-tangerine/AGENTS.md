# L4 AGENTS.md — golden-tangerine

상위 `/Users/izowooi/git/t1/AGENTS.md`를 따르며, 이 문서는 Golden Tangerine 전용 안전 계약만
추가한다.

## 목적과 불변 조건

- Golden Black의 sports-resolution 가설을 두 기존 wallet에서 최소 `$5`로 A/B 검증한다.
- arm A는 `[0.94,0.95]`, arm B는 `[0.92,0.93]`; threshold 외 처치 차이를 만들지 않는다.
- label/price/token이 정렬된 2-outcome sports market의 양 token을 사용한다. 팀명 moneyline
  (`negRisk=false`)과 Yes/No proposition(`negRisk=true`)을 모두 포함하되 `negRisk`는 명시적
  boolean이어야 한다. Gamma endDate `(0h,6h]`, liquidity `10k`, cumulative volume `5k`다.
- 첫 exact-book band observation만 사용하고 FOK BUY 후 resolution까지 보유한다.
- 조기 SELL, stop, TP, account-wide wallet reconciliation/wind-down을 추가하지 않는다.
- job DB가 만든 trade만 관리한다. 수동 wallet position을 탐색·편입·청산하지 않는다.
- `$5`, max positions/event/new `3/1/1`, frozen clocks를 완화하지 않는다.

## 작업 전 확인

1. `README.md`, `STRATEGY.md`, `OPERATIONS.md`
2. `config.yaml`과 `research/frozen-2026-08-20/PREREGISTRATION.md`
3. 공통 `docs/retro/EVIDENCE_CONTRACT.md`
4. Jenkins 작업이면 local inventory와 실제 redacted config

## 실행과 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job tangerine-test
```

live 변경은 Jenkins timer를 먼저 끄고 수동 build·console·DB/daily-rsync를 검증한 뒤 H/5를
복원한다. clean build나 기존 DB migration/import를 하지 않는다. credential은 기존 Jenkins 값을
보존하되 코드·문서·출력·커밋에 넣지 않는다.

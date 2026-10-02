# polylab 자동 변경 이력

최신이 위. autopilot(validator 통과분)과 결정론 ladder가 기록한다.

## 2026-10-02 KST · 연구자 결정 반영 (수동) · engine none

- `watermelon-cat` params: prob_min 0.92→0.93, take_profit_delta 0.02, take_profit_cap 0.99, max_sells_per_cycle 1→5 (human) — 연구자 결정 `ai:watermelon-soccer-late-collapse`: take-profit early 적용. 근거·한계 `docs/research/backtests/2026-10-02-watermelon-tp-apricot.md`
- `watermelon-dog` params: prob_min 0.92→0.93, take_profit_delta 0.04, take_profit_cap 0.99, max_sells_per_cycle 1→5 (human) — 같은 결정. cat(TP 0.02) 대 dog(TP 0.04) A/B
- `apricot-fruit` params: entry_tick_minute 85→95 (human) — 연구자 결정 `ai:apricot-fruit-tick-inferior`: tick 95 가 전·후반 표본 모두에서 80/85/90 보다 우수(28개 조합 중 27)


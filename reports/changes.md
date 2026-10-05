# polylab 자동 변경 이력

최신이 위. autopilot(validator 통과분)과 결정론 ladder가 기록한다.

## 2026-10-06 08:00 KST · 일일 2026-10-06-morning · engine claude

- `watermelon-cat` mode: [soccer] mode live→paper (ladder) — ladder: 45 trades at floor with cumulative loss
- `watermelon-dog` mode: [soccer] mode live→paper (ladder) — ladder: 45 trades at floor with cumulative loss

## 2026-10-03 08:00 KST · 일일 2026-10-03-morning · engine claude

- `plum-king` mode: mode live→paper (ai:claude) — 1분 주기 Jenkins가 급락을 따라잡지 못해 실체결 손절가가 설계값(entry-SL_delta=0.61) 대비 −0.15 낮은 0.46에서 체결(Ukraine vs NI, trades_recent.json). Ireland·Seattle에서도 동일 패턴 반복. 절대가격 TP(0.9) 구조가 연구자 결정(조기 익절 우선)과 부합하지 않음. n=8로 통계 결론 불가이나 운영 위험 누적, paper 전환 후 가설 재설계 권장.
- `plum-queen` mode: mode live→paper (ai:claude) — 동일 Ukraine vs NI 경기에서 SL delta 0.17(king 0.12보다 넓음)이 오히려 급락 구간에서 더 깊게 물려 실체결가 0.45(설계 0.56 대비 −0.11). SL을 넓게 잡는 설계가 1분 주기 집행 불능 문제를 악화시킴을 확인. n=7로 통계 결론 불가이나 운영 위험이 동일하게 존재.

## 2026-10-02 KST · 연구자 결정 반영 (수동) · engine none

- `watermelon-cat` params: prob_min 0.92→0.93, take_profit_delta 0.02, take_profit_cap 0.99, max_sells_per_cycle 1→5 (human) — 연구자 결정 `ai:watermelon-soccer-late-collapse`: take-profit early 적용. 근거·한계 `docs/research/backtests/2026-10-02-watermelon-tp-apricot.md`
- `watermelon-dog` params: prob_min 0.92→0.93, take_profit_delta 0.04, take_profit_cap 0.99, max_sells_per_cycle 1→5 (human) — 같은 결정. cat(TP 0.02) 대 dog(TP 0.04) A/B
- `apricot-fruit` params: entry_tick_minute 85→95 (human) — 연구자 결정 `ai:apricot-fruit-tick-inferior`: tick 95 가 전·후반 표본 모두에서 80/85/90 보다 우수(28개 조합 중 27)


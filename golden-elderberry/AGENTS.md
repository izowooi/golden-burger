# L3 AGENTS.md — golden-elderberry

Polymarket **Panic Fade** 전략 봇: 원래 favorite(≥70%)이던 토큰의 공황 투매 급락(≥12%p)을 바닥 안정화 확인 후 역매수하고 반등 시 청산한다.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 공통 규칙), L1 `/Users/izowooi/git/AGENTS.md`.
- 전략 근거와 파라미터는 `STRATEGY.md`, 실행법은 `README.md` 참조.

## 실행 / 검증 명령

```bash
uv sync --frozen                          # 의존성 설치 (재현 가능)
uv sync --extra dev && uv run pytest      # 테스트
uv run python main.py run --simulate      # 시뮬레이션 드라이런 (실키 필요)
uv run python main.py config              # 설정 확인 (네트워크 불필요)
```

## 코드 구조 (전략 변경 위치)

- **`src/polybot/strategy/signals.py`** — 전략 시그널 = **순수 함수**. 진입(`evaluate_panic_fade`)/청산(`evaluate_exit`) 판정이 전부 여기 있다. **전략을 바꾸려면 이 파일만 수정**하고 `tests/test_signals.py`를 갱신한다.
- `strategy/scanner.py` / `strategy/trader.py` — signals를 호출하는 오케스트레이션 (시장 조회, 주문 실행). 시그널 로직을 여기에 넣지 않는다.
- `bot.py` — 사이클: Phase 0 스냅샷 저장(YES 가격 기준) → 1 청산 체크 → 2 스캔 → 3 재진입 체크+매수 → 4 스냅샷 정리. Gamma sweep은 사이클당 1회.
- `api/history_client.py` — prices-history 백필 (best-effort, 실패 시 조용히 None).
- `config.py` — env > config.yaml > 기본값 병합. 새 파라미터는 이 3단 구조를 유지한다.

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (체결 가정) — cherry와의 A/B 비교를 위해 유지
- `POLYMARKET_PRIVATE_KEY`/`POLYMARKET_FUNDER_ADDRESS` env 필수, signature_type=1, chain_id=137
- `py-clob-client-v2>=1.0.1` (구버전 py-clob-client 금지 — order_version_mismatch)
- data/<job>/ 분리, trades_sim.db 시뮬레이션 분리
- gamma 페이지네이션 offset>0 HTTP 422 = 정상 종료
- 스냅샷은 항상 **YES 가격** 기준 저장 (NO쪽 평가는 signals에서 1-p 환산)

## 금지 사항

- 실키(.env, private key, 주소)를 커밋/출력하지 않는다
- `data/`를 커밋하지 않는다 (.gitignore 등록됨)
- 다른 `golden-*` 폴더를 수정하지 않는다 (독립 프로젝트)

## CLAUDE.md

같은 폴더의 `CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.

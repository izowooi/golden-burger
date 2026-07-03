# L3 AGENTS.md — golden-fig

Polymarket 자동매매 봇: **Hope Crusher** — 롱샷(YES 5~25%) 시장의 희망 프리미엄을 페이드해 항상 NO 토큰을 매수하고, 시간 가치 소멸(theta)을 수확하는 전략.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 운영 지침), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·파라미터·리스크는 `STRATEGY.md`, 실행법은 `README.md` 참조.

## 실행 / 검증

```bash
uv sync --frozen                              # 의존성 설치
uv sync --extra dev && uv run pytest          # 테스트
uv run python main.py run --simulate --job test   # 시뮬레이션 (실키 필요 - CLOB 인증)
uv run python main.py run                     # 실거래 (Jenkins)
uv run python main.py config                  # 설정 확인 (네트워크 불필요)
uv run python main.py status                  # 포지션/통계 확인
```

## 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 순수 함수** (스냅샷 리스트/숫자 입력 → 판정 출력). 진입/청산 규칙 변경은 여기만 수정하면 된다. `tests/test_signals.py`가 그대로 전략 검증이다.
- `strategy/scanner.py`: Gamma sweep(1회) + 스냅샷 저장 + signals 호출.
- `strategy/trader.py`: 주문 실행 + EXPIRED 마감 처리. 판정은 signals에 위임.
- `api/history_client.py`: CLOB `/prices-history` 백필. 실패 시 조용히 None (봇은 정상 동작).
- `db/`: trades / market_snapshots(YES 가격 기준) / skipped_markets(쿨다운 판정용).

## 구조 특이사항 (기존 golden-* 봇과 다른 점)

- 방향이 전략에 내장: 항상 NO 토큰(`clobTokenIds[1]`) 매수. `--yes-only` 플래그 없음.
- 재진입 허용: `condition_id` unique 아님. HOLDING 중이거나 쿨다운(`POLYBOT_REENTRY_COOLDOWN_HOURS`, 기본 24h) 이내만 skip.
- 윈도우는 timestamp 기반(`get_window`/`is_window_valid`). invalid면 백필 시도, 그래도 invalid면 진입 금지 (관대한 cold-start 폴백 금지).
- trailing stop 없음. TP 목표가는 0.99 캡.

## 금지

- 실제 API key/private key를 어떤 파일에도 쓰지 않는다 (`.env`는 로컬 전용, 커밋 금지).
- `data/` 디렉토리 커밋 금지 (`.gitignore` 등록됨).
- 다른 `golden-*` 폴더 수정 금지 — 각 봇은 독립 프로젝트다.

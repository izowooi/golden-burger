# L3 AGENTS.md — golden-mango

Polymarket **Patience Premium** 전략 봇 — "거의 확실한" 계약의 settlement discount(자본 잠김 할인)를
연환산 캐리 수익률 단일 수식 `y = ((1-p)/p) × (8760/h) >= y_min` 으로 걸러 수확한다.
공개 문헌(arXiv settlement discounting 등)에서 독립 도출된 전략이다.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 공통 규칙), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·규칙·리스크: `STRATEGY.md`. 실행·env 표: `README.md`.

## 실행/검증 명령

```bash
uv sync --frozen                # 의존성 설치 (lock 기준)
uv sync --extra dev             # + pytest
uv run pytest                   # 유닛 테스트 (signals/config/window/trader)
uv run python main.py config    # 병합된 설정 확인 (env 필요, 네트워크 불필요)
uv run python main.py run --simulate --job test   # 시뮬레이션 (실키 필요 - CLOB 인증)
```

- 시뮬레이션도 CLOB 가격 조회에 실키가 필요하다 (주문만 가짜). 네트워크 없는 검증은 `pytest` + `config` 명령까지.
- 코드 수정 후에는 최소 `uv run pytest`를 통과시킨다.

## 구조와 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 전략의 단일 소스.** 캐리 수식(`carry_yield`)·진입(`evaluate_entry`)·
  청산(`evaluate_exit`)이 순수 함수로 모여 있고, scanner/trader는 호출만 한다.
  **전략을 바꾸려면 이 파일(+`config.py` 기본값)과 `tests/test_signals.py`만 수정하면 된다.**
- `strategy/scanner.py`: Gamma 필터 + 스냅샷 로드/백필 + evaluate_entry 호출.
- `strategy/trader.py`: 주문 실행 + EXPIRED 마감 처리 + 회고 로깅 컬럼 기록.
- `bot.py` 사이클: Phase 0 스냅샷 저장 → 1 청산(EXPIRED 포함) → 2 스캔 → 3 재진입 체크+매수 → 4 스냅샷 정리.
  Gamma sweep은 1회만 수행해 Phase 0/2가 공유한다.
- `api/history_client.py`: CLOB `/prices-history` 백필 — 문서화되지 않은 endpoint라
  **모든 예외를 조용히 None 처리**하는 것이 의도된 동작이다. 고치지 말 것.
  unix 변환은 `to_unix_utc` 사용 (naive datetime `.timestamp()` 금지 — 로컬 타임존 버그).
- 스냅샷(`market_snapshots.probability`)은 항상 YES(index 0) 가격 기준. favorite이 NO면 signals가 부호를 뒤집는다.
- 경계 비교는 `signals.EPSILON`(1e-9)으로 float 오차를 흡수한다.

## 이 봇의 고유 특성 (변경 시 STRATEGY.md 동기화 필수)

- **trailing stop 없음** — 0.99 수렴 보유가 본질. `take_profit_percent` 기본 9.99는
  목표가 0.99 캡만 작동시키기 위한 의도된 값이다.
- 재진입 허용: `condition_id` unique 아님. HOLDING 중이거나 쿨다운(24h) 이내만 skip.
- 윈도우는 timestamp 기반(`get_window`/`is_window_valid`). invalid면 백필 시도, 그래도 invalid면 진입 금지.
- 회고 로깅 표준(§A): trades에 `strategy_name`("mango")/`mode`("live"/"sim")/`volume_24h_at_buy`/
  `carry_yield_at_buy`/`momentum_6h_at_buy`/`carry_yield_at_exit` 기록. CSV export에도 포함.
  이 계약을 깨면 교차 봇 포스트모템 쿼리가 깨진다.

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (체결 가정 포함 — 기존 봇과의 A/B 비교용, 한계는 STRATEGY.md §8)
- `POLYMARKET_PRIVATE_KEY`/`POLYMARKET_FUNDER_ADDRESS` env 필수, chain_id=137. signature_type은 `POLYMARKET_SIGNATURE_TYPE` env (기본 1=구형 프록시 계정, 2026+ 신규 계정은 3=POLY_1271 — 1로 서명 시 "maker address not allowed" 거절)
- `POLYBOT_BUY_AMOUNT`(USDC), MIN_ORDER_SIZE=5주 체크
- `data/<job>/` 분리, `trades_sim.db` 시뮬레이션 분리
- gamma 페이지네이션의 offset>0 HTTP 422 = 정상 종료 처리
- `py-clob-client-v2>=1.0.1` (구버전 py-clob-client 금지 — order_version_mismatch)

## 금지

- 실키(private key, funder address 실값)를 어떤 파일에도 쓰지 않는다. `.env`는 커밋 금지, `.env.example`만 유지.
- `data/`를 git에 커밋하지 않는다 (`.gitignore` 등록됨).
- 다른 `golden-*` 폴더를 수정하지 않는다 (READ-ONLY 참조만).

`CLAUDE.md`는 `@AGENTS.md` 한 줄만 둔다.

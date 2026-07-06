# L3 AGENTS.md — golden-orange

Polymarket **Fear Spike Fade** 전략 봇 — 평시 YES <= 15% tail 시장이 공포 헤드라인에 +0.10 이상 급등한 뒤, 스파이크가 멈추면(90분 경과 + 45분 신고가 없음 + 거래량 2배) NO 토큰을 매수해 공포 프리미엄의 감쇠를 수확한다. probability neglect(Sunstein) 기반, 공개 문헌에서 독립 도출.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 운영 지침), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·파라미터·리스크는 `STRATEGY.md`, 실행법·env 표는 `README.md` 참조.

## 실행 / 검증

```bash
uv sync --frozen                              # 의존성 설치 (lock 고정)
uv sync --extra dev && uv run pytest          # 테스트
uv run python main.py run --simulate --job test   # 시뮬레이션 (실키 필요 - CLOB 가격 조회)
uv run python main.py run                     # 실거래 (Jenkins)
uv run python main.py config                  # 설정 확인 (네트워크 불필요)
uv run python main.py status                  # 포지션/통계 확인
```

- 코드 수정 후 최소 `uv run pytest` + `main.py config`(더미 env 가능)를 통과시킨다.

## 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 순수 함수 (전략의 단일 소스).** 스냅샷 리스트/숫자 입력 → 판정 출력, I/O 없음. **전략을 바꾸려면 이 파일(+`config.py` 기본값)만 수정하면 된다.** `tests/test_signals.py`가 그대로 전략 검증이다.
- `strategy/scanner.py`: Gamma sweep(1회) + 스냅샷 저장 + `evaluate_entry` 호출.
- `strategy/trader.py`: 주문 실행 + 청산 체인(손절 → **retrace_target** → 익절 0.99캡 → max_holding → time_exit) + EXPIRED 처리. retrace 판정은 trade에 저장된 `base_price_at_buy`/`spike_peak_at_buy` vs 스냅샷 최신 YES.
- `api/history_client.py`: CLOB `/prices-history` 백필(7일). 비공식 endpoint라 **모든 예외는 조용히 None** — 고치지 말 것. naive datetime은 `to_unix_utc`로 UTC 고정 (`.timestamp()` 직접 호출 금지).
- `db/`: trades(§A 회고 컬럼: strategy_name/mode/volume_24h_at_buy/시그널 `*_at_buy`/`yes_price_at_exit`) / market_snapshots(YES 가격 기준) / skipped_markets(쿨다운 판정용).

## 구조 특이사항

- 방향이 전략에 내장: 항상 NO 토큰(`clobTokenIds[1]`) 매수. `--yes-only` 플래그 없음.
- 재진입 허용: `condition_id` unique 아님. HOLDING 중이거나 쿨다운(`POLYBOT_REENTRY_COOLDOWN_HOURS`, 기본 24h) 이내만 skip.
- 윈도우는 timestamp 기반(`get_window`/`is_window_valid`). invalid면 백필 시도, 그래도 invalid면 진입 금지.
- trailing stop 없음. 주 청산은 retrace_target, TP는 보조(0.99 캡).
- 파라미터 추가 시: `config.py`(env > yaml > 기본값) → `config.yaml` 주석 → README/STRATEGY 표 동기화.

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (체결 가정 포함 — cherry와의 A/B 비교용, 한계는 STRATEGY.md §8)
- `POLYMARKET_PRIVATE_KEY`/`POLYMARKET_FUNDER_ADDRESS` env 필수, chain_id=137. signature_type은 `POLYMARKET_SIGNATURE_TYPE` env (기본 1=구형 프록시 계정, 2026+ 신규 계정은 3=POLY_1271 — 1로 서명 시 "maker address not allowed" 거절)
- `POLYBOT_BUY_AMOUNT`(USDC), MIN_ORDER_SIZE=5주 체크
- `data/<job>/` 분리, `trades_sim.db` 시뮬레이션 분리
- gamma 페이지네이션의 offset>0 HTTP 422 = 정상 종료 처리
- `py-clob-client-v2>=1.0.1` (구버전 py-clob-client 금지 — order_version_mismatch)

## 금지

- 실키(private key, funder address 실값)를 어떤 파일에도 쓰지 않는다. `.env`는 커밋 금지, `.env.example`만 유지.
- `data/`를 git에 커밋하지 않는다 (.gitignore 등록됨).
- 다른 `golden-*` 폴더를 수정하지 않는다 (READ-ONLY 참조만, 각 봇은 독립 A/B 대상).

## CLAUDE.md

같은 폴더의 `CLAUDE.md`는 `@AGENTS.md` 한 줄만 둔다.

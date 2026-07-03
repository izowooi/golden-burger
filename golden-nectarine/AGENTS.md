# L3 AGENTS.md — golden-nectarine

Polymarket **Bottom Fisher** 전략 봇: 장기(30일+) tail~중간 구간(YES 3~50%) 시장에서 YES 가격이 20일 롤링 최저가 이하로 떨어지면 매수하고, 보유 120h(5일) 경과 시 손익 무관 무조건 청산한다(calendar exit). QuantPedia 공개 백테스트(X=20/Y=5)의 복제 구현.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 공통 규칙), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·파라미터·리스크는 `STRATEGY.md`, 실행법·env 표는 `README.md` 참조.

## 실행 / 검증 명령

```bash
uv sync --frozen                          # 의존성 설치 (재현 가능)
uv sync --extra dev && uv run pytest      # 테스트
uv run python main.py run --simulate --job test   # 시뮬레이션 1사이클 (실키 필요 - CLOB 인증)
uv run python main.py config              # 설정 확인 (네트워크 불필요)
uv run python main.py status              # 포지션/통계 확인
```

- 시뮬레이션도 CLOB 가격 조회에 실키가 필요하다 (주문만 가짜). 네트워크 없는 검증은 `pytest` + `config` 명령까지.
- 코드 수정 후에는 최소 `uv run pytest`를 통과시킨다.

## 코드 구조 (전략 변경 위치)

- **`src/polybot/strategy/signals.py` = 전략의 단일 소스.** 진입(`evaluate_bottom_fisher`)/청산(`evaluate_exit`) 판정이 순수 함수(스냅샷 리스트/숫자 입력 → 판정 출력)로 모여 있다. **전략을 바꾸려면 이 파일(+`config.py` 기본값)만 수정**하고 `tests/test_signals.py`를 갱신한다. 경계 비교는 `EPSILON=1e-9`로 float 오차를 흡수한다.
- `strategy/scanner.py`: Gamma sweep(사이클당 1회) + 스냅샷 저장 + 20일 백필(fidelity=60) + signals 호출.
- `strategy/trader.py`: 주문 실행 + zero-midpoint 가드 + EXPIRED 마감. 판정은 signals에 위임.
- `bot.py`: Phase 0 스냅샷 저장(YES 가격 기준) → 1 청산 체크 → 2 스캔 → 3 재진입 체크+매수 → 4 스냅샷 정리(60일 보존).
- `api/history_client.py`: CLOB `/prices-history` 백필 — **20일 룩백의 생명선**. 문서화되지 않은 endpoint라 모든 예외를 조용히 None 처리하는 것이 의도된 동작이다. naive datetime은 `to_unix_utc`로 UTC 고정한다 (`.timestamp()` 직접 사용 금지 — KST 머신에서 9시간 어긋남).
- `db/`: trades(회고 로깅 표준: `strategy_name`/`mode`/`volume_24h_at_buy`/`rolling_min_at_buy`/`lookback_days_at_buy`/`hold_hours_at_exit`) / market_snapshots(YES 가격 기준) / skipped_markets(쿨다운 판정용). init_database가 신규 컬럼을 best-effort ALTER.

## 구조 특이사항 (다른 golden-* 봇과 다른 점)

- **주 청산 경로가 가격이 아니라 달력이다**: 보유 120h 도달 시 `max_holding`으로 무조건 청산. SL/TP ±30%는 안전판일 뿐.
- YES 매수 고정 (백테스트와 동일 조건). `--yes-only` 플래그 없음, 1-p 환산 없음.
- 재진입 쿨다운 기본 168h (7일) — 롤링 최저가 부근 연속 재진입 방지.
- 룩백 윈도우 invalid면 백필 시도, 그래도 invalid면 진입 금지 (관대한 cold-start 폴백 금지).

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (체결 가정 포함 — cherry와의 A/B 비교용, 한계는 STRATEGY.md §8)
- `POLYMARKET_PRIVATE_KEY`/`POLYMARKET_FUNDER_ADDRESS` env 필수, signature_type=1, chain_id=137
- `POLYBOT_BUY_AMOUNT`(USDC), MIN_ORDER_SIZE=5주 체크
- `data/<job>/` 분리, `trades_sim.db` 시뮬레이션 분리
- gamma 페이지네이션의 offset>0 HTTP 422 = 정상 종료 처리
- `py-clob-client-v2>=1.0.1` (구버전 py-clob-client 금지 — order_version_mismatch)
- 스냅샷은 항상 **YES 가격** 기준 저장

## 금지 사항

- 실키(.env, private key, 주소 실값)를 어떤 파일에도 쓰지 않는다. `.env`는 커밋 금지, `.env.example`만 유지.
- `data/`를 커밋하지 않는다 (.gitignore 등록됨).
- 다른 `golden-*` 폴더를 수정하지 않는다 (독립 프로젝트, READ-ONLY 참조만).

## CLAUDE.md

같은 폴더의 `CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.

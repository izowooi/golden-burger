# L3 AGENTS.md — golden-lime

Polymarket **Shock Follow** 전략 봇 — 충격 뉴스 급변(6h 내 +0.10 점프) 중 거래량 폭증 + 고점 유지가 확인된 "진짜 정보"에만 편승한다. golden-elderberry(Panic Fade)와 정반대 트리거의 A/B 쌍.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 공통 규칙), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·규칙: `STRATEGY.md`. 실행·env 표: `README.md`.

## 실행/검증 명령

```bash
uv sync --frozen            # 의존성 설치 (재현 가능)
uv sync --extra dev         # + pytest
uv run pytest               # 유닛테스트 (signals/config/window)
uv run python main.py run --simulate --job test   # 시뮬레이션 사이클 1회
uv run python main.py config                      # 설정 확인 (네트워크 불필요)
```

- 시뮬레이션도 CLOB 가격 조회에 실키가 필요하다 (주문만 가짜). 네트워크 없는 검증은 `pytest` + `config` 명령까지.
- 코드 수정 후에는 최소 `uv run pytest`를 통과시킨다.

## 구조와 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 전략의 단일 소스.** 진입/청산 시그널이 순수 함수(스냅샷 리스트/숫자 입력 → 판정 출력)로 모여 있고, scanner/trader는 이를 호출만 한다. **전략을 바꾸려면 이 파일(+`config.py`의 기본값)만 수정하면 된다.**
- `strategy/scanner.py`: Gamma 시장 필터 + 스냅샷 로드/백필 + evaluate_entry 호출.
- `strategy/trader.py`: 주문 실행 + 청산 판정 체인 (EXPIRED 처리 포함).
- `bot.py`: Phase 0(스냅샷 저장) → 1(청산) → 2(스캔) → 3(매수) → 4(스냅샷 정리). Gamma sweep은 1회만 수행해 Phase 0/2가 공유.
- `api/history_client.py`: CLOB `/prices-history` 백필 — 문서화되지 않은 endpoint라 **모든 예외를 조용히 None 처리**하는 것이 의도된 동작이다. 고치지 말 것.
- 스냅샷(`market_snapshots.probability`)은 항상 YES(index 0) 가격 기준. NO 방향 판정은 `signals.invert_series()`가 담당.

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (체결 가정 포함 — cherry와의 A/B 비교용, 한계는 STRATEGY.md §8에 명시)
- `POLYMARKET_PRIVATE_KEY`/`POLYMARKET_FUNDER_ADDRESS` env 필수, chain_id=137. signature_type은 `POLYMARKET_SIGNATURE_TYPE` env (기본 1=구형 프록시 계정, 2026+ 신규 계정은 3=POLY_1271 — 1로 서명 시 "maker address not allowed" 거절)
- `POLYBOT_BUY_AMOUNT`(USDC), MIN_ORDER_SIZE=5주 체크
- `data/<job>/` 분리, `trades_sim.db` 시뮬레이션 분리
- gamma 페이지네이션의 offset>0 HTTP 422 = 정상 종료 처리
- `py-clob-client-v2>=1.0.1` (구버전 py-clob-client 금지 — order_version_mismatch)

## 금지

- 실키(private key, funder address 실값)를 어떤 파일에도 쓰지 않는다. `.env`는 커밋 금지, `.env.example`만 유지.
- `data/`를 git에 커밋하지 않는다.
- 다른 `golden-*` 폴더를 수정하지 않는다 (READ-ONLY 참조만).

## CLAUDE.md

같은 폴더의 `CLAUDE.md`는 `@AGENTS.md` 한 줄만 둔다.

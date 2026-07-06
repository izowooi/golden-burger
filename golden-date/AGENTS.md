# L3 AGENTS.md — golden-date

Polymarket **Conviction Ladder** 전략 봇 (시간 사다리 확률 밴드 + 모멘텀 게이트, cherry 고도화).

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 공통 규칙), L1 워크스페이스 규칙.
- 전략 근거·명세: `STRATEGY.md`. 실행/운영: `README.md`.

## 실행/검증 명령

```bash
uv sync --frozen                # 의존성 설치 (lock 기준)
uv sync --extra dev             # 개발(테스트) 의존성 포함
uv run pytest                   # 유닛 테스트
uv run python main.py config    # 병합된 설정 확인 (env 필요, 네트워크 불필요)
uv run python main.py run --simulate --job test   # 시뮬레이션 (실키 필요 - CLOB 인증)
```

## 코드 구조 핵심

- **전략 로직 = `src/polybot/strategy/signals.py`의 순수 함수**. 스냅샷 리스트/숫자 입력 → 판정 출력.
  진입 사다리·모멘텀 게이트·청산 우선순위를 바꾸려면 이 파일과 `tests/test_signals.py`만 수정하면 된다.
- `strategy/scanner.py`(스캔·백필 병합)와 `strategy/trader.py`(주문 실행)는 signals를 호출하는 껍데기다.
- 설정은 `config.py`에서 env > `config.yaml` > 코드 기본값 순으로 병합된다. 파라미터 추가 시 세 곳(env 파싱, yaml, 기본값)과 README/STRATEGY 표를 함께 갱신한다.
- `bot.py` 사이클: Phase 0 스냅샷 저장 → 1 청산(EXPIRED 처리 포함) → 2 스캔 → 3 재진입 체크+매수 → 4 스냅샷 정리. Gamma sweep은 1회만 수행해 Phase 0/2가 공유한다.
- 스냅샷 probability는 **항상 YES 가격 기준**이다. favorite이 NO면 signals가 부호를 뒤집는다.

## 금지 사항

- 실키(`POLYMARKET_PRIVATE_KEY` 등)·`.env` 커밋 금지. `.env.example`만 커밋한다.
- `data/` 커밋 금지 (`.gitignore` 등록됨).
- 다른 `golden-*` 폴더 수정 금지 — 각 봇은 독립 프로젝트다.
- 구버전 `py-clob-client` 사용 금지 (`order_version_mismatch`) — `py-clob-client-v2>=1.0.1`만 사용.

## 유지해야 하는 패턴 (변경 금지)

- GTC limit order at midpoint (cherry와의 A/B 비교용, 한계는 STRATEGY.md §8)
- signature_type은 `POLYMARKET_SIGNATURE_TYPE` env (기본 1=구형 프록시 계정, 2026+ 신규 계정은 3=POLY_1271), `chain_id=137`, `MIN_ORDER_SIZE=5`주 체크
- `data/<job>/` 분리, `trades_sim.db` 시뮬레이션 분리
- Gamma 페이지네이션의 offset>0 HTTP 422 = 정상 종료 처리

`CLAUDE.md`는 `@AGENTS.md` 한 줄만 둔다.

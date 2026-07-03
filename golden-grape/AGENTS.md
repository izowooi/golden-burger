# L3 AGENTS.md — golden-grape

Polymarket Cascade Rider(정보 폭포 편승) 전략 봇. 24h 소폭 드리프트(+4~10%p) + 버킷 일관성 + 거래량 가속이 확인된 시장에 편승한다.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 운영 지침), L1 `/Users/izowooi/git/AGENTS.md` (전역 규칙).
- 전략 근거·명세는 `STRATEGY.md`, 실행법·env 표는 `README.md` 참조.

## 실행/검증 명령

```bash
uv sync --frozen                                  # 의존성 설치 (lock 고정)
uv sync --extra dev                               # 테스트 포함 설치
uv run pytest                                     # 유닛테스트
uv run python main.py run --simulate --job test   # 시뮬레이션 드라이런
uv run python main.py config                      # 설정 확인 (네트워크 불필요)
```

- 시뮬레이션도 CLOB 가격 조회를 위해 `.env` 실키가 필요하다 (주문만 가짜).
- `main.py config`는 더미 env로도 동작한다 — 코드 변경 후 최소 sanity check로 사용.

## 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 순수 함수 (전략의 단일 소스)**. 스냅샷 리스트/숫자 입력 → 판정 출력. I/O 없음. **전략을 바꾸려면 여기만 바꾸면 된다.** scanner/trader는 이 함수들을 호출만 한다.
- `strategy/scanner.py`: gamma sweep 필터링 + 스냅샷 로드/백필 + `evaluate_entry` 호출.
- `strategy/trader.py`: 주문 실행 + 청산 우선순위(손절 → 익절 0.99캡 → drift_death → 트레일링 → 시간) + EXPIRED 처리.
- `api/history_client.py`: CLOB `/prices-history` 백필. **모든 예외는 조용히 None** — 실패해도 봇이 정상 동작해야 한다.
- 파라미터 추가 시: `config.py`(env > yaml > 기본값 병합) → `config.yaml` 주석 → README/STRATEGY 표 동기화.

## 테스트 규약

- `tests/test_signals.py`: 합성 스냅샷 fixture로 진입 O/X 경계 케이스 검증. 시그널 로직 변경 시 여기부터 갱신 (TDD).
- `tests/test_window.py`: timestamp 기반 윈도우/커버리지 검증 (banana의 count 기반 윈도우 버그 회귀 방지).
- `tests/test_config.py`: env 오버라이드 동작.

## 금지

- 실키(`POLYMARKET_PRIVATE_KEY` 등)·`.env` 커밋 금지. `.env.example`만 커밋.
- `data/` 커밋 금지 (.gitignore 등록됨 — 런타임 DB/로그/CSV).
- 다른 `golden-*` 폴더 수정 금지 (각 봇은 독립 프로젝트, A/B 비교 대상).
- 구버전 `py-clob-client` 사용 금지 — `py-clob-client-v2>=1.0.1`만 (order_version_mismatch).
- GTC limit @ midpoint 체결 가정 패턴은 cherry와의 A/B 비교를 위해 유지 — 임의 변경 금지.

## CLAUDE.md

같은 폴더의 `CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.

# L3 AGENTS.md — golden-honeydew

Polymarket **Night Watch** 전략 봇 — 한산 시간대(UTC 06-13시/주말)에 뉴스 없이 발생한
가격 이탈을 24h median 복원 방향으로 매수하는 Python/uv 프로젝트.

- 상위 계층: L2 `/Users/izowooi/git/t1/AGENTS.md` (모노레포 규칙), L1 워크스페이스 전역 규칙.
- 전략 근거·규칙 상세: `STRATEGY.md`. 실행법·env var: `README.md`.

## 실행/검증 명령

```bash
uv sync --frozen              # 의존성 설치 (lock 기준)
uv sync --extra dev           # 테스트 의존성 포함
uv run pytest                 # 유닛테스트 (signals/config/window)
uv run python main.py run --simulate --job test   # 시뮬레이션 사이클 1회
uv run python main.py config  # 병합된 설정 확인 (네트워크 불필요)
```

- `run`/`status`/`config` 모두 `POLYMARKET_PRIVATE_KEY`·`POLYMARKET_FUNDER_ADDRESS` env 필수.
- 시뮬레이션 모드도 가격 조회는 실제 API를 사용하므로 유효한 키가 필요하다.

## 전략 로직 위치

- **`src/polybot/strategy/signals.py` = 전략의 전부.** 순수 함수(스냅샷 리스트/숫자 입력 →
  판정 출력)로 분리되어 있다. 전략을 바꾸려면 이 파일(과 대응 테스트)만 수정하면 된다.
- `strategy/scanner.py`/`strategy/trader.py`는 signals를 호출하는 배선 계층이다.
  I/O(Gamma/CLOB/DB)와 판정 로직을 섞지 않는다.
- 파라미터 추가 시: `config.py`(env > yaml > 기본값 병합) → `config.yaml` 주석 →
  `README.md`/`STRATEGY.md` env 표 순서로 함께 갱신한다.

## 구조 메모

- 사이클: Phase 0 스냅샷 저장 → 1 청산(EXPIRED 처리 포함) → 2 스캔 → 3 매수 → 4 스냅샷 정리.
  Gamma sweep은 사이클당 1회만 (Phase 0/2 공유).
- 스냅샷은 YES 가격 기준으로 저장한다. cold start는 `api/history_client.py`의
  prices-history 백필로 보완하며, 백필 실패는 조용히 무시된다 (진입 안 함으로 처리).
- 재진입은 영구 차단이 아니라 쿨다운(`POLYBOT_REENTRY_COOLDOWN_HOURS`, 기본 24h) 방식.
  `trades.condition_id`는 unique가 아니다.
- `py-clob-client-v2>=1.0.1` 필수. 구버전 `py-clob-client`는 order_version_mismatch로 금지.

## 금지

- 실키(private key, funder address 등)를 어떤 파일에도 커밋하지 않는다 (`.env.example`만 허용).
- `data/`(DB·로그·CSV)를 커밋하지 않는다.
- 다른 `golden-*` 폴더를 수정하지 않는다 (각 봇은 의도적으로 독립 코드베이스).
- `CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.

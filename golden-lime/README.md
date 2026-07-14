# Golden Lime - Polymarket 자동 매매 봇

**Shock Follow** 전략 기반 Polymarket 자동 매매 봇입니다. 충격 뉴스로 인한 급변(6h 내 +0.10 점프) 중 "진짜 정보"(거래량 폭증 + 고점 유지)만 골라 점프 방향에 편승합니다. golden-elderberry(Panic Fade)와 정반대 트리거의 A/B 쌍입니다.

전략 근거·규칙 상세는 **[STRATEGY.md](./STRATEGY.md)** 참조.

## 개요

- **매수 조건**: 6h 윈도우 최저가 대비 +0.10 점프 + 기준가 [0.15, 0.70] + 현재가 <= 0.85 + 최근 60분 되돌림 <= 0.02 + 거래량 24h 평균 x2.0 + 해결까지 >= 24h
- **매도 조건**: 손절 -8% → 익절 +12%(목표가 0.99 캡) → 트레일링 -6% → 모멘텀 사망(3h 변화 <= 0) → 해결 12h 전
- **방향**: YES 급등이면 YES 매수, YES 급락(=NO 급등)이면 NO 매수
- **재진입**: 영구 밴 없음 — 마지막 매도/skip 후 24h 쿨다운

## Quickstart

```bash
cd golden-lime

# 1. 의존성 설치
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 편집: POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS 입력
# (https://polymarket.com/settings?tab=export-private-key)

# 3. API 연결 테스트
uv run python scripts/test_api_key.py

# 4. 시뮬레이션 실행 (실제 주문 없음, 반드시 먼저)
uv run python main.py run --simulate --job test

# 5. 설정 확인
uv run python main.py config
```

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 기본값은 active(기존 매매). 퇴역할 때만 아래 주석을 해제합니다.
# export POLYBOT_LIFECYCLE_MODE=close_only
# 전략 주요 파라미터 (기본값 사용 시 생략 가능)
export POLYBOT_JUMP_MIN=0.10
export POLYBOT_VOL_MULT_MIN=2.0
export POLYBOT_MAX_PULLBACK=0.02

cd ./golden-lime
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3~5분 주기 cron 트리거를 권장합니다 (스냅샷 해상도 = 실행 주기).

### 전략 퇴역 (`close_only`)

`POLYBOT_LIFECYCLE_MODE=close_only`는 Phase 0 스냅샷, 기존 포지션의 정상
청산 판단, Phase 4 정리를 계속하면서 신규 후보 스캔과 BUY를 차단합니다. 전량을
즉시 매도하는 모드가 아니며, Lime에는 최대 보유 시간 제한이 없으므로
손절·익절·trailing·momentum-death·만기 12시간 전 조건 중 하나가 충족될 때까지
7일 이상 남을 수도 있습니다. `archive_only`는 모든 주문 경로를 끄고 스냅샷과
정리만 수행하므로 포지션과 미체결 주문이 모두 정리된 뒤에만 사용합니다.

5분 Jenkins 주기는 그대로 사용할 수 있습니다. 기존과 같은 `--job`과 workspace를
유지하고 concurrent build를 비활성화해 이전 사이클과 겹치지 않게 하세요. 전환 전
이미 접수된 GTC BUY는 `close_only`가 자동 취소하지 않으므로, 동일한 계정 credential로
저장소 루트의 `tools/wind_down.py cancel --side BUY`를 dry-run한 뒤 `--yes`로 한 번
취소해야 합니다. 전환 직후 `main.py config`의 `Lifecycle Mode: close_only`와 실행
로그의 `Phase 2/3 건너뜀`을 확인하세요. `POLYBOT_BUY_AMOUNT=0`은 설정 검증에서
거부되므로 신규 진입 차단 용도로 사용하지 않습니다.
전체 전환·잔여 포지션 절차는
[전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따릅니다.

## CLI

| 명령 | 설명 |
|------|------|
| `uv run python main.py run` | 매매 사이클 1회 실행 (Jenkins용) |
| `uv run python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 로그만) |
| `uv run python main.py run --job <name>` | job별 DB/로그 분리 |
| `uv run python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL env보다 우선) |
| `uv run python main.py status` | 보유 포지션·통계 JSON 출력 |
| `uv run python main.py config` | 현재 설정 출력 |

## 환경변수 전체 표

우선순위: **env > config.yaml > 코드 기본값**

### 필수 (API 인증)

| env | 설명 |
|-----|------|
| `POLYMARKET_PRIVATE_KEY` | CLOB L1 인증 private key (0x 접두사 자동 제거) |
| `POLYMARKET_FUNDER_ADDRESS` | funder 지갑 주소 |

### 공통 (선택)

| env | 기본 | 의미 |
|-----|------|------|
| `POLYBOT_LIFECYCLE_MODE` | active | `active` / `close_only` / `archive_only` |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.12 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 비활성) |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 최고점 대비 하락률 |
| `POLYBOT_ENTRY_HOURS_MIN` | 24 | 진입 최소 잔여 시간 |
| `POLYBOT_EXIT_HOURS` | 12 | 시간 청산 기준 |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

### Shock Follow 전략 (선택)

| env | 기본 | 의미 |
|-----|------|------|
| `POLYBOT_JUMP_WINDOW_HOURS` | 6 | 점프 감지 윈도우 (시간) |
| `POLYBOT_JUMP_MIN` | 0.10 | 윈도우 내 최저가 대비 최소 상승폭 |
| `POLYBOT_BASE_MIN` | 0.15 | 점프 시작 기준가 하한 |
| `POLYBOT_BASE_MAX` | 0.70 | 점프 시작 기준가 상한 |
| `POLYBOT_CURRENT_MAX` | 0.85 | 현재가 상한 (러닝룸) |
| `POLYBOT_HOLD_WINDOW_MINUTES` | 60 | 고점 유지 확인 윈도우 (분) |
| `POLYBOT_MAX_PULLBACK` | 0.02 | 고점 대비 최대 되돌림 |
| `POLYBOT_VOL_MULT_MIN` | 2.0 | 거래량 확인 배수 |
| `POLYBOT_DEATH_WINDOW_HOURS` | 3 | 모멘텀 사망 판정 윈도우 (청산) |

## data/ 구조

```
data/
└── {job_name}/
    ├── trades.db            # 실거래 SQLite DB
    ├── trades_sim.db        # 시뮬레이션 DB (분리)
    ├── trades_YYYY-MM.csv   # 완료 거래 월별 CSV
    └── logs/
        └── YYYYMMDD.log     # 일자별 로그
```

`data/`는 git에 커밋하지 않습니다 (.gitignore 등록).

## 시뮬레이션 → 실전 전환 절차

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim` 을 Jenkins 주기 실행. `data/sim/trades_sim.db`에서 진입 빈도·신호 품질 확인.
   - 주의: 시뮬레이션도 CLOB 가격 조회에 실키(.env)가 필요합니다 (주문만 가짜).
2. **소액 실전**: `POLYBOT_BUY_AMOUNT=5` 로 `--simulate` 제거 후 실행. 4주/30+ 거래 기준으로 판정 (STRATEGY.md §6).
3. **증액**: 승률·평균 손익 기준 통과 시 `POLYBOT_BUY_AMOUNT` 상향.
4. **모니터링**: `status` 명령의 `expired` 카운트가 뜨면 해결된 시장의 수동 redeem이 필요합니다.

## 개발/검증

```bash
uv sync --extra dev
uv run pytest            # signals/config/window 유닛테스트
```

전략 로직은 `src/polybot/strategy/signals.py`의 순수 함수에 모여 있습니다. 전략 변경은 이 파일(+config)만 수정하면 됩니다.

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요
- **테스트**: 실제 거래 전 반드시 시뮬레이션 모드로 테스트하세요
- **소액 시작**: 처음에는 `POLYBOT_BUY_AMOUNT=5` 수준의 소액을 권장합니다
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요

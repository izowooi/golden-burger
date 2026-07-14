# Golden Elderberry - Polymarket 자동 매매 봇

Panic Fade 전략 기반 Polymarket 자동 매매 봇입니다. 원래 favorite(≥70%)이던 토큰이 공황 투매로 12%p 이상 급락한 뒤 바닥 안정화가 확인되면 역매수하고, 반등(+10%) 시 청산합니다.

전략 근거·정밀 명세·리스크는 [STRATEGY.md](STRATEGY.md) 참조.

## 개요

- **매수 조건**: 48h 최고가(ref) ≥ 70% + 낙폭 ≥ 12%p + 현재가 35~75% + 최근 45분 바닥 안정화(std ≤ 0.02) + 해결까지 48h 이상
- **매도 조건**: 손절 -10% → 익절 +10%(0.99 캡) → 보유 48h 초과 → 해결 24h 전
- **재진입**: 영구 밴 없음. HOLDING 중이거나 마지막 청산/skip 후 24h 이내면 차단
- **방향**: ref 윈도우의 YES peak와 NO peak(`1-YES`)를 비교해 더 높았던 실제 former favorite을 매수 (전략에 내장, `--yes-only` 플래그 없음)

## Quickstart

```bash
cd golden-elderberry

# 1. 의존성 설치
uv sync

# 2. 환경변수 설정 (.env 파일 작성)
cp .env.example .env
# POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS 입력

# 3. API 연결 테스트
uv run python scripts/test_api_key.py

# 4. 시뮬레이션 실행 (실제 주문 없음)
uv run python main.py run --simulate --job test
```

## CLI

```bash
uv run python main.py run                # 실거래 사이클 1회 (Jenkins용)
uv run python main.py run --simulate     # 시뮬레이션 모드 (trades_sim.db 분리)
uv run python main.py run --job myjob    # job별 DB/로그 분리
uv run python main.py run --verbose      # DEBUG 로그 (LOG_LEVEL env보다 우선)
uv run python main.py status             # 보유 포지션/통계 JSON 출력
uv run python main.py config             # 현재 설정 출력
```

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 퇴역할 때만: export POLYBOT_LIFECYCLE_MODE=close_only
# 전략 파라미터 예시 (생략 시 기본값)
export POLYBOT_DROP_MIN=0.12
export POLYBOT_REENTRY_COOLDOWN_HOURS=24
export POLYBOT_MAX_HOLDING_HOURS=48

cd ./golden-elderberry
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

5분 주기로 실행할 때는 Jenkins 동시 빌드를 비활성화하고, 최초 실행 로그의 `Lifecycle` 값과 `config` 출력이 기대한 모드인지 확인하세요.

### Lifecycle 운영

- `active`(기본): 기존과 동일하게 스냅샷·청산·신규 매수를 모두 수행합니다.
- `close_only`: Phase 0 스냅샷과 Phase 1 청산, Phase 4 정리는 유지하고 스캔·신규 매수를 차단합니다.
- `archive_only`: 스냅샷과 정리만 유지하고 매수·매도 주문을 모두 차단합니다.

`close_only` 전환은 기존에 접수된 GTC BUY 주문을 취소하지 않습니다. 전환 직후 동일 계정으로 [전략 종료 플레이북](../docs/strategy-wind-down-playbook.md)의 dry-run을 확인한 뒤 GTC BUY 주문을 한 번 취소하세요. Panic Fade의 정상 최대 보유는 48시간이지만, 주문 실패·미체결·EXPIRED 여부까지 확인한 후 archive로 전환해야 합니다.

## 환경변수 전체 표

우선순위: **env > config.yaml > 코드 기본값**

### 필수

| env | 설명 |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | Polymarket private key (0x 접두사 허용) |
| `POLYMARKET_FUNDER_ADDRESS` | 지갑 주소 |

### 공통 (전 봇 동일 이름)

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_LIFECYCLE_MODE` | active | `active` / `close_only` / `archive_only` |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.10 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.10 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 (cold start 완화) |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 필터 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

### Panic Fade 전략

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_REF_WINDOW_HOURS` | 48 | 기준가(ref) 산출 윈도우 |
| `POLYBOT_REF_EXCLUDE_RECENT_HOURS` | 3 | ref 산출 시 제외할 최근 구간 |
| `POLYBOT_REF_MIN` | 0.70 | ref 최소값 (원래 favorite이어야 함) |
| `POLYBOT_DROP_MIN` | 0.12 | 최소 낙폭 (ref - 현재가) |
| `POLYBOT_CURRENT_MIN` | 0.35 | 진입 밴드 하한 (붕괴 배제) |
| `POLYBOT_CURRENT_MAX` | 0.75 | 진입 밴드 상한 |
| `POLYBOT_STAB_WINDOW_MINUTES` | 45 | 바닥 안정화 확인 윈도우 (분) |
| `POLYBOT_STAB_MAX_STD` | 0.02 | 안정화 최대 표준편차 |
| `POLYBOT_ENTRY_HOURS_MIN` | 48 | 해결까지 최소 잔여 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | 48 | 최대 보유 시간 (초과 시 반등 실패 청산) |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 이 시간 전 청산 |

## data/ 구조

```
data/
└── {job_name}/
    ├── trades.db            # 실거래 SQLite DB
    ├── trades_sim.db        # 시뮬레이션 DB (분리)
    ├── trades_YYYY-MM.csv   # 완료 거래 월별 CSV
    └── logs/
        └── YYYYMMDD.log
```

- `trades`: 거래 레코드 (재진입 허용이므로 condition_id는 unique 아님)
- `market_snapshots`: **YES 가격 기준** 시계열 (신호 계산용, 보관 7일)
- `skipped_markets`: skip 기록 (재진입 쿨다운 시작점)

## 시뮬레이션 → 실전 전환 절차

1. `uv run python main.py run --simulate --job sim-test`를 Jenkins에 등록해 1-2주 시뮬레이션 (시뮬레이션도 가격 조회에 실키가 필요)
2. `data/sim-test/trades_sim.db`로 승률/평균손익 검증 (기준: STRATEGY.md §6)
3. `POLYBOT_BUY_AMOUNT=1`로 소액 실전 시작 (`--simulate` 제거)
4. 4주 + 30건 이상 검증 후 증액

## 테스트

```bash
uv sync --extra dev
uv run pytest
```

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요 (`.gitignore`에 등록됨)
- **data/ 커밋 금지**: 런타임 DB/로그는 git에 넣지 않습니다
- **테스트**: 실제 거래 전 반드시 시뮬레이션 모드로 테스트하세요
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요
- **EXPIRED 포지션**: 해결된 시장을 청산하지 못하면 EXPIRED로 마감되며 **수동 redeem이 필요**합니다 (로그 WARNING 확인)

# Golden Orange — Fear Spike Fade Bot

Polymarket 자동매매 봇. 평시 확률 15% 이하 tail 시장이 공포 헤드라인에 +10%p 이상 급등한 뒤 스파이크가 멈추면 **NO 토큰을 매수**해 공포 프리미엄의 감쇠를 수확한다.

전략 근거·규칙·리스크 상세: [STRATEGY.md](./STRATEGY.md)

## Quickstart

```bash
# 1. 의존성 설치
uv sync

# 2. .env 작성 (실키는 절대 커밋 금지)
cp .env.example .env
# POLYMARKET_PRIVATE_KEY=0x...
# POLYMARKET_FUNDER_ADDRESS=0x...

# 3. 시뮬레이션 실행 (주문 없이 사이클 1회)
uv run python main.py run --simulate --job test

# 4. 설정 확인 (네트워크 불필요)
uv run python main.py config

# 5. 상태/통계 확인
uv run python main.py status --job test
```

테스트:

```bash
uv sync --extra dev
uv run pytest
```

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=1000
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 전략 주요 파라미터 (기본값과 다르게 운영할 때만)
# export POLYBOT_JUMP_MIN=0.10
# export POLYBOT_SPIKE_WAIT_MINUTES=90
# export POLYBOT_RETRACE_RATIO=0.5

cd ./golden-orange
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3~5분 간격 실행을 권장한다 (스파이크 시작/스톨 판정이 스냅샷 밀도에 의존).

## CLI

| 명령 | 설명 |
|---|---|
| `run [--config F] [--job J] [--simulate] [--verbose]` | 트레이딩 사이클 1회 실행 |
| `status [--job J]` | 포지션/통계 JSON 출력 |
| `config` | 병합된 설정 출력 (네트워크 불필요) |

방향이 전략에 내장(항상 NO 매수)이므로 `--yes-only` 플래그는 없다.

## 환경 변수

우선순위: **env > config.yaml > 기본값**

### 전략 파라미터

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BASE_WINDOW_DAYS` | 7 | base(평시 확률) 계산 윈도우 (일) |
| `POLYBOT_BASE_EXCLUDE_RECENT_HOURS` | 6 | base 계산에서 제외할 최근 시간 |
| `POLYBOT_BASE_MAX` | 0.15 | base 상한 (평시 tail 시장만 대상) |
| `POLYBOT_JUMP_MIN` | 0.10 | 스파이크 최소 상승폭 (yes_now - base) |
| `POLYBOT_YES_MAX` | 0.30 | 스파이크 후 YES 상한 |
| `POLYBOT_SPIKE_WAIT_MINUTES` | 90 | 스파이크 시작 후 대기 시간 (분) |
| `POLYBOT_STALL_WINDOW_MINUTES` | 45 | 신고가 부재(스톨) 확인 윈도우 (분) |
| `POLYBOT_VOL_MULT_MIN` | 2.0 | 거래량 확인: volume24h >= 윈도우 평균 x 배수 |
| `POLYBOT_RETRACE_RATIO` | 0.5 | retrace 익절: YES <= base + ratio x (peak - base) |
| `POLYBOT_ENTRY_HOURS_MIN` | 72 | 진입 조건: 해결까지 최소 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | 72 | 최대 보유 시간 (되돌림 실패 청산) |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 이 시간 전 청산 |

### 공통 파라미터

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0=비활성) |
| `POLYBOT_TAKE_PROFIT` | 0.08 | 보조 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.10 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1=무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB /prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값=비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (`--verbose`가 최우선) |

### 필수 인증 (env 전용)

| env | 설명 |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | 지갑 private key (0x 접두어 허용) |
| `POLYMARKET_FUNDER_ADDRESS` | funder 지갑 주소 |

## data/ 구조

```
data/<job>/
├── trades.db            # 실거래 DB (SQLite)
├── trades_sim.db        # 시뮬레이션 DB (분리)
├── trades_YYYY-MM.csv   # 월별 완료 거래 export (시그널 컬럼 포함)
└── logs/YYYYMMDD.log    # 일별 로그
```

trades 테이블에는 회고 분석용 컬럼이 기록된다: `strategy_name`("orange"), `mode`("live"/"sim"), `volume_24h_at_buy`, `base_price_at_buy`, `spike_peak_at_buy`, `spike_age_minutes_at_buy`, `vol_mult_at_buy`, `yes_price_at_exit`.

`data/`는 git에 커밋하지 않는다 (.gitignore 등록).

## 시뮬레이션 → 실전 전환

1. `--simulate --job orange-sim`으로 2주+ 운영, 진입 빈도/사유 분포 확인
2. `data/orange-sim/trades_sim.db`의 성적 검토 (STRATEGY.md §6 판단 기준)
3. `POLYBOT_BUY_AMOUNT` 소액으로 `--simulate` 제거 후 실전 4주+
4. 판단 기준 충족 시 증액. 미충족 시 STRATEGY.md §7 베리에이션으로 파라미터 조정 후 재검증

주의: 시뮬레이션도 CLOB midpoint 조회에 실키가 필요하다 (주문만 가짜). 네트워크 없는 검증은 `pytest` + `config` 명령까지.

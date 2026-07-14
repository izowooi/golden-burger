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
# 기본값은 active(기존 매매). 퇴역할 때만 아래 주석을 해제합니다.
# export POLYBOT_LIFECYCLE_MODE=close_only
# 전략 주요 파라미터 (기본값과 다르게 운영할 때만)
# export POLYBOT_JUMP_MIN=0.10
# export POLYBOT_SPIKE_WAIT_MINUTES=90
# export POLYBOT_RETRACE_RATIO=0.5

cd ./golden-orange
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3~5분 간격 실행을 권장한다 (스파이크 시작/스톨 판정이 스냅샷 밀도에 의존).

### 전략 퇴역 (`close_only`)

`POLYBOT_LIFECYCLE_MODE=close_only`는 Phase 0 스냅샷, 기존 포지션의 정상
청산 판단, Phase 4 정리를 계속하면서 신규 후보 스캔과 BUY를 차단합니다. Phase 0은
retrace 청산에 쓰는 최신 YES 가격을 제공하므로 퇴역 중에도 유지됩니다. 전량을 즉시
매도하는 모드가 아니며, 정상 시장이라면 기존 포지션은 retrace·손절·익절 또는 최대
보유 72시간/만기 24시간 전 조건으로 청산을 시도합니다. `archive_only`는 모든 주문
경로를 끄고 스냅샷과 정리만 수행하므로 포지션과 미체결 주문이 모두 정리된 뒤에만
사용합니다.

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
| `POLYBOT_LIFECYCLE_MODE` | active | `active` / `close_only` / `archive_only` |
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

# Golden Honeydew - Polymarket 자동 매매 봇

**Night Watch 전략**: 한산 시간대(UTC 06-13시 = 미 동부 새벽, 또는 주말)에 뉴스 없이 발생한
가격 이탈을 복원 방향으로 매수합니다. 전략 근거·규칙 상세는 [STRATEGY.md](STRATEGY.md) 참고.

- **진입**: 한산 시간대 + 24h median 대비 |편차| >= 0.05 + 거래량 급증 아님 + 매수가 0.30~0.90
- **청산**: 손절 -6% → 익절 +6%(목표가 0.99 캡) → 최대 보유 24h → 해결 12h 전
- **방향**: 하락 이탈 → 그 토큰 매수(반등), 상승 이탈 → 반대 토큰 매수(페이드). trailing 없음.

## Quickstart

```bash
cd golden-honeydew

# 1. 의존성 설치 (uv)
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 에 POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS 입력
# (https://polymarket.com/settings?tab=export-private-key)

# 3. 시뮬레이션으로 먼저 검증 (실제 주문 없음)
uv run python main.py run --simulate --job test

# 4. 설정/상태 확인
uv run python main.py config
uv run python main.py status --job test
```

API 연결 테스트: `uv run python scripts/test_api_key.py`

## 매매 사이클

```
Phase 0: 스캔 대상 시장 스냅샷 저장 (YES 가격 기준, median 계산용)
Phase 1: 보유 포지션 청산 체크 (해결된 시장은 EXPIRED 마감 + 수동 redeem 경고)
Phase 2: 전략 스캔 (한산 시간대가 아니면 전체 skip)
Phase 3: 후보별 재진입 쿨다운 체크 → 매수
Phase 4: 오래된 스냅샷 정리
```

Gamma 전체 sweep은 사이클당 1회만 수행합니다 (Phase 0/2 공유).

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 전략 주요 파라미터 (필요 시 조정)
export POLYBOT_QUIET_HOURS_UTC="6-13"
export POLYBOT_DEV_MIN=0.05
export POLYBOT_MAX_HOLDING_HOURS=24

cd ./golden-honeydew
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3-5분 간격 cron 트리거를 권장합니다. 한산 시간대(UTC 06-13시/주말)가 아니면 봇이 스스로
진입 스캔을 skip하므로 24시간 내내 돌려도 됩니다 (스냅샷 축적을 위해 오히려 계속 돌려야 합니다).

## CLI

| 명령 | 설명 |
|---|---|
| `uv run python main.py run` | 트레이딩 사이클 1회 실행 (Jenkins용) |
| `uv run python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 로그만) |
| `uv run python main.py run --job <name>` | job별 DB/로그 분리 (`data/<name>/`) |
| `uv run python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL env보다 우선) |
| `uv run python main.py status` | 보유 포지션·통계 JSON 출력 |
| `uv run python main.py config` | 병합된 최종 설정 출력 |

## 환경변수 전체 표

우선순위: **환경변수 > config.yaml > 코드 기본값**

### 필수 (API 인증)

| env | 설명 |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | CLOB L1 인증 (0x 접두사 자동 제거) |
| `POLYMARKET_FUNDER_ADDRESS` | 지갑 주소 (signature_type=1, chain_id=137) |

### 공통 파라미터

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0=비활성) |
| `POLYBOT_TAKE_PROFIT` | 0.06 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.06 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1=무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 (cold start 보완) |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값=비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

### Night Watch 전략 파라미터

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_QUIET_HOURS_UTC` | "6-13" | 진입 허용 UTC 시간대 (자정 넘는 "22-4" 지원) |
| `POLYBOT_QUIET_WEEKENDS` | true | 주말(토/일 UTC) 전체 진입 허용 |
| `POLYBOT_MEDIAN_LOOKBACK_HOURS` | 24 | median 계산 윈도우 |
| `POLYBOT_DEV_MIN` | 0.05 | 최소 편차 \|현재가 - median\| |
| `POLYBOT_VOL_SPIKE_BLOCK` | 1.5 | 거래량 급증 차단 배수 (뉴스 배제) |
| `POLYBOT_ENTRY_PROB_MIN` | 0.30 | 매수 토큰 가격 하한 |
| `POLYBOT_ENTRY_PROB_MAX` | 0.90 | 매수 토큰 가격 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | 24 | 해결까지 최소 잔여 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | 24 | 최대 보유 시간 (복원 실패 시 회전) |
| `POLYBOT_EXIT_HOURS` | 12 | 해결 N시간 전 청산 |

## data/ 구조

```
data/
└── {job_name}/
    ├── trades.db           # 실거래 SQLite DB
    ├── trades_sim.db       # 시뮬레이션 DB (분리)
    ├── trades_YYYY-MM.csv  # 완료 거래 월별 CSV (deviation_at_buy/deviation_at_exit,
    │                       #   strategy_name/mode/volume_24h_at_buy 등 회고 분석 컬럼 포함)
    └── logs/YYYYMMDD.log   # 일자별 로그
```

`data/`는 git에 커밋하지 않습니다 (.gitignore).

## 시뮬레이션 → 실전 전환 절차

1. `uv run python main.py run --simulate --job sim-test`를 Jenkins에 3-5분 간격 등록.
2. 1-2주 후 `data/sim-test/trades_sim.db`와 월별 CSV로 진입 빈도·exit_reason 분포 확인.
3. 문제 없으면 Jenkins 스크립트에서 `--simulate` 제거 + `POLYBOT_BUY_AMOUNT` 소액(예: 5)으로 실전 시작.
4. 4주 + 30건 이상 거래 후 STRATEGY.md §6 기준으로 증액/조정/중단 판단.
5. `status` 출력과 로그에서 `EXPIRED (수동 redeem 필요)` 포지션을 주기적으로 확인하고
   Polymarket UI에서 직접 redeem.

## 테스트

```bash
uv sync --extra dev
uv run pytest
```

전략 시그널은 `src/polybot/strategy/signals.py`에 순수 함수로 분리되어 있어
합성 스냅샷으로 진입/청산 경계를 검증합니다.

## 주의사항

- **보안**: `.env`는 절대 git에 커밋하지 마세요.
- **테스트**: 실거래 전 반드시 시뮬레이션으로 검증하세요.
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요.

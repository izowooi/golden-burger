# Golden Nectarine — Polymarket Bottom Fisher Bot

장기(30일+) 시장에서 YES 가격이 **20일 롤링 최저가** 이하로 떨어지면 매수하고, **보유 120시간(5일) 경과 시 무조건 청산**하는 평균 회귀 봇. QuantPedia 공개 백테스트(X=20/Y=5, 비용 반영 CAR +18.9~22.1%)의 복제 구현이다.

전략 근거·규칙·리스크 상세: [STRATEGY.md](./STRATEGY.md)

## Quickstart

```bash
# 1. 의존성 설치
uv sync

# 2. .env 작성 (실키는 절대 커밋 금지)
cp .env.example .env
#   POLYMARKET_PRIVATE_KEY=<지갑 private key>
#   POLYMARKET_FUNDER_ADDRESS=<지갑 주소>

# 3. 시뮬레이션 1사이클 (실제 주문 없음, CLOB 가격 조회에는 실키 필요)
uv run python main.py run --simulate --job test

# 4. 설정 확인 (네트워크 불필요)
uv run python main.py config

# 5. 테스트
uv sync --extra dev && uv run pytest
```

## Jenkins 실행 스크립트

3~5분 간격 주기 실행을 전제로 한다 (스냅샷 축적 + 청산 체크).

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=1000
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 전략 주요 파라미터 (기본값과 다르게 운용할 때만)
# export POLYBOT_MAX_POSITIONS=10        # 군집 손실 방지 권장
# export POLYBOT_HOLD_HOURS=120          # calendar exit (백테스트 Y=5일)
# export POLYBOT_LOOKBACK_DAYS=20        # 롤링 최저가 룩백

cd ./golden-nectarine
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

## CLI

| 명령 | 설명 |
|------|------|
| `python main.py run` | 트레이딩 사이클 1회 실행 (Jenkins용) |
| `python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 기록만, DB 분리) |
| `python main.py run --job <name>` | job별 DB 분리 (`data/<job>/`) |
| `python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL env보다 우선) |
| `python main.py status` | 포지션/통계 JSON 출력 |
| `python main.py config` | 병합된 설정 출력 (네트워크 불필요) |

## 환경변수 전체 표

우선순위: **env > config.yaml > 기본값**

| env | 기본값 | 의미 |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | (필수) | 지갑 private key |
| `POLYMARKET_FUNDER_ADDRESS` | (필수) | 지갑 주소 |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 10000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 비활성) |
| `POLYBOT_LOOKBACK_DAYS` | 20 | 롤링 최저가 룩백 (일) |
| `POLYBOT_EXCLUDE_RECENT_HOURS` | 24 | 최저가 산출 시 제외할 최근 구간 (시간) |
| `POLYBOT_HOLD_HOURS` | 120 | calendar exit 보유 시간 (주 청산 경로) |
| `POLYBOT_PROB_MIN` | 0.03 | 진입 YES 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.50 | 진입 YES 가격 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | 720 | 해결까지 최소 시간 (30일+ 장기 시장만) |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 임박 청산 기준 |
| `POLYBOT_TAKE_PROFIT` | 0.30 | 익절 안전판 (+30%, 목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.30 | 손절 안전판 (-30%) |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 = 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 168 | 재진입 쿨다운 (7일) |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 (20일 룩백의 생명선) |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 기본 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

## data/ 구조

```
data/<job>/
├── trades.db            # 실거래 DB (trades / market_snapshots / skipped_markets)
├── trades_sim.db        # 시뮬레이션 DB (--simulate 시 분리)
├── trades_YYYY-MM.csv   # 월별 거래 이력 (시그널 컬럼 포함, 청산 시 append)
└── logs/YYYYMMDD.log
```

`data/`는 git에 커밋하지 않는다 (.gitignore 등록).

## 시뮬레이션 → 실전 전환 절차

1. `--simulate --job sim-test`로 1~2주 운용, 진입 빈도·백필 동작 확인 (`lookback_days_at_buy` 분포).
2. `POLYBOT_BUY_AMOUNT`를 최소로 실전 시작 (`--simulate` 제거). `POLYBOT_MAX_POSITIONS` 상한 권장.
3. 4주+ / 30+ 거래 후 STRATEGY.md §6 기준으로 판단.
4. `status` 명령과 월별 CSV로 회고. EXPIRED 포지션은 수동 redeem 필요 (로그에 WARNING).

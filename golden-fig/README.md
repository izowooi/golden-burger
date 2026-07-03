# Golden Fig - Polymarket 자동 매매 봇

**Hope Crusher** 전략 기반 Polymarket 자동 매매 봇입니다. 롱샷(YES 5~25%) 시장에서 **항상 NO 토큰(75~95%)을 매수**해, 희망 보유자들이 늦게 놓는 시간 가치 소멸(theta)을 수확합니다.

전략 근거·정밀 명세·리스크는 [STRATEGY.md](STRATEGY.md)를 참조하세요.

## 개요

- **매수 조건**: YES 5~25% (→ NO 75~95% 매수) + 해결까지 24~240시간 + 사건 진행 배제 게이트(YES 24h 변화 ≤ +0.02, 6h 급등 < 0.05) + 스냅샷 윈도우 유효
- **매도 조건**: 손절 -10% → 익절 +6%(목표가 0.99 캡) → 해결 2시간 전 청산. 트레일링 스탑 없음
- **방향 내장**: 항상 NO 토큰 매수. `--yes-only` 플래그 없음
- **재진입**: 영구 금지 대신 쿨다운 24시간 (HOLDING 중이면 skip)

## Quickstart

```bash
cd golden-fig

# 1. 의존성 설치
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 편집: POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS 입력
# (https://polymarket.com/settings?tab=export-private-key)

# 3. API 연결 테스트
uv run python scripts/test_api_key.py

# 4. 시뮬레이션 실행 (실제 주문 없음, 가격 조회는 실제 API)
uv run python main.py run --simulate --job test

# 5. 설정/상태 확인
uv run python main.py config
uv run python main.py status --job test
```

## CLI

| 명령 | 설명 |
|------|------|
| `uv run python main.py run` | 트레이딩 사이클 1회 실행 (Jenkins용) |
| `uv run python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 로그만) |
| `uv run python main.py run --config <file> --job <name>` | 커스텀 config/job (job별 DB 분리) |
| `uv run python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL env보다 우선) |
| `uv run python main.py status` | 보유 포지션/통계 JSON 출력 |
| `uv run python main.py config` | 현재 설정 출력 |

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 전략 파라미터 예시 (기본값과 다르게 운영할 때만)
export POLYBOT_YES_MAX=0.25
export POLYBOT_TAKE_PROFIT=0.06
export POLYBOT_REENTRY_COOLDOWN_HOURS=24

cd ./golden-fig
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3~5분 주기 cron 트리거를 권장합니다 (스냅샷 축적이 시그널 품질을 좌우).

## 환경변수 전체 표

우선순위: **env > config.yaml > 코드 기본값**

### 필수 (API 인증)

| env | 설명 |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | Polymarket private key (0x 접두사 허용) |
| `POLYMARKET_FUNDER_ADDRESS` | 지갑 주소 |

### 공통

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 10000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 비활성) |
| `POLYBOT_TAKE_PROFIT` | 0.06 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.10 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 필터 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

### Hope Crusher 전략

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_YES_MIN` | 0.05 | YES 롱샷 밴드 하한 |
| `POLYBOT_YES_MAX` | 0.25 | YES 롱샷 밴드 상한 (NO 매수가 0.75~0.95) |
| `POLYBOT_YES_RISE_BLOCK_24H` | 0.02 | YES 24h 변화가 이 값 초과면 skip |
| `POLYBOT_YES_SPIKE_BLOCK_6H` | 0.05 | 최근 6h YES 급등이 이 값 이상이면 skip |
| `POLYBOT_RISE_LOOKBACK_HOURS` | 24 | 24h 게이트 lookback |
| `POLYBOT_SPIKE_LOOKBACK_HOURS` | 6 | 6h 게이트 lookback |
| `POLYBOT_ENTRY_HOURS_MIN` | 24 | 진입 최소 잔여 시간 |
| `POLYBOT_ENTRY_HOURS_MAX` | 240 | 진입 최대 잔여 시간 (10일) |
| `POLYBOT_EXIT_HOURS` | 2 | 해결 N시간 전 청산 |

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

`data/`는 git에 커밋하지 않습니다 (`.gitignore` 등록).

DB 테이블: `trades`(재진입 허용 - condition_id unique 아님, EXPIRED 상태 포함), `market_snapshots`(YES 가격 기준), `skipped_markets`(쿨다운 판정용 timestamp 포함).

`trades` 회고 로깅 컬럼 (A/B 포스트모템 계약, CSV export에도 포함): `strategy_name`(항상 "fig"), `mode`("live"/"sim"), `volume_24h_at_buy`(매수 시점 gamma volume24hr), `yes_price_at_exit`(청산 시점 1 - NO 매도가). 기존 DB 파일은 `init_database`의 best-effort ALTER로 자동 마이그레이션된다.

## 시뮬레이션 → 실전 전환 절차

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job fig-sim`을 Jenkins 주기 실행. 시뮬레이션도 CLOB 인증과 실제 가격 조회를 하므로 실키 `.env`가 필요합니다.
2. **결과 검토**: `data/fig-sim/trades_sim.db`에서 승률·exit_reason 분포 확인 (기준: STRATEGY.md §6).
3. **소액 실전**: `POLYBOT_BUY_AMOUNT`를 최소로 두고 `--simulate` 제거. NO 0.95 매수 시 최소 주문 5주 요건 때문에 약 $4.75 이상 필요.
4. **증액**: 4주 / 30건 이상에서 기준 통과 시 금액 상향.

## 테스트

```bash
uv sync --extra dev
uv run pytest
```

전략 시그널은 `src/polybot/strategy/signals.py`의 순수 함수로 분리되어 있어, `tests/test_signals.py`의 합성 스냅샷 케이스가 그대로 전략 검증입니다.

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요
- **테스트**: 실제 거래 전 반드시 시뮬레이션 모드로 테스트하세요
- **꼬리 리스크**: NO 매수는 이익 상한이 작고 손실 하한이 큽니다(최대 -85%). 사건이 실제로 일어나는 시장 하나가 익절 여러 건을 지울 수 있습니다. STRATEGY.md §5 필독
- **EXPIRED 포지션**: 해결된 시장을 청산하지 못한 채 24시간이 지나면 EXPIRED로 마감 처리되고 로그에 "수동 redeem 필요" 경고가 남습니다. Polymarket UI에서 직접 redeem 하세요
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요

## 라이선스

MIT License

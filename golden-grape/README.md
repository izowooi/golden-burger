# Golden Grape - Polymarket 자동 매매 봇

Cascade Rider(정보 폭포 편승) 전략 기반 Polymarket 자동 매매 봇입니다. 24시간 동안 **소폭(+4~10%p)이지만 일관된 드리프트 + 거래량 가속**이 확인된 시장에 편승해, 정보가 대중 전체에 확산되기 전 구간의 추가 이동을 수확합니다.

전략의 근거·정밀 명세·리스크는 [STRATEGY.md](STRATEGY.md) 참조.

## 개요

- **매수 조건**: 매수 토큰 가격 40~80% + 24h 드리프트 +4~10%p + 4h 버킷 6개 중 70% 이상 비음 변화 + 거래량 가속 1.2x + 해결까지 48h 이상
- **매도 조건**: 손절 -8% → 익절 +15%(목표가 0.99 캡) → 드리프트 소멸(6h 변화 <= 0, 3포인트·50% 시간 커버 필수) → 트레일링 스탑 -6% → 해결 24h 전
- **방향**: YES 상승 드리프트면 YES 매수, YES 하락이면 NO 매수 (숏 없음)
- **재진입**: 영구 차단 없음 — 청산/skip 후 24h 쿨다운 뒤 재진입 허용

## Quickstart

```bash
cd golden-grape

# 1. 의존성 설치
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 편집:
#   POLYMARKET_PRIVATE_KEY=0xYourPrivateKey
#   POLYMARKET_FUNDER_ADDRESS=0xYourWalletAddress
# (키 발급: https://polymarket.com/settings?tab=export-private-key)

# 3. API 연결 테스트 (선택)
uv run python scripts/test_api_key.py

# 4. 시뮬레이션 실행 (실제 주문 없음)
uv run python main.py run --simulate --job test
```

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 퇴역할 때만: export POLYBOT_LIFECYCLE_MODE=close_only
# 전략 파라미터 예시 (선택)
export POLYBOT_DRIFT_MIN=0.04
export POLYBOT_CONSISTENCY_MIN=0.70
export POLYBOT_VOL_ACCEL_MIN=1.2

cd ./golden-grape
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

3~5분 주기 cron 트리거를 권장합니다 (스냅샷 축적 주기 = 드리프트 판정 해상도). 5분 주기 Jenkins에서는 동시 빌드를 비활성화하고, 최초 실행 로그의 `Lifecycle` 값과 `config` 출력이 기대한 모드인지 확인하세요.

### Lifecycle 운영

- `active`(기본): 기존과 동일하게 스냅샷·청산·신규 매수를 모두 수행합니다.
- `close_only`: Phase 0 스냅샷과 Phase 1 청산, Phase 4 정리는 유지하고 스캔·신규 매수를 차단합니다.
- `archive_only`: 스냅샷과 정리만 유지하고 매수·매도 주문을 모두 차단합니다.

`close_only` 전환은 기존에 접수된 GTC BUY 주문을 취소하지 않습니다. 전환 직후 동일 계정으로 [전략 종료 플레이북](../docs/strategy-wind-down-playbook.md)의 dry-run을 확인한 뒤 GTC BUY 주문을 한 번 취소하세요. Cascade Rider에는 최대 보유 시간이 없어 자연 청산 완료 시점을 보장할 수 없습니다. 명시적인 grace 종료일과 수동 flatten/검토 기준을 정하고 wallet/CLOB/DB가 모두 비었는지 확인한 뒤 archive로 전환하세요.

## CLI

| 명령 | 설명 |
|------|------|
| `uv run python main.py run` | 트레이딩 사이클 1회 실행 (Jenkins용) |
| `uv run python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 로그만) |
| `uv run python main.py run --job <name>` | job별 DB 분리 (`data/<name>/`) |
| `uv run python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL보다 우선) |
| `uv run python main.py status` | 보유 포지션·통계 JSON 출력 |
| `uv run python main.py config` | 현재 설정 출력 |

## 환경변수 전체 표

우선순위: **env > config.yaml > 코드 기본값**

### 필수

| env var | 설명 |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | 지갑 private key (0x prefix 허용) |
| `POLYMARKET_FUNDER_ADDRESS` | Polymarket 지갑 주소 |

### 공통 (전 봇 동일 이름)

| env var | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_LIFECYCLE_MODE` | active | `active` / `close_only` / `archive_only` |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.15 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (h) |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 기본 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |

### Cascade Rider 전략

| env var | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_PROB_MIN` | 0.40 | 매수 토큰 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.80 | 매수 토큰 가격 상한 |
| `POLYBOT_DRIFT_LOOKBACK_HOURS` | 24 | 드리프트 판정 윈도우 (h) |
| `POLYBOT_DRIFT_MIN` | 0.04 | 드리프트 하한 |
| `POLYBOT_DRIFT_MAX` | 0.10 | 드리프트 상한 |
| `POLYBOT_BUCKET_HOURS` | 4 | 일관성 버킷 크기 (h) |
| `POLYBOT_CONSISTENCY_MIN` | 0.70 | 비음 버킷 비율 하한 |
| `POLYBOT_VOL_ACCEL_MIN` | 1.2 | 거래량 가속 배수 하한 |
| `POLYBOT_DEATH_WINDOW_HOURS` | 6 | 드리프트 소멸 판정 윈도우 (h) |
| `POLYBOT_DEATH_WINDOW_MIN_POINTS` | 3 | 소멸 판정 최소 스냅샷 수 |
| `POLYBOT_DEATH_WINDOW_MIN_COVERAGE` | 0.5 | 소멸 윈도우 최소 시간 커버리지 |
| `POLYBOT_ENTRY_HOURS_MIN` | 48 | 해결까지 최소 잔여시간 (h) |
| `POLYBOT_EXIT_HOURS` | 24 | 시간 청산 기준 (h) |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 트레일링 스탑 % |

## 사이클 구조

```
Gamma 전체 sweep 1회 (Phase 0과 2가 결과 공유)
Phase 0: 스캔 대상 시장 스냅샷 저장 (YES 가격 기준)
Phase 1: 보유 포지션 청산 체크 (해결 시장 EXPIRED 처리 포함)
Phase 2: Cascade Rider 스캔 → 후보 목록 (윈도우 invalid면 prices-history 백필)
Phase 3: 후보별 재진입 쿨다운 체크 → 매수 실행
Phase 4: 오래된 스냅샷 정리 (보존 7일)
```

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

`data/`는 git에 커밋하지 않습니다 (.gitignore 등록됨).

## 시뮬레이션 → 실전 전환 절차

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim-grape`를 Jenkins 주기 실행. 시뮬레이션에도 CLOB 가격 조회를 위해 실키(.env)가 필요합니다 (주문만 가짜).
2. **결과 검토**: `data/sim-grape/trades_sim.db`에서 신호 빈도·가상 손익·exit_reason 분포 확인.
3. **소액 실전**: `--simulate` 제거 + `POLYBOT_BUY_AMOUNT=5` 수준으로 시작.
4. **판단**: 4주 & 30+ 거래 후 승률/평균손익으로 증액·기각 결정 (STRATEGY.md §6 기준).

## 데이터 분석

```bash
sqlite3 data/{job_name}/trades.db
```

```sql
-- 진입/청산 사유별 손익 (status는 enum 이름으로 저장됨: HOLDING/COMPLETED/EXPIRED)
SELECT entry_reason, exit_reason, COUNT(*) cnt, ROUND(SUM(realized_pnl), 4) pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY entry_reason, exit_reason ORDER BY pnl DESC;

-- 드리프트 강도별 성과 (매수 시점 시그널 값이 컬럼으로 저장됨)
SELECT ROUND(drift_at_buy, 2) drift_bin, COUNT(*) cnt, ROUND(AVG(realized_pnl), 4) avg_pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY drift_bin ORDER BY drift_bin;

-- EXPIRED (수동 redeem 필요) 확인
SELECT id, question, buy_amount FROM trades WHERE status = 'EXPIRED';

-- 회고 로깅 컬럼 (A/B 포스트모템용): strategy_name('grape'), mode('live'/'sim'),
-- volume_24h_at_buy(매수 시점 gamma volume24hr), drift_at_exit(청산 시점 최근 6h 드리프트)
SELECT mode, exit_reason, COUNT(*) cnt,
       ROUND(AVG(drift_at_exit), 3) avg_drift_at_exit,
       ROUND(AVG(realized_pnl), 4) avg_pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY mode, exit_reason;
```

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요.
- **테스트**: 실거래 전 반드시 시뮬레이션 모드로 검증하세요.
- **소액 시작**: 처음에는 `POLYBOT_BUY_AMOUNT=5` 수준을 권장합니다.
- **EXPIRED 포지션**: 해결된 시장에 남은 포지션은 봇이 EXPIRED로 마감만 하고 redeem은 하지 않습니다. Polymarket UI에서 수동 redeem이 필요합니다.
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요.

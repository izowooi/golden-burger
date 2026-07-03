# Golden Date - Polymarket 자동 매매 봇

**Conviction Ladder** 전략 기반 Polymarket 자동 매매 봇입니다.
해결까지 남은 시간이 적을수록 높은 확신(가격)을 요구하는 3단 시간 사다리 확률 밴드로 favorite을 매수하고,
하락 추세 favorite은 모멘텀 게이트로 배제합니다. cherry(Resolution Momentum)의 5가지 확인된 허점을 수정한 고도화 버전입니다.

전략의 근거·정밀 명세·리스크는 [STRATEGY.md](STRATEGY.md)를 참조하세요.

## 개요

- **매수 조건**: favorite 가격이 시간 사다리 밴드 안 + 최근 6h 하락 추세 아님 + 유동성/거래량 통과
  - 6h < 잔여 <= 24h: 확률 [0.80, 0.95]
  - 24h < 잔여 <= 72h: 확률 [0.75, 0.92]
  - 72h < 잔여 <= 168h: 확률 [0.70, 0.88]
- **매도 조건**: 손절 -8% → 익절 +12%(목표가 0.99 캡) → 트레일링 스탑 -5% → 해결 2시간 전 청산
- **재진입**: 영구 one-shot 금지 대신 24h 쿨다운 후 재진입 허용
- **데이터**: 자체 스냅샷 축적 + CLOB `prices-history` 백필로 cold start 해결

## Quickstart

```bash
cd golden-date

# 1. 의존성 설치
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에 POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS 입력

# 3. API 연결 테스트
uv run python scripts/test_api_key.py

# 4. 시뮬레이션 실행 (실제 주문 없음, 가격 조회는 실 API라 실키 필요)
uv run python main.py run --simulate --job test

# 5. 설정/상태 확인
uv run python main.py config
uv run python main.py status --job test
```

## API 키 생성 방법

1. [Polymarket](https://polymarket.com) 로그인
2. [Settings > Export Private Key](https://polymarket.com/settings?tab=export-private-key) 이동
3. Private Key와 Wallet Address를 `.env`에 입력

```env
POLYMARKET_PRIVATE_KEY=0xYourPrivateKeyHere
POLYMARKET_FUNDER_ADDRESS=0xYourWalletAddress
```

## Jenkins 실행 스크립트

```bash
#!/bin/bash
export POLYBOT_BUY_AMOUNT=20
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 전략 주요 env 예시
export POLYBOT_MOMENTUM_MIN_CHANGE=-0.01
export POLYBOT_REENTRY_COOLDOWN_HOURS=24
export POLYBOT_TRAILING_STOP_PERCENT=0.05

cd ./golden-date
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

Jenkins job별로 DB/로그를 분리하려면 `--job <이름>`을 붙이세요. YES 토큰만 매수하려면 `--yes-only`를 추가합니다.

## CLI

| 명령 | 설명 |
|------|------|
| `uv run python main.py run` | 트레이딩 사이클 1회 실행 (Jenkins용, 루프 없음) |
| `uv run python main.py run --simulate` | 시뮬레이션 모드 (주문 없이 로그만, DB는 `trades_sim.db`) |
| `uv run python main.py run --job <name>` | job별 DB/로그 분리 (`data/<name>/`) |
| `uv run python main.py run --yes-only` | index 0(Yes/1위 후보) 토큰만 매수, No 포지션 제외 |
| `uv run python main.py run --verbose` | DEBUG 로그 (LOG_LEVEL보다 우선) |
| `uv run python main.py status` | 보유 포지션·통계 JSON 출력 |
| `uv run python main.py config` | 병합된 최종 설정 출력 |

## 환경변수 전체 표

우선순위: **env > config.yaml > 코드 기본값**

### 필수 (API 인증)

| 변수 | 설명 |
|------|------|
| `POLYMARKET_PRIVATE_KEY` | CLOB L1 인증 (0x 접두사 자동 제거) |
| `POLYMARKET_FUNDER_ADDRESS` | 주문 funder 지갑 주소 |

### 공통

| 변수 | 기본 | 의미 |
|------|------|------|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC (달러 단위) |
| `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 5000 | 최소 24h 거래량 $ (gamma volume24hr) |
| `POLYBOT_TAKE_PROFIT` | 0.12 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1: 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 필터 비활성) |
| `POLYBOT_YES_ONLY` | false | Yes(index 0) 토큰만 매수 (CLI `--yes-only`가 우선) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR, `--verbose`가 우선) |

### 시간 사다리

| 변수 | 기본 | 의미 |
|------|------|------|
| `POLYBOT_ENTRY_HOURS_MIN` | 6 | 잔여 시간이 이 값 이하이면 진입 금지 |
| `POLYBOT_LADDER_H1` | 24 | 밴드1 상한 시간 |
| `POLYBOT_BAND1_MIN` / `POLYBOT_BAND1_MAX` | 0.80 / 0.95 | 밴드1 확률 구간 |
| `POLYBOT_LADDER_H2` | 72 | 밴드2 상한 시간 |
| `POLYBOT_BAND2_MIN` / `POLYBOT_BAND2_MAX` | 0.75 / 0.92 | 밴드2 확률 구간 |
| `POLYBOT_LADDER_H3` | 168 | 밴드3 상한 시간 (진입 최대 잔여 시간) |
| `POLYBOT_BAND3_MIN` / `POLYBOT_BAND3_MAX` | 0.70 / 0.88 | 밴드3 확률 구간 |

### 모멘텀 게이트 / 청산

| 변수 | 기본 | 의미 |
|------|------|------|
| `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | 모멘텀 윈도우 길이 (시간) |
| `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.01 | favorite 가격 변화 하한 (이 값 미만 하락 시 진입 배제) |
| `POLYBOT_EXIT_HOURS` | 2 | 해결까지 이 시간 미만이면 청산 |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.05 | 최고점 대비 하락률 |

## data/ 구조

```
data/
└── {job_name}/
    ├── trades.db            # 실거래 SQLite DB (trades / market_snapshots / skipped_markets)
    ├── trades_sim.db        # 시뮬레이션 DB (실거래와 분리)
    ├── trades_YYYY-MM.csv   # 완료 거래 월별 CSV
    └── logs/YYYYMMDD.log    # 일자별 로그
```

`data/`는 git에 커밋하지 않습니다 (`.gitignore` 등록).

trades 테이블과 월별 CSV에는 A/B 포스트모템용 회고 컬럼이 함께 기록됩니다:
`strategy_name`("date"), `mode`("live"/"sim"), `volume_24h_at_buy`,
`ladder_band_at_buy`(사다리 밴드 1/2/3), `momentum_at_buy`.
기존 DB 파일은 봇 기동 시 best-effort `ALTER TABLE`로 자동 마이그레이션됩니다 (기존 행은 NULL).

## 시뮬레이션 → 실전 전환 절차

1. `uv run python main.py run --simulate --job sim` 으로 수일간 시뮬레이션 축적
2. `data/sim/trades_sim.db`와 월별 CSV로 진입/청산 사유별 성과 검토 (STRATEGY.md §6 판단 기준)
3. 소액 실전: `POLYBOT_BUY_AMOUNT=5` (기본값) 상태로 `--simulate` 없이 실행
4. 4주 / 30+ 거래 후 승률·평균 손익 확인, 문제 없으면 Jenkins env로 금액 상향
5. 주의: `config.yaml`의 `simulation_mode: false` 상태에서 `--simulate` 플래그 생략 시 실거래 발생

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요
- **테스트**: 실제 거래 전 반드시 시뮬레이션 모드로 테스트하세요
- **EXPIRED 포지션**: 해결 후 24h가 지나도 청산하지 못한 포지션은 `EXPIRED`로 마감 처리되며, Polymarket 웹에서 수동 redeem이 필요합니다 (로그 WARNING 확인)
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요

## 개발

```bash
uv sync --extra dev
uv run pytest
```

전략 로직은 `src/polybot/strategy/signals.py`의 순수 함수에 모여 있습니다. 전략을 바꾸려면 이 파일과 테스트만 수정하면 됩니다.

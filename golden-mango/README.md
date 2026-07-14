# Golden Mango - Polymarket 자동 매매 봇

**Patience Premium** 전략 기반 Polymarket 자동 매매 봇입니다.
"거의 확실한" 계약이 자본 잠김 회피(조급함) 때문에 만기까지 할인되어 거래되는
settlement discount를, 연환산 캐리 수익률 단일 수식으로 걸러 수확합니다.

```
연환산 캐리 y = ((1 - p) / p) × (8760 / hours_left),  진입 ⇔ y >= 2.0
```

전략의 근거·정밀 명세·리스크는 [STRATEGY.md](STRATEGY.md)를 참조하세요.

## 개요

- **매수 조건**: favorite 가격 p ∈ [0.85, 0.985] + 잔여 6h~336h + 연환산 캐리 y >= 2.0
  + 최근 6h 급락 아님(>= -0.02) + 유동성 $20,000+
- **매도 조건**: 손절 -6% → 0.99 도달 시 익절(수렴 보유, trailing 없음) → 해결 2시간 전 청산
- **재진입**: 영구 one-shot 금지 대신 24h 쿨다운 후 재진입 허용
- **데이터**: 자체 스냅샷 축적 + CLOB `prices-history` 백필로 cold start 해결
- **회고 로깅**: trades에 `strategy_name`/`mode`/`volume_24h_at_buy`/`carry_yield_at_buy`/
  `momentum_6h_at_buy`/`carry_yield_at_exit` 수치 컬럼 기록 (교차 봇 A/B 포스트모템용)

## Quickstart

```bash
cd golden-mango

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
export POLYBOT_BUY_AMOUNT=1000
export POLYMARKET_PRIVATE_KEY=<Jenkins credential>
export POLYMARKET_FUNDER_ADDRESS=<Jenkins credential>
export LOG_LEVEL=INFO
# 기본값은 active(기존 매매). 퇴역할 때만 아래 주석을 해제합니다.
# export POLYBOT_LIFECYCLE_MODE=close_only
# 전략 주요 env 예시
export POLYBOT_YIELD_MIN=2.0
export POLYBOT_MAX_POSITIONS=10
export POLYBOT_REENTRY_COOLDOWN_HOURS=24

cd ./golden-mango
/Users/jongwoopark/.local/bin/uv sync
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

Jenkins job별로 DB/로그를 분리하려면 `--job <이름>`을 붙이세요.
YES 토큰만 매수하려면 `--yes-only`를 추가합니다.
UMA 오라클 리스크 때문에 실전에서는 `POLYBOT_MAX_POSITIONS` 제한과 소액 사이징을 권장합니다
(STRATEGY.md §5).

### 전략 퇴역 (`close_only`)

`POLYBOT_LIFECYCLE_MODE=close_only`는 Phase 0 스냅샷, 기존 포지션의 정상
청산 판단, Phase 4 정리를 계속하면서 신규 후보 스캔과 BUY를 차단합니다. 전량을
즉시 매도하는 모드가 아닙니다. Mango에는 최대 보유 시간 제한이 없고 수렴 보유가
전략의 본질이므로, 손절·0.99 도달·만기 2시간 전 조건 중 하나가 충족될 때까지
포지션이 남을 수 있습니다. `archive_only`는 모든 주문 경로를 끄고 스냅샷과 정리만
수행하므로 포지션과 미체결 주문이 모두 정리된 뒤에만 사용합니다.

5분 Jenkins 주기는 그대로 사용할 수 있습니다. 기존과 같은 `--job`과 workspace를
유지하고 concurrent build를 비활성화해 이전 사이클과 겹치지 않게 하세요. 전환 전
이미 접수된 GTC BUY는 `close_only`가 자동 취소하지 않으므로, 동일한 계정 credential로
저장소 루트의 `tools/wind_down.py cancel --side BUY`를 dry-run한 뒤 `--yes`로 한 번
취소해야 합니다. 전환 직후 `main.py config`의 `Lifecycle Mode: close_only`와 실행
로그의 `Phase 2/3 건너뜀`을 확인하세요. `POLYBOT_BUY_AMOUNT=0`은 설정 검증에서
거부되므로 신규 진입 차단 용도로 사용하지 않습니다. `--yes-only` 설정은
`close_only` 중에는 신규 진입이 없어 영향을 주지 않습니다.
전체 전환·잔여 포지션 절차는
[전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따릅니다.

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
| `POLYBOT_LIFECYCLE_MODE` | active | `active` / `close_only` / `archive_only` |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC (달러 단위) |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 필터 비활성) |
| `POLYBOT_TAKE_PROFIT` | 9.99 | 사실상 미사용 — 익절 목표가는 0.99 캡으로 고정 |
| `POLYBOT_STOP_LOSS` | -0.06 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1: 무제한, 실전은 제한 권장) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 필터 비활성) |
| `POLYBOT_YES_ONLY` | false | Yes(index 0) 토큰만 매수 (CLI `--yes-only`가 우선) |
| `LOG_LEVEL` | INFO | 로그 레벨 (DEBUG/INFO/WARNING/ERROR, `--verbose`가 우선) |

### 캐리 진입 (핵심 수식: y = ((1-p)/p) × (8760/h))

| 변수 | 기본 | 의미 |
|------|------|------|
| `POLYBOT_YIELD_MIN` | 2.0 | 연환산 캐리 허들 (2.0 = 연 200%) |
| `POLYBOT_PROB_MIN` | 0.85 | favorite 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.985 | favorite 가격 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | 6 | 잔여 시간이 이 값 이하이면 진입 금지 |
| `POLYBOT_ENTRY_HOURS_MAX` | 336 | 잔여 시간이 이 값 초과이면 진입 금지 (14일) |

### 모멘텀 가드 / 청산

| 변수 | 기본 | 의미 |
|------|------|------|
| `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | 모멘텀 가드 윈도우 길이 (시간) |
| `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.02 | favorite 가격 변화 하한 (미만 급락 시 진입 배제) |
| `POLYBOT_EXIT_HOURS` | 2 | 해결까지 이 시간 미만이면 청산 |

## data/ 구조

```
data/
└── {job_name}/
    ├── trades.db            # 실거래 SQLite DB (trades / market_snapshots / skipped_markets)
    ├── trades_sim.db        # 시뮬레이션 DB (실거래와 분리)
    ├── trades_YYYY-MM.csv   # 완료 거래 월별 CSV (시그널 컬럼 포함)
    └── logs/YYYYMMDD.log    # 일자별 로그
```

`data/`는 git에 커밋하지 않습니다 (`.gitignore` 등록).

## 시뮬레이션 → 실전 전환 절차

1. `uv run python main.py run --simulate --job sim` 으로 수일간 시뮬레이션 축적
2. `data/sim/trades_sim.db`와 월별 CSV로 진입/청산 사유별 성과 검토 (STRATEGY.md §6 판단 기준)
3. 소액 실전: `POLYBOT_BUY_AMOUNT=5` (기본값) + `POLYBOT_MAX_POSITIONS=10` 상태로
   `--simulate` 없이 실행
4. 4주 / 30+ 거래 후 승률·평균 손익·`carry_yield_at_buy` 구간별 성과 확인,
   문제 없으면 Jenkins env로 금액 상향
5. 주의: `config.yaml`의 `simulation_mode: false` 상태에서 `--simulate` 플래그 생략 시 실거래 발생

## 주의사항

- **보안**: `.env` 파일은 절대 git에 커밋하지 마세요
- **테스트**: 실제 거래 전 반드시 시뮬레이션 모드로 테스트하세요
- **오라클 리스크**: favorite이 해결 직전 뒤집히는 UMA 분쟁 사례가 존재합니다.
  전액 손실 1회가 수십 회의 캐리 수익을 지울 수 있으므로 포지션 수 제한 + 소액 사이징이
  이 전략의 생존 조건입니다 (STRATEGY.md §5)
- **EXPIRED 포지션**: 해결 후 24h가 지나도 청산하지 못한 포지션은 `EXPIRED`로 마감 처리되며,
  Polymarket 웹에서 수동 redeem이 필요합니다 (로그 WARNING 확인)
- **리스크**: 자동 매매는 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요

## 개발

```bash
uv sync --extra dev
uv run pytest
```

전략 로직은 `src/polybot/strategy/signals.py`의 순수 함수에 모여 있습니다.
전략을 바꾸려면 이 파일과 테스트만 수정하면 됩니다.

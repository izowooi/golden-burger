# Golden Papaya — Final Five

표준 이진 Polymarket의 YES가 archive cadence에서 처음 관측된 95% 상향 돌파 뒤 해결까지
수렴하는지를 검증하는 소액 자동매매 봇이다.

```text
진입 = strict standard binary YES
     + persisted_previous_yes < 0.95 <= persisted_current_yes <= 0.97
     + 0m < consecutive_snapshot_gap <= 30m
     + earlier_observation_at_or_above_0.95 == false
     + 0h < hours_left <= 72h
청산 = unresolved 상태에서 current_yes <= 0.90
그 외 = resolution과 redeem 증거를 분리해 기록
```

기본값은 주문 $5, 최대 20포지션, event당 1포지션, 유동성 $1,000 이상,
24h 거래량 하한 $0이다. 사전 해결 익절·trailing stop·time exit은 없다. 상세 가설과
기각 기준은 [STRATEGY.md](STRATEGY.md)를 참조한다.

## Quickstart

```bash
cd golden-papaya
uv sync --frozen --extra dev
cp .env.example .env
# .env에 private key/funder만 로컬로 입력

uv run python scripts/test_api_key.py
uv run polybot config
uv run pytest
uv run polybot run --simulate --job sim
```

시뮬레이션은 주문을 만들지 않지만 CLOB 가격·주문 조회를 위해 실 API 인증이 필요하다.
실거래는 별도 계정과 job/SQLite에서 충분한 simulation evidence를 확보한 뒤 명시적으로
simulation을 해제한다.

## 시장·주문 계약

- outcomes가 정확히 `Yes`와 `No`이고 token ID가 정확히 2개인 표준(non-negRisk) 시장만
  대상으로 한다.
- YES-only는 전략 불변식이다. `--yes-only` 없이 실행해도 NO를 선택하지 않는다.
- 현재 sweep에서 저장·commit된 양의 snapshot ID와 그 직전 persisted snapshot이 모두 있어야
  한다. 두 관측의 간격은 기본 `0 < gap <= 30분`이어야 하며, 직전 값이 없거나 stale하면
  fail closed한다. sweep 사이 실제 시장의 교차 시점은 interval-censored다.
- 보존된 선행 snapshot 중 하나라도 이미 0.95 이상이면 이후 dip/re-cross를 새 first crossing으로
  해석하지 않는다. 최초 0.95 관측은 snapshot commit 시 one-shot을 소진한다. 따라서 그 시점에
  유동성·volume·잔여시간·event cap·fresh ask 같은 후속 gate에서 주문이 거부돼도 재시도하지
  않는다.
- 교차 후보가 생기면 fresh best ask를 다시 조회한다. ask가 없거나 0.97을 넘으면 주문하지
  않는다. 로컬 `trades` 행의 접수 가격·수량·상태는 주문 intent/lifecycle 기록이며, 실제
  노출과 체결 평가는 exact order ID로 연결된 confirmed fill을 사용한다.
- 같은 event의 상관 파생 시장은 최대 1개만 보유한다.
- 현재 YES signal/midpoint가 0.90 이하가 되면 fresh best bid를 확인해 SELL을 시도하지만,
  얇은 호가에서는 미체결·부분체결·큰 슬리피지가 발생할 수 있다. DB 상태만 보고 손절
  완료로 간주하지 않는다.
- 해결 결과(YES=1, NO=0, 드문 ambiguous=0.5 payout)와 token redeem은 주문 fill이 아니다.
  live에서는 confirmed BUY fill이 없는 intent를 resolution 포지션으로 정산하지 않는다.
  confirmed SELL fill 또는 별도 resolution/redeem evidence 없이는 actual P&L을 만들지 않는다.

## Jenkins 예시

Private key와 funder는 Jenkins Credentials Binding으로 주입한다. 아래에는 실값을 넣지 않는다.

```bash
#!/bin/bash
set +x

# POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS는 Credentials Binding에서 주입
export POLYMARKET_SIGNATURE_TYPE=3  # 이 계정이 POLY_1271일 때만 3; 구형 프록시는 1
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_MIN_LIQUIDITY=1000
export POLYBOT_MIN_VOLUME_24H=0
export POLYBOT_MAX_SNAPSHOT_GAP_MINUTES=30
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=72
export POLYBOT_MAX_POSITIONS=20
export POLYBOT_MAX_EVENT_POSITIONS=1
export LOG_LEVEL=INFO

cd ./golden-papaya
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot run --job papaya
```

이 예시는 저장소의 `simulation_mode: true`를 그대로 사용하므로 실제 주문을 만들지 않는다.
live 전환은 운영 승인 후 `config.yaml`을 명시적으로 `false`로 바꾸고 새 config hash cohort로
배포하는 별도 변경이다.

Jenkins concurrent build는 끄고, simulation/live 및 다른 전략과 `--job`과 DB를 공유하지
않는다. signature type은 계정 종류에 맞춰야 하며 신규 계정이라고 무조건 3인 것은 아니다.
`scripts/test_api_key.py`로 같은 Jenkins credential 조합을 먼저 확인한다.

## Lifecycle

| 모드 | 스냅샷/archive | 기존 포지션 | 신규 BUY |
|---|---:|---:|---:|
| `active` | O | 손절·resolution 대사 | O |
| `close_only` | O | 손절·resolution 대사 | X |
| `archive_only` | O | 주문 경로 X | X |

`close_only`는 즉시 청산이 아니다. Final Five에는 time exit이 없으므로 0.90 손절이
발생하지 않은 포지션은 resolution/redeem까지 남을 수 있다. open order, wallet position,
execution ledger, redeem 대상이 모두 대사된 뒤에만 `archive_only`로 전환한다. papaya는
저유동성 후보의 반사실 검증을 위해 자체 archive를 최소 60일 유지한다.
전체 절차는 [전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따른다.

## 환경변수

우선순위는 `env > config.yaml > 코드 기본값`이다. 전략 경계는 운영 중 임의 변경하지 않고
새 `config_hash` cohort로만 실험한다.

| 변수 | 기본 | 설명 |
|---|---:|---|
| `POLYMARKET_PRIVATE_KEY` | 필수 | CLOB 서명 key; 파일/로그/commit 금지 |
| `POLYMARKET_FUNDER_ADDRESS` | 필수 | 실제 funder; 보고서/commit 금지 |
| `POLYMARKET_SIGNATURE_TYPE` | `1` | 계정에 맞는 `1` 또는 `3` |
| `POLYBOT_LIFECYCLE_MODE` | `active` | `active` / `close_only` / `archive_only` |
| `POLYBOT_BUY_AMOUNT` | `5` | 주문 금액 USDC |
| `POLYBOT_MIN_LIQUIDITY` | `1000` | 최소 유동성 USD |
| `POLYBOT_MIN_VOLUME_24H` | `0` | 최소 24h 거래량 USD; 0은 비활성 |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | `24` | 같은 condition 재진입 쿨다운 시간 |
| `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES` | `30` | 연속 persisted snapshot 사이 허용 최대 간격, inclusive |
| `POLYBOT_MIN_ORDER_SIZE` | `5` | venue 최소 주문 shares |
| `POLYBOT_MIN_ORDER_BUFFER_SHARES` | `0.10` | 가격 반올림용 최소 shares 여유 |
| `POLYBOT_YES_ONLY` | `true` | 전략 불변식; false는 설정 검증 실패 |
| `POLYBOT_EXCLUDED_CATEGORIES` | 빈 값 | comma 구분 제외 category |
| `POLYBOT_ENTRY_PROB_MIN` | `0.95` | 진입가 하한/상향 교차 임계값 |
| `POLYBOT_ENTRY_PROB_MAX` | `0.97` | 진입가 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | `0` | 진입 잔여시간 설정 하한. 기본 0일 때 실제 조건은 `hours_left > 0` |
| `POLYBOT_ENTRY_HOURS_MAX` | `72` | 진입 잔여시간 상한, inclusive |
| `POLYBOT_STOP_PRICE` | `0.90` | 절대 YES 손절 가격 |
| `POLYBOT_MAX_POSITIONS` | `20` | 동시 포지션 상한 |
| `POLYBOT_MAX_EVENT_POSITIONS` | `1` | event별 포지션 상한 |
| `POLYBOT_ARCHIVE_PROB_MIN` | `0.80` | 자체 archive YES 하한 |
| `POLYBOT_ARCHIVE_HOURS_MAX` | `168` | archive 잔여시간 상한 |
| `POLYBOT_SNAPSHOT_RETENTION_DAYS` | `60` | archive 보존일 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |

환경변수 이름은 `uv run polybot config` 출력과 `.env.example`을 기준으로 한다.

## 데이터와 회고

```text
data/{job}/
├── trades.db
├── trades_sim.db
├── trades_YYYY-MM.csv
└── logs/YYYYMMDD.log
```

월간 회고 전 다음 strict audit를 통과해야 한다.

```bash
uv run --project ../polybot-observability polybot-retro audit \
  --db data/papaya/trades.db --days 30 --output-dir "$HOME/polybot-retro/papaya" --strict
```

진입 성공률이나 DB의 `realized_pnl`만으로 전략을 평가하지 않는다. confirmed fill size/price,
fee와 liquidity role, event-cluster 유효 표본, archive coverage, resolution 결과 및 실제 redeem
evidence를 함께 검증한다. 절차와 sweep grid는
[golden-papaya 회고 문서](../docs/retro/golden-papaya.md)를 따른다.

### 재현 가능한 월간 offline replay

`scripts/backtest.py`는 네트워크와 운영 DB를 열지 않고 immutable CSV만 읽는다. CSV에는
가격·호가·유동성 외에 `outcomes`, `token_ids`, `yes_token_id`, `neg_risk`가 반드시 있어야
하며, 각각 strict `Yes/No`, 서로 다른 두 token, YES token identity, 명시적 `false`를
증명해야 한다. `outcomes`와 `token_ids`는 JSON array 문자열이다.

```bash
uv run python scripts/backtest.py /absolute/path/papaya-research.csv \
  --output-dir "$HOME/polybot-retro/papaya-2026-08" \
  --review-start 2026-08-01 \
  --review-end 2026-08-31
```

replay는 30분 이내의 연속 snapshot에서 생긴 최초 crossing만 한 번 소비하고, 후속 filter에
탈락해도 재-crossing을 새 최초 crossing으로 바꾸지 않는다. entry lower/upper, stop,
liquidity, volume24h, hours max의 1,296개 조합을 실행하며 동시 20포지션·event당 1포지션을
시간축으로 적용한다. 산출물은 midpoint 반사실과 observed bid/ask 가상 체결을 분리한다.
confirmed-fill 열은 exact execution ledger를 별도 join하기 전까지 `null`이며, 가상 체결을
actual 성과로 승격하지 않는다. `manifest.json`은 입력·산출물 SHA-256, UTC review window,
가정과 한계를 함께 고정한다.

## 주의

0.95 매수의 만기 총수익률은 비용 전 약 5.26%뿐이다. 0으로 끝나는 1건은 약 19건의
정상 승리를 지울 수 있고, spread·fee·미체결은 그보다 더 불리하게 만든다. 낮은 유동성
기본값은 가설을 넓게 관측하기 위한 값이지 안전 보장이 아니다. 소액과 event cap을 유지하고,
strict evidence 없이 금액을 키우지 않는다.

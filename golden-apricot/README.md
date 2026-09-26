# Golden Apricot

> **현행 A/B — 2026-09-26:** MLB A=Eco: first complete pair + [90,92] minutes. B=Fruit: first complete pair + [85,87] minutes. Sole treatment is entry tick. Both midpoint favorite, baseline exact ask5 .90-.999, target $15/$10/$5 full FOK ladder, full-holding bid .90+ and positive net proceeds after all BUY/SELL fees; otherwise exact resolution. No stop/time exit. Existing DB/runtime names retained; the tick90 substring in Fruit runtime is historical, not its resolved tick.
> 아래 과거 epoch의 고정값보다 이 현행 계약과 resolved config가 우선한다. 후보의 과거 재생은 미래 수익 보장이 아니다.


MLB 첫 공통 HOME/AWAY 틱 후 `[90,92]`분에 baseline `$5` ask VWAP이 `.90-.999`인
midpoint favorite를 목표 `$15`으로 매수하는 두 계좌 replication cohort다. fresh depth에서
`$15/$10/$5` 중 전량 체결 가능한 최대 금액 한 건만 FOK로 제출한다. 2026-09-15/16 두 consecutive MLB
slate의 `$10` forward 손실 뒤 목표액을 잠시 `$5`로 감액했고, 종료 210경기의
`$10` full-depth 재생에서 모든 다섯 시간 분할이 양수인 보수적 후보로 진입·청산값을 교정했다.
이전 Tick50 cohort와 기존 보유의 진입 시점 파라미터는 소급 변경하지 않는다.

| Jenkins | Runtime | Exit |
|---|---|---|
| polybot-eco | apricot-live-eco-mlb-tick90-tp95-v2 | first net-positive full bid at/above 0.90, else resolution |
| polybot-fruit | apricot-live-fruit-mlb-tick90-tp95-v2 | first net-positive full bid at/above 0.90, else resolution |

```bash
uv sync --frozen --extra dev
uv run pytest tests
uv run polybot config --live --job apricot-live-eco-mlb-tick90-tp95-v2
```

1분 live cycle은 compact SQLite maintenance와 60일 retention scan을 실행하지 않는다. DB
compaction·retention은 경기 진입창과 겹치지 않는 별도 maintenance 절차에서 수행한다. cycle
안의 maintenance는 DB가 커졌을 때 Jenkins build를 수십 분 점유해 `[90,92]`분 진입창을
놓치게 하므로 금지한다.
이미 배포된 live DB는 매 cycle마다 전체 `create_all`/additive schema upgrade도 반복하지 않는다.
새 DB의 최초 실행과 별도 deployment preflight에서만 schema를 생성·교정한다.
Execution ledger 역시 배포된 schema를 read-only version check로 열고, 매 cycle DDL과 90일
legacy-order bootstrap을 반복하지 않는다. Confirmed 경제손실 한도는 experiment capital과
분리된 절대값 `$300`이다.

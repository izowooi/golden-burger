# Golden Apricot

MLB 첫 공통 HOME/AWAY 틱 후 50분 favorite를 목표 `$5`로 매수하는 live A/B다.
2026-09-13 재교정에서 `$10` scale cohort의 tail risk가 확인되어 두 MLB runtime만 `$5`로
되돌렸으며, 다른 종목 runtime에는 이 값을 상속하지 않는다.

| Jenkins | Runtime | Exit |
|---|---|---|
| polybot-eco | apricot-live-eco-mlb-tick50-hold-v1 | bid VWAP 0.98, else resolution |
| polybot-fruit | apricot-live-fruit-mlb-tick50-tp99-v1 | bid VWAP 0.99, else resolution |

```bash
uv sync --frozen --extra dev
uv run pytest tests
uv run polybot config --live --job apricot-live-eco-mlb-tick50-hold-v1
```

1분 live cycle은 compact SQLite maintenance와 60일 retention scan을 실행하지 않는다. DB
compaction·retention은 경기 진입창과 겹치지 않는 별도 maintenance 절차에서 수행한다. cycle
안의 maintenance는 DB가 커졌을 때 Jenkins build를 수십 분 점유해 `[50,52]`분 진입창을
놓치게 하므로 금지한다.
이미 배포된 live DB는 매 cycle마다 전체 `create_all`/additive schema upgrade도 반복하지 않는다.
새 DB의 최초 실행과 별도 deployment preflight에서만 schema를 생성·교정한다.
Execution ledger 역시 배포된 schema를 read-only version check로 열고, 매 cycle DDL과 90일
legacy-order bootstrap을 반복하지 않는다. Confirmed 경제손실 한도는 experiment capital과
분리된 절대값 `$300`이다.

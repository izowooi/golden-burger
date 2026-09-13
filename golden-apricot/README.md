# Golden Apricot

MLB 첫 공통 HOME/AWAY 틱 후 50분 favorite를 목표 `$10`으로 매수하는 live A/B다.
이 증액은 아래 두 MLB runtime에만 고정되며 이후 다른 종목 runtime에는 상속되지 않는다.

| Jenkins | Runtime | Exit |
|---|---|---|
| polybot-eco | apricot-live-eco-mlb-tick50-hold-v1 | bid VWAP 0.98, else resolution |
| polybot-fruit | apricot-live-fruit-mlb-tick50-tp99-v1 | bid VWAP 0.99, else resolution |

```bash
uv sync --frozen --extra dev
uv run pytest tests
uv run polybot config --live --job apricot-live-eco-mlb-tick50-hold-v1
```

1분 live cycle은 compact SQLite maintenance를 실행하지 않는다. DB compaction은 경기 진입창과
겹치지 않는 별도 maintenance 절차에서 수행한다. cycle 시작 maintenance는 DB가 커졌을 때
Jenkins build를 수십 분 점유해 `[50,52]`분 진입창을 놓치게 하므로 금지한다.
이미 배포된 live DB는 매 cycle마다 전체 `create_all`/additive schema upgrade도 반복하지 않는다.
새 DB의 최초 실행과 별도 deployment preflight에서만 schema를 생성·교정한다.

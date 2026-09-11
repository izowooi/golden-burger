# Golden Apricot

MLB 첫 공통 HOME/AWAY 틱 후 50분 favorite를 목표 `$10`으로 매수하는 live A/B다.
이 증액은 아래 두 MLB runtime에만 고정되며 이후 다른 종목 runtime에는 상속되지 않는다.

| Jenkins | Runtime | Exit |
|---|---|---|
| polybot-eco | apricot-live-eco-mlb-tick50-hold-v1 | resolution hold |
| polybot-fruit | apricot-live-fruit-mlb-tick50-tp99-v1 | bid VWAP 0.99, else resolution |

```bash
uv sync --frozen --extra dev
uv run pytest tests
uv run polybot config --live --job apricot-live-eco-mlb-tick50-hold-v1
```

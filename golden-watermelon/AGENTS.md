# L3 AGENTS.md — Golden Watermelon

상위 `../AGENTS.md`를 따른다. 이 프로젝트는 accountless displayed-book research collector다.

## Active contract

- Data/schema/universe/classifier:
  `watermelon-five-major-sports-inplay-match-winner-v6` /
  `golden-watermelon-v4b-schema-v1` /
  `watermelon-soccer-mlb-nba-nfl-nhl-2026-09-v4b` /
  `watermelon-major-sports-identity-v2`.
- White `watermelon-white-1m-v4b`: `FAST_1M`, 1분.
- `watermelon-grey-5m-v4b`: `CONTROL_5M`, 5분 예약 runtime. 현재 `polybot-grey` Jenkins는
  Golden Peach collector이므로 덮어쓰지 않는다.
- Entry `[2026-09-03T12:00:00Z,2026-10-03T12:00:00Z)`, follow-up
  `2026-10-10T12:00:00Z`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Active preregistration:
  `research/frozen-2026-09-03-five-major-sports-v4b/PREREGISTRATION.md`.

## 불변 조건

- Soccer는 EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL exact identity와 정규시간
  HOME/DRAW/AWAY YES를 수집한다.
- MLB/NBA/NFL/NHL은 exact major-league root/season, 두 팀, direct top-level whole-game
  moneyline을 수집한다. World Series, NBA Finals, Super Bowl, Stanley Cup Final은 exact
  identity일 때 포함한다.
- e-sports, MiLB/G League/AHL/ECHL/NCAA, child/period/quarter/inning/spread/total/prop/future/
  advancement는 제외한다.
- 다섯 family를 numeric Gamma tag `100350/100381/745/450/899`로 각각 독립
  cursor-complete하게 읽는다.
  research collector에는 volume/liquidity selection gate를 넣지 않는다.
- entry grid `0.95/0.96/0.97/0.98/0.99`, stop grid
  `0.95/0.93/0.90/0.85/0.80/0.70`, notional ladder `$5..$1000`을 유지한다.
- Soccer source minute `75/80/85`만 late-entry replay에 사용한다. MLB/NBA/NFL/NHL clock은 raw evidence로
  보존하되 soccer minute strata와 합치지 않는다.
- accepted Soccer event는 distinct HOME/DRAW/AWAY condition/token 3개, MLB/NBA/NFL/NHL event는 한
  condition의 HOME/AWAY token 2개가 정확히 있어야 한다. gap은 HIGH다.
- `--live`, credential, signer, order SDK를 HTTP/DB 전에 거절하고 lifecycle은 `archive_only`다.

## Evidence와 저장소

Gamma request/raw page/family cursor, event/market/outcome identity, full CLOB levels, source clock,
signal/episode/path/stop/resolution, config/source/run/storage/DB check를 append-only로 보존한다.
displayed book은 actual fill이나 realized P&L이 아니다. family·threshold·stop·notional rung을 독립
거래처럼 합산하지 않는다.

v4a 이하 DB는 immutable archive다. v4b는 CREATE-only migration과 새 runtime DB를 사용한다.
기존 DB에 `ALTER TABLE`, migration/import/copy/merge/backfill/delete/clean을 하지 않는다.

## 검증과 배포

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

코드 변경 시 White timer를 먼저 끈다. timer 없는 수동 build에서 family별 cursor, exact
identity, market structure, CLOB book, DB/storage를 확인한 뒤 `* * * * *`와 `H/5 * * * *`를
복원한다. 자연 실행 2회와 daily-rsync verified DB를 확인한다.

24시간에는 collection health만 본다. follow-up과 사전 표본 gate 전 수익성, best family,
threshold/stop, late-entry minute, live notional이나 scale-up을 주장하지 않는다.

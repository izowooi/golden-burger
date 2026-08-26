# L3 AGENTS.md — Golden Watermelon

상위 `../AGENTS.md`를 따른다. 이 프로젝트는 accountless displayed-book research collector다.

## Active contract

- Data/schema/universe/classifier:
  `soccer-inplay-elite-competition-match-winner-v3` /
  `golden-watermelon-v3a-schema-v1` /
  `soccer-elite-leagues-uefa-2026-08-v3c` /
  `soccer-elite-competition-identity-v3`.
- White `watermelon-white-1m-v3c`: `FAST_1M`, 1분.
- Grey `watermelon-grey-5m-v3c`: `CONTROL_5M`, 5분.
- Entry `[2026-08-26T18:30:00Z,2026-09-02T18:30:00Z)`, follow-up
  `2026-09-09T18:30:00Z`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Active preregistration:
  `research/frozen-2026-08-26-uefa-clock-scale-v3c/PREREGISTRATION.md`.

## 불변 조건

- EPL/epl, Bundesliga/bun, Ligue 1/fl1, LaLiga/lal, MLS/mls, Serie A/sea의 exact sport
  id/name/primaryTagId/series/team identity만 허용한다.
- UEFA Champions League/ucl와 UEFA Europa League/uel는 exact competition tag, single
  series, slug prefix, UEFA resolution host와 two-team relation으로 분류한다. team domestic
  league equality는 적용하지 않는다.
- e-sports, 허용되지 않은 cup/league, child market, advancement, extra time, penalty market은
  제외한다.
- top-level regular-time moneyline의 HOME/DRAW/AWAY YES만 허용한다.
- volume/liquidity gate를 추가하지 않는다. exact full book depth 자체를 측정한다.
- entry `0.95/0.96/0.97/0.98/0.99`, stop
  `0.95/0.93/0.90/0.85/0.80/0.70`, primary `$5`를 유지한다.
- Gamma numeric `gameId`와 production WSS `gameId`를 exact join한다. 문서형 `slug`는
  fallback일 뿐이다. source `period/elapsed/clock`만 timing evidence로 쓰며 kickoff time으로
  match minute를 추정하지 않는다. late replay grid는 `75/80/85`다.
- notional replay grid는 `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500`다.
- `--live`, credential, signer, order SDK를 HTTP/DB 전에 거절하고 lifecycle은 `archive_only`다.

## Evidence와 저장소

Gamma requests/raw pages/cursor, `event_observations`, market/outcome, CLOB full levels,
`SPORTS_CLOCK_UPDATE`, signal/episode/path/stop/resolution, config/source/run/storage/DB check를
append-only로 보존한다. WSS failure와 coverage gap은 숨기지 않고 HIGH evidence issue로 남긴다.
displayed book은 actual fill이나 realized P&L이 아니다.

새 DB는 CREATE-only migration으로 만든다. 기존 DB는 writable open 전에 application ID, user
version, schema/universe/classifier/mapping/migration/schema hash를 exact preflight한다. runtime
`ALTER TABLE`, migration/import/copy/merge/backfill/delete/clean을 금지한다.

`watermelon-white-1m-v3b`, `watermelon-grey-5m-v3b`와 data contract
`soccer-inplay-major-league-match-winner-v2`, v1은 immutable archive다. active v3c에 합치지
않는다.

## 검증과 배포

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

코드 변경 시 White/Grey timer를 먼저 끈다. unit/contract test와 timer 없는 수동 build에서
Gamma cursor, exact identity, WSS clock coverage, DB/storage를 확인한 뒤에만 `* * * * *`와
`H/5 * * * *`를 복원한다. 각 자연 실행 2회와 daily-rsync verified DB를 확인한다.

24시간에는 collection health만 본다. follow-up과 사전 표본 gate 전 수익성, best threshold,
late-entry minute, live notional, scale-up을 주장하지 않는다. 8개 competition 중 하나라도
evaluable resolution이 없으면 macro estimate/CI는 `null`이다.

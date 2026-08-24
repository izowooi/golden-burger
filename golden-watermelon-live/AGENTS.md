# L4 AGENTS.md — golden-watermelon-live

상위 `/Users/izowooi/git/t1/AGENTS.md`를 따른다. 이 문서는 Cat/Dog 최소금액 live A/B의
프로젝트 전용 안전 계약이다.

## 목적

`golden-watermelon` White/Grey accountless evidence에서 파생한 in-play soccer
home/draw/away 전략을 기존 `polybot-cat`과 `polybot-dog` wallet에서 exact `$5`로
prospective 검증한다. collector 프로젝트와 DB는 수정·병합하지 않는다.

## 기술과 주요 파일

- Python 3.11+, uv, SQLAlchemy/SQLite, `py-clob-client-v2`
- entrypoint: `src/polybot/main.py`의 `polybot`
- frozen config: `config.yaml`, `src/polybot/config.py`
- universe identity: `src/polybot/league_classifier.py`,
  `src/polybot/api/gamma_client.py`, `src/polybot/strategy/filters.py`
- execution: `src/polybot/api/clob_client.py`, `src/polybot/strategy/trader.py`
- protocol: `STRATEGY.md`,
  `research/frozen-2026-08-24/PREREGISTRATION.md`
- Jenkins runbook: `OPERATIONS.md`

## 불변 조건

- Cat `[0.98,0.999]`, Dog `[0.99,0.999]`; threshold 외 처치 차이 금지
- EPL, Bundesliga, Ligue 1, LaLiga, MLS의 frozen numeric identity만 허용
- top-level whole-match moneyline의 HOME/DRAW/AWAY YES token만 허용
- 경기 시작 후 `[0h,4h]`, explicit `live=true`, `ended=false`
- exact `$5` full-depth ask walk와 FOK BUY
- best-bid `<=0.70` trigger, 전체 보유 shares의 full-depth bid walk와 FOK SELL
- full bid depth가 없으면 부분 수량으로 줄여 팔지 않음
- 주문/account 최대 `$5/20`, event 1, cycle 신규 20, 720h 재진입 제한
- bot DB가 만든 trade만 관리. wallet 수동 position의 조회·편입·청산 금지
- accepted order는 fill이 아님. exact terminal fill/fee 대사 전 상태 확정 금지
- entry `[2026-08-24T13:00:00Z,2026-08-31T13:00:00Z)`,
  follow-up `2026-09-07T13:00:00Z`
- cohort는 `config_hash × strategy_source_digest × mode × job_name`;
  Git commit은 provenance만 담당

## 실행과 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job watermelon-test
```

실주문은 Jenkins에서 명시적 `--live`와 기존 credential을 모두 제공할 때만 허용한다.
`.env.example`에는 key 이름만 두고 실제 값은 커밋하지 않는다. Gamma/CLOB 외부 응답의
cursor, league identity, token alignment, book depth, fill, fee, payout이 불명확하면 fail closed한다.

## Jenkins 변경

live 코드를 바꿀 때는 Cat/Dog timer를 먼저 끄고 unit/contract test와 수동 build를 통과한 뒤
`H/5 * * * *`를 복원한다. clean build, workspace wipe, DB migration/import를 하지 않는다.
새 runtime job으로 과거 Papaya DB와 분리한다. 배포 뒤 console과 `daily-rsync` verified DB에서
두 번 이상의 cycle, source digest, order/open state를 확인한다.

5분 polling은 연속 stop 보장이 아니다. `0.70`과 실제 full-depth sell VWAP의 gap을 숨기거나
trigger 가격 체결로 기록하지 않는다. 24시간 health 전 수익성, 7일 전 arm 승자나 scale-up을
주장하지 않는다.

# L4 AGENTS.md — Golden Watermelon Live

상위 `../AGENTS.md`를 따른다. Cat/Dog real-money A/B의 project safety contract다.

## Active contract

- Cat: `polybot-cat` / `watermelon-live-cat-96-1m-v2h` / `[0.96,0.999]`.
- Dog: `polybot-dog` / `watermelon-live-dog-99-1m-v2h` / `[0.99,0.999]`.
- 두 잡 모두 non-concurrent `* * * * *`, exact `$5`.
- Entry `[2026-08-26T18:30:00Z,2026-09-02T18:30:00Z)`, follow-up
  `2026-09-09T18:30:00Z`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Active preregistration: `research/frozen-2026-08-26-uefa-v2h/PREREGISTRATION.md`.

## 불변 조건

- EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A exact domestic identity와 UCL/UEL exact
  competition identity만 허용한다.
- UCL/UEL는 competition tag/series/prefix/UEFA resolution host를 모두 요구하고 team domestic
  league equality는 적용하지 않는다.
- top-level regular-time HOME/DRAW/AWAY moneyline YES만 허용한다. e-sports, child,
  advancement, extra time, penalty market은 주문 전에 fail closed한다.
- 경기 시작 뒤 `[0h,4h]`, explicit live/not-ended.
- exact `$5` full ask walk와 FOK BUY; best bid `<=0.70` trigger 뒤 full shares bid walk와 FOK SELL.
- account/open 20, event 1, cycle 20; manual wallet position 편입·청산 금지.
- accepted order는 fill이 아니다. exact fill/fee 전 lifecycle 확정 금지.
- emergency SELL은 Gamma+CLOB 독립 open proof, post-proof fresh full book, `0.65` execution
  floor, 10%p spread, 35% projected-loss cap을 모두 통과해야 하며 cycle당 1건만 허용한다.
- 경제손익(confirmed SELL + proven resolution)이 `-$10`이면 신규 BUY를 자동 차단한다.
- open state와 unresolved BUY reservation을 함께 max-position capacity에 계산한다.
- PENDING_BUY/PENDING_SELL/QUARANTINED/orphan/fill-fee gap이 있으면 신규 BUY를 막는다.
- future timing/notional 선택은 White/Grey evidence에서 하며 v2h live 금액은 `$5`로 유지한다.

## 검증과 Jenkins

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

live 코드 변경 전 Cat/Dog timer를 먼저 끈다. test와 timer 없는 수동 build가 성공하고 console,
DB, pending state, source digest를 확인한 뒤에만 timer를 복원한다. clean/wipe/migration/import를
하지 않는다. 자연 build 각 2회와 daily-rsync verified DB를 확인한다.

timed build는 검증된 exact commit을 workspace에 pin하고 `NullSCM`으로 실행한다. 원격 checkout,
`uv sync`, `polybot config`, `polybot status`는 release 배포 단계에서만 수행한다. 새로운 release는
timer off → GitSCM 수동 build → exact commit/source 검증 → NullSCM pin → timer 복원 순서다.

1분 polling은 연속 stop 또는 threshold fill을 보장하지 않는다. trigger와 actual full-depth
VWAP gap, zero fill과 book closure를 숨기지 않는다. 24시간 health 전 수익성, follow-up 전 arm
winner, White/Grey gate 전 scale-up을 주장하지 않는다.

v2f/v2g와 과거 Papaya DB는 immutable archive다. active v2h와 copy/merge/backfill하지 않는다.

# L4 AGENTS.md — Golden Watermelon Live

상위 `../AGENTS.md`를 따른다. Soccer/MLB/NHL real-money A/B의 project safety contract다.

## Active contract

| family | A | B | cadence/notional |
|---|---|---|---|
| Soccer | `polybot-cat` / `watermelon-live-cat-96-1m-v2h` / 0.96 | `polybot-dog` / `watermelon-live-dog-99-1m-v2h` / 0.99 | 1m / current target `$5` |
| MLB | `polybot-bear` / `watermelon-live-bear-mlb-96-1m-v3a` / 0.96 | `polybot-tiger` / `watermelon-live-tiger-mlb-99-1m-v3a` / 0.99 | 1m / current target `$5` |
| NHL | `polybot-lion` / `watermelon-live-lion-nhl-96-1m-v3a` / 0.96 | `polybot-wolf` / `watermelon-live-wolf-nhl-99-1m-v3a` / 0.99 | 1m / current target `$5` |

- Entry `[2026-08-29T04:00:00Z,2026-09-05T04:00:00Z)`, follow-up cutoff
  `2026-09-12T04:00:00Z`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Active preregistration:
  `research/frozen-2026-09-03-mlb-guard-epoch-v3f/PREREGISTRATION.md`.
- MLB의 신규 진입 중지용 경제손익은 `2026-09-02T12:12:00Z` 이후 거래만 계산한다. 이전
  손실은 DB·회고 증거에 보존하며, 성과 집계에서 삭제하거나 새 묶음과 합치지 않는다.

Cat/Dog는 기존 bot-owned position을 관리해야 하므로 v2h DB를 이어 쓴다. 신규 MLB/NHL job만
새 runtime DB를 만든다. DB clean/wipe/migration/copy/merge/backfill을 하지 않는다.

## 불변 조건

- Jenkins `POLYBOT_SPORT_FAMILY=soccer|mlb|nhl`만으로 family를 선택하며 별도 전략 fork를 만들지
  않는다.
- Soccer는 EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL exact identity와 정규시간
  HOME/DRAW/AWAY YES만 허용한다.
- MLB/NHL은 exact major-league root/season, 두 팀, direct top-level whole-game moneyline만
  허용한다. World Series/Stanley Cup Final은 exact identity일 때 포함한다.
- e-sports, MiLB/AHL/ECHL/NCAA, child/period/spread/total/prop/future/advancement는 fail closed한다.
- Gamma server gate는 cumulative volume `$5,000` 및 liquidity `$5,000`; cursor-complete 최대
  4페이지 뒤 fresh exact `$5` full CLOB book을 최종 확인한다.
- baseline exact `$5` book으로 신호를 비교한다. 운영 목표액을 올렸을 때는 같은 fresh book과
  arm 상한 안에서 `$5` 이상 전량 가능한 가장 큰 사다리 금액 하나를 FOK BUY한다. signed
  maker/taker precision을 POST 전에 확인하며 accepted order는 fill이 아니다.
- account/open 20, event 1, cycle BUY 5, cycle emergency SELL 1. manual wallet position은
  편입·청산하지 않는다.
- PENDING BUY/orphan BUY/BUY fill-fee gap, 일반 QUARANTINED와 경제손익 증거 누락은 신규 BUY를
  막는다. SELL-only intent·대사 실패는 같은 token/event에만 격리하고 다른 event는 계속한다.
  연속 손절 실패는 180분 뒤 성공 매도로 꾸미지 않고 open-capacity를 유지한 QUARANTINED로
  자동 격리 종결한다. execution ledger 결합 실패는 중복 SELL 방지를 위해 즉시 격리한다.
- effective stop은 `max(0.70, confirmed entry VWAP-0.30)`다. 독립 Gamma+CLOB OPEN proof와 proof
  뒤 fresh complete book, spread `<=0.10`을 요구한다. 정상 연속 book은 stop 대비 5pp/35%
  envelope를 유지하고, 검증된 OPEN 상태의 불연속 gap은 envelope가 손절을 무력화하지 않게 한다.
  종료 후 cleanup bid는 OPEN proof에서 차단한다.
- 이 전략에는 별도 익절 주문이 없고 proven resolution까지 보유한다. 따라서 Golden Plum의
  부분 익절 계약을 억지로 적용하지 않으며, 손절은 확인된 보유 잔여 전량 FOK만 허용한다.
- 각 cycle 후보는 POST 전 `QUEUED_NO_POST`로 일괄 기록한다. 명시적인 pre-submission/no-POST만
  fresh in-band snapshot에서 재시도하며, POST 가능성이 있으면 ledger 대사 전 재시도하지 않는다.
  같은 event의 반대 결과는 기존 stop SELL confirmed 후 다음 cycle부터만 진입할 수 있다.
  반대 token 전환은 한 번뿐이며 같은 token 재매수와 세 번째 진입은 720시간 동안 금지한다.
- `DELAYED` FOK BUY/SELL은 exact order·전체 인증 token trade 부재와 cancellation 증거가 모두
  맞을 때만 2분 뒤 0체결로 종결한다. 모호하면 PENDING을 유지한다.
- confirmed SELL + proven resolution 경제손익 `<=-$10`이면 신규 BUY를 자동 차단한다.
- 경과시간은 거래 조건이 아니다. 42초 이후 요청 금지나 process alarm을 사용하지 않는다.
  각 HTTP 요청은 finite timeout을 사용하고 50초 초과는 telemetry warning으로 남긴다.
- DB별 nonblocking run lock으로 겹친 trigger를 skip한다. 한 cycle이 1분을 넘더라도 두 process가
  같은 DB나 주문 lifecycle을 동시에 만지지 않는다.

## 검증과 Jenkins

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

live 코드 변경 전 여섯 job timer를 끈다. test와 timer 없는 수동 build가 성공하고 console, DB,
pending state, source digest를 확인한 뒤 timer를 복원한다. 정기 shell은 lock hash가 바뀔 때만
`uv sync --frozen`하고 평소에는 `uv run --frozen --no-sync polybot run ...` 하나만 실행한다.
concurrent build와 Clean before checkout은 끈다.

timer 복원 뒤 각 job의 자연 build 2회와 daily-rsync verified DB를 확인한다. 1분 polling은
threshold 체결이나 stop 가격을 보장하지 않는다. gap, zero fill, book closure와 overlap skip을
숨기지 않는다. 24시간 health 전 수익성, follow-up 전 arm/sport winner, White/Grey gate 전
scale-up을 주장하지 않는다.

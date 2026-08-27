# Golden Coconut

Golden Coconut은 계좌·주문 경로가 없는 major-sports lifecycle moneyline research collector다.
축구(EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UCL, UEL), MLB, NBA, NFL, NHL을
동일한 5분 cadence에서 경기 전부터 종료·해결까지 관측한다. canonical runtime은
`coconut-major-sports-lifecycle-5m-v7`, Jenkins job은 `polybot-gold`다.

`--simulate`와 `--shadow`는 실제 공개 Gamma/CLOB/Sports feed를 읽는 accountless mode를
뜻한다. 가짜 가격이나 체결을 만들지 않는다. `--live`, `active`, `close_only`, credential,
wallet, signing, authenticated endpoint와 transaction SDK는 source-level로 금지된다.

## 수집 계약

각 cycle은 다음 5개 logical Gamma `/events/keyset` sweep을 `closed=false`, 실제 경기 시작 시각
`start_time_min/max=slot-24h..slot+48h`로 독립적으로 terminal cursor까지 읽는다. Soccer logical
sweep은 아래 8개 대회 query tag로 fan-out하고, 모든 physical cursor가 끝나야 완료된다.
다섯 family는 각자 격리된 credential-free HTTP session에서 동시에 시작하며, thread 완료 순서와
무관하게 frozen family order로 정규화한다.
`live=true`를 discovery gate로 사용하지 않으며,
discovery 범위를 벗어나거나 closed로 바뀐 accepted game도 event ID로 terminal state까지
follow-up한다.

| family | physical query tag(s) | exact sport/root identity |
|---|---|---|
| soccer | 306, 1494, 102070, 780, 100100, 101962, 100977, 101787 | EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UCL, UEL |
| MLB | 100381 | sport 8, primary tag 100381, root 3 |
| NBA | 745 | sport 34, primary tag 745, root 10345 |
| NFL | 450 | sport 10, primary tag 450, root 10187 |
| NHL | 899 | sport 35, primary tag 899, root 10346 |

모든 sweep은 `closed=false`, `include_children=false`, `related_tags=false`다. 서버 응답의
canonical schedule도 UTC half-open interval로 재검증하며 누락·오염·범위 밖 신규 event는 raw
payload를 보존한 채 거절한다. liquidity와 `volumeNum`,
`volume24hr`는 selection gate가 아니라 strata로만 저장된다. 다섯 family 중 하나라도 cursor가
미완결이면 그 cycle은 health failure이며 threshold episode를 해석하지 않는다.

HTTP 응답은 socket connect/read timeout과 별개로 attempt당 15초의 전체 wall-clock 경계를
적용한다. 작은 chunk가 계속 도착해 read timeout을 우회하더라도 partial bytes와 timeout receipt를
남기고 응답을 닫은 뒤 최대 2회만 재시도한다. 모두 실패하면 page를 누락시키지 않고 cycle을
fail closed한다.

Soccer는 exact top-level `moneyline`의 result-specific `[Yes, No]`, `negRisk=true` 구조에서
HOME/DRAW/AWAY의 Yes token만 관측한다. 미국 4종목은 official major identity의 exact direct
two-team outcome, `negRisk=false`만 허용한다. child/period/spread/total/prop/future/advancement,
e-sports와 minor/G League/AHL/ECHL/NCAA는 제외한다.

미국 event series는 `sport.series` root와 같은 값이라고 가정하지 않는다. exact sport/root/tag,
두 팀의 league, 그리고 frozen root-or-season series shape를 함께 검증한다. Soccer draw descriptor는
bare `Draw` 또는 `Draw (<exact event title>)`만 허용해 production Gamma 형식을 빠뜨리지 않으면서
부분 문자열 오인도 막는다.

공식 MLB/NBA/NFL/NHL preseason은 제외하지 않는다. 대신 `PRESEASON`으로 저장해
`REGULAR`·`POSTSEASON`과 절대로 합치지 않는다. 불명확한 source phase는 `UNKNOWN`이다.

`DISCOVERED_OPEN`은 부재가 아니라 명시적 lifecycle unknown stratum이다. 해당 game의 open
whole-game moneyline book은 수집하지만 `PREGAME`·`IN_PLAY`와 합치지 않는다. 미래 scheduled
start가 있으면 lifecycle을 바꾸지 않고 `PRESTART_CANDIDATE` anchor만 기록할 수 있다.

## Book과 crossing evidence

public CLOB full book은 token/cycle당 canonical gzip blob 한 행으로만 저장한다. 같은 snapshot에서
`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000` executable ladder를
walk한다. fee endpoint가 응답한 값만
기록하며 fallback fee는 없다.

threshold grid는 `0.75`부터 `0.99`까지 `0.01` 간격이다. token/cycle마다 vector 한 행을 저장한다.

- 첫 executable observation이 threshold 이상: `LEFT_CENSORED`, episode 없음
- 직전 full observation보다 450초를 초과한 구간에서 threshold를 넘음: `GAP_CENSORED`, episode 없음
- 450초 이내 직전 값 `< X`, 현재 값 `>= X`: `UPWARD_CROSSING`, episode 1개

이 evidence는 displayed-book counterfactual이며 fill 또는 realized performance가 아니다.

## 저장 구조

daily-rsync 통합 때문에 active DB 이름은 반드시 다음과 같다.

```text
data/coconut-major-sports-lifecycle-5m-v7/trades_sim.db
```

`trades_sim.db`는 filename 호환 계약일 뿐이다. SQLite에는 `orders`, `fills`, `positions`,
`wallets`, `trades`, P&L table이 없다. UTC date가 바뀌면 prior active file을
`trades_sim_YYYYMMDD.db` whole-shard로 보존하고 새 active DB를 create-only로 만든다. row update,
delete, migration, `ALTER TABLE`, auto-prune은 금지한다.

## 로컬 검증과 CLI

```bash
uv lock
uv sync --frozen --extra dev
uv run pytest
uv build

uv run polybot config --simulate --job coconut-major-sports-lifecycle-5m-v7
uv run polybot status --simulate --job coconut-major-sports-lifecycle-5m-v7
uv run polybot health --simulate --job coconut-major-sports-lifecycle-5m-v7
uv run polybot analyze --simulate --job coconut-major-sports-lifecycle-5m-v7 \
  --db /absolute/path/to/trades_sim.db
```

Analyzer는 단일 cohort의 unique `SUCCEEDED`·five-family cursor-complete cycle만 선택해 health,
lifecycle/anchor coverage, sport-equal coverage, season/notional-separated
liquidity/volume/depth/threshold strata, game clustering과 missing-sport null을 보고한다.
health-only 자료에서 profitability 결론은 항상 `null`이다. 운영 절차는 [OPERATIONS.md](OPERATIONS.md), frozen 질문은
[STRATEGY.md](STRATEGY.md)를 따른다.

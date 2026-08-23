# Frozen preregistration — Soccer Major-League In-Play Match Winner v3

- Frozen at: `2026-08-23T15:00:00Z` (`2026-08-24 00:00 KST`).
- Data contract: `soccer-inplay-major-league-match-winner-v1`.
- Mode: accountless displayed-book simulation only.
- Entry window: `[2026-08-23T15:00:00Z, 2026-08-30T15:00:00Z)`.
- Resolution follow-up end: `2026-09-06T15:00:00Z`.
- First health review: after `2026-08-24T16:00:00Z`
  (`2026-08-25 01:00 KST`).

## Primary question

선정한 5개 축구 리그의 경기 중 whole-match winner exact displayed `$5` ask가 X 이상일 때,
resolution 보유 또는 Y 이하 executable bid stop의 fee 후 반사실 ROI와 고확률 역전 빈도는
어떠하며 1분 cadence가 5분 cadence보다 tail loss를 더 잘 관측하는가?

첫 7일은 collection health와 league coverage를 우선한다. 이 기간 중 ROI, threshold X,
stop Y 또는 live 승격을 선택하지 않는다.

## Frozen universe

Gamma `/events/keyset` request envelope:

- `closed=false`
- `live=true`
- `tag_slug=soccer`
- page size 500, max 4
- liquidity/volume filter 없음

각 event는 다음을 모두 만족해야 한다.

1. `event.tags[].slug`에 `soccer`가 있다.
2. `event.sport.sport/name`이 아래 allowlist의 code/name과 정확히 일치한다.
3. e-sports tag가 없다.
4. nested market은 exactly one event relation, two teams/tokens/prices,
   `sportsMarketType=moneyline`, open/orderbook/accepting이며 경기 시작 후이다.
5. non-negRisk team labels 또는 negRisk team-win YES가 event team과 정확히 대응한다.

| league | Gamma code | exact Gamma name |
|---|---|---|
| English Premier League | `epl` | `Premier League` |
| Bundesliga | `bun` | `Bundesliga` |
| Ligue 1 | `fl1` | `Ligue 1` |
| LaLiga / Primera División | `lal` | `LaLiga` |
| Major League Soccer | `mls` | `MLS` |

unknown/missing/mismatched metadata는 fail closed한다. `sport_family`, `league_code`,
`league_name`, `series_slug`, event tags와 team league metadata를 observation에 보존한다.
e-sports와 비허용 축구 리그는 CLOB token fetch와 episode 생성 대상이 아니다.

## Frozen treatment

| runtime job | arm | nominal cadence |
|---|---|---:|
| `watermelon-white-1m-v3` | `FAST_1M` | 1 minute |
| `watermelon-grey-5m-v3` | `CONTROL_5M` | 5 minutes |

두 arm의 universe, source, entry/stop grid, `$5` depth와 fee model은 동일하며 cadence만 다르다.
paired unit은 `condition_id × token_id × entry_threshold`이다.

## Frozen entry and exit grid

- Entry X: `0.95, 0.96, 0.97, 0.98, 0.99`.
- Entry: first full-depth observation above X 또는 upward cross.
- Primary: `HOLD_TO_RESOLUTION`.
- Stops Y: `STOP_0.95, STOP_0.93, STOP_0.90, STOP_0.85, STOP_0.80, STOP_0.70`.
- Early take-profit: none.
- Notional: exact displayed ask depth `$5`.
- Fee: source schedule, missing enabled fee rate는 conservative `0.05` fallback.

진입 book의 contemporaneous bid는 post-entry path/stop에 재사용하지 않는다. stop은 best bid
trigger와 full-depth VWAP, gap, partial fill, remaining retry를 분리한다. resolution은 public
CLOB에서 exactly one winning token일 때만 인정한다.

## Health and falsification gates

24시간 health gate:

- DB quick check/data contract/config/source digest 정상
- cursor completeness 100%
- FAST cadence coverage ≥95%, CONTROL ≥90%
- eligible outcome CLOB attempt coverage ≥95%
- league metadata가 있는 eligible event 100%
- CRITICAL/HIGH issue 0
- external free ≥50GiB, used ratio <90%
- FAST natural build runtime p95 <45 seconds

7일에는 리그별 observation/event/episode/resolution coverage와 고확률 패배 evidence를
기술한다. threshold/stop 선택은 resolution follow-up과 사전 표본 gate가 충족된 경우에만
가능하다. 같은 코호트를 본 뒤 allowlist/grid를 조용히 바꾸지 않는다. 표본 부족은
inconclusive이며 변경은 새 preregistration/runtime/DB epoch로 수행한다.

## Evidence identity and v2 preservation

cohort key는 `config_hash × strategy_source_digest × mode × job_name`이다. raw Gamma/CLOB,
request receipt, complete sweep, normalized league metadata, full books/levels, decision, episode,
path/stop/resolution, run/config/storage/DB check를 append-only로 보존한다. actual fill 또는
realized P&L이 아닌 displayed-book counterfactual이다.

기존 `watermelon-white-1m-v2`와 `watermelon-grey-5m-v2` DB 및
`research/frozen-2026-08-23/`은 전체 스포츠 예비 코호트로 보존한다. v3 DB로 migration,
merge, clean 또는 rewrite하지 않는다.

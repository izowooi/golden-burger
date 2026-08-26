# Frozen preregistration — Soccer Major-League In-Play Match Winner v3b

- Frozen decision timestamp: `2026-08-26T10:58:44Z`.
- Entry window: `[2026-08-26T15:00:00Z, 2026-09-02T15:00:00Z)`.
- Resolution follow-up end: `2026-09-09T15:00:00Z`.
- First collection-health review: after `2026-08-27T15:00:00Z`.
- Data contract: `soccer-inplay-major-league-match-winner-v2`.
- Schema profile: `golden-watermelon-v3a-schema-v1` (schema unchanged).
- Universe profile: `soccer-major-leagues-2026-08-v3b`.
- Classifier: `soccer-major-league-identity-v2`.
- League mapping SHA-256:
  `fdec6c9f49fff8aae0d8009233cbe0ca0324c385b2c4a49e1486e1cc1cdf7024`.
- Mode: accountless displayed-book counterfactual simulation only.

v3a DB와 preregistration은 immutable evidence다. v3b는 같은 외장 workspace에 새 runtime
`watermelon-white-1m-v3b`와 `watermelon-grey-5m-v3b` DB를 만들며 v3a file을 clean,
ALTER, migrate, copy, merge, backfill 또는 delete하지 않는다.

## Amendment rationale

v3a verified source cutoff `2026-08-26T10:33:51Z`에서 White 1분은 4,001 success run,
Grey 5분은 801 success run이었다. White는 0.96 threshold episode 7건(6승 1패), 0.99는
1건(1승)을 기록했다. 발렌시아–베티스의 베티스 승리는 executable ask 0.97에서 0.96
episode로 포착됐지만 0.98/0.99에는 포함되지 않았다. 반대로 뉴캐슬–리버풀의 0.96
episode는 다음 1분 path에서 executable bid가 약 0.27로 gap-down했다. 이 자료는
0.96/0.99를 최적값으로 확정하지 않으며, 낮은 기준의 신호량과 rare loss를 함께 수집해야
함을 보여준다.

Serie A(`sea`) event가 v3a raw census에서 exact authority tuple을 반복적으로 제공했지만
allowlist 밖이라 `LEAGUE_NOT_ALLOWED`로 보존됐다. v3b는 이 이미 관측된 identity를 사전
등록해 여섯 번째 league로 추가한다.

v3a collector는 negRisk Draw proposition을 제외했지만 live classifier는 home/draw/away를
허용했다. 또한 missing live flag를 inferred-live로 받아들이고 non-negRisk two-team market도
허용했다. 이 모집단 불일치는 v3b 배포 전에 발견됐다. v3b는 명시적인 Draw/Tie proposition의
`YES`를 team-win `YES`와 동일하게 수집하되, collector와 live 모두 parent 없는 explicit
open/live/not-ended event, game age `[0h,4h]`, exact negRisk `[Yes,No]`만 허용한다.

## Frozen source and league envelope

Gamma `/events/keyset` request는 `closed=false`, `live=true`, numeric soccer
`tag_id=100350`, `related_tags=false`, page size 500, max 4, terminal keyset cursor를
사용한다. liquidity/volume gate는 없다. source page는 classifier 전에 raw로 저장한다.

공통 required tag IDs는 `1/100639/100350`이다.

| league | sport id/code/name | primary tag | series id/slug | team league | extra required event tag |
|---|---|---:|---|---|---:|
| EPL | `2/epl/Premier League` | 306 | `10188/premier-league-2025` | epl | `82,306` |
| Bundesliga | `7/bun/Bundesliga` | 1494 | `10194/bundesliga-2025` | bun | `1494` |
| Ligue 1 | `11/fl1/Ligue 1` | 102070 | `10195/ligue-1-2025` | fl1 | `102070` |
| LaLiga | `3/lal/LaLiga` | 780 | `10193/la-liga-2025` | lal | `780` |
| MLS | `33/mls/MLS` | 100100 | `10189/mls-2025` | mls | `100100` |
| Serie A | `12/sea/Serie A` | 100618 | `10203/serie-a-2025` | sea | `101962` |

sport/event tags, one exact series relation, seriesSlug, exactly two team leagues와 e-sports
제외 조건이 모두 맞아야 ACCEPTED다. 허용 code의 authority drift는 HIGH issue이며 CLOB과
episode를 차단한다.

## Frozen match and settlement scope

- top-level whole-match `sportsMarketType=moneyline`만 허용한다.
- exact negRisk `[Yes,No]` home/draw/away proposition만 허용한다. Draw descriptor는
  `Draw`/`Tie` 또는 event의 두 team과 정확히 조합된 표현만 허용하고 `Draw No Bet`은 제외한다.
- negRisk `NO`, child event, prop, cup, 2부, e-sports는 제외한다.
- description이 `this market refers only ... first 90 minutes of regular play plus stoppage
  time`을 명시해야 한다. 누락되거나 extra time/penalty shoot-out을 포함하거나 다른 절과
  모순되는 scope는 fail closed하며 명시적인 excluded 문구만 허용한다.
- Gamma `endDate`는 실제 경기 종료로 해석하지 않는다. gameStartTime과 explicit
  live/ended/open flags 및 terminal one-hot payout만 사용한다.

정규시간 market의 결과는 90분과 stoppage time까지만이다. 연장전과 승부차기는 payout에
포함하지 않는다. 관측 clock은 event live 상태를 사용하므로 후반 추가시간을 90분 숫자로
잘라내지 않는다.

## Frozen treatment

| runtime | arm | cadence |
|---|---|---:|
| `watermelon-white-1m-v3b` | FAST_1M | 1 minute |
| `watermelon-grey-5m-v3b` | CONTROL_5M | 5 minutes |

두 arm은 cadence 외 모든 source/config/grid가 같다. entry threshold는
`0.95/0.96/0.97/0.98/0.99`, stop replay는 hold와
`0.95/0.93/0.90/0.85/0.80/0.70`, notional은 displayed exact `$5`다. trigger와
actual bid VWAP, gap, partial depth, retry를 분리 저장한다.

## Review gates

첫 24시간에는 수익성·threshold·stop을 선택하지 않는다.

- DB quick/FK/schema/application/user/migration/config/source digest exact
- cursor complete 100%, drift 0, CRITICAL/HIGH 0
- FAST cadence ≥95%, CONTROL ≥90%, FAST natural runtime p95 <45s
- eligible outcome CLOB attempt ≥95%, full-depth coverage와 exclusion reason 완전성
- six-league supply/accepted coverage를 league별로 보고하되 경기 부재를 오류로 만들지 않음
- external free ≥50GiB, used ratio <90%

수익성 판정은 entry와 follow-up 종료 뒤 confirmation resolved unique event 총 100개 이상,
league별 20개 이상, resolution coverage 90% 이상, exact entry evidence 100%, six-league
macro event bootstrap lower bound가 0 초과인 경우에만 가능하다. 표본 부족은 inconclusive다.

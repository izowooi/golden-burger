# Frozen preregistration — Soccer Major-League In-Play Match Winner v3a

- Frozen decision timestamp: `2026-08-23T15:07:00Z` (`2026-08-24 00:07 KST`).
- Entry window: `[2026-08-23T16:00:00Z, 2026-08-30T16:00:00Z)`
  (`2026-08-24 01:00 KST` 즉시 시작).
- Resolution follow-up end: `2026-09-06T16:00:00Z`.
- First health review: after `2026-08-24T16:00:00Z` (`2026-08-25 01:00 KST`).
- Data contract: `soccer-inplay-major-league-match-winner-v2`.
- Schema profile: `golden-watermelon-v3a-schema-v1`.
- Universe profile: `soccer-major-leagues-2026-08-v3a`.
- Classifier: `soccer-major-league-identity-v1`.
- League mapping SHA-256:
  `3b843d62e87ebe9ba84c2986a4229d1fa5760d5e06a39204dc5acb3da6a433bb`.
- Mode: accountless displayed-book counterfactual simulation only.

Git commit은 provenance일 뿐 freeze identity가 아니다. 이 문서, `config.yaml`, `STRATEGY.md`,
classifier와 CREATE-only migration의 SHA-256을 `MANIFEST.sha256`에 고정하고, runtime
`strategy_source_digest`가 같은 파일들을 다시 hash한다. manifest 검증 후 내용이 달라지면
이 epoch는 실행하지 않는다.

## Primary question

선정한 5개 축구 리그의 whole-match winner exact displayed `$5` ask가 X 이상일 때 resolution
보유 또는 Y 이하 executable bid stop의 fee 후 event-equal ROI와 고확률 역전 빈도는 어떠하며,
1분 cadence가 5분 cadence보다 tail loss를 더 잘 관측하는가?

첫 7일에는 collection health, identity drift, league coverage만 판정한다. ROI, threshold X,
stop Y 또는 live 승격을 선택하지 않는다.

## Frozen source envelope

Gamma `/events/keyset` request는 다음과 정확히 같아야 한다.

- `closed=false`
- `live=true`
- `tag_id=100350`
- `related_tags=false`
- page size 500, max 4, keyset cursor complete
- liquidity/volume filter 없음

`tag_slug`, title, event slug 또는 series-only server filter를 사용하지 않는다. 받은 모든 page를
먼저 raw payload로 저장하고, event identity가 ACCEPTED인 경우에만 nested market과 CLOB을
처리한다.

## Frozen league identity mapping

공통 required event/sport tag IDs는 `1`(Sports), `100639`(Games), `100350`(Soccer)이다.
숫자 ID는 input의 string/int 표현과 무관하게 canonical decimal string으로 비교한다.

| league | sport id | code | exact name | primaryTagId | extra required tag IDs | series id | exact series slug | team league |
|---|---:|---|---|---:|---|---:|---|---|
| EPL | 2 | `epl` | `Premier League` | 306 | `82,306` | 10188 | `premier-league-2025` | `epl` |
| Bundesliga | 7 | `bun` | `Bundesliga` | 1494 | `1494` | 10194 | `bundesliga-2025` | `bun` |
| Ligue 1 | 11 | `fl1` | `Ligue 1` | 102070 | `102070` | 10195 | `ligue-1-2025` | `fl1` |
| LaLiga | 3 | `lal` | `LaLiga` | 780 | `780` | 10193 | `la-liga-2025` | `lal` |
| MLS | 33 | `mls` | `MLS` | 100100 | `100100` | 10189 | `mls-2025` | `mls` |

ACCEPTED event는 다음을 전부 만족해야 한다.

1. `event.sport.id/sport/name/primaryTagId/series`가 한 frozen row와 정확히 일치한다.
2. `event.sport.tags`와 `event.tags[].id`가 공통 및 league required tag IDs를 모두 포함한다.
3. `event.series`가 정확히 한 개이며 id/slug와 top-level `seriesSlug`가 frozen row와 일치한다.
4. 정확히 두 team이 있고 두 `teams[].league` 값이 모두 frozen team league와 일치한다.
5. e-sports tag ID `64` 또는 slug `esports/e-sports`가 없다.

비허용 code인 cup, 2부, 다른 축구 리그와 e-sports는 `REJECTED`다. 허용 code인데 위 authority
field가 누락·충돌하면 `DRIFT`이며 CLOB 조회와 episode를 금지하고 HIGH issue를 남긴다.
series rollover나 registry rename을 runtime에서 자동 수용하지 않고 새 prereg/runtime/DB
epoch로 전환한다.

## Event-level evidence contract

각 source event를 sweep당 한 번 `event_observations`에 append-only로 저장한다. raw page FK,
page/request receipt, canonical event hash, raw sport/tags/series/teams JSON, normalized numeric IDs,
team leagues, classifier/mapping identity, `ACCEPTED/REJECTED/DRIFT`와 reason을 보존한다.
market row는 `event_observation_id`만 참조하며 event tag/team/series JSON을 반복하지 않는다.

새 DB는 absent path에 CREATE-only bootstrap으로만 만든다. exact application ID는
`1196903731`(`GWM3`), user version은 `301`이다. 기존 file은 writable connection, WAL 또는
DDL 전에 read-only로 이 두 값, singleton metadata, contract, schema/universe/classifier/
mapping/migration/schema hash 및 canonical registry mapping JSON을 모두 검증한다. runtime
`ALTER TABLE`은 금지한다.
Frozen schema SHA-256은
`70baef885a69b0200bb11c8325530cc88a49be2f1b78e27fb046c097a1716e32`이며 DB의 self-declared
값만 신뢰하지 않고 application constant 및 live `sqlite_schema` fingerprint와 모두 비교한다.

## Frozen treatment and runtime epoch

| runtime job | arm | nominal cadence |
|---|---|---:|
| `watermelon-white-1m-v3a` | `FAST_1M` | 1 minute |
| `watermelon-grey-5m-v3a` | `CONTROL_5M` | 5 minutes |

DB는 각각 `data/<runtime-job>/trades_sim.db`에 새로 생성한다. 기존
`watermelon-white-1m-v2`, `watermelon-grey-5m-v2`, `watermelon-white-1m-v3`,
`watermelon-grey-5m-v3` DB와 `research/frozen-2026-08-23/`,
`research/frozen-2026-08-24-soccer/`은 archive evidence다. migration, merge, copy, backfill,
rewrite, delete 또는 clean하지 않는다.

두 arm은 cadence 외 source, mapping, classifier, schema, entry/stop grid와 fee model이 같아야
한다. pair analyzer는 exact contract/universe/classifier/mapping 일치와 서로 다른 FAST/CONTROL
arm을 강제한다. paired unit은 `condition_id × token_id × entry_threshold`이며 league가 서로
다르면 즉시 실패한다.

## Frozen entry and exit grid

- Entry X: `0.95, 0.96, 0.97, 0.98, 0.99`.
- Entry: first full-depth observation above X 또는 upward cross.
- Primary exit: `HOLD_TO_RESOLUTION`.
- Stops Y: `STOP_0.95, STOP_0.93, STOP_0.90, STOP_0.85, STOP_0.80, STOP_0.70`.
- Early take-profit: none.
- Notional: exact displayed ask depth `$5`.
- Fee: source schedule, enabled fee rate 누락 시 conservative `0.05` fallback.

진입 book의 contemporaneous bid는 path/stop에 재사용하지 않는다. stop은 best bid trigger와
full-depth VWAP, gap, partial fill, remaining retry를 분리한다. resolution은 public CLOB에서
exactly one winning token일 때만 인정한다.

## Estimator and outcome gate

Primary estimator는 league-macro event-equal fee-net ROI다.

1. `event_id` 안의 같은 threshold/policy episode를 먼저 평균한다.
2. 각 league 안에서 event mean을 동일 가중한다.
3. EPL/Bundesliga/Ligue 1/LaLiga/MLS 다섯 league mean을 각각 20%로 macro-average한다.
4. 다섯 리그 중 하나라도 evaluable resolved event가 없으면 macro estimate와 CI는 `null`이다.
5. 2,000회 deterministic bootstrap은 league별 event를 replacement sampling한 뒤 다섯 mean을
   동일 가중한다.

자연 모집단 pooled event-equal 값은 secondary이며 MLS 같은 다수 league가 지배할 수 있음을
표시한다. outcome promotion 검정은 entry/follow-up 종료 뒤, threshold마다 confirmation
구간 resolved unique event 총 100개 이상 및 league별 20개 이상, resolution coverage 90% 이상,
exact `$5` entry quote 100%, macro bootstrap 95% lower bound가 0 초과일 때만 가능하다. 표본
부족은 inconclusive이며 기간·mapping·grid를 조용히 바꾸지 않는다.

## Health gates

- DB quick check와 exact schema/application/user/mapping preflight 정상
- cursor completeness 100%
- FAST cadence coverage ≥95%, CONTROL ≥90%
- eligible outcome CLOB attempt coverage ≥95%
- ACCEPTED event identity coverage 100%, DRIFT 0
- CRITICAL/HIGH issue 0
- external free ≥50GiB, used ratio <90%
- FAST natural build runtime p95 <45 seconds

이 DB의 ask→bid/resolution은 actual fill 또는 realized P&L이 아닌 displayed-book
counterfactual이다. credential, order SDK, `--live`, active/close-only mode를 source-level로
금지한다.

# Golden Coconut 회고 계약

Evidence 해석은 [`EVIDENCE_CONTRACT.md`](EVIDENCE_CONTRACT.md)를 따른다.

- `REVIEW_START`: `polybot-gold`의 첫 successful source run receipt time
- `REVIEW_END`: `REVIEW_START + 24h` exclusive (첫 collection-health 점검)
- timezone: UTC half-open `[REVIEW_START, REVIEW_END)`; 화면에는 KST를 병기한다.

## 상태

2026-08-27 v1은 immutable archive다. Lifecycle v2는 `start_date_*`가 경기 시작이 아니라 event
생성일을 제한해 범위 밖 경기가 유입됐고, `DISCOVERED_OPEN` gate 때문에 book evidence가 0이었다.
V3는 실제 경기 시작 시각과 `DISCOVERED_OPEN` book 수집을 바로잡았지만, broad soccer tag 한 개가
72시간 창에서 2,030개 event/21 pages를 반환해 20-page cap에 걸렸다. `polybot-gold` build #22는
five-family cursor gate에서 실패했고 book/vector evidence가 0이므로 v2와 v3 모두 immutable
invalidated collection으로 보존한다.

2026-08-28 v4는 같은 source window에서 broad-v3가 수락한 30개 soccer event를 모두 포함했던
EPL·Bundesliga·Ligue 1·LaLiga·MLS·Serie A·UCL·UEL의 frozen 8-tag union으로 discovery를
fan-out해 cursor·book 수집에는 성공했다. 하지만 실제 NFL 14건의 season series
`12185/nfl-2026`을 sport root `10187`과 다르다는 이유로 HIGH drift 처리했고, production draw
descriptor `Draw (<event title>)`도 모두 거절했다. v4 역시 immutable invalidated collection이다.
v5는 semantic root-or-season identity와 exact parenthetical draw를 새 DB에서 검증해 첫 두
cycle을 성공시켰다. 그러나 Gamma의 한 200 응답이 58.8초 동안 작은 chunk로 이어지며 per-read
12초 timeout을 우회했고, 자연 실행 #27/#28이 연속으로 90초 receipt-skew gate를 초과했다.
두 실패 cycle은 estimand에 들어가지 않았고 v5 DB는 immutable 보존한다. v6는 동일 universe와
estimand를 새 DB에서 유지하면서 socket read 5초, 전체 HTTP attempt 15초, 최대 2회 bounded retry와
partial-response receipt를 강제한다. 아직 profitability verdict는 없다.

v6 첫 운영 build #29에서 개별 attempt 제한은 정상 작동했지만, five-family sweep 자체가
순차 실행되어 Gamma 첫 receipt부터 CLOB 마지막 receipt까지 약 109초가 걸렸다. 90초 gate가
이를 올바르게 차단했고 episode는 승인되지 않았다. v6는 immutable invalidated receipt-skew
evidence로 보존한다. v7은 universe·classifier·schema·estimand를 바꾸지 않고 다섯 family를
각기 격리된 HTTP session에서 동시에 시작한다. 결과는 frozen family order로 정규화하며 worker
하나라도 실패하면 partial census를 publish하지 않는다.

## Frozen cohort

- data contract: `major-sports-lifecycle-census-v7`
- runtime: `coconut-major-sports-lifecycle-5m-v7`
- Jenkins: `polybot-gold`
- active DB: `data/coconut-major-sports-lifecycle-5m-v7/trades_sim.db`
- families: soccer, MLB, NBA, NFL, NHL
- cadence: 5분
- estimator stage: collection health only

성과 회고 전 `config_hash × strategy_source_digest × mode × job_name`을 분리하고 frozen
`SPORTS_REGISTRY.json` SHA-256, UTC half-open range, verified DB absolute path/SHA-256, source/sync
cutoff를 기록한다. active와 UTC archive가 같은 inode인 handoff 순간에는 하나만 분석한다.

## 첫 health gate

- 다섯 logical family 각각 terminal cursor이며 frozen query-tag set 누락·repeat·page-cap 위반 0
- exact major identity drift와 e-sports/minor false-positive 0
- 공식 US preseason은 `PRESEASON`으로 존재하고 regular/postseason과 분리
- eligible token의 book attempt, canonical gzip uniqueness, ladder completeness
- threshold vector uniqueness와 left/gap censoring 계약
- unique/void/tie resolution 분리
- event-by-ID follow-up, explicit lifecycle terminal coverage, schedule revision, WSS/Gamma provenance
- T-24h/T-60m/last-prestart anchor의 measured missingness와 no-imputation
- `$5..$1000` 각 notional별 ladder/vector 완전성
- 5분 atomic slot, 225/30/240초 deadline, receipt skew 90초, HTTP attempt wall 15초
- append-only trigger, create-only schema, rollback, `PRAGMA quick_check=ok`
- external APFS marker, 150 GiB/70%/80% storage gate
- sport별 missing cell과 macro null behavior

CRITICAL/HIGH issue, incomplete family cursor, evidence gap이 있으면 threshold 비교와 승격을 중단한다.

## 분석 금지

health-only data에서 ROI, realized P&L, fill quality, best sport, best threshold, live promotion을
결론내리지 않는다. liquidity/volume은 selection gate가 아니며 retrospective cutoff로 universe를
바꾸지 않는다. PRESEASON을 REGULAR/POSTSEASON과 합쳐 표본을 늘리지 않는다.

## 향후 회고

충분한 prospective 기간과 resolution coverage가 쌓인 뒤에도 game cluster 내 outcome equal →
sport 내 game equal → 5 sport equal macro 순으로 평가한다. sport 하나가 비면 macro와 interval은
`null`이다. displayed book replay를 actual fill로 해석하지 않으며 live 후보는 별도 전략 프로젝트,
confirmed fill/fee 계약과 독립 시간구간을 새로 요구한다.

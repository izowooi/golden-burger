# Golden Coconut 회고 계약

Evidence 해석은 [`EVIDENCE_CONTRACT.md`](EVIDENCE_CONTRACT.md)를 따른다.

- `REVIEW_START`: `polybot-gold`의 첫 successful source run receipt time
- `REVIEW_END`: `REVIEW_START + 24h` exclusive (첫 collection-health 점검)
- timezone: UTC half-open `[REVIEW_START, REVIEW_END)`; 화면에는 KST를 병기한다.

## 상태

2026-08-27 신규 prospective collector 구현. 아직 실측 cohort와 profitability verdict는 없다.

## Frozen cohort

- data contract: `major-sports-inplay-moneyline-census-v1`
- runtime: `coconut-major-sports-5m-v1`
- Jenkins: `polybot-gold` (parent integration 예정)
- active DB: `data/coconut-major-sports-5m-v1/trades_sim.db`
- families: soccer, MLB, NBA, NFL, NHL
- cadence: 5분
- estimator stage: collection health only

성과 회고 전 `config_hash × strategy_source_digest × mode × job_name`을 분리하고 frozen
`SPORTS_REGISTRY.json` SHA-256, UTC half-open range, verified DB absolute path/SHA-256, source/sync
cutoff를 기록한다. active와 UTC archive가 같은 inode인 handoff 순간에는 하나만 분석한다.

## 첫 health gate

- 다섯 family 각각 terminal cursor이며 repeat/page-cap 위반 0
- exact major identity drift와 e-sports/minor false-positive 0
- 공식 US preseason은 `PRESEASON`으로 존재하고 regular/postseason과 분리
- eligible token의 book attempt, canonical gzip uniqueness, ladder completeness
- threshold vector uniqueness와 left/gap censoring 계약
- unique/void/tie resolution 분리
- 5분 atomic slot, 225/30/240초 deadline, receipt skew 90초
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

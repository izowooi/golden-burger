# 2026-08-07 Golden Pomegranate capacity amendment

## 변경 사유

최초 global `closed=false` cycle은 139,310 markets, 278,620 outcomes를 관측했고 DB가
1,379,725,312 bytes로 증가했다. 이를 30분마다 반복하면 단순 선형 추정만으로 약 66GB/day라
1TB 외장 volume에서 30일 연구 구간을 유지할 수 없다. 첫 실행 runtime도 753.392초로 15분
slot의 headroom이 부족했다.

따라서 2026-08-06 최초 preregistration의 global-envelope/15-minute 운영 부분은 이 문서로
전향적으로 대체한다. 과거 문서는 당시 결정의 provenance로 보존하며 소급 수정하지 않는다.

## 새 수집 envelope

- cadence: 60분
- Gamma `closed=false`
- `liquidity_num_min=10000`
- `volume_num_min=2000` (cumulative source field)
- dynamic `end_date_max = cycle_started_at + 120 days`
- category, sports, probability, active, standard-binary client-side filter 없음
- 반환된 envelope는 terminal keyset cursor까지 전수 저장
- CLOB/Data API/resolution, append-only와 component missingness 계약은 유지

2026-08-07 실제 API count는 global 139,310 대비 bounded 2,899 markets였다. 이 수치는 운영
capacity 추정값이지 고정 분모가 아니며 각 cycle의 실제 count와 source clock을 DB에 남긴다.

## 새 운영 cohort

runtime job은 `pomegranate-hourly-v1`이다. 기존 `pomegranate-local` DB와 섞지 않는다. cohort는
기존대로 `config_hash × strategy_source_digest × mode × job_name × schema_profile`로 분리한다.
Git commit은 provenance일 뿐 cohort key가 아니다.

## 판정 일정

7/14/30/60~90/120일의 운영·분석 일정과 capacity stop 조건은
[OPERATIONS.md](../OPERATIONS.md)를 따른다. 최소 전략 연구 구간은 30일이며 120일은 retention
horizon이다.

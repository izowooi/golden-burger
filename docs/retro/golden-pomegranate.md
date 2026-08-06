# Golden Pomegranate — Market Observatory 수집 회고

> 작성일: 2026-08-06
> 성격: accountless research instrument
> 거래 성과·execution ledger: **N/A — source-level no-order**

이 문서는 `golden-pomegranate`가 “수익을 냈는가”가 아니라, 미래 전략을 만들 만큼
완전하고 편향을 측정할 수 있는 point-in-time 자료를 수집했는지 판정한다. 분석자는 먼저
[Evidence Contract](EVIDENCE_CONTRACT.md)를 읽되, trading 전용 confirmed-fill gate를 이
수집기에 억지로 적용하지 않는다.

## 리뷰 범위 고정

회고를 시작할 때 다음 값을 문서 첫머리에 실제 UTC half-open range로 교체한다.

```text
REVIEW_START=<YYYY-MM-DDT00:00:00Z>
REVIEW_END=<YYYY-MM-DDT00:00:00Z>  # exclusive
TIMEZONE=UTC
JENKINS_JOB=<jenkins-job>
RUNTIME_JOB=<locate 결과의 runtime_job>
EXPECTED_CADENCE_MINUTES=15
```

Daily Rsync에서 `database_sim` active DB와 해당 기간의
`database_research_archive` shard를 모두 locate/verify한다. 각 DB의 절대 경로,
`local_sha256`, source/sync cutoff, remote path를 적는다. directory 이름이나 transfer plan만
보고 동기화 성공을 추정하지 않는다.

```bash
# REVIEW_END는 exclusive이므로 Daily Rsync의 inclusive 날짜 옵션에는 전날을 넣는다.
REVIEW_FROM_DATE=<YYYY-MM-DD>
REVIEW_TO_DATE=<YYYY-MM-DD>
JENKINS_JOB=<jenkins-job>
ARTIFACT_DIR=<absolute-review-artifact-dir>
REPO_ROOT=<absolute-path-to-t1>
mkdir -p "$ARTIFACT_DIR"

cd "$REPO_ROOT/daily-rsync"
uv run daily-rsync locate \
  --job "$JENKINS_JOB" --strategy golden-pomegranate \
  --from-date "$REVIEW_FROM_DATE" --to-date "$REVIEW_TO_DATE" \
  > "$ARTIFACT_DIR/pomegranate-locate.json"
uv run daily-rsync verify \
  --job "$JENKINS_JOB" --strategy golden-pomegranate \
  --from-date "$REVIEW_FROM_DATE" --to-date "$REVIEW_TO_DATE" \
  > "$ARTIFACT_DIR/pomegranate-verify.json"
```

`verify`가 현재 configured source와 해당 Jenkins job/strategy에 대해 `SUCCESS`인지 확인하고,
locate가 반환한 **실제** `runtime_job`과 `current_databases`/
`research_archives` 각각의 catalog `local_path`를 복사한다. active DB 하나만 검사하거나
repository-local 기본 경로를 대신 쓰지 않는다. 아래 명령을 검증된 모든 DB마다 반복한다.

```bash
RUNTIME_JOB=<locate 결과의 runtime_job>
DB=<locate 결과의 절대 local_path>
ARTIFACT_DIR=<absolute-review-artifact-dir>

cd "$REPO_ROOT/golden-pomegranate"
uv run polybot health --simulate --job "$RUNTIME_JOB" --db "$DB"
uv run polybot status --simulate --job "$RUNTIME_JOB" --db "$DB"
uv run polybot export-manifest --simulate --job "$RUNTIME_JOB" --db "$DB" \
  --output "$ARTIFACT_DIR/$(basename "$DB").manifest.json"
```

완료된 UTC day는 immutable dated shard로 덮여야 하며, 과거 날짜를 mutable active DB로
대체하지 않는다. 각 artifact의 latest sync attempt와 latest successful sync가 모두
`SUCCESS`이고 conflict가 없으며 checksum/`quick_check`가 일치해야 한다. export manifest는
각 DB의 물리 무결성·schema·table count·source cutoff 증거이지 여러 shard를 합친
`[REVIEW_START, REVIEW_END)` 의미 범위 판정이 아니다. 범위 coverage는 catalog metadata와 아래
UTC-bounded SQL을 함께 대사한다.

`polybot-retro audit --strict`는 거래용 table/fill 계약을 검사하므로 이 수집기의 primary
gate가 아니다. 다른 trading DB와 합칠 때는 Pomegranate에서 materialize한 immutable
dataset checksum을 먼저 고정한 뒤, trading DB에는 기존 strict audit를 별도로 실행한다.

## 사전 등록된 7일 health gate

첫 7일은 parameter 탐색이나 전략 선택에 사용하지 않고 수집기 자체를 판정한다.

| 항목 | 통과 기준 | 실패 시 |
|---|---:|---|
| successful complete sweep | Jenkins timer 예정 slot의 95% 이상 | 15분 유지, retry/runtime 복구 |
| cursor completeness | 성공 run 100% | 해당 run 전부 분석 제외, API 진단 |
| full membership | membership/observation=`raw_market_count`, distinct condition=`unique_condition_count` 100% | schema/transaction 복구 |
| returned-row observation coverage | Gamma가 반환한 raw market row의 100% | parser/missingness 복구 |
| variable outcome identity | outcome label/token/index 불일치 0건 | 분석 중단 |
| p95 cycle runtime | 8분 미만일 때만 10분 검토 | 15분 유지 또는 30분 완화 |
| overlap | 0건 목표, 발생률 1% 미만 | Jenkins 동시 실행/timeout 수정 |
| DB integrity | 모든 active/archive shard `quick_check=ok` | 즉시 수집 중단·복구 |
| storage forecast | 120일 뒤에도 150GiB 이상 free | cadence/book tier/보존매체 재설계 |
| CLOB book selection | bucket·reason·coverage 누락 0건 | book 자료로 threshold 비교 금지 |
| public trade windows | complete watermark 후퇴 0건, `possible_gap` 0건 | 해당 구간 trade-flow 분석 금지 |
| trade privacy projection | profile name·bio·image 보존 0건 | schema/sanitizer 복구 |
| resolution follow-up | due backlog가 지속 증가하지 않음 | probe budget/주기 조정 |

10분으로 바꾸는 것은 데이터가 많을수록 좋다는 이유만으로 허용하지 않는다. 위 gate를 7일
통과하고 p95가 8분 미만이며 외장 디스크 120일 forecast가 안전할 때 새 collection contract로
시작한다. 그렇지 않으면 `H/15`를 유지한다.

예정 slot의 primary denominator는 동기화된 Jenkins console에서 build cause가
`Started by timer`인 slot이다. 수동/SCM 재실행은 성공 sweep evidence로는 보존하되 scheduled
coverage의 분자·분모에는 넣지 않는다. DB의 `cadence_coverage`는 중복/누락을 찾는 보조 지표로
사용하며, 그것만으로 scheduled build cause를 추정하지 않는다.

## 기본 무결성 SQL

모든 query는 verified copy에 read-only로 실행한다. 실패/부분 run을 정상 자료로 섞지 않는다.

```sql
-- 1. run 및 완전 sweep
WITH scoped_runs AS (
    SELECT run_id
    FROM research_run_events
    WHERE event_type = 'STARTED'
      AND strategy_name = 'golden-pomegranate'
      AND event_at >= :review_start AND event_at < :review_end
), ranked_events AS (
    SELECT e.run_id, e.event_type, e.event_at,
           ROW_NUMBER() OVER (
               PARTITION BY e.run_id ORDER BY e.event_at DESC, e.event_id DESC
           ) AS position
    FROM research_run_events e
    JOIN scoped_runs s ON s.run_id = e.run_id
)
SELECT event_type AS latest_event, COUNT(*) AS run_count
FROM ranked_events
WHERE position = 1
GROUP BY event_type;

SELECT e.config_hash, c.strategy_source_digest, e.mode, e.job_name,
       cc.contract_name AS schema_profile,
       COUNT(DISTINCT e.run_id) AS run_count
FROM research_run_events e
JOIN research_config_versions c ON c.config_hash = e.config_hash
CROSS JOIN collection_contracts cc
WHERE e.event_type = 'STARTED'
  AND e.event_at >= :review_start AND e.event_at < :review_end
GROUP BY e.config_hash, c.strategy_source_digest, e.mode, e.job_name,
         cc.contract_name;

SELECT cursor_complete, COUNT(*)
FROM market_sweeps
WHERE completed_at >= :review_start AND completed_at < :review_end
GROUP BY cursor_complete;

-- 2. membership/observation 대사
WITH sweep_counts AS (
    SELECT s.sweep_id,
           s.raw_market_count,
           s.unique_condition_count,
           (SELECT COUNT(*) FROM market_sweep_memberships m
            WHERE m.sweep_id = s.sweep_id) AS membership_count,
           (SELECT COUNT(*) FROM market_observations o
            WHERE o.sweep_id = s.sweep_id) AS observation_count,
           (SELECT COUNT(DISTINCT m.condition_id)
            FROM market_sweep_memberships m
            WHERE m.sweep_id = s.sweep_id AND m.condition_id IS NOT NULL)
               AS observed_unique_condition_count
    FROM market_sweeps s
    WHERE s.completed_at >= :review_start AND s.completed_at < :review_end
)
SELECT *
FROM sweep_counts
WHERE membership_count <> raw_market_count
   OR observation_count <> raw_market_count
   OR observed_unique_condition_count <> unique_condition_count;

-- 3. outcome identity와 volume 의미 분리
SELECT COUNT(*) AS observations_without_outcomes
FROM market_observations m
WHERE m.page_received_at >= :review_start AND m.page_received_at < :review_end
  AND NOT EXISTS (
    SELECT 1 FROM outcome_observations o
    WHERE o.observation_id = m.observation_id
  );

SELECT COUNT(*) AS suspicious_volume_aliases
FROM market_observations
WHERE page_received_at >= :review_start AND page_received_at < :review_end
  AND volume_total IS NOT NULL AND volume_24h IS NOT NULL
  AND volume_total < volume_24h;

-- 4. sampled CLOB coverage와 오류
SELECT selection_reason, status, COUNT(*)
FROM orderbook_selections
WHERE selected_at >= :review_start AND selected_at < :review_end
GROUP BY selection_reason, status;

-- 5. resolution과 redeemable 분리
SELECT lookup_status, resolved, redeemable, COUNT(*)
FROM resolution_observations
WHERE observed_at >= :review_start AND observed_at < :review_end
GROUP BY lookup_status, resolved, redeemable;
```

Schema version이 바뀌면 column 이름을 임의로 추정하지 말고 해당 shard의
`collection_contracts`, schema migration note와 export manifest를 기준으로 query를 버전별로
분리한다.

## 전략 탐색용 dataset을 만들 때

원본 DB를 직접 수정하지 않는다. 다음 단계는 별도 read-only materialization이다.

1. verified complete sweeps만 고른다.
2. 시장 생성·관측 시점 당시 알 수 있었던 값만 사용한다. 미래 `closed`, 최종 outcome,
   사후 수정된 `endDate`로 과거 universe를 거르지 않는다.
3. source receipt time을 시간축으로 쓰고, sweep 완료 시각을 모든 market의 동일 관측시각으로
   소급하지 않는다.
4. 같은 event의 sibling markets를 같은 train/test split에 둔다.
5. Gamma reference price와 sampled CLOB executable quote를 구분한다. CLOB 표본은 기록된
   bucket/reason/inclusion coverage로 selection bias를 보고한다.
6. public trade tape는 verified complete window만 쓰고 `possible_gap`, watermark gap, source
   stabilization lag을 숨기지 않는다. polling row를 WebSocket의 모든 order-book tick으로 간주하지 않는다.
7. `volume_total`과 `volume_24h`를 바꿔 쓰지 않는다. missing을 0이나 forward-fill로 채우지 않는다.
8. +15m/+30m/+1h/+3h/+6h/+24h/+72h forward label, MFE/MAE, barrier 결과는 raw table이
   아니라 versioned derived artifact에 생성하고 cutoff/embargo/checksum을 남긴다.
9. resolution은 one-hot source evidence와 observed time을 기준으로 하며 redeemable 또는
   wallet redeem transaction을 resolution과 같은 사실로 취급하지 않는다.

## 무효화·중단 조건

다음 중 하나면 그 기간으로 threshold, liquidity, volume, sports, horizon을 최적화하지 않는다.

- 필요한 shard가 없거나 Daily Rsync verify가 실패했다.
- 성공으로 표시된 run의 cursor가 불완전하거나 membership/observation atomicity가 깨졌다.
- source receipt time 또는 outcome/token identity가 없다.
- CLOB selection reason/coverage 없이 sampled book을 전체 universe처럼 사용해야 한다.
- trade-flow 가설에 필요한 시간창이 `possible_gap`이거나 complete watermark로 덮이지 않는다.
- resolution backlog가 잘리거나 source-missing을 0 payout으로 대체해야 한다.
- 여러 collection contract/cadence/schema가 섞였는데 분리할 수 없다.
- disk watermark 때문에 일부 자료가 조용히 삭제·rollup되었다.

이 경우 결과는 “전략이 나쁘다”가 아니라 **evidence gap**이다. 먼저 수집·동기화·복구
계약을 고친 뒤 새로운 독립 기간을 고정한다.

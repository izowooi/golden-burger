# Golden Raspberry — Queue Echo 수집·가설 회고

> 성격: accountless hypothesis collector
> 거래 성과·confirmed fill: **N/A — source-level no-order**

이 문서는 displayed-depth 가설을 평가하기 위한 `queue-echo-v1` 수집 완전성과 `$5`
ask-to-bid counterfactual을 판정한다. 먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)를
읽되, 거래 봇의 order/fill audit를 이 DB에 적용하지 않는다.

## 리뷰 범위 고정

분석 전에 다음 UTC half-open range와 실제 Daily Rsync 증거를 기록한다.

```text
REVIEW_START=<YYYY-MM-DDTHH:MM:SSZ>
REVIEW_END=<YYYY-MM-DDTHH:MM:SSZ>  # exclusive
TIMEZONE=UTC
JENKINS_JOBS=polybot-do,polybot-re,polybot-mi
RUNTIME_JOBS=raspberry-do-shard-0,raspberry-re-shard-1,raspberry-mi-shard-2
```

각 Jenkins job에 대해 `daily-rsync scan`으로 현재 strategy/runtime을 확인하고 별도 plan으로
동기화한다. `locate`·`verify`가 반환한 canonical DB 절대 경로, `local_sha256`, remote path,
latest successful sync 시각과 source cutoff를 모두 남긴다. 세 DB 중 하나라도 없거나 verify가
실패하면 성과 분석을 중단한다.

## 분석 명령

```bash
cd golden-raspberry
uv run python scripts/analyze_experiment.py \
  --start "$REVIEW_START" --end "$REVIEW_END" \
  --db DO=/absolute/verified/do/trades_sim.db \
  --db RE=/absolute/verified/re/trades_sim.db \
  --db MI=/absolute/verified/mi/trades_sim.db \
  --output /absolute/review/queue-echo-analysis.json
```

파일명 label은 Jenkins 별칭일 뿐 experimental arm이 아니다. 각 hash shard가 DO·RE·MI를
모두 계산하며 MI 3회 지속만 primary다. 분석기는 DB를 `mode=ro&immutable=1`로 열고 세
shard의 결과 행을 합쳐 event-cluster bootstrap을 한 번 수행한다.

## 24시간·7일 health gate

24시간에는 수익성이나 threshold를 판단하지 않는다. 다음을 확인해 계측 결함만 수정한다.

- 세 shard의 SUCCESS cadence, duplicate/off-slot과 cross-shard condition overlap
- Gamma terminal cursor와 successful sweep의 atomic publish
- 요청한 YES/NO token pair coverage, 동일 request ID 100%와 raw CLOB payload linkage
- 단일 `config_hash × strategy_source_digest × mode × job_name` cohort
- DO/RE/MI history gap·cooldown·entry clock lineage
- 60~75분 follow-up censoring과 neutral/opposite control missingness
- `PRAGMA quick_check`, CRITICAL/HIGH issue, runtime p95/max, disk growth

7 complete UTC day에도 collection health만 확정한다. expected slot coverage 95% 미만,
pair-token coverage 95% 미만, raw linkage 100% 미만, p95 180초 이상, max 240초 이상,
CRITICAL/HIGH issue 또는 여러 cohort가 있으면 결과는 evidence gap이다. 먼저 수집기를 고치고
최종 healthy cohort의 시작점을 다시 고정한다.

## 30일 MI 판정

단일 healthy cohort 30일, quote-complete MI signal 50개, event cluster 30개, 20 UTC day,
outcome coverage 90%, neutral match 80%를 모두 요구한다. seed `20260813`, 20,000회
event-cluster bootstrap의 familywise 98.33% lower bound가 raw·10.4bps·72.5bps stress에서
모두 양수이고, SIGNAL−neutral 95% lower bound와 전반/후반 severe-stress 평균도 양수여야 한다.

하나라도 실패하면 `STOP_UNRESEARCHABLE`이다. DO/RE를 사후 primary로 고르거나 같은 자료에서
score, volume, horizon, gap을 완화하지 않는다. 통과해도 `SHADOW_REVIEW_ONLY`이며 실거래
승인이 아니다. live 검정은 별도 프로젝트·risk budget·execution evidence·새 사전등록을
필요로 한다.

MI가 instantaneous DO보다 persistence 정보를 추가했다는 별도 진술은 MI까지 도달한 같은
episode의 severe-stress `MI−DO`를 event별로 pair한 95% lower bound가 양수일 때만 허용한다.

## 무효화 조건

- source receipt 시각, token identity, raw payload 또는 terminal cursor가 없다.
- missing/insufficient follow-up을 0, 마지막 가격, resolution payout으로 채워야 한다.
- 세 DB를 job별 arm처럼 비교하거나 다른 request 시각의 missingness를 treatment로 해석한다.
- 여러 source/config cohort를 분리하지 못한다.
- append-only row가 UPDATE/DELETE됐거나 disk guard로 evidence를 조용히 줄였다.

이 경우 “가설 실패”가 아니라 **수집 증거 실패**로 기록하고 새 독립 기간을 시작한다.

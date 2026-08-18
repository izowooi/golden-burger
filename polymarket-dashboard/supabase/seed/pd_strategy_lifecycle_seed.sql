-- Idempotent seed for the lifecycle dashboard.
-- Evidence dates are UTC and come from repository strategy contracts, retrospectives,
-- and read-only Jenkins inspection. Runtime build health is intentionally populated by
-- scripts/sync-jenkins-status.mjs instead of this file.

insert into public.pd_strategies (
  strategy_id,
  display_name,
  codename,
  thesis,
  strategy_kind,
  lifecycle_stage,
  operating_status,
  evaluation_started_at,
  phase_started_at,
  evaluation_horizon_days,
  current_summary,
  attention_note,
  source_ref,
  hidden_by_default,
  sort_order
)
values
  (
    'golden-strawberry', 'Golden Strawberry', 'Last Mile',
    '0.95 상향 교차 뒤 1.00 수렴과 0.85 이하 실패 경로를 함께 기록한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-15T04:00:00Z', '2026-08-15T04:00:00Z', 7,
    '계정·주문 없는 frozen 7일 관측 cohort를 외장 디스크에서 수집 중이다.',
    '첫 24시간 collection-health 검토가 기한을 넘겼다. 수익성 판단 전에 수집 계약부터 확인해야 한다.',
    'golden-strawberry/STRATEGY.md', false, 10
  ),
  (
    'golden-raspberry', 'Golden Raspberry', 'Queue Echo',
    'YES·NO book의 지속적 displayed-depth imbalance가 60분 뒤 실행가능 수익률을 예측하는지 검정한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-13T12:00:00Z', '2026-08-13T12:00:00Z', 30,
    'DO·RE·MI 세 독립 shard의 external-v2 confirmatory cohort를 수집 중이다.',
    null,
    'golden-raspberry/STRATEGY.md', false, 20
  ),
  (
    'golden-kiwi', 'Golden Kiwi', 'Micro-Cascade',
    '3~5회의 작은 연속 상승이 이후 60분에도 지속되는지 네 simulation arm으로 검정한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z', 30,
    '네 팔을 5분 offset cadence로 수집한다. live 실행은 source-level로 차단되어 있다.',
    '이전 OOS gate는 네 팔 모두 실패했다. 현재 cohort 결과를 과거 결과와 섞지 않는다.',
    'golden-kiwi/STRATEGY.md', false, 30
  ),
  (
    'golden-pomegranate', 'Golden Pomegranate', 'Market Observatory',
    '거래 없이 Gamma census·public trade tape·표본 CLOB book을 append-only로 보존한다.',
    'INFRA_RESEARCH', 'PRODUCTION', 'ACTIVE',
    '2026-08-06T00:00:00Z', '2026-08-07T00:00:00Z', null,
    '외장 APFS에서 accountless market observatory를 정기 운영 중이다.',
    '용량 증가와 shard backup/retention 계약을 월간으로 확인한다.',
    'golden-pomegranate/STRATEGY.md', false, 40
  ),
  (
    'golden-papaya', 'Golden Papaya', 'Final Five',
    '해결 72시간 이내 첫 0.95 상향 교차를 소액 live cohort로 반증한다.',
    'LIVE_TRADING', 'LIVE_VALIDATION', 'ACTIVE',
    '2026-08-12T12:17:10Z', '2026-08-12T12:17:10Z', 30,
    '24h·72h 두 시간축 live cohort가 실행 중이다.',
    null,
    'golden-papaya/STRATEGY.md', false, 100
  ),
  (
    'golden-queen', 'Golden Queen', 'Crown Momentum',
    '해결 직전 첫 0.90 상향 교차가 0.98 또는 resolution 1로 수렴하는지 검증한다.',
    'LIVE_TRADING', 'LIVE_VALIDATION', 'ACTIVE',
    '2026-08-12T12:02:01Z', '2026-08-12T12:02:01Z', 30,
    '12h·24h 두 진입 시간축을 소액 live로 비교 중이다.',
    null,
    'golden-queen/STRATEGY.md', false, 110
  ),
  (
    'golden-blueberry', 'Golden Blueberry', 'Closing Surge',
    '해결 임박 첫 0.85 교차의 직전 급등폭 +2pp와 +5pp가 선별력을 갖는지 비교한다.',
    'LIVE_TRADING', 'LIVE_VALIDATION', 'ACTIVE',
    '2026-08-12T17:30:14Z', '2026-08-12T17:30:14Z', 30,
    '두 live arm과 별도 shadow research job을 함께 운영한다.',
    null,
    'golden-blueberry/STRATEGY.md', false, 120
  ),
  (
    'golden-melon', 'Golden Melon', 'Resolution Sprint',
    '해결 임박 첫 0.85~0.93 상향 교차를 24h volume gate 세 수준으로 비교한다.',
    'LIVE_TRADING', 'LIVE_VALIDATION', 'ACTIVE',
    '2026-08-05T13:14:00Z', '2026-08-05T13:14:00Z', 30,
    'high·mid·low volume 세 live arm을 실행 중이며 day-7 저활동 회고를 완료했다.',
    '체결 표본이 아직 적어 day-30 전 파라미터 승자를 고르지 않는다.',
    'golden-melon/STRATEGY.md', false, 130
  ),
  (
    'golden-quince', 'Golden Quince', 'Spread Harvest',
    '같은 진입 신호에서 passive·nearest·cross 실행 방식만 바꿔 비용 차이를 검증한다.',
    'LIVE_TRADING', 'LIVE_VALIDATION', 'ACTIVE',
    '2026-08-12T15:26:26Z', '2026-08-12T15:26:26Z', 30,
    '세 execution arm을 $5 live cohort로 동시에 운영한다.',
    '첫 체결 표본이 충분해질 때까지 실행 방식의 승자를 선언할 수 없다.',
    'golden-quince/STRATEGY.md', false, 140
  ),
  (
    'golden-cherry', 'Golden Cherry', 'Resolution Momentum',
    '해결 임박한 높은 확률 YES의 사전 해결 수렴과 청산 규칙을 운용한다.',
    'LIVE_TRADING', 'STABILIZATION', 'ACTIVE',
    '2026-08-14T11:40:00Z', '2026-08-18T10:33:00Z', 30,
    'Yellow와 Orange가 서로 다른 수치로 같은 source 전략을 live 운용한다.',
    '두 job 모두 partial-fill 수량을 요청량으로 오인할 수 있는 lifecycle 결함이 남아 있다. Orange는 open position 100개 상한에도 도달했다.',
    'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md', false, 150
  ),
  (
    'golden-elderberry', 'Golden Elderberry', 'Panic Fade',
    'favorite 급락 뒤 안정화 구간의 과잉반응 되돌림을 노린다.',
    'LIVE_TRADING', 'STABILIZATION', 'CLOSE_ONLY',
    '2026-07-05T09:51:00Z', '2026-08-14T10:31:00Z', null,
    'polybot-cherry가 신규 진입 없이 기존 포지션 정리만 수행한다.',
    '과거 CLOB intent 대사 문제 이후 close-only 상태다. 8월 말 재검토 전 신규 진입을 열지 않는다.',
    'golden-elderberry/STRATEGY.md', false, 160
  ),
  (
    'golden-apple', 'Golden Apple', 'Probability Ladder',
    '0.80 이상 매수, 0.90 청산의 초기 확률 기반 전략이다.',
    'LEGACY_TRADING', 'IMPLEMENTED', 'INACTIVE',
    null, null, null,
    '코드는 보존되어 있으나 현재 이 전략 폴더를 실행하는 Jenkins job은 확인되지 않았다.',
    '계정의 Apple 표시는 현재 전략 배치를 뜻하지 않는다. Jenkins 실제 cd 경로로만 재배포 여부를 판단한다.',
    'golden-apple/README.md', false, 300
  ),
  (
    'golden-banana', 'Golden Banana', 'Golden Cross Momentum',
    '0.85~0.97 구간에서 장·단기 모멘텀 교차를 이용하는 초기 전략이다.',
    'LEGACY_TRADING', 'IMPLEMENTED', 'INACTIVE',
    null, null, null,
    '코드는 보존되어 있으나 현재 이 전략 폴더를 실행하는 Jenkins job은 확인되지 않았다.',
    'GOLDEN-BANANA 계정명과 polybot-yellow의 실제 golden-cherry 배치를 혼동하지 않는다.',
    'golden-banana/README.md', false, 310
  ),
  (
    'golden-grape', 'Golden Grape', 'Cascade Rider',
    '24h의 완만하고 일관된 드리프트와 거래량 가속 뒤 추가 이동을 노린다.',
    'LIVE_TRADING', 'IMPLEMENTED', 'PAUSED',
    null, null, null,
    '전략과 코드가 구현되어 있으나 현재 연결된 Jenkins 검증 job은 없다.',
    '검증을 재개하려면 별도 simulation cohort와 검토 일정을 먼저 등록해야 한다.',
    'golden-grape/STRATEGY.md', false, 320
  ),
  (
    'golden-orange', 'Golden Orange', 'Fear Spike Fade',
    'tail 시장의 공포 급등이 멈춘 뒤 NO를 매수해 감정 프리미엄 감쇠를 검증한다.',
    'LIVE_TRADING', 'IMPLEMENTED', 'PAUSED',
    null, null, null,
    '전략과 코드가 구현되어 있으나 현재 연결된 Jenkins 검증 job은 없다.',
    'polybot-orange는 이 전략이 아니라 golden-cherry를 실행한다. 이름만으로 연결하면 안 된다.',
    'golden-orange/STRATEGY.md', false, 330
  ),
  (
    'golden-date', 'Golden Date', 'Conviction Ladder',
    '남은 시간에 따른 확률 사다리로 favorite 수렴을 노렸던 전략이다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSE_ONLY',
    null, '2026-07-29T00:00:00Z', null,
    '가설은 폐쇄됐고 polybot-red는 잔여 계정 정리만 수행한다.',
    '신규 진입 금지. account-wide wind-down이 끝날 때까지만 close-only job을 유지한다.',
    'docs/retro/golden-date-2026-07-verdict.md', true, 900
  ),
  (
    'golden-fig', 'Golden Fig', 'Hope Crusher',
    '롱샷 YES의 시간가치 소멸을 NO 매수로 수확하려던 전략이다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSED',
    null, '2026-07-28T00:00:00Z', null,
    '실측 calibration edge가 음수여서 폐쇄했다.',
    null,
    'docs/retro/golden-mango-fig-2026-07-verdict.md', true, 910
  ),
  (
    'golden-honeydew', 'Golden Honeydew', 'Night Watch',
    '미국 새벽·주말의 비정보성 가격 이탈 복원을 노렸던 전략이다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSED',
    null, '2026-07-30T00:00:00Z', null,
    'strict confirmed-fill 성과와 주요 slice가 음수여서 폐쇄했다.',
    null,
    'docs/retro/golden-honeydew-2026-07-verdict.md', true, 920
  ),
  (
    'golden-lime', 'Golden Lime', 'Shock Follow',
    '거래량을 동반한 급등의 추가 지속을 추종하려던 전략이다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSED',
    null, '2026-07-28T00:00:00Z', null,
    '백테스트에서 가설이 기각되어 폐쇄했다.',
    'polybot-lime은 이 폐쇄 전략이 아니라 golden-melon의 mid arm을 실행한다.',
    'docs/retro/golden-lime-2026-07-backtest-verdict.md', true, 930
  ),
  (
    'golden-mango', 'Golden Mango', 'Patience Premium',
    '해결 임박 계약의 settlement discount를 연환산 carry로 선별하려던 전략이다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSED',
    null, '2026-07-28T00:00:00Z', null,
    'carry hurdle이 시간가치가 아니라 손실확률을 탐지해 폐쇄했다.',
    null,
    'docs/retro/golden-mango-fig-2026-07-verdict.md', true, 940
  ),
  (
    'golden-nectarine', 'Golden Nectarine', 'Bottom Fisher',
    '20일 롤링 최저가를 매수해 5일 뒤 청산하는 평균회귀 전략이었다.',
    'LIVE_TRADING', 'CLOSED', 'CLOSED',
    null, '2026-07-30T00:00:00Z', null,
    '대사된 120h subset이 음수여서 폐쇄했다.',
    null,
    'docs/retro/golden-nectarine-2026-07-verdict.md', true, 950
  )
on conflict (strategy_id) do update set
  display_name = excluded.display_name,
  codename = excluded.codename,
  thesis = excluded.thesis,
  strategy_kind = excluded.strategy_kind,
  lifecycle_stage = excluded.lifecycle_stage,
  operating_status = excluded.operating_status,
  evaluation_started_at = excluded.evaluation_started_at,
  phase_started_at = excluded.phase_started_at,
  evaluation_horizon_days = excluded.evaluation_horizon_days,
  current_summary = excluded.current_summary,
  attention_note = excluded.attention_note,
  source_ref = excluded.source_ref,
  hidden_by_default = excluded.hidden_by_default,
  sort_order = excluded.sort_order;

insert into public.pd_jenkins_jobs (
  job_name,
  strategy_id,
  runtime_job,
  mode,
  treatment_label,
  schedule,
  expected_cadence_minutes,
  workspace_class,
  notes
)
values
  ('polybot-cat', 'golden-papaya', 'papaya-live-24h', 'LIVE', '24h', 'H/10 * * * *', 10, 'INTERNAL', 'Final Five 24h arm'),
  ('polybot-dog', 'golden-papaya', 'papaya-live-72h', 'LIVE', '72h', 'H/10 * * * *', 10, 'INTERNAL', 'Final Five 72h arm'),
  ('polybot-queen', 'golden-queen', 'queen-live-24h', 'LIVE', '24h', 'H/5 * * * *', 5, 'INTERNAL', 'Crown Momentum 24h arm'),
  ('polybot-king', 'golden-queen', 'queen-live-12h', 'LIVE', '12h', 'H/5 * * * *', 5, 'INTERNAL', 'Crown Momentum 12h arm'),
  ('polybot-eagle', 'golden-blueberry', 'blueberry-live-a-2pp', 'LIVE', '+2pp', '*/5 * * * *', 5, 'INTERNAL', 'Closing Surge A arm'),
  ('polybot-fox', 'golden-blueberry', 'blueberry-live-b-5pp', 'LIVE', '+5pp', '*/5 * * * *', 5, 'INTERNAL', 'Closing Surge B arm'),
  ('polybot-shadow', 'golden-blueberry', 'blueberry-shadow-research', 'SHADOW', 'shadow evidence', 'H/5 * * * *', 5, 'EXTERNAL', 'Large accountless shadow archive on external storage'),
  ('polybot-fruit', 'golden-melon', 'polybot-melon-high', 'LIVE', 'volume high', 'H/5 * * * *', 5, 'INTERNAL', 'Resolution Sprint high-volume arm'),
  ('polybot-lime', 'golden-melon', 'polybot-melon-mid', 'LIVE', 'volume mid', 'H/5 * * * *', 5, 'INTERNAL', 'Job name does not mean golden-lime'),
  ('polybot-wolf', 'golden-melon', 'polybot-melon-low', 'LIVE', 'volume low', 'H/5 * * * *', 5, 'INTERNAL', 'Resolution Sprint low-volume arm'),
  ('polybot-bear', 'golden-quince', 'polybot-quince-passive', 'LIVE', 'passive', 'H/5 * * * *', 5, 'INTERNAL', 'Spread Harvest passive arm'),
  ('polybot-eco', 'golden-quince', 'polybot-quince-nearest', 'LIVE', 'nearest', 'H/5 * * * *', 5, 'INTERNAL', 'Spread Harvest nearest arm'),
  ('polybot-tiger', 'golden-quince', 'polybot-quince-cross', 'LIVE', 'cross', 'H/5 * * * *', 5, 'INTERNAL', 'Spread Harvest cross arm'),
  ('polybot-yellow', 'golden-cherry', 'default', 'LIVE', 'baseline', 'H/5 * * * *', 5, 'INTERNAL', 'Golden Banana account currently runs golden-cherry'),
  ('polybot-orange', 'golden-cherry', 'default', 'LIVE', 'adjusted thresholds', 'H/5 * * * *', 5, 'INTERNAL', 'Job name does not mean golden-orange'),
  ('polybot-cherry', 'golden-elderberry', 'default', 'CLOSE_ONLY', 'wind-down', 'H/5 * * * *', 5, 'INTERNAL', 'Panic Fade close-only'),
  ('polybot-red', 'golden-date', 'default', 'CLOSE_ONLY', 'wind-down', 'H/5 * * * *', 5, 'INTERNAL', 'Closed Date account wind-down'),
  ('golden-pomegranate', 'golden-pomegranate', 'pomegranate-15m-v2', 'RESEARCH', 'market observatory', 'H/15 * * * *', 15, 'EXTERNAL', 'Credential-free accountless collector'),
  ('polybot-kiwi-a', 'golden-kiwi', 'kiwi-sim-a-3x1', 'SIMULATION', '3-step / +1pp', '0-59/5 * * * *', 5, 'INTERNAL', 'Research-only arm A'),
  ('polybot-kiwi-b', 'golden-kiwi', 'kiwi-sim-b-3x2', 'SIMULATION', '3-step / +2pp', '1-59/5 * * * *', 5, 'INTERNAL', 'Research-only arm B'),
  ('polybot-kiwi-c', 'golden-kiwi', 'kiwi-sim-c-5x1', 'SIMULATION', '5-step / +1pp', '2-59/5 * * * *', 5, 'INTERNAL', 'Research-only arm C'),
  ('polybot-kiwi-d', 'golden-kiwi', 'kiwi-sim-d-5x2', 'SIMULATION', '5-step / +2pp', '3-59/5 * * * *', 5, 'INTERNAL', 'Research-only arm D'),
  ('polybot-do', 'golden-raspberry', 'raspberry-do-shard-0', 'RESEARCH', 'DO / 1 observation', '0-59/5 * * * *', 5, 'EXTERNAL', 'Queue Echo shard 0'),
  ('polybot-re', 'golden-raspberry', 'raspberry-re-shard-1', 'RESEARCH', 'RE / 2 observations', '1-59/5 * * * *', 5, 'EXTERNAL', 'Queue Echo shard 1'),
  ('polybot-mi', 'golden-raspberry', 'raspberry-mi-shard-2', 'RESEARCH', 'MI / 3 observations', '2-59/5 * * * *', 5, 'EXTERNAL', 'Queue Echo primary shard 2'),
  ('polybot-shadow-one', 'golden-strawberry', 'strawberry-shadow-one', 'RESEARCH', 'frozen last-mile cohort', '7-59/10 * * * *', 10, 'EXTERNAL', 'Credential-free Last Mile collector')
on conflict (job_name) do update set
  strategy_id = excluded.strategy_id,
  runtime_job = excluded.runtime_job,
  mode = excluded.mode,
  treatment_label = excluded.treatment_label,
  schedule = excluded.schedule,
  expected_cadence_minutes = excluded.expected_cadence_minutes,
  workspace_class = excluded.workspace_class,
  notes = excluded.notes;

insert into public.pd_strategy_checkpoints (
  checkpoint_id,
  strategy_id,
  checkpoint_type,
  title,
  due_at,
  status,
  completed_at,
  instructions,
  source_ref
)
values
  ('strawberry:first24:20260816', 'golden-strawberry', 'COLLECTION_HEALTH', '첫 24시간 collection health', '2026-08-16T04:00:00Z', 'PENDING', null, 'cadence, cursor/membership, crossing book, Gamma metadata, path/resolution, cohort, DB 무결성과 저장공간 증가량만 검증한다.', 'golden-strawberry/STRATEGY.md'),
  ('strawberry:day7:20260822', 'golden-strawberry', 'DAY_7_REVIEW', 'Frozen entry window 종료 검토', '2026-08-22T04:00:00Z', 'PENDING', null, 'collection validity를 먼저 판정하고, 수익성 결론은 resolution coverage가 충분할 때까지 보류한다.', 'golden-strawberry/STRATEGY.md'),
  ('strawberry:resolution:20260921', 'golden-strawberry', 'DAY_30_REVIEW', 'Resolution follow-up 종료', '2026-09-21T04:00:00Z', 'PENDING', null, 'terminal Gamma payout과 경로 coverage를 확정하고 frozen cohort를 판정한다.', 'golden-strawberry/STRATEGY.md'),
  ('raspberry:first24:20260814', 'golden-raspberry', 'COLLECTION_HEALTH', 'External-v2 첫 24시간 health', '2026-08-14T12:00:00Z', 'PENDING', null, '세 shard cadence와 pair/follow-up/control/cohort/DB를 정확한 24시간 범위로 확인한다.', 'docs/retro-summaries/019-golden-raspberry-external-workspace-restart-2026-08-13.md'),
  ('raspberry:day7:20260820', 'golden-raspberry', 'DAY_7_REVIEW', 'Queue Echo 7일 collection review', '2026-08-20T12:00:00Z', 'PENDING', null, '세 shard의 evidence completeness와 neutral/opposite control을 검토한다. threshold는 바꾸지 않는다.', 'golden-raspberry/STRATEGY.md'),
  ('raspberry:day30:20260912', 'golden-raspberry', 'DAY_30_REVIEW', 'Queue Echo frozen cohort 판정', '2026-09-12T12:00:00Z', 'PENDING', null, '사전 등록 gate로만 판정하고 DO/RE를 사후 primary로 바꾸지 않는다.', 'golden-raspberry/STRATEGY.md'),
  ('kiwi:day7:20260820', 'golden-kiwi', 'DAY_7_REVIEW', 'Filtered universe 7일 health', '2026-08-20T00:00:00Z', 'PENDING', null, '5분 cadence와 runtime, 네 팔의 동일 source cohort, follow-up coverage를 확인한다.', 'golden-kiwi/STRATEGY.md'),
  ('kiwi:day30:20260912', 'golden-kiwi', 'DAY_30_REVIEW', 'Micro-Cascade 독립 검정', '2026-09-12T00:00:00Z', 'PENDING', null, 'frozen primary B gate를 그대로 적용한다. 통과해도 shadow review만 허용한다.', 'golden-kiwi/STRATEGY.md'),
  ('pomegranate:monthly:20260907', 'golden-pomegranate', 'MONTHLY_REVIEW', 'Observatory 월간 capacity review', '2026-09-07T00:00:00Z', 'PENDING', null, 'daily shard, checksum, backup restore, source component coverage와 외장 디스크 증가량을 확인한다.', 'golden-pomegranate/STRATEGY.md'),
  ('papaya:day7:20260819', 'golden-papaya', 'DAY_7_REVIEW', 'Final Five 7일 운영 검토', '2026-08-19T12:17:10Z', 'PENDING', null, '두 시간축의 cadence, confirmed fill, lifecycle completeness와 표본 수만 확인한다.', 'golden-papaya/STRATEGY.md'),
  ('papaya:day30:20260911', 'golden-papaya', 'DAY_30_REVIEW', 'Final Five 30일 판정', '2026-09-11T12:17:10Z', 'PENDING', null, '단일 cohort와 strict confirmed-fill evidence로 수익성·승격 여부를 판정한다.', 'golden-papaya/STRATEGY.md'),
  ('queen:day7:20260819', 'golden-queen', 'DAY_7_REVIEW', 'Crown Momentum 7일 운영 검토', '2026-08-19T12:02:01Z', 'PENDING', null, '12h·24h cadence와 첫 crossing/confirmed fill lifecycle을 확인한다.', 'golden-queen/STRATEGY.md'),
  ('queen:day30:20260911', 'golden-queen', 'DAY_30_REVIEW', 'Crown Momentum 30일 판정', '2026-09-11T12:02:01Z', 'PENDING', null, 'strict evidence로 12h·24h를 비교하고 동시 파라미터 변경을 피한다.', 'golden-queen/STRATEGY.md'),
  ('blueberry:day7:20260819', 'golden-blueberry', 'DAY_7_REVIEW', 'Closing Surge 7일 운영 검토', '2026-08-19T17:30:14Z', 'PENDING', null, '+2pp·+5pp arm의 cadence와 exact lifecycle, shadow evidence coverage를 확인한다.', 'golden-blueberry/STRATEGY.md'),
  ('blueberry:day30:20260911', 'golden-blueberry', 'DAY_30_REVIEW', 'Closing Surge 30일 판정', '2026-09-11T17:30:14Z', 'PENDING', null, '두 arm의 confirmed-fill 표본과 비용 후 기대값을 사전 등록 gate로 비교한다.', 'golden-blueberry/STRATEGY.md'),
  ('melon:day7:20260812', 'golden-melon', 'DAY_7_REVIEW', 'Resolution Sprint 저활동 검토', '2026-08-12T13:14:00Z', 'COMPLETED', '2026-08-14T21:09:00Z', '세 volume arm의 저활동 원인과 evidence gap을 점검했다.', 'docs/retro-summaries/024-quince-melon-live-review-2026-08-15.md'),
  ('melon:day30:20260904', 'golden-melon', 'DAY_30_REVIEW', 'Resolution Sprint 30일 판정', '2026-09-04T13:14:00Z', 'PENDING', null, 'high·mid·low volume arm을 단일 cohort confirmed-fill 기준으로 비교한다.', 'golden-melon/STRATEGY.md'),
  ('quince:day7:20260819', 'golden-quince', 'DAY_7_REVIEW', 'Spread Harvest 7일 execution review', '2026-08-19T15:26:26Z', 'PENDING', null, 'passive·nearest·cross의 후보/주문/체결 coverage와 비용만 비교한다.', 'golden-quince/STRATEGY.md'),
  ('quince:day30:20260911', 'golden-quince', 'DAY_30_REVIEW', 'Spread Harvest 30일 판정', '2026-09-11T15:26:26Z', 'PENDING', null, 'maker/taker 처치축 외 신호 파라미터를 동시에 바꾸지 않고 판정한다.', 'golden-quince/STRATEGY.md'),
  ('cherry:stability:20260818', 'golden-cherry', 'STABILITY_GATE', 'Partial-fill lifecycle 결함 해소', '2026-08-18T12:00:00Z', 'PENDING', null, 'Yellow·Orange의 partially filled BUY/SELL을 exact fill size로 전환하고 PENDING 고착·open cap을 재검증한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('cherry:day7:20260821', 'golden-cherry', 'DAY_7_REVIEW', '재활성 cohort 7일 안정성 검토', '2026-08-21T11:40:00Z', 'PENDING', null, 'lifecycle 결함 해소 뒤 single-cohort cadence와 fill coverage를 먼저 확인한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('cherry:day30:20260913', 'golden-cherry', 'DAY_30_REVIEW', 'Yellow·Orange 파라미터 비교', '2026-09-13T11:40:00Z', 'PENDING', null, '동일 source의 파라미터 차이를 strict fill evidence와 counterfactual sweep으로 비교한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('elderberry:monthend:20260830', 'golden-elderberry', 'STABILITY_GATE', 'Panic Fade close-only 재검토', '2026-08-30T15:00:00Z', 'PENDING', null, '잔여 position·intent 대사를 확인하고 신규 진입 재개 또는 폐쇄를 결정한다.', 'golden-elderberry/STRATEGY.md'),
  ('apple:deployment', 'golden-apple', 'DEPLOYMENT_DECISION', '현재 배치 여부 결정', null, 'BLOCKED', null, '새 검증 가설과 Jenkins job을 지정하기 전에는 운영 상태를 inactive로 유지한다.', 'golden-apple/README.md'),
  ('banana:deployment', 'golden-banana', 'DEPLOYMENT_DECISION', '현재 배치 여부 결정', null, 'BLOCKED', null, '계정명과 전략 배치를 분리하고 새 cohort를 사전 등록한 뒤 재검증한다.', 'golden-banana/README.md'),
  ('grape:deployment', 'golden-grape', 'DEPLOYMENT_DECISION', 'Simulation cohort 설계', null, 'BLOCKED', null, 'Jenkins 배치 전에 독립 시간구간과 evidence gate를 등록한다.', 'golden-grape/STRATEGY.md'),
  ('orange:deployment', 'golden-orange', 'DEPLOYMENT_DECISION', 'Simulation cohort 설계', null, 'BLOCKED', null, 'polybot-orange 이름과 분리된 새 job·cohort를 등록해야 한다.', 'golden-orange/STRATEGY.md')
on conflict (checkpoint_id) do update set
  strategy_id = excluded.strategy_id,
  checkpoint_type = excluded.checkpoint_type,
  title = excluded.title,
  due_at = excluded.due_at,
  status = excluded.status,
  completed_at = excluded.completed_at,
  instructions = excluded.instructions,
  source_ref = excluded.source_ref;

update public.pd_strategies
set attention_level = case strategy_id
  when 'golden-strawberry' then 'CRITICAL'
  when 'golden-raspberry' then 'INFO'
  when 'golden-kiwi' then 'WATCH'
  when 'golden-pomegranate' then 'INFO'
  when 'golden-melon' then 'INFO'
  when 'golden-quince' then 'INFO'
  when 'golden-cherry' then 'CRITICAL'
  when 'golden-elderberry' then 'CRITICAL'
  when 'golden-apple' then 'INFO'
  when 'golden-banana' then 'INFO'
  when 'golden-grape' then 'INFO'
  when 'golden-orange' then 'WATCH'
  when 'golden-date' then 'CRITICAL'
  when 'golden-lime' then 'INFO'
  else 'NONE'
end;

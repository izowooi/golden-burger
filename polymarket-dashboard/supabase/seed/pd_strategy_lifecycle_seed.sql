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
    'golden-strawberry', 'Golden Strawberry', '0.95 돌파 후 가격 경로 수집',
    '시장 가격이 0.95를 처음 넘은 뒤 1.00 수렴, 0.85 하락, 최종 해결까지의 경로를 함께 기록한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-15T04:00:00Z', '2026-08-15T04:00:00Z', 7,
    '실제 주문 없이 7일 동안 신규 사례를 모으고, 이후 최종 해결 결과까지 추적한다.',
    '첫 24시간 collection-health 검토가 기한을 넘겼다. 수익성 판단 전에 수집 계약부터 확인해야 한다.',
    'golden-strawberry/STRATEGY.md', false, 10
  ),
  (
    'golden-raspberry', 'Golden Raspberry', '호가 잔량과 60분 뒤 가격 비교',
    'YES·NO 호가 잔량의 불균형이 60분 뒤 가격 움직임을 예측하는지 검증한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-13T12:00:00Z', '2026-08-13T12:00:00Z', 30,
    '외장 하드의 세 수집 구간에서 같은 검증 항목을 나눠 기록하고 있다.',
    null,
    'golden-raspberry/STRATEGY.md', false, 20
  ),
  (
    'golden-kiwi', 'Golden Kiwi', '연속 상승 후속 움직임 검증',
    '5분 간격의 작은 연속 상승이 60분 뒤에도 이어지는지 네 조건으로 검증한다.',
    'SIMULATION_RESEARCH', 'SIMULATION', 'ACTIVE',
    '2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z', 30,
    '연속 횟수와 누적 상승폭을 조합한 네 조건을 5분 간격으로 비교하고 있다.',
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
    '2026-08-05T13:13:37Z', '2026-08-05T13:13:37Z', 30,
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
    'LEGACY_TRADING', 'CLOSED', 'CLOSED',
    null, null, null,
    '사용자 확인으로 폐쇄됐다. 정확한 폐쇄일은 기록되지 않아 미정이다.',
    '폐쇄일은 확인되지 않았다.',
    'polymarket-dashboard/supabase/migrations/20260818150000_pd_golden_apple_closed_v1.sql', true, 300
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
  ('polybot-do', 'golden-raspberry', 'raspberry-do-shard-0', 'RESEARCH', '수집 구간 1/3', '0-59/5 * * * *', 5, 'EXTERNAL', '호가 잔량 검증 자료의 첫 번째 구간'),
  ('polybot-re', 'golden-raspberry', 'raspberry-re-shard-1', 'RESEARCH', '수집 구간 2/3', '1-59/5 * * * *', 5, 'EXTERNAL', '호가 잔량 검증 자료의 두 번째 구간'),
  ('polybot-mi', 'golden-raspberry', 'raspberry-mi-shard-2', 'RESEARCH', '수집 구간 3/3', '2-59/5 * * * *', 5, 'EXTERNAL', '호가 잔량 검증 자료의 세 번째 구간'),
  ('polybot-shadow-one', 'golden-strawberry', 'strawberry-shadow-one', 'RESEARCH', '0.95 돌파 경로 수집', '7-59/10 * * * *', 10, 'EXTERNAL', '실제 주문 없는 가격 경로 수집')
on conflict (job_name) do update set
  strategy_id = excluded.strategy_id,
  runtime_job = excluded.runtime_job,
  mode = excluded.mode,
  treatment_label = excluded.treatment_label,
  schedule = excluded.schedule,
  expected_cadence_minutes = excluded.expected_cadence_minutes,
  workspace_class = excluded.workspace_class,
  notes = excluded.notes;

update public.pd_jenkins_jobs as job
set
  treatment_label = valueset.treatment_label,
  purpose = valueset.purpose,
  test_size_label = valueset.test_size_label,
  experiment_started_at = valueset.started_at::timestamptz,
  experiment_ends_at = valueset.ends_at::timestamptz,
  notes = valueset.notes
from (
  values
    ('polybot-cat', '24시간', '해결까지 24시간 이내인 0.95 첫 돌파를 검증', '거래 $5 · 계정 $300', '2026-08-12T12:17:10Z', '2026-09-11T12:17:10Z', '2026-08-12 clean 옵션 제거 뒤 새 검증 시작'),
    ('polybot-dog', '72시간', '해결까지 72시간 이내인 0.95 첫 돌파를 검증', '거래 $5 · 계정 $300', '2026-08-12T12:17:26Z', '2026-09-11T12:17:26Z', '2026-08-12 clean 옵션 제거 뒤 새 검증 시작'),
    ('polybot-queen', '24시간', '해결까지 24시간 이내인 0.90 첫 돌파를 검증', '거래 $100 · 계정 $3,000', '2026-08-12T12:02:07Z', '2026-09-11T12:02:07Z', '2026-08-12 clean 옵션 제거 뒤 새 검증 시작'),
    ('polybot-king', '12시간', '해결까지 12시간 이내인 0.90 첫 돌파를 검증', '거래 $100 · 계정 $3,000', '2026-08-12T12:02:01Z', '2026-09-11T12:02:01Z', '2026-08-12 clean 옵션 제거 뒤 새 검증 시작'),
    ('polybot-eagle', '급등폭 +2%p', '직전 가격보다 최소 2%p 급등한 후보를 실거래로 검증', '거래 $5 · 실험 한도 $150', '2026-08-05T13:13:37Z', '2026-09-04T13:13:37Z', 'Blueberry의 완화 조건'),
    ('polybot-fox', '급등폭 +5%p', '직전 가격보다 최소 5%p 급등한 후보를 실거래로 검증', '거래 $5 · 실험 한도 $150', '2026-08-05T13:13:47Z', '2026-09-04T13:13:47Z', 'Blueberry의 엄격 조건'),
    ('polybot-shadow', '자료 수집', 'Blueberry 후보의 가격·호가·해결 경로를 실제 주문 없이 수집', '실거래 없음', null, '2026-09-04T13:13:47Z', 'Blueberry 30일 검증 종료일까지 외장 하드에 보조 자료 수집'),
    ('polybot-fruit', '거래량 높음', '24시간 거래량 기준을 높게 둔 조건을 검증', '거래 $5 · 계정 $300', '2026-08-05T13:14:21Z', '2026-09-04T13:14:21Z', '계정 별칭은 fox, Jenkins 이름은 polybot-fruit'),
    ('polybot-lime', '거래량 중간', '24시간 거래량 기준을 중간으로 둔 조건을 검증', '거래 $5 · 계정 $300', '2026-08-05T13:14:35Z', '2026-09-04T13:14:35Z', 'Jenkins 이름과 golden-lime 전략을 혼동하지 않음'),
    ('polybot-wolf', '거래량 낮음', '24시간 거래량 기준을 낮게 둔 조건을 검증', '거래 $5 · 계정 $780', '2026-08-05T13:14:45Z', '2026-09-04T13:14:45Z', 'Melon의 낮은 거래량 조건'),
    ('polybot-bear', '지정가 대기', '호가를 기다리는 주문 방식의 체결 비용을 검증', '거래 $5 · 계정 $300', '2026-08-12T15:26:26Z', '2026-09-11T15:26:26Z', '2026-08-13 KST 새 DB로 검증 시작'),
    ('polybot-eco', '근접 지정가', '현재 호가에 가까운 지정가 방식의 체결 비용을 검증', '거래 $5 · 계정 $300', '2026-08-12T15:26:32Z', '2026-09-11T15:26:32Z', '2026-08-13 KST 새 DB로 검증 시작'),
    ('polybot-tiger', '즉시 체결', '즉시 체결하는 주문 방식의 체결 비용을 검증', '거래 $5 · 계정 $300', '2026-08-12T15:26:34Z', '2026-09-11T15:26:34Z', '2026-08-13 KST 새 DB로 검증 시작'),
    ('polybot-yellow', '기준값', 'Golden Cherry의 기준 수치로 실제 거래와 청산을 운영', '실거래', '2026-08-14T11:40:00Z', '2026-09-13T11:40:00Z', 'Jenkins 이름과 계정명은 전략 폴더명과 다름'),
    ('polybot-orange', '조정값', 'Golden Cherry에서 진입·청산 수치를 조정한 조건을 비교', '실거래', '2026-08-14T11:40:00Z', '2026-09-13T11:40:00Z', 'Golden Orange 전략이 아니라 Golden Cherry를 실행'),
    ('polybot-cherry', '청산 전용', '기존 Elderberry 포지션만 정리하고 신규 진입은 받지 않음', '신규 거래 없음', '2026-07-05T09:51:00Z', '2026-08-30T15:00:00Z', '8월 말에 재개 또는 폐쇄 검토'),
    ('polybot-red', '폐쇄 정리', '폐쇄된 Date 전략 계정의 잔여 포지션만 정리', '신규 거래 없음', null, null, 'Date 전략은 폐쇄됨'),
    ('golden-pomegranate', '공개 시장 상시 수집', '공개 시장·체결·표본 호가를 거래 없이 장기 보존', '실거래 없음', '2026-08-06T00:00:00Z', null, '종료일 없이 운영하며 월간 저장공간 점검'),
    ('polybot-kiwi-a', '3회 · +1%p', '3회 연속 상승과 누적 +1%p 조건을 가상 검증', '가상 $5 · 실거래 없음', '2026-08-13T00:00:00Z', '2026-09-12T00:00:00Z', '실거래 실행은 코드에서 차단'),
    ('polybot-kiwi-b', '3회 · +2%p', '3회 연속 상승과 누적 +2%p 조건을 가상 검증', '가상 $5 · 실거래 없음', '2026-08-13T00:00:00Z', '2026-09-12T00:00:00Z', '실거래 실행은 코드에서 차단'),
    ('polybot-kiwi-c', '5회 · +1%p', '5회 연속 상승과 누적 +1%p 조건을 가상 검증', '가상 $5 · 실거래 없음', '2026-08-13T00:00:00Z', '2026-09-12T00:00:00Z', '실거래 실행은 코드에서 차단'),
    ('polybot-kiwi-d', '5회 · +2%p', '5회 연속 상승과 누적 +2%p 조건을 가상 검증', '가상 $5 · 실거래 없음', '2026-08-13T00:00:00Z', '2026-09-12T00:00:00Z', '실거래 실행은 코드에서 차단'),
    ('polybot-do', '수집 구간 1/3', '전체 시장을 세 구간으로 나눈 첫 번째 자료 수집', '가상 $5 · 실거래 없음', '2026-08-13T12:00:00Z', '2026-09-12T12:00:00Z', '세 구간 모두 동일한 검증 조건을 계산'),
    ('polybot-re', '수집 구간 2/3', '전체 시장을 세 구간으로 나눈 두 번째 자료 수집', '가상 $5 · 실거래 없음', '2026-08-13T12:00:00Z', '2026-09-12T12:00:00Z', '세 구간 모두 동일한 검증 조건을 계산'),
    ('polybot-mi', '수집 구간 3/3', '전체 시장을 세 구간으로 나눈 세 번째 자료 수집', '가상 $5 · 실거래 없음', '2026-08-13T12:00:00Z', '2026-09-12T12:00:00Z', '세 구간 모두 동일한 검증 조건을 계산'),
    ('polybot-shadow-one', '0.95 돌파 경로 수집', '0.95 첫 돌파 뒤 상승·하락·최종 해결 경로를 실제 주문 없이 수집', '가상 $5 · 실거래 없음', '2026-08-15T04:00:00Z', '2026-09-21T04:00:00Z', '신규 사례는 2026-08-22까지, 기존 사례의 최종 결과는 2026-09-21까지 추적')
) as valueset(job_name, treatment_label, purpose, test_size_label, started_at, ends_at, notes)
where job.job_name = valueset.job_name;

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
  ('strawberry:first24:20260816', 'golden-strawberry', 'COLLECTION_HEALTH', '첫 24시간 수집 상태 확인', '2026-08-16T04:00:00Z', 'PENDING', null, '수집 주기, 시장 목록, 호가, 시장 정보, 가격 경로, 최종 결과, DB 무결성과 저장공간 증가량만 확인한다.', 'golden-strawberry/STRATEGY.md'),
  ('strawberry:day7:20260822', 'golden-strawberry', 'DAY_7_REVIEW', '신규 사례 수집 종료', '2026-08-22T04:00:00Z', 'PENDING', null, '수집 데이터가 빠짐없는지 먼저 확인하고 최종 결과가 충분히 모일 때까지 수익성 결론은 보류한다.', 'golden-strawberry/STRATEGY.md'),
  ('strawberry:resolution:20260921', 'golden-strawberry', 'DAY_30_REVIEW', '최종 결과 추적 종료', '2026-09-21T04:00:00Z', 'PENDING', null, '각 시장의 최종 결과와 전체 가격 경로를 확인해 고정된 검증 집단을 판정한다.', 'golden-strawberry/STRATEGY.md'),
  ('raspberry:first24:20260814', 'golden-raspberry', 'COLLECTION_HEALTH', '첫 24시간 수집 상태 확인', '2026-08-14T12:00:00Z', 'PENDING', null, '세 수집 구간의 실행 주기, YES·NO 짝, 후속 가격, 비교군과 DB 무결성을 확인한다.', 'docs/retro-summaries/019-golden-raspberry-external-workspace-restart-2026-08-13.md'),
  ('raspberry:day7:20260820', 'golden-raspberry', 'DAY_7_REVIEW', '7일 데이터 품질 확인', '2026-08-20T12:00:00Z', 'PENDING', null, '세 수집 구간의 누락과 비교군을 확인하며 검증 기준은 바꾸지 않는다.', 'golden-raspberry/STRATEGY.md'),
  ('raspberry:day30:20260912', 'golden-raspberry', 'DAY_30_REVIEW', '30일 가설 판정', '2026-09-12T12:00:00Z', 'PENDING', null, '미리 정한 기준으로만 가설을 판정하고 결과를 본 뒤 대표 조건을 바꾸지 않는다.', 'golden-raspberry/STRATEGY.md'),
  ('kiwi:day7:20260820', 'golden-kiwi', 'DAY_7_REVIEW', '7일 데이터 품질 확인', '2026-08-20T00:00:00Z', 'PENDING', null, '5분 실행 주기와 네 조건의 동일한 데이터 범위, 60분 뒤 가격 기록을 확인한다.', 'golden-kiwi/STRATEGY.md'),
  ('kiwi:day30:20260912', 'golden-kiwi', 'DAY_30_REVIEW', '30일 가설 판정', '2026-09-12T00:00:00Z', 'PENDING', null, '미리 정한 대표 조건을 그대로 판정하며 통과해도 실제 거래 전 추가 검토가 필요하다.', 'golden-kiwi/STRATEGY.md'),
  ('pomegranate:monthly:20260907', 'golden-pomegranate', 'MONTHLY_REVIEW', '월간 저장공간·백업 확인', '2026-09-07T00:00:00Z', 'PENDING', null, '일별 DB, 체크섬, 백업 복구, 수집 항목 누락과 외장 디스크 증가량을 확인한다.', 'golden-pomegranate/STRATEGY.md'),
  ('papaya:day7:20260819', 'golden-papaya', 'DAY_7_REVIEW', '7일 체결·운영 확인', '2026-08-19T12:17:10Z', 'PENDING', null, '24시간·72시간 조건의 실행 주기, 실제 체결, 주문 상태와 표본 수를 확인한다.', 'golden-papaya/STRATEGY.md'),
  ('papaya:day30:20260911', 'golden-papaya', 'DAY_30_REVIEW', '30일 최종 판정', '2026-09-11T12:17:10Z', 'PENDING', null, '검증 기간을 섞지 않고 확인된 실제 체결만으로 안정화 또는 폐쇄를 판정한다.', 'golden-papaya/STRATEGY.md'),
  ('queen:day7:20260819', 'golden-queen', 'DAY_7_REVIEW', '7일 체결·운영 확인', '2026-08-19T12:02:01Z', 'PENDING', null, '12시간·24시간 조건의 실행 주기와 첫 돌파 이후 주문·체결 상태를 확인한다.', 'golden-queen/STRATEGY.md'),
  ('queen:day30:20260911', 'golden-queen', 'DAY_30_REVIEW', '30일 최종 판정', '2026-09-11T12:02:01Z', 'PENDING', null, '확인된 실제 체결로 두 시간 조건을 비교하며 중간에 여러 수치를 함께 바꾸지 않는다.', 'golden-queen/STRATEGY.md'),
  ('blueberry:day7:20260819', 'golden-blueberry', 'DAY_7_REVIEW', '다음 운영 상태 확인', '2026-08-19T17:30:14Z', 'PENDING', null, '+2%p·+5%p 조건의 실행 주기와 정확한 주문·체결, 보조 자료 수집 범위를 확인한다.', 'golden-blueberry/STRATEGY.md'),
  ('blueberry:day30:20260911', 'golden-blueberry', 'DAY_30_REVIEW', '급등폭 +2%p·+5%p 최종 비교', '2026-09-04T13:13:37Z', 'PENDING', null, '두 조건의 확인된 실제 체결 수와 비용을 포함한 결과를 미리 정한 기준으로 비교한다.', 'golden-blueberry/STRATEGY.md'),
  ('melon:day7:20260812', 'golden-melon', 'DAY_7_REVIEW', '거래량 세 조건의 초기 검토', '2026-08-12T13:14:00Z', 'COMPLETED', '2026-08-14T21:09:00Z', '세 거래량 조건에서 거래가 적었던 원인과 데이터 누락을 점검했다.', 'docs/retro-summaries/024-quince-melon-live-review-2026-08-15.md'),
  ('melon:day30:20260904', 'golden-melon', 'DAY_30_REVIEW', '거래량 세 조건의 최종 비교', '2026-09-04T13:14:00Z', 'PENDING', null, '높음·중간·낮음 조건을 같은 기간의 확인된 실제 체결로 비교한다.', 'golden-melon/STRATEGY.md'),
  ('quince:day7:20260819', 'golden-quince', 'DAY_7_REVIEW', '주문 방식별 체결 상태 확인', '2026-08-19T15:26:26Z', 'PENDING', null, '지정가 대기·근접 지정가·즉시 체결의 후보, 주문, 체결과 비용을 비교한다.', 'golden-quince/STRATEGY.md'),
  ('quince:day30:20260911', 'golden-quince', 'DAY_30_REVIEW', '주문 방식별 최종 비교', '2026-09-11T15:26:26Z', 'PENDING', null, '주문 방식 이외의 진입 조건은 함께 바꾸지 않고 세 방식의 비용을 판정한다.', 'golden-quince/STRATEGY.md'),
  ('cherry:stability:20260818', 'golden-cherry', 'STABILITY_GATE', '일부 체결 처리 오류 해소', '2026-08-18T12:00:00Z', 'PENDING', null, 'Yellow·Orange가 일부 체결된 실제 수량을 사용하고 대기 상태나 포지션 상한에 잘못 걸리지 않는지 확인한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('cherry:day7:20260821', 'golden-cherry', 'DAY_7_REVIEW', '재가동 7일 안정성 확인', '2026-08-21T11:40:00Z', 'PENDING', null, '주문 상태 오류를 해소한 뒤 같은 코드·설정 기간의 실행 주기와 체결 누락을 먼저 확인한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('cherry:day30:20260913', 'golden-cherry', 'DAY_30_REVIEW', '기준값·조정값 최종 비교', '2026-09-13T11:40:00Z', 'PENDING', null, '같은 코드에서 달리 둔 수치를 확인된 실제 체결과 대안 수치 계산으로 비교한다.', 'docs/retro-summaries/027-golden-cherry-orange-yellow-followup-2026-08-18.md'),
  ('elderberry:monthend:20260830', 'golden-elderberry', 'STABILITY_GATE', '청산 상태와 재개 여부 검토', '2026-08-30T15:00:00Z', 'PENDING', null, '남은 포지션과 주문 기록을 맞춘 뒤 신규 진입 재개 또는 폐쇄를 결정한다.', 'golden-elderberry/STRATEGY.md'),
  ('apple:deployment', 'golden-apple', 'DEPLOYMENT_DECISION', '폐쇄 확인', null, 'CANCELLED', null, '사용자 확인으로 폐쇄됐으며 정확한 폐쇄일은 미정이다.', 'polymarket-dashboard/supabase/migrations/20260818150000_pd_golden_apple_closed_v1.sql'),
  ('banana:deployment', 'golden-banana', 'DEPLOYMENT_DECISION', '현재 배치 여부 결정', null, 'BLOCKED', null, '계정명과 전략 배치를 분리하고 새 cohort를 사전 등록한 뒤 재검증한다.', 'golden-banana/README.md'),
  ('grape:deployment', 'golden-grape', 'DEPLOYMENT_DECISION', '시뮬레이션 계획 수립', null, 'BLOCKED', null, 'Jenkins 배치 전에 독립된 검증 기간과 판정 기준을 등록한다.', 'golden-grape/STRATEGY.md'),
  ('orange:deployment', 'golden-orange', 'DEPLOYMENT_DECISION', '시뮬레이션 계획 수립', null, 'BLOCKED', null, 'polybot-orange와 구분되는 새 Jenkins 잡과 검증 기간을 등록해야 한다.', 'golden-orange/STRATEGY.md')
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

update public.pd_strategies as strategy
set
  thesis = valueset.thesis,
  current_summary = valueset.current_summary
from (
  values
    ('golden-apple', '가격이 0.80 이상이면 매수하고 0.90에서 청산하도록 구현한 초기 확률 전략이다.', '사용자 확인으로 폐쇄됐다. 정확한 폐쇄일은 기록되지 않아 미정이다.'),
    ('golden-banana', '0.85~0.97 가격 구간에서 단기 평균선이 장기 평균선을 넘는 움직임을 이용한다.', '코드는 구현되어 있지만 현재 이 폴더를 실행하는 Jenkins 잡은 없다.'),
    ('golden-blueberry', '해결이 가까운 시장에서 가격이 0.85를 처음 넘을 때 직전 상승폭 +2%p와 +5%p 조건을 비교한다.', '두 개의 $5 실거래 잡과 실제 주문 없는 보조 자료 수집 잡을 함께 운영한다.'),
    ('golden-cherry', '해결이 가까운 고확률 YES 시장의 진입·청산 수치를 비교하고 주문 상태를 안정화한다.', 'polybot-yellow와 polybot-orange가 서로 다른 수치로 같은 전략 코드를 실행한다.'),
    ('golden-date', '남은 시간별 가격 기준으로 우세한 결과의 수렴을 노렸으나 가설이 기각됐다.', '전략은 폐쇄됐고 polybot-red는 잔여 계정 정리만 수행한다.'),
    ('golden-elderberry', '우세한 결과의 가격이 급락한 뒤 과잉반응이 되돌아오는 구간을 노린다.', 'polybot-cherry가 신규 진입 없이 기존 포지션 정리만 수행한다.'),
    ('golden-fig', '가능성이 낮은 YES의 가격 하락을 NO 매수로 이용하려 했으나 가설이 기각됐다.', '실제 해결 확률과 시장 가격의 차이가 불리해 폐쇄했다.'),
    ('golden-grape', '24시간 동안 완만하고 일관되게 움직이며 거래량이 늘어난 시장의 추가 이동을 노린다.', '구현은 완료됐지만 현재 연결된 Jenkins 검증 잡은 없다.'),
    ('golden-honeydew', '미국 새벽과 주말에 정보 없이 벌어진 가격 이탈이 되돌아오는지 검증했다.', '확인된 실제 체결 성과가 주요 조건에서 모두 음수여서 폐쇄했다.'),
    ('golden-kiwi', '5분 간격의 작은 연속 상승이 60분 뒤에도 이어지는지 네 조건으로 검증한다.', '연속 횟수와 누적 상승폭을 조합한 네 조건을 5분 간격으로 비교하고 있다.'),
    ('golden-lime', '거래량을 동반한 급등이 이후에도 이어지는지 검증했으나 가설이 기각됐다.', '과거 가격 자료를 이용한 검증에서 가설이 기각되어 폐쇄했다.'),
    ('golden-mango', '해결이 가까운 시장의 정산 전 할인폭이 보유 시간을 보상하는지 검증했다.', '할인폭이 시간 보상보다 손실 가능성을 나타내는 경우가 많아 폐쇄했다.'),
    ('golden-melon', '해결이 가까운 시장에서 가격이 0.85~0.93을 처음 넘을 때 24시간 거래량 기준 세 수준을 비교한다.', '거래량 높음·중간·낮음의 세 $5 실거래 잡을 운영하며 30일 판정을 기다리고 있다.'),
    ('golden-nectarine', '20일 최저가 부근에서 매수해 5일 뒤 청산하는 가격 회복 전략을 검증했다.', '확인된 5일 보유 거래 결과가 음수여서 폐쇄했다.'),
    ('golden-orange', '가능성이 낮은 시장에서 공포성 가격 급등이 멈춘 뒤 NO를 매수하는 전략이다.', '구현은 완료됐지만 현재 연결된 Jenkins 검증 잡은 없다.'),
    ('golden-papaya', '해결까지 24시간 또는 72시간 남은 시장이 0.95를 처음 넘은 뒤 결과를 비교한다.', '24시간·72시간 조건을 각각 $5 실거래로 검증하고 있다.'),
    ('golden-pomegranate', '거래하지 않고 공개 시장 목록, 체결 기록과 표본 호가를 계속 저장한다.', '외장 하드에서 공개 시장 자료를 15분마다 수집하며 월간 용량을 점검한다.'),
    ('golden-queen', '해결 직전 가격이 0.90을 처음 넘은 뒤 0.98 또는 최종 1에 도달하는지 검증한다.', '12시간·24시간 조건을 각각 $100 실거래로 비교하고 있다.'),
    ('golden-quince', '같은 진입 조건에서 지정가 대기, 근접 지정가, 즉시 체결의 비용 차이를 비교한다.', '세 주문 방식을 각각 $5 실거래로 동시에 검증하고 있다.'),
    ('golden-raspberry', 'YES·NO 호가 잔량의 불균형이 60분 뒤 가격 움직임을 예측하는지 검증한다.', '외장 하드의 세 수집 구간에서 같은 검증 항목을 나눠 기록하고 있다.'),
    ('golden-strawberry', '시장 가격이 0.95를 처음 넘은 뒤 1.00 수렴, 0.85 하락, 최종 해결까지의 경로를 함께 기록한다.', '실제 주문 없이 7일 동안 신규 사례를 모으고 이후 최종 해결 결과까지 추적한다.')
) as valueset(strategy_id, thesis, current_summary)
where strategy.strategy_id = valueset.strategy_id;

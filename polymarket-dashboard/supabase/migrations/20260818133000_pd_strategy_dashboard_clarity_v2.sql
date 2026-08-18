-- Make the lifecycle dashboard useful without relying on internal strategy codenames.
-- These fields are descriptive metadata only; the Jenkins collector updates health
-- columns independently and trading jobs do not read this table.

alter table public.pd_jenkins_jobs
  add column if not exists purpose text,
  add column if not exists test_size_label text,
  add column if not exists experiment_started_at timestamptz,
  add column if not exists experiment_ends_at timestamptz;

comment on column public.pd_jenkins_jobs.purpose is
  'Plain-language description of what this Jenkins job collects or validates.';
comment on column public.pd_jenkins_jobs.test_size_label is
  'Human-readable trade or virtual test size; not used by trading code.';
comment on column public.pd_jenkins_jobs.experiment_started_at is
  'Operator-facing start of the currently evaluated run, not necessarily the first historical build.';
comment on column public.pd_jenkins_jobs.experiment_ends_at is
  'Planned review or collection end. Null means no end has been scheduled.';

update public.pd_strategies
set
  codename = case strategy_id
    when 'golden-strawberry' then '0.95 돌파 후 가격 경로 수집'
    when 'golden-raspberry' then '호가 잔량과 60분 뒤 가격 비교'
    when 'golden-kiwi' then '연속 상승 후속 움직임 검증'
    else codename
  end,
  thesis = case strategy_id
    when 'golden-strawberry' then '시장 가격이 0.95를 처음 넘은 뒤 1.00 수렴, 0.85 하락, 최종 해결까지의 경로를 함께 기록한다.'
    when 'golden-raspberry' then 'YES·NO 호가 잔량의 불균형이 60분 뒤 가격 움직임을 예측하는지 검증한다.'
    when 'golden-kiwi' then '5분 간격의 작은 연속 상승이 60분 뒤에도 이어지는지 네 조건으로 검증한다.'
    else thesis
  end,
  current_summary = case strategy_id
    when 'golden-strawberry' then '실제 주문 없이 7일 동안 신규 사례를 모으고, 이후 최종 해결 결과까지 추적한다.'
    when 'golden-raspberry' then '외장 하드의 세 수집 구간에서 같은 검증 항목을 나눠 기록하고 있다.'
    when 'golden-kiwi' then '연속 횟수와 누적 상승폭을 조합한 네 조건을 5분 간격으로 비교하고 있다.'
    else current_summary
  end,
  evaluation_started_at = case
    when strategy_id = 'golden-blueberry' then '2026-08-05T13:13:37Z'::timestamptz
    else evaluation_started_at
  end,
  phase_started_at = case
    when strategy_id = 'golden-blueberry' then '2026-08-05T13:13:37Z'::timestamptz
    else phase_started_at
  end
where strategy_id in ('golden-strawberry', 'golden-raspberry', 'golden-kiwi', 'golden-blueberry');

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

update public.pd_strategy_checkpoints
set
  title = case checkpoint_id
    when 'strawberry:first24:20260816' then '첫 24시간 수집 상태 확인'
    when 'strawberry:day7:20260822' then '신규 사례 수집 종료'
    when 'strawberry:resolution:20260921' then '최종 결과 추적 종료'
    when 'raspberry:first24:20260814' then '첫 24시간 수집 상태 확인'
    when 'raspberry:day7:20260820' then '7일 데이터 품질 확인'
    when 'raspberry:day30:20260912' then '30일 가설 판정'
    when 'kiwi:day7:20260820' then '7일 데이터 품질 확인'
    when 'kiwi:day30:20260912' then '30일 가설 판정'
    when 'pomegranate:monthly:20260907' then '월간 저장공간·백업 확인'
    when 'papaya:day7:20260819' then '7일 체결·운영 확인'
    when 'papaya:day30:20260911' then '30일 최종 판정'
    when 'queen:day7:20260819' then '7일 체결·운영 확인'
    when 'queen:day30:20260911' then '30일 최종 판정'
    when 'blueberry:day7:20260819' then '다음 운영 상태 확인'
    when 'blueberry:day30:20260911' then '급등폭 +2%p·+5%p 최종 비교'
    when 'melon:day7:20260812' then '거래량 세 조건의 초기 검토'
    when 'melon:day30:20260904' then '거래량 세 조건의 최종 비교'
    when 'quince:day7:20260819' then '주문 방식별 체결 상태 확인'
    when 'quince:day30:20260911' then '주문 방식별 최종 비교'
    when 'cherry:stability:20260818' then '일부 체결 처리 오류 해소'
    when 'cherry:day7:20260821' then '재가동 7일 안정성 확인'
    when 'cherry:day30:20260913' then '기준값·조정값 최종 비교'
    when 'elderberry:monthend:20260830' then '청산 상태와 재개 여부 검토'
    when 'grape:deployment' then '시뮬레이션 계획 수립'
    when 'orange:deployment' then '시뮬레이션 계획 수립'
    else title
  end,
  instructions = case checkpoint_id
    when 'strawberry:first24:20260816' then '수집 주기, 시장 목록, 호가, 시장 정보, 가격 경로, 최종 결과, DB 무결성과 저장공간 증가량만 확인한다.'
    when 'strawberry:day7:20260822' then '수집 데이터가 빠짐없는지 먼저 확인하고 최종 결과가 충분히 모일 때까지 수익성 결론은 보류한다.'
    when 'strawberry:resolution:20260921' then '각 시장의 최종 결과와 전체 가격 경로를 확인해 고정된 검증 집단을 판정한다.'
    when 'raspberry:first24:20260814' then '세 수집 구간의 실행 주기, YES·NO 짝, 후속 가격, 비교군과 DB 무결성을 확인한다.'
    when 'raspberry:day7:20260820' then '세 수집 구간의 누락과 비교군을 확인하며 검증 기준은 바꾸지 않는다.'
    when 'raspberry:day30:20260912' then '미리 정한 기준으로만 가설을 판정하고 결과를 본 뒤 대표 조건을 바꾸지 않는다.'
    when 'kiwi:day7:20260820' then '5분 실행 주기와 네 조건의 동일한 데이터 범위, 60분 뒤 가격 기록을 확인한다.'
    when 'kiwi:day30:20260912' then '미리 정한 대표 조건을 그대로 판정하며 통과해도 실제 거래 전 추가 검토가 필요하다.'
    when 'pomegranate:monthly:20260907' then '일별 DB, 체크섬, 백업 복구, 수집 항목 누락과 외장 디스크 증가량을 확인한다.'
    else instructions
  end,
  due_at = case
    when checkpoint_id = 'blueberry:day30:20260911' then '2026-09-04T13:13:37Z'::timestamptz
    else due_at
  end
where strategy_id in (
  'golden-strawberry', 'golden-raspberry', 'golden-kiwi', 'golden-pomegranate',
  'golden-papaya', 'golden-queen', 'golden-blueberry', 'golden-melon',
  'golden-quince', 'golden-cherry', 'golden-elderberry', 'golden-grape', 'golden-orange'
);

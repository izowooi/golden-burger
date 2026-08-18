-- Operator-confirmed correction: Golden Apple is closed, but the exact closure date is unknown.
update public.pd_strategies
set
  lifecycle_stage = 'CLOSED',
  operating_status = 'CLOSED',
  phase_started_at = null,
  current_summary = '사용자 확인으로 폐쇄됐다. 정확한 폐쇄일은 기록되지 않아 미정이다.',
  attention_level = 'INFO',
  attention_note = '폐쇄일은 확인되지 않았다.',
  source_ref = 'polymarket-dashboard/supabase/migrations/20260818150000_pd_golden_apple_closed_v1.sql',
  hidden_by_default = true
where strategy_id = 'golden-apple';

update public.pd_strategy_checkpoints
set
  title = '폐쇄 확인',
  status = 'CANCELLED',
  due_at = null,
  completed_at = null,
  instructions = '사용자 확인으로 폐쇄됐으며 정확한 폐쇄일은 미정이다.',
  source_ref = 'polymarket-dashboard/supabase/migrations/20260818150000_pd_golden_apple_closed_v1.sql'
where checkpoint_id = 'apple:deployment';

alter table public.pd_strategies
  drop constraint if exists pd_strategies_lifecycle_stage_check;

alter table public.pd_strategies
  add constraint pd_strategies_lifecycle_stage_check check (
    lifecycle_stage in (
      'IDEA',
      'IMPLEMENTING',
      'IMPLEMENTED',
      'SIMULATION',
      'LIVE_VALIDATION',
      'STABILIZATION',
      'PROFITABILITY',
      'PRODUCTION',
      'CLOSED'
    )
  );

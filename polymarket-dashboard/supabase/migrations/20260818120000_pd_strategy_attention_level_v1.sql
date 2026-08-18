alter table public.pd_strategies
  add column attention_level text not null default 'NONE'
  check (attention_level in ('NONE', 'INFO', 'WATCH', 'CRITICAL'));

comment on column public.pd_strategies.attention_level is
  'Severity of attention_note. INFO is contextual and does not downgrade strategy health.';

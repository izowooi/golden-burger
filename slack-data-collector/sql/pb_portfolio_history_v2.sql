-- Required additive migration for the atomic pb-portfolio/v2 daily writer.
-- Apply pb_portfolio_schema.sql first. Snapshot-run columns and the two RPCs are
-- runtime dependencies; deployment history and cash-flow tables support analysis.

-- This file is also intended for direct Supabase SQL Editor/psql application.
-- Keep the full additive migration atomic so a failed constraint/function/grant
-- cannot leave a half-installed writer contract.
begin;

create table if not exists public.pb_strategy_deployments (
  deployment_id bigint generated always as identity primary key,
  account_id text not null
    references public.pb_algorithm_accounts(account_id) on delete restrict,
  strategy_code text not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  jenkins_job_name text,
  git_commit text,
  config_hash text,
  created_at timestamptz not null default now(),
  constraint pb_strategy_deployments_strategy_code_check
    check (strategy_code ~ '^golden-[a-z0-9-]+$'),
  constraint pb_strategy_deployments_effective_range_check
    check (effective_to is null or effective_to > effective_from),
  unique (account_id, effective_from)
);

comment on table public.pb_strategy_deployments is
  'Effective-dated account-to-strategy assignment. Use this, not the mutable account catalog algorithm_code, for historical attribution.';

create index if not exists pb_strategy_deployments_account_range_idx
  on public.pb_strategy_deployments (account_id, effective_from desc, effective_to);

create or replace function public.pb_reject_overlapping_strategy_deployments()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  -- Serialize writers for one account before checking the effective range.
  -- Without this transaction-scoped lock, concurrent inserts can both observe
  -- no overlap and commit conflicting history rows.
  perform pg_advisory_xact_lock(hashtextextended(new.account_id, 0));
  if exists (
    select 1
    from public.pb_strategy_deployments existing
    where existing.account_id = new.account_id
      and existing.deployment_id <> new.deployment_id
      and tstzrange(existing.effective_from, existing.effective_to, '[)')
          && tstzrange(new.effective_from, new.effective_to, '[)')
  ) then
    raise exception 'overlapping strategy deployment for account %', new.account_id;
  end if;
  return new;
end;
$$;

create or replace function public.pb_text_array_is_unique_nonempty(input_values text[])
returns boolean
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select
    cardinality(input_values) = (
      select count(distinct value)
      from unnest(input_values) as item(value)
    )
    and array_position(input_values, null) is null
    and not exists (
      select 1
      from unnest(input_values) as item(value)
      where btrim(value) = ''
    );
$$;

drop trigger if exists pb_strategy_deployments_no_overlap
  on public.pb_strategy_deployments;
create constraint trigger pb_strategy_deployments_no_overlap
after insert or update on public.pb_strategy_deployments
deferrable initially immediate
for each row execute function public.pb_reject_overlapping_strategy_deployments();

create table if not exists public.pb_snapshot_runs (
  snapshot_run_id uuid primary key default gen_random_uuid(),
  report_date date not null,
  reported_at timestamptz not null,
  source_schema_version text not null,
  status text not null,
  expected_account_count smallint not null,
  observed_account_count smallint not null,
  expected_account_ids text[] not null,
  observed_account_ids text[] not null,
  error_code text,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint pb_snapshot_runs_status_check
    check (status in ('STARTED', 'COMPLETE', 'FAILED')),
  constraint pb_snapshot_runs_counts_check
    check (
      expected_account_count >= 0
      and observed_account_count >= 0
      and observed_account_count <= expected_account_count
      and expected_account_count = cardinality(expected_account_ids)
      and observed_account_count = cardinality(observed_account_ids)
      and public.pb_text_array_is_unique_nonempty(expected_account_ids)
      and public.pb_text_array_is_unique_nonempty(observed_account_ids)
      and observed_account_ids <@ expected_account_ids
    ),
  constraint pb_snapshot_runs_complete_check
    check (
      status <> 'COMPLETE'
      or (
        observed_account_count = expected_account_count
        and observed_account_ids @> expected_account_ids
        and expected_account_ids @> observed_account_ids
        and completed_at is not null
      )
    ),
  constraint pb_snapshot_runs_current_account_contract_check
    check (
      expected_account_count = 6
      and expected_account_ids @> array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox'
      ]::text[]
      and expected_account_ids <@ array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox'
      ]::text[]
    )
);

-- CREATE TABLE IF NOT EXISTS does not retrofit constraints on an earlier
-- deployment of an earlier migration revision. Add the stronger array invariant once.
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'public.pb_snapshot_runs'::regclass
      and conname = 'pb_snapshot_runs_array_shape_check'
  ) then
    alter table public.pb_snapshot_runs
      add constraint pb_snapshot_runs_array_shape_check
      check (
        expected_account_count = cardinality(expected_account_ids)
        and observed_account_count = cardinality(observed_account_ids)
        and public.pb_text_array_is_unique_nonempty(expected_account_ids)
        and public.pb_text_array_is_unique_nonempty(observed_account_ids)
        and observed_account_ids <@ expected_account_ids
      ) not valid;
  end if;
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'public.pb_snapshot_runs'::regclass
      and conname = 'pb_snapshot_runs_current_account_contract_check'
  ) then
    alter table public.pb_snapshot_runs
      add constraint pb_snapshot_runs_current_account_contract_check
      check (
        expected_account_count = 6
        and expected_account_ids @> array[
          'golden-apple-1', 'golden-apple-2', 'golden-banana',
          'golden-cherry', 'golden-eco', 'golden-fox'
        ]::text[]
        and expected_account_ids <@ array[
          'golden-apple-1', 'golden-apple-2', 'golden-banana',
          'golden-cherry', 'golden-eco', 'golden-fox'
        ]::text[]
      ) not valid;
  end if;
end;
$$;

comment on table public.pb_snapshot_runs is
  'Completeness marker for an atomic future snapshot writer. Consumers should use only COMPLETE runs.';

create index if not exists pb_snapshot_runs_date_status_idx
  on public.pb_snapshot_runs (report_date desc, status);

alter table public.pb_daily_portfolio_totals
  add column if not exists snapshot_run_id uuid
    references public.pb_snapshot_runs(snapshot_run_id) on delete restrict;

create index if not exists pb_daily_algorithm_balances_snapshot_run_id_idx
  on public.pb_daily_algorithm_balances (snapshot_run_id);
create index if not exists pb_daily_portfolio_totals_snapshot_run_id_idx
  on public.pb_daily_portfolio_totals (snapshot_run_id);
alter table public.pb_daily_portfolio_totals
  add column if not exists source_schema_version text;

alter table public.pb_daily_algorithm_balances
  add column if not exists snapshot_run_id uuid
    references public.pb_snapshot_runs(snapshot_run_id) on delete restrict;
alter table public.pb_daily_algorithm_balances
  add column if not exists source_schema_version text;

-- Existing deployments may contain legacy rows that do not reconcile to the
-- cent. NOT VALID preserves those rows for audit while enforcing the contract
-- for every new or updated snapshot.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.pb_daily_portfolio_totals'::regclass
      and conname = 'pb_daily_portfolio_totals_nonnegative_check'
  ) then
    alter table public.pb_daily_portfolio_totals
      add constraint pb_daily_portfolio_totals_nonnegative_check
      check (total_value >= 0 and position_value >= 0 and cash_value >= 0)
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.pb_daily_portfolio_totals'::regclass
      and conname = 'pb_daily_portfolio_totals_breakdown_check'
  ) then
    alter table public.pb_daily_portfolio_totals
      add constraint pb_daily_portfolio_totals_breakdown_check
      check (total_value = position_value + cash_value)
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.pb_daily_algorithm_balances'::regclass
      and conname = 'pb_daily_algorithm_balances_nonnegative_check'
  ) then
    alter table public.pb_daily_algorithm_balances
      add constraint pb_daily_algorithm_balances_nonnegative_check
      check (total_value >= 0 and position_value >= 0 and cash_value >= 0)
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.pb_daily_algorithm_balances'::regclass
      and conname = 'pb_daily_algorithm_balances_breakdown_check'
  ) then
    alter table public.pb_daily_algorithm_balances
      add constraint pb_daily_algorithm_balances_breakdown_check
      check (total_value = position_value + cash_value)
      not valid;
  end if;
end;
$$;

create or replace function public.pb_portfolio_writer_preflight_v2()
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  actual_mapping jsonb;
  expected_mapping constant jsonb := jsonb_build_object(
    'GOLDEN-APPLE (1)', 'golden-apple-1',
    'GOLDEN-APPLE (2)', 'golden-apple-2',
    'GOLDEN-BANANA', 'golden-banana',
    'GOLDEN-CHERRY', 'golden-cherry',
    'GOLDEN-ECO', 'golden-eco',
    'GOLDEN-FOX', 'golden-fox'
  );
begin
  select coalesce(jsonb_object_agg(account.jenkins_name, account.account_id), '{}'::jsonb)
  into actual_mapping
  from public.pb_algorithm_accounts account;

  if actual_mapping <> expected_mapping then
    raise exception 'pb_algorithm_accounts exact six-account mapping mismatch';
  end if;

  return jsonb_build_object(
    'contract_version', 'pb-portfolio/v2',
    'account_count', 6
  );
end;
$$;

create or replace function public.pb_write_complete_portfolio_snapshot_v2(
  p_report_date date,
  p_reported_at timestamptz,
  p_source_message_ts numeric,
  p_source_schema_version text,
  p_balances jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  expected_ids constant text[] := array[
    'golden-apple-1', 'golden-apple-2', 'golden-banana',
    'golden-cherry', 'golden-eco', 'golden-fox'
  ]::text[];
  observed_ids text[];
  snapshot_id uuid;
  account_count integer;
  written_count integer;
  portfolio_total numeric;
  portfolio_position numeric;
  portfolio_cash numeric;
begin
  perform public.pb_portfolio_writer_preflight_v2();

  if p_report_date is null or p_reported_at is null
     or not isfinite(p_report_date) or not isfinite(p_reported_at) then
    raise exception 'report_date and reported_at are required';
  end if;
  if p_source_message_ts is null
     or p_source_message_ts::text in ('NaN', 'Infinity', '-Infinity')
     or p_source_message_ts < 0 then
    raise exception 'source_message_ts must be nonnegative';
  end if;
  if p_source_schema_version <> 'pb-portfolio/v2' then
    raise exception 'unsupported source schema version: %', p_source_schema_version;
  end if;
  if p_balances is null or jsonb_typeof(p_balances) <> 'array' then
    raise exception 'balances must be a JSON array';
  end if;

  select
    count(*),
    array_agg(item.account_id order by item.account_id),
    sum(item.total_value),
    sum(item.position_value),
    sum(item.cash_value)
  into
    account_count,
    observed_ids,
    portfolio_total,
    portfolio_position,
    portfolio_cash
  from jsonb_to_recordset(p_balances) as item(
    account_id text,
    total_value numeric,
    position_value numeric,
    cash_value numeric
  );

  if account_count <> 6 or observed_ids <> expected_ids then
    raise exception 'balances must contain the exact six stable account IDs';
  end if;
  if exists (
    select 1
    from jsonb_to_recordset(p_balances) as item(
      account_id text,
      total_value numeric,
      position_value numeric,
      cash_value numeric
    )
    where item.account_id is null
      or item.total_value is null
      or item.position_value is null
      or item.cash_value is null
      or item.total_value::text in ('NaN', 'Infinity', '-Infinity')
      or item.position_value::text in ('NaN', 'Infinity', '-Infinity')
      or item.cash_value::text in ('NaN', 'Infinity', '-Infinity')
      or least(item.total_value, item.position_value, item.cash_value) < 0
      or item.total_value <> round(item.total_value, 2)
      or item.position_value <> round(item.position_value, 2)
      or item.cash_value <> round(item.cash_value, 2)
      or item.total_value <> item.position_value + item.cash_value
  ) then
    raise exception 'every balance must be nonnegative, cent precision, and reconciled';
  end if;
  if portfolio_total <> portfolio_position + portfolio_cash then
    raise exception 'portfolio total does not reconcile';
  end if;

  insert into public.pb_snapshot_runs (
    report_date, reported_at, source_schema_version, status,
    expected_account_count, observed_account_count,
    expected_account_ids, observed_account_ids, completed_at
  ) values (
    p_report_date, p_reported_at, p_source_schema_version, 'COMPLETE',
    6, 6, expected_ids, observed_ids, statement_timestamp()
  )
  returning snapshot_run_id into snapshot_id;

  insert into public.pb_daily_algorithm_balances as existing (
    report_date, account_id, total_value, position_value, cash_value,
    currency, reported_at, source_message_ts, updated_at,
    snapshot_run_id, source_schema_version
  )
  select
    p_report_date, item.account_id, item.total_value, item.position_value,
    item.cash_value, 'USD', p_reported_at, p_source_message_ts,
    statement_timestamp(), snapshot_id, p_source_schema_version
  from jsonb_to_recordset(p_balances) as item(
    account_id text,
    total_value numeric,
    position_value numeric,
    cash_value numeric
  )
  on conflict (report_date, account_id) do update set
    total_value = excluded.total_value,
    position_value = excluded.position_value,
    cash_value = excluded.cash_value,
    currency = excluded.currency,
    reported_at = excluded.reported_at,
    source_message_ts = excluded.source_message_ts,
    updated_at = excluded.updated_at,
    snapshot_run_id = excluded.snapshot_run_id,
    source_schema_version = excluded.source_schema_version
  where excluded.source_message_ts >= existing.source_message_ts;
  get diagnostics written_count = row_count;
  if written_count <> 6 then
    raise exception 'stale or incomplete algorithm balance write rejected';
  end if;

  insert into public.pb_daily_portfolio_totals as existing (
    report_date, total_value, position_value, cash_value, currency,
    reported_at, source_message_ts, updated_at,
    snapshot_run_id, source_schema_version
  ) values (
    p_report_date, portfolio_total, portfolio_position, portfolio_cash, 'USD',
    p_reported_at, p_source_message_ts, statement_timestamp(),
    snapshot_id, p_source_schema_version
  )
  on conflict (report_date) do update set
    total_value = excluded.total_value,
    position_value = excluded.position_value,
    cash_value = excluded.cash_value,
    currency = excluded.currency,
    reported_at = excluded.reported_at,
    source_message_ts = excluded.source_message_ts,
    updated_at = excluded.updated_at,
    snapshot_run_id = excluded.snapshot_run_id,
    source_schema_version = excluded.source_schema_version
  where excluded.source_message_ts >= existing.source_message_ts;
  get diagnostics written_count = row_count;
  if written_count <> 1 then
    raise exception 'stale portfolio total write rejected';
  end if;

  return jsonb_build_object(
    'snapshot_run_id', snapshot_id,
    'report_date', p_report_date,
    'account_count', account_count,
    'total_value', portfolio_total,
    'position_value', portfolio_position,
    'cash_value', portfolio_cash
  );
end;
$$;

create table if not exists public.pb_external_cash_flows (
  cash_flow_id uuid primary key default gen_random_uuid(),
  account_id text not null
    references public.pb_algorithm_accounts(account_id) on delete restrict,
  occurred_at timestamptz not null,
  flow_type text not null,
  amount numeric(18, 6) not null,
  currency text not null default 'USD',
  source text not null,
  source_reference text not null,
  note text,
  created_at timestamptz not null default now(),
  constraint pb_external_cash_flows_type_check
    check (flow_type in ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER_IN', 'TRANSFER_OUT')),
  constraint pb_external_cash_flows_amount_check check (amount > 0),
  constraint pb_external_cash_flows_currency_check check (currency ~ '^[A-Z]{3}$'),
  unique (account_id, source, source_reference)
);

comment on table public.pb_external_cash_flows is
  'User-controlled external capital flows for TWR. Polymarket TRADE/SPLIT/MERGE/REDEEM/REWARD/CONVERSION/MAKER_REBATE/REFERRAL_REWARD activity is not an external flow.';

create index if not exists pb_external_cash_flows_account_time_idx
  on public.pb_external_cash_flows (account_id, occurred_at desc);

alter table public.pb_strategy_deployments enable row level security;
alter table public.pb_snapshot_runs enable row level security;
alter table public.pb_external_cash_flows enable row level security;

revoke all on table public.pb_strategy_deployments from anon, authenticated;
revoke all on table public.pb_snapshot_runs from anon, authenticated;
revoke all on table public.pb_external_cash_flows from anon, authenticated;

-- SECURITY INVOKER RPCs require explicit least-privilege table grants for the
-- server-only service role; never grant these writes to anon/authenticated.
grant select on table public.pb_algorithm_accounts to service_role;
grant select, insert, update on table public.pb_daily_algorithm_balances
  to service_role;
grant select, insert, update on table public.pb_daily_portfolio_totals
  to service_role;
grant select, insert on table public.pb_snapshot_runs to service_role;

revoke all on function public.pb_portfolio_writer_preflight_v2()
  from public, anon, authenticated;
revoke all on function public.pb_write_complete_portfolio_snapshot_v2(
  date, timestamptz, numeric, text, jsonb
) from public, anon, authenticated;
grant execute on function public.pb_portfolio_writer_preflight_v2()
  to service_role;
grant execute on function public.pb_write_complete_portfolio_snapshot_v2(
  date, timestamptz, numeric, text, jsonb
) to service_role;

-- PostgREST can otherwise keep returning PGRST202 from a stale schema cache even
-- after both functions exist. PostgreSQL delivers this notification on commit.
notify pgrst, 'reload schema';
commit;

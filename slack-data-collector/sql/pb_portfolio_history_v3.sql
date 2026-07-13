-- Nine-account pb-portfolio/v3 contract and atomic daily writer.
-- Apply pb_portfolio_schema.sql and pb_portfolio_history_v2.sql first.
-- Historical v2 six-account snapshot runs remain valid; new writes require the
-- exact v3 nine-account set.

begin;

insert into public.pb_algorithm_accounts (
  account_id, jenkins_name, algorithm_code, instance_no, sort_order
) values
  ('golden-apple-1', 'GOLDEN-APPLE (1)', 'golden-apple', 1, 1),
  ('golden-banana', 'GOLDEN-BANANA', 'golden-banana', null, 2),
  ('golden-cherry', 'GOLDEN-CHERRY', 'golden-cherry', null, 3),
  ('golden-apple-2', 'GOLDEN-APPLE (2)', 'golden-apple', 2, 4),
  ('golden-eco', 'GOLDEN-ECO', 'golden-honeydew', null, 5),
  ('golden-fox', 'GOLDEN-FOX', 'golden-nectarine', null, 6),
  ('golden-lion', 'GOLDEN-LION', 'golden-lion', null, 7),
  ('golden-tiger', 'GOLDEN-TIGER', 'golden-tiger', null, 8),
  ('golden-wolf', 'GOLDEN-WOLF', 'golden-wolf', null, 9)
on conflict (account_id) do nothing;

-- The v2 migration installed an exact-six check. Replace it with a versioned
-- contract so historical v2 rows and new v3 rows can coexist. NOT VALID keeps
-- pre-migration evidence queryable while enforcing the check for new writes.
alter table public.pb_snapshot_runs
  drop constraint if exists pb_snapshot_runs_current_account_contract_check;

alter table public.pb_snapshot_runs
  add constraint pb_snapshot_runs_current_account_contract_check
  check (
    (
      source_schema_version = 'pb-portfolio/v2'
      and expected_account_count = 6
      and expected_account_ids @> array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox'
      ]::text[]
      and expected_account_ids <@ array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox'
      ]::text[]
    )
    or
    (
      source_schema_version = 'pb-portfolio/v3'
      and expected_account_count = 9
      and expected_account_ids @> array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox',
        'golden-lion', 'golden-tiger', 'golden-wolf'
      ]::text[]
      and expected_account_ids <@ array[
        'golden-apple-1', 'golden-apple-2', 'golden-banana',
        'golden-cherry', 'golden-eco', 'golden-fox',
        'golden-lion', 'golden-tiger', 'golden-wolf'
      ]::text[]
    )
  ) not valid;

create or replace function public.pb_portfolio_writer_preflight_v3()
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
    'GOLDEN-FOX', 'golden-fox',
    'GOLDEN-LION', 'golden-lion',
    'GOLDEN-TIGER', 'golden-tiger',
    'GOLDEN-WOLF', 'golden-wolf'
  );
begin
  select coalesce(
    jsonb_object_agg(account.jenkins_name, account.account_id),
    '{}'::jsonb
  )
  into actual_mapping
  from public.pb_algorithm_accounts account;

  if actual_mapping <> expected_mapping then
    raise exception 'pb_algorithm_accounts exact nine-account mapping mismatch';
  end if;

  return jsonb_build_object(
    'contract_version', 'pb-portfolio/v3',
    'account_count', 9
  );
end;
$$;

create or replace function public.pb_write_complete_portfolio_snapshot_v3(
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
    'golden-cherry', 'golden-eco', 'golden-fox',
    'golden-lion', 'golden-tiger', 'golden-wolf'
  ]::text[];
  observed_ids text[];
  snapshot_id uuid;
  account_count integer;
  written_count integer;
  portfolio_total numeric;
  portfolio_position numeric;
  portfolio_cash numeric;
begin
  perform public.pb_portfolio_writer_preflight_v3();

  if p_report_date is null or p_reported_at is null
     or not isfinite(p_report_date) or not isfinite(p_reported_at) then
    raise exception 'report_date and reported_at are required';
  end if;
  if p_source_message_ts is null
     or p_source_message_ts::text in ('NaN', 'Infinity', '-Infinity')
     or p_source_message_ts < 0 then
    raise exception 'source_message_ts must be nonnegative';
  end if;
  if p_source_schema_version <> 'pb-portfolio/v3' then
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

  if account_count <> 9 or observed_ids <> expected_ids then
    raise exception 'balances must contain the exact nine stable account IDs';
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
    9, 9, expected_ids, observed_ids, statement_timestamp()
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
  if written_count <> 9 then
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

grant select on table public.pb_algorithm_accounts to service_role;
grant select, insert, update on table public.pb_daily_algorithm_balances
  to service_role;
grant select, insert, update on table public.pb_daily_portfolio_totals
  to service_role;
grant select, insert on table public.pb_snapshot_runs to service_role;

revoke all on function public.pb_portfolio_writer_preflight_v3()
  from public, anon, authenticated;
revoke all on function public.pb_write_complete_portfolio_snapshot_v3(
  date, timestamptz, numeric, text, jsonb
) from public, anon, authenticated;
grant execute on function public.pb_portfolio_writer_preflight_v3()
  to service_role;
grant execute on function public.pb_write_complete_portfolio_snapshot_v3(
  date, timestamptz, numeric, text, jsonb
) to service_role;

notify pgrst, 'reload schema';
commit;

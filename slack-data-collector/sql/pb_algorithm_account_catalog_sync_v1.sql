-- Explicit, add-only account catalog registration for daily-report operators.
-- Apply pb_portfolio_history_v3.sql first. The daily snapshot job never invokes
-- this RPC automatically.

begin;

create or replace function public.pb_register_algorithm_accounts_v1(
  p_accounts jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  requested_count integer;
  inserted_count integer;
  catalog_count integer;
begin
  if p_accounts is null
     or jsonb_typeof(p_accounts) <> 'array'
     or jsonb_array_length(p_accounts) < 1 then
    raise exception 'accounts must be a non-empty JSON array';
  end if;

  select count(*) into requested_count
  from jsonb_to_recordset(p_accounts) as item(
    account_id text,
    jenkins_name text,
    algorithm_code text,
    instance_no integer,
    sort_order smallint
  );

  if exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text,
      jenkins_name text,
      algorithm_code text,
      instance_no integer,
      sort_order smallint
    )
    where item.account_id is null
      or item.account_id !~ '^[a-z0-9]+(-[a-z0-9]+)*$'
      or item.jenkins_name is null
      or btrim(item.jenkins_name) = ''
      or item.jenkins_name <> upper(btrim(item.jenkins_name))
      or item.algorithm_code is null
      or item.algorithm_code !~ '^[a-z0-9]+(-[a-z0-9]+)*$'
      or (item.instance_no is not null and item.instance_no < 1)
      or item.sort_order is null
      or item.sort_order < 1
  ) then
    raise exception 'account catalog input contains an invalid field';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text, jenkins_name text, algorithm_code text,
      instance_no integer, sort_order smallint
    )
    group by item.account_id
    having count(*) > 1
  ) or exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text, jenkins_name text, algorithm_code text,
      instance_no integer, sort_order smallint
    )
    group by item.jenkins_name
    having count(*) > 1
  ) or exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text, jenkins_name text, algorithm_code text,
      instance_no integer, sort_order smallint
    )
    group by item.sort_order
    having count(*) > 1
  ) then
    raise exception 'account catalog input contains duplicate IDs, names, or sort orders';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text, jenkins_name text, algorithm_code text,
      instance_no integer, sort_order smallint
    )
    join public.pb_algorithm_accounts existing
      on existing.account_id = item.account_id
    where existing.jenkins_name is distinct from item.jenkins_name
       or existing.algorithm_code is distinct from item.algorithm_code
       or existing.instance_no is distinct from item.instance_no
       or existing.sort_order is distinct from item.sort_order
  ) or exists (
    select 1
    from jsonb_to_recordset(p_accounts) as item(
      account_id text, jenkins_name text, algorithm_code text,
      instance_no integer, sort_order smallint
    )
    join public.pb_algorithm_accounts existing
      on existing.jenkins_name = item.jenkins_name
      or existing.sort_order = item.sort_order
    where existing.account_id <> item.account_id
  ) then
    raise exception 'account catalog input conflicts with an existing mapping';
  end if;

  insert into public.pb_algorithm_accounts (
    account_id, jenkins_name, algorithm_code, instance_no, sort_order
  )
  select
    item.account_id,
    item.jenkins_name,
    item.algorithm_code,
    item.instance_no,
    item.sort_order
  from jsonb_to_recordset(p_accounts) as item(
    account_id text,
    jenkins_name text,
    algorithm_code text,
    instance_no integer,
    sort_order smallint
  )
  on conflict (account_id) do nothing;

  get diagnostics inserted_count = row_count;

  select count(*) into catalog_count
  from public.pb_algorithm_accounts;

  return jsonb_build_object(
    'requested_count', requested_count,
    'inserted_count', inserted_count,
    'catalog_count', catalog_count
  );
end;
$$;

revoke all on function public.pb_register_algorithm_accounts_v1(jsonb) from public;
revoke all on function public.pb_register_algorithm_accounts_v1(jsonb) from anon;
revoke all on function public.pb_register_algorithm_accounts_v1(jsonb) from authenticated;
grant execute on function public.pb_register_algorithm_accounts_v1(jsonb) to service_role;

notify pgrst, 'reload schema';

commit;

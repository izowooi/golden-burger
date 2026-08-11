-- Daily host/filesystem capacity evidence for the private Polymarket dashboard.
-- Writers and readers must use a server-only Supabase Secret key. No browser role
-- receives direct table or RPC access.

begin;

create table if not exists public.pb_host_storage_daily (
  report_date date not null,
  host_id text not null,
  mount_id text not null,
  mount_label text not null,
  mount_path text not null,
  total_bytes bigint not null,
  used_bytes bigint not null,
  available_bytes bigint not null,
  reported_at timestamptz not null,
  source_schema_version text not null default 'pb-storage/v1',
  updated_at timestamptz not null default statement_timestamp(),
  primary key (report_date, host_id, mount_id),
  constraint pb_host_storage_daily_host_id_check
    check (host_id ~ '^[a-z0-9][a-z0-9._-]{0,62}$'),
  constraint pb_host_storage_daily_mount_id_check
    check (mount_id ~ '^[a-z0-9][a-z0-9._-]{0,62}$'),
  constraint pb_host_storage_daily_mount_label_check
    check (length(mount_label) between 1 and 100),
  constraint pb_host_storage_daily_mount_path_check
    check (left(mount_path, 1) = '/' and length(mount_path) <= 1024),
  constraint pb_host_storage_daily_bytes_check
    check (
      total_bytes > 0
      and used_bytes >= 0
      and available_bytes >= 0
      and used_bytes <= total_bytes
      and available_bytes <= total_bytes
    ),
  constraint pb_host_storage_daily_schema_check
    check (source_schema_version = 'pb-storage/v1')
);

create index if not exists pb_host_storage_daily_latest_idx
  on public.pb_host_storage_daily (host_id, mount_id, report_date desc);

alter table public.pb_host_storage_daily enable row level security;

create or replace function public.pb_storage_writer_preflight_v1()
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
  perform 1 from public.pb_host_storage_daily limit 1;
  return jsonb_build_object('contract_version', 'pb-storage/v1');
end;
$$;

create or replace function public.pb_write_host_storage_snapshot_v1(
  p_report_date date,
  p_reported_at timestamptz,
  p_host_id text,
  p_mounts jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
  mount_count integer;
  distinct_mount_count integer;
  written_count integer;
begin
  perform public.pb_storage_writer_preflight_v1();

  if p_report_date is null or p_reported_at is null
     or not isfinite(p_report_date) or not isfinite(p_reported_at) then
    raise exception 'report_date and reported_at are required';
  end if;
  if p_host_id is null or p_host_id !~ '^[a-z0-9][a-z0-9._-]{0,62}$' then
    raise exception 'invalid host_id';
  end if;
  if p_mounts is null or jsonb_typeof(p_mounts) <> 'array'
     or jsonb_array_length(p_mounts) < 1
     or jsonb_array_length(p_mounts) > 32 then
    raise exception 'mounts must be a JSON array containing 1..32 rows';
  end if;

  select count(*), count(distinct item.mount_id)
  into mount_count, distinct_mount_count
  from jsonb_to_recordset(p_mounts) as item(
    mount_id text,
    mount_label text,
    mount_path text,
    total_bytes bigint,
    used_bytes bigint,
    available_bytes bigint
  );

  if mount_count <> distinct_mount_count then
    raise exception 'mount_id must be unique within one host snapshot';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_mounts) as item(
      mount_id text,
      mount_label text,
      mount_path text,
      total_bytes bigint,
      used_bytes bigint,
      available_bytes bigint
    )
    where item.mount_id is null
      or item.mount_id !~ '^[a-z0-9][a-z0-9._-]{0,62}$'
      or item.mount_label is null
      or length(item.mount_label) not between 1 and 100
      or item.mount_path is null
      or left(item.mount_path, 1) <> '/'
      or length(item.mount_path) > 1024
      or item.total_bytes is null
      or item.used_bytes is null
      or item.available_bytes is null
      or item.total_bytes <= 0
      or item.used_bytes < 0
      or item.available_bytes < 0
      or item.used_bytes > item.total_bytes
      or item.available_bytes > item.total_bytes
  ) then
    raise exception 'invalid mount capacity snapshot';
  end if;

  insert into public.pb_host_storage_daily as existing (
    report_date,
    host_id,
    mount_id,
    mount_label,
    mount_path,
    total_bytes,
    used_bytes,
    available_bytes,
    reported_at,
    source_schema_version,
    updated_at
  )
  select
    p_report_date,
    p_host_id,
    item.mount_id,
    item.mount_label,
    item.mount_path,
    item.total_bytes,
    item.used_bytes,
    item.available_bytes,
    p_reported_at,
    'pb-storage/v1',
    statement_timestamp()
  from jsonb_to_recordset(p_mounts) as item(
    mount_id text,
    mount_label text,
    mount_path text,
    total_bytes bigint,
    used_bytes bigint,
    available_bytes bigint
  )
  on conflict (report_date, host_id, mount_id) do update set
    mount_label = excluded.mount_label,
    mount_path = excluded.mount_path,
    total_bytes = excluded.total_bytes,
    used_bytes = excluded.used_bytes,
    available_bytes = excluded.available_bytes,
    reported_at = excluded.reported_at,
    source_schema_version = excluded.source_schema_version,
    updated_at = excluded.updated_at
  where excluded.reported_at >= existing.reported_at;

  get diagnostics written_count = row_count;
  if written_count <> mount_count then
    raise exception 'stale or incomplete host storage snapshot rejected';
  end if;

  return jsonb_build_object(
    'contract_version', 'pb-storage/v1',
    'report_date', p_report_date,
    'host_id', p_host_id,
    'mount_count', mount_count,
    'reported_at', p_reported_at
  );
end;
$$;

revoke all on table public.pb_host_storage_daily
  from public, anon, authenticated;
grant select, insert, update on table public.pb_host_storage_daily
  to service_role;

revoke all on function public.pb_storage_writer_preflight_v1()
  from public, anon, authenticated;
revoke all on function public.pb_write_host_storage_snapshot_v1(
  date, timestamptz, text, jsonb
) from public, anon, authenticated;
grant execute on function public.pb_storage_writer_preflight_v1()
  to service_role;
grant execute on function public.pb_write_host_storage_snapshot_v1(
  date, timestamptz, text, jsonb
) to service_role;

notify pgrst, 'reload schema';
commit;

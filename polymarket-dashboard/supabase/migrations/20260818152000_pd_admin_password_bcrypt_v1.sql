-- Move password verification into Postgres so Cloudflare's request CPU budget is not spent on a KDF.
alter table public.pd_admin_credentials
  drop constraint if exists pd_admin_credentials_password_salt_check,
  drop constraint if exists pd_admin_credentials_password_hash_check,
  drop constraint if exists pd_admin_credentials_iterations_check;

alter table public.pd_admin_credentials
  drop column password_salt,
  drop column iterations,
  add column algorithm text not null default 'BCRYPT'
    check (algorithm = 'BCRYPT');

update public.pd_admin_credentials
set password_hash = extensions.crypt(
  encode(extensions.gen_random_bytes(32), 'hex'),
  extensions.gen_salt('bf', 10)
);

alter table public.pd_admin_credentials
  add constraint pd_admin_credentials_password_hash_check check (
    algorithm = 'BCRYPT' and char_length(password_hash) = 60
  );

comment on table public.pd_admin_credentials is
  'Server-only bcrypt credential verifier. Never stores or returns the plaintext password.';

create or replace function public.pd_verify_strategy_admin_password(candidate_password text)
returns boolean
language plpgsql
security invoker
set search_path = pg_catalog, public, extensions
as $$
declare
  stored_hash text;
begin
  if candidate_password is null or char_length(candidate_password) > 256 then
    return false;
  end if;

  select password_hash into stored_hash
  from public.pd_admin_credentials
  where credential_id = 'strategy-dashboard' and algorithm = 'BCRYPT';

  return stored_hash is not null
    and extensions.crypt(candidate_password, stored_hash) = stored_hash;
end;
$$;

create or replace function public.pd_set_strategy_admin_password(candidate_password text)
returns void
language plpgsql
security invoker
set search_path = pg_catalog, public, extensions
as $$
begin
  if candidate_password is null
     or char_length(candidate_password) < 8
     or char_length(candidate_password) > 256 then
    raise exception 'administrator password must contain between 8 and 256 characters';
  end if;

  insert into public.pd_admin_credentials (
    credential_id,
    password_hash,
    algorithm
  ) values (
    'strategy-dashboard',
    extensions.crypt(candidate_password, extensions.gen_salt('bf', 10)),
    'BCRYPT'
  )
  on conflict (credential_id) do update set
    password_hash = excluded.password_hash,
    algorithm = excluded.algorithm;
end;
$$;

revoke all on function public.pd_verify_strategy_admin_password(text) from public, anon, authenticated;
revoke all on function public.pd_set_strategy_admin_password(text) from public, anon, authenticated;
grant execute on function public.pd_verify_strategy_admin_password(text) to service_role;
grant execute on function public.pd_set_strategy_admin_password(text) to service_role;

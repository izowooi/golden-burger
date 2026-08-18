-- Server-only credential verifier for the lightweight strategy dashboard admin mode.
-- Password material is provisioned separately and is never committed to source control.
create table public.pd_admin_credentials (
  credential_id text primary key,
  password_salt text not null check (password_salt ~ '^[0-9a-f]{32}$'),
  password_hash text not null check (password_hash ~ '^[0-9a-f]{64}$'),
  iterations integer not null check (iterations between 100000 and 1000000),
  updated_at timestamptz not null default now(),
  constraint pd_admin_credentials_singleton check (credential_id = 'strategy-dashboard')
);

comment on table public.pd_admin_credentials is
  'Server-only PBKDF2 credential verifier. Never stores or returns the plaintext password.';

create trigger pd_admin_credentials_set_updated_at
before update on public.pd_admin_credentials
for each row execute function public.pd_set_updated_at();

alter table public.pd_admin_credentials enable row level security;
revoke all on table public.pd_admin_credentials from anon, authenticated;

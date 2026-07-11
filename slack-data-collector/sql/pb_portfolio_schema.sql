create table if not exists public.pb_algorithm_accounts (
  account_id text primary key,
  jenkins_name text not null unique,
  algorithm_code text not null,
  instance_no smallint,
  sort_order smallint not null,
  created_at timestamptz not null default now(),
  constraint pb_algorithm_accounts_account_id_format_check
    check (account_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  constraint pb_algorithm_accounts_instance_no_check
    check (instance_no is null or instance_no > 0)
);

comment on table public.pb_algorithm_accounts is
  'Polymarket Bot 알고리즘 계정 카탈로그. Jenkins 이름과 안정적 account_id를 매핑한다.';

create table if not exists public.pb_daily_portfolio_totals (
  report_date date primary key,
  total_value numeric(18, 2) not null,
  position_value numeric(18, 2) not null,
  cash_value numeric(18, 2) not null,
  currency text not null default 'USD',
  reported_at timestamptz not null,
  source_message_ts numeric(20, 6) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint pb_daily_portfolio_totals_currency_check
    check (currency ~ '^[A-Z]{3}$'),
  constraint pb_daily_portfolio_totals_nonnegative_check
    check (total_value >= 0 and position_value >= 0 and cash_value >= 0),
  constraint pb_daily_portfolio_totals_breakdown_check
    check (total_value = position_value + cash_value)
);

comment on table public.pb_daily_portfolio_totals is
  'Polymarket Bot 날짜별 전체 포트폴리오 잔고. 같은 날짜는 최신 Slack ts가 우선한다.';

create table if not exists public.pb_daily_algorithm_balances (
  report_date date not null,
  account_id text not null references public.pb_algorithm_accounts(account_id) on delete restrict,
  total_value numeric(18, 2) not null,
  position_value numeric(18, 2) not null,
  cash_value numeric(18, 2) not null,
  currency text not null default 'USD',
  reported_at timestamptz not null,
  source_message_ts numeric(20, 6) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (report_date, account_id),
  constraint pb_daily_algorithm_balances_currency_check
    check (currency ~ '^[A-Z]{3}$'),
  constraint pb_daily_algorithm_balances_nonnegative_check
    check (total_value >= 0 and position_value >= 0 and cash_value >= 0),
  constraint pb_daily_algorithm_balances_breakdown_check
    check (total_value = position_value + cash_value)
);

comment on table public.pb_daily_algorithm_balances is
  'Polymarket Bot 날짜별 알고리즘 계정 잔고. total, position, cash를 분리 저장한다.';

create index if not exists pb_daily_algorithm_balances_account_date_idx
  on public.pb_daily_algorithm_balances (account_id, report_date desc);

alter table public.pb_algorithm_accounts enable row level security;
alter table public.pb_daily_portfolio_totals enable row level security;
alter table public.pb_daily_algorithm_balances enable row level security;

revoke all on table public.pb_algorithm_accounts from anon, authenticated;
revoke all on table public.pb_daily_portfolio_totals from anon, authenticated;
revoke all on table public.pb_daily_algorithm_balances from anon, authenticated;

-- Apply pb_portfolio_history_v2.sql immediately after this base schema. The live
-- daily writer requires its preflight + atomic snapshot RPC; these three tables
-- alone are retained only as the base/backfill compatibility layer.

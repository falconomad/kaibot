-- Kaibot v2 initial schema (tech design v2 §4). Paper trading only.
-- All tables live in schema `kaibot`. RLS is ON everywhere with NO anon/authenticated policies:
-- the dashboard never talks to Postgres directly; the API on the VM uses the service role.
create schema if not exists kaibot;

-- ---------- runs ----------
create table kaibot.run (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  amount_usd    numeric(14,2) not null check (amount_usd between 1000 and 100000),
  eur_ref       numeric(14,2),
  fx_usd_per_eur numeric(10,6),
  status        text not null default 'scheduled' check (status in ('scheduled','running','paused','stopped','ended')),
  end_reason    text,
  peak_equity   numeric(14,2)
);
create table kaibot.segment (
  id            bigint generated always as identity primary key,
  run_id        bigint not null references kaibot.run(id),
  level         text not null check (level in ('conservative','balanced','aggressive')),
  from_session  date not null,
  policy_hash   text not null
);
create table kaibot.universe_daily (
  session       date not null,
  symbol        text not null,
  quality_score numeric(5,2),
  adv_usd       numeric(18,2),
  sector        text,
  sector_etf    text,
  primary key (session, symbol)
);

-- ---------- decisions ----------
create table kaibot.signal (
  id            bigint generated always as identity primary key,
  run_id        bigint references kaibot.run(id),
  strategy      text not null,
  mode          text not null check (mode in ('live','shadow')),
  symbol        text not null,
  ts            timestamptz not null,
  features_hash text not null,
  features      jsonb not null,
  entry         numeric(14,4),
  stop          numeric(14,4),
  target        numeric(14,4),
  reasons       text[] not null default '{}',
  rules_veto    text
);
create index on kaibot.signal (run_id, ts desc);
create table kaibot.room_session (
  id            bigint generated always as identity primary key,
  signal_id     bigint not null references kaibot.signal(id),
  proposer      jsonb, critic jsonb, judge jsonb,
  valid         boolean not null,
  verdict       text check (verdict in ('allow','downgrade','veto')),
  latency_ms    integer,
  cost_usd      numeric(10,5)
);
create table kaibot.gate_run (
  id            bigint generated always as identity primary key,
  signal_id     bigint not null references kaibot.signal(id),
  ts            timestamptz not null default now(),
  policy_hash   text not null,
  code_sha      text not null,
  checks        jsonb not null,
  approved      boolean not null,
  qty           numeric(14,4) not null default 0,
  binding       text
);

-- ---------- execution ----------
create table kaibot."order" (
  id              bigint generated always as identity primary key,
  signal_id       bigint references kaibot.signal(id),
  client_order_id text not null unique,          -- kb-{run}-{signal}-{attempt}; idempotency at the venue
  venue           text not null default 'alpaca_paper' check (venue in ('alpaca_paper','sim')),
  legs            jsonb not null,
  status          text not null,
  submitted_at    timestamptz,
  updated_at      timestamptz not null default now()
);
create table kaibot.fill (
  id              bigint generated always as identity primary key,
  order_id        bigint not null references kaibot."order"(id),
  ts              timestamptz not null,
  side            text not null check (side in ('buy','sell')),
  qty             numeric(14,4) not null,
  venue_px        numeric(14,4) not null,
  shadow_px       numeric(14,4) not null,
  fees            numeric(10,4) not null default 0,
  tag             text                               -- e.g. user_flatten
);
create table kaibot.ledger (
  run_id          bigint not null references kaibot.run(id),
  ts              timestamptz not null,
  cash            numeric(14,2) not null,
  positions       jsonb not null,
  equity_venue    numeric(14,2) not null,
  equity_shadow   numeric(14,2) not null,
  primary key (run_id, ts)
);
create table kaibot.daily_result (
  run_id          bigint not null references kaibot.run(id),
  session         date not null,
  pnl_venue       numeric(14,2) not null,
  pnl_shadow      numeric(14,2) not null,
  pnl_reported    numeric(14,2) generated always as (least(pnl_venue, pnl_shadow)) stored,
  spy_ret         numeric(10,6),
  cash_ret        numeric(10,6),
  trades          integer not null default 0,
  primary key (run_id, session)
);

-- ---------- ops ----------
create table kaibot.halt (
  id bigint generated always as identity primary key,
  run_id bigint references kaibot.run(id),
  ts timestamptz not null default now(), kind text not null, reason text not null, resumes_at timestamptz
);
create table kaibot.command (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  idempotency_key text not null unique,
  kind text not null check (kind in ('pause','resume','flatten_and_stop','new_run','set_level')),
  payload jsonb not null default '{}',
  applied_at timestamptz
);
create table kaibot.heartbeat (
  process text primary key, ts timestamptz not null, detail jsonb
);

-- ---------- evidence ----------
create table kaibot.filing (
  accession text primary key, cik text not null, symbol text, form text not null,
  accepted_at timestamptz not null, available_ts timestamptz not null, url text
);
create table kaibot.news_cluster (
  id bigint generated always as identity primary key,
  tickers text[] not null, first_seen timestamptz not null, n_outlets integer not null default 1,
  canonical_headline text not null, triage jsonb
);
create table kaibot.news_item (
  id bigint generated always as identity primary key,
  cluster_id bigint references kaibot.news_cluster(id),
  source text not null, published_at timestamptz not null, available_ts timestamptz not null,
  headline text not null, url text, tickers text[] not null default '{}', finbert numeric(5,4),
  headline_sha1 text not null
);
create index on kaibot.news_item (available_ts desc);
create table kaibot.macro_event (
  id bigint generated always as identity primary key,
  kind text not null, at timestamptz not null, source text not null, expected jsonb, actual jsonb
);

-- ---------- LLM budget ----------
create table kaibot.llm_call (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(), model text not null, purpose text not null,
  reserved_usd numeric(10,5) not null, actual_usd numeric(10,5), input_tokens integer, output_tokens integer,
  cache_read_tokens integer
);
create table kaibot.budget_day (
  day date primary key, spent_usd numeric(10,4) not null default 0, state text not null default 'ok'
);

-- ---------- research ----------
create table kaibot.trial (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(), strategy text not null, params jsonb not null,
  code_sha text not null, window_start date not null, window_end date not null, metrics jsonb
);

-- ---------- append-only, hash-chained event log ----------
create table kaibot.event (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  run_id bigint references kaibot.run(id),
  type text not null,
  payload jsonb not null,
  prev_hash text,
  hash text not null
);
create or replace function kaibot.forbid_mutation() returns trigger language plpgsql
  set search_path = '' as $$
begin
  raise exception 'kaibot.event is append-only';
end $$;
create trigger event_append_only before update or delete on kaibot.event
  for each row execute function kaibot.forbid_mutation();

-- ---------- lock everything down ----------
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'kaibot' loop
    execute format('alter table kaibot.%I enable row level security', t.tablename);
  end loop;
end $$;
revoke all on schema kaibot from anon, authenticated;

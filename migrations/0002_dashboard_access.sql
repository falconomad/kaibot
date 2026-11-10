-- Dashboard access (decision 10). The browser never reads kaibot tables directly: it calls a few
-- SECURITY DEFINER functions in `public`, each of which first checks that the caller is the single owner.
-- anon can call nothing. The only write is kb_new_run, which queues a command for the engine.

-- ---------- owner ----------
create table kaibot.owner (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  claimed_at timestamptz not null default now()
);
alter table kaibot.owner enable row level security;
create unique index owner_singleton on kaibot.owner ((true));   -- at most one owner, ever

-- ---------- run kinds + round-trip trades ----------
alter table kaibot.run add column kind text not null default 'paper' check (kind in ('paper', 'replay'));
alter table kaibot.run add column label text;
alter table kaibot.run add column level text check (level in ('conservative', 'balanced', 'aggressive'));
alter table kaibot.run add column started_on date;
alter table kaibot.run add column ended_on date;
alter table kaibot.run drop constraint if exists run_amount_usd_check;
alter table kaibot.run add constraint run_amount_usd_check
  check (kind = 'replay' or amount_usd between 1000 and 100000);

-- One row per closed (or open) round trip. The engine writes it on exit; replays write it directly.
create table kaibot.trade (
  id           bigint generated always as identity primary key,
  run_id       bigint not null references kaibot.run(id) on delete cascade,
  signal_id    bigint references kaibot.signal(id),
  strategy     text not null,
  symbol       text not null,
  entry_ts     timestamptz not null,
  exit_ts      timestamptz,
  qty          numeric(14,4) not null,
  entry_px     numeric(14,4) not null,
  exit_px      numeric(14,4),
  stop_px      numeric(14,4),
  target_px    numeric(14,4),
  risk_usd     numeric(14,2),
  exit_reason  text,
  pnl_usd      numeric(14,2),
  verdict      text check (verdict in ('allow', 'downgrade', 'veto')),
  features     jsonb not null default '{}'
);
create index on kaibot.trade (run_id, entry_ts desc);
alter table kaibot.trade enable row level security;

-- ---------- functions ----------
create or replace function kaibot.is_owner() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from kaibot.owner where user_id = (select auth.uid()));
$$;

-- First signed-in user becomes the owner; after that nobody else can.
create or replace function public.kb_claim_owner() returns boolean
language plpgsql security definer set search_path = '' as $$
begin
  if (select auth.uid()) is null then return false; end if;
  insert into kaibot.owner (user_id) select (select auth.uid())
    where not exists (select 1 from kaibot.owner);
  return kaibot.is_owner();
end $$;

create or replace function public.kb_runs() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(to_jsonb(r) order by r.id desc) from kaibot.run r), '[]'::jsonb);
end $$;

create or replace function public.kb_overview(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select jsonb_build_object(
    'run', (select to_jsonb(r) from kaibot.run r where r.id = p_run),
    'daily', coalesce((select jsonb_agg(jsonb_build_object(
        'session', d.session, 'pnl', d.pnl_reported, 'pnl_venue', d.pnl_venue, 'pnl_shadow', d.pnl_shadow,
        'spy_ret', d.spy_ret, 'trades', d.trades) order by d.session)
      from kaibot.daily_result d where d.run_id = p_run), '[]'::jsonb),
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h), '[]'::jsonb),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts, 'payload', c.payload))
      from kaibot.command c where c.applied_at is null), '[]'::jsonb)
  ) into out;
  return out;
end $$;

create or replace function public.kb_trades(p_run bigint, p_limit int default 50, p_offset int default 0) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object(
    'total', (select count(*) from kaibot.trade t where t.run_id = p_run),
    'rows', coalesce((select jsonb_agg(to_jsonb(x) - 'features') from (
      select * from kaibot.trade t where t.run_id = p_run
      order by t.entry_ts desc limit least(greatest(p_limit, 1), 500) offset greatest(p_offset, 0)) x), '[]'::jsonb));
end $$;

create or replace function public.kb_trade(p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare t kaibot.trade;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select * into t from kaibot.trade where id = p_id;
  if t.id is null then return null; end if;
  return jsonb_build_object(
    'trade', to_jsonb(t),
    'run', (select to_jsonb(r) from kaibot.run r where r.id = t.run_id),
    'gate', (select to_jsonb(g) from kaibot.gate_run g where g.signal_id = t.signal_id order by g.ts desc limit 1),
    'room', (select to_jsonb(s) from kaibot.room_session s where s.signal_id = t.signal_id limit 1),
    'fills', coalesce((select jsonb_agg(to_jsonb(f) order by f.ts) from kaibot.fill f
             join kaibot."order" o on o.id = f.order_id where o.signal_id = t.signal_id), '[]'::jsonb));
end $$;

-- Onboarding: queue a new paper run for the engine. It never trades from here.
create or replace function public.kb_new_run(p_amount numeric, p_level text, p_idem text) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if p_amount is null or p_amount < 1000 or p_amount > 100000 then raise exception 'amount must be $1,000-$100,000'; end if;
  if p_level not in ('conservative', 'balanced', 'aggressive') then raise exception 'unknown level'; end if;
  if p_idem is null or length(p_idem) < 8 then raise exception 'idempotency key required'; end if;
  insert into kaibot.command (idempotency_key, kind, payload)
    values (p_idem, 'new_run', jsonb_build_object('amount_usd', p_amount, 'level', p_level))
    on conflict (idempotency_key) do nothing;
  return jsonb_build_object('queued', true);
end $$;

revoke all on function kaibot.is_owner() from public, anon, authenticated;
revoke all on function public.kb_claim_owner(), public.kb_runs(), public.kb_overview(bigint),
  public.kb_trades(bigint, int, int), public.kb_trade(bigint), public.kb_new_run(numeric, text, text)
  from public, anon;
grant execute on function public.kb_claim_owner(), public.kb_runs(), public.kb_overview(bigint),
  public.kb_trades(bigint, int, int), public.kb_trade(bigint), public.kb_new_run(numeric, text, text)
  to authenticated;
-- the definer functions call kaibot.is_owner() as their owner (postgres), so authenticated needs no grant on it

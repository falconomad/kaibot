-- 0039: a narrow key path for the helpers, so that only the engine holds the service key (M10 ticket 1.26, issue
-- #451; engine audit of 21 Nov 2026, finding S1).
--
-- Status (applied or pending): docs/ops/migrations_pending.md. One-time setup after applying: docs/ops/helper_role.md.
--
-- Why. The price relay (scripts/price_stream.py), the notice publisher (scripts/publish_notices.py) and the
-- research scripts used the service key. That key bypasses every policy: a process that holds it can insert any
-- command, take the engine lease, write a fresh engine heartbeat (so the dead-man never fires) or replace the
-- owner row. The helpers need three writes and one read.
--
-- What changes. A helper is an ordinary Supabase Auth user (email and password, created once by the stakeholder)
-- listed in kaibot.helper. It signs in with the publishable key and gets the `authenticated` role, which has no
-- right on any kaibot table (0004). It can call only the functions below, each of which checks kaibot.is_helper():
--   kb_helper_whoami()                      is this sign-in a helper? (the setup check)
--   kb_helper_heartbeat(process, detail)    upsert the heartbeat row of a helper process: 'price_stream' only,
--                                           never 'engine' or 'watchdog' (the dead-man and the watchdog read those)
--   kb_helper_publish_notices(rows, lo, hi) upsert market notices and withdraw the window's rows no longer found
--                                           (what publish_notices.py did with three table calls)
--   kb_helper_trades(run, strategy, after, limit)   nine columns of the closed trades, read-only, paged by id
--                                           (scripts/run_ablation.py)
-- and it may SEND on the private Realtime channel kb:prices (one INSERT policy on realtime.messages; 0032's SELECT
-- policy for the owner is unchanged). Nothing else: no command, no lease, no halt, no run, no ledger, no owner row.
-- The owner is never a helper (is_helper is false for the owner's user id), so the dashboard's sign-in gains nothing.
--
-- Additive: `create table if not exists`, `create or replace function`, revoke/grant, `create policy` only when it
-- does not exist yet. No delete, drop or truncate; the two `update`s are inside the new functions (a heartbeat
-- upsert, a notice upsert / withdrawal). No engine object changes: the engine, the watchdog and the dead-man work
-- the same before and after. Any time, also while the engine runs. Until the setup of docs/ops/helper_role.md is
-- done the helpers keep using the service key, unchanged.
--
-- Check after applying (read-only):
--   select has_table_privilege('authenticated', 'kaibot.helper', 'select');                               -- false
--   select has_function_privilege('anon', 'public.kb_helper_heartbeat(text, jsonb)', 'execute');         -- false
--   select policyname, cmd, roles from pg_policies where schemaname = 'realtime' and tablename = 'messages'
--     and policyname = 'kb helper sends prices';                                    -- one row, INSERT, {authenticated}
--   select count(*) from kaibot.helper;                                              -- 0 until the setup's step 2

create table if not exists kaibot.helper (
  user_id  uuid primary key references auth.users (id) on delete cascade,
  name     text not null check (length(name) between 1 and 60),
  added_at timestamptz not null default now()
);
alter table kaibot.helper enable row level security;  -- no policies: only service_role and the SQL editor
revoke all on kaibot.helper from public, anon, authenticated;
grant select, insert, delete on kaibot.helper to service_role;

create or replace function kaibot.is_helper() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from kaibot.helper h where h.user_id = (select auth.uid()))
     and not exists (select 1 from kaibot.owner o where o.user_id = (select auth.uid()));
$$;
revoke all on function kaibot.is_helper() from public, anon, authenticated;

create or replace function public.kb_helper_whoami() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('helper', kaibot.is_helper());
$$;

create or replace function public.kb_helper_heartbeat(p_process text, p_detail jsonb) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not kaibot.is_helper() then raise exception 'not authorised' using errcode = '42501'; end if;
  if p_process is null or p_process not in ('price_stream') then
    raise exception 'not a helper process: %', coalesce(p_process, 'null') using errcode = '22023';
  end if;
  if p_detail is not null and (jsonb_typeof(p_detail) <> 'object' or length(p_detail::text) > 8000) then
    raise exception 'detail must be an object of at most 8000 characters' using errcode = '22023';
  end if;
  insert into kaibot.heartbeat (process, ts, detail) values (p_process, now(), p_detail)
  on conflict (process) do update set ts = excluded.ts, detail = excluded.detail;
end $$;

create or replace function public.kb_helper_publish_notices(p_rows jsonb, p_lo date, p_hi date) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare n int; gone jsonb;
begin
  if not kaibot.is_helper() then raise exception 'not authorised' using errcode = '42501'; end if;
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) > 500 then
    raise exception 'rows must be an array of at most 500 notices' using errcode = '22023';
  end if;
  if p_lo is null or p_hi is null or p_hi < p_lo or p_hi - p_lo > 60 then
    raise exception 'the window must be at most 60 days' using errcode = '22023';
  end if;
  insert into kaibot.market_notice (id, kind, flag, tier, session, symbol, title, starts, first_eligible, detail,
                                    available_ts, withdrawn, updated_at)
  select r->>'id', r->>'kind', r->>'flag', r->>'tier', (r->>'session')::date, r->>'symbol', r->>'title',
         (r->>'starts')::timestamptz, (r->>'first_eligible')::date, coalesce(r->'detail', '{}'::jsonb),
         (r->>'available_ts')::timestamptz, coalesce((r->>'withdrawn')::boolean, false), now()
  from jsonb_array_elements(p_rows) r
  on conflict (id) do update set kind = excluded.kind, flag = excluded.flag, tier = excluded.tier,
    session = excluded.session, symbol = excluded.symbol, title = excluded.title, starts = excluded.starts,
    first_eligible = excluded.first_eligible, detail = excluded.detail, available_ts = excluded.available_ts,
    withdrawn = excluded.withdrawn, updated_at = excluded.updated_at;
  get diagnostics n = row_count;
  with w as (
    update kaibot.market_notice m set withdrawn = true, updated_at = now()
    where not m.withdrawn and m.session between p_lo and p_hi
      and not exists (select 1 from jsonb_array_elements(p_rows) r where r->>'id' = m.id)
    returning m.id)
  select coalesce(jsonb_agg(w.id order by w.id), '[]'::jsonb) into gone from w;
  return jsonb_build_object('written', n, 'withdrawn', gone);
end $$;

create or replace function public.kb_helper_trades(p_run bigint default null, p_strategy text default null,
                                                   p_after bigint default 0, p_limit int default 1000) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_helper() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(to_jsonb(x) order by x.id) from (
    select t.id, t.run_id, t.strategy, t.symbol, t.entry_ts, t.exit_ts, t.risk_usd, t.pnl_usd, t.exit_reason
    from kaibot.trade t
    where t.exit_ts is not null and t.id > coalesce(p_after, 0)
      and (p_run is null or t.run_id = p_run) and (p_strategy is null or t.strategy = p_strategy)
    order by t.id limit least(greatest(coalesce(p_limit, 1000), 1), 1000)) x), '[]'::jsonb);
end $$;

create or replace function public.kb_price_stream_sender() returns boolean
language sql stable security definer set search_path = '' as $$
  select kaibot.is_helper();
$$;

revoke all on function public.kb_helper_whoami() from public, anon;
revoke all on function public.kb_helper_heartbeat(text, jsonb) from public, anon;
revoke all on function public.kb_helper_publish_notices(jsonb, date, date) from public, anon;
revoke all on function public.kb_helper_trades(bigint, text, bigint, int) from public, anon;
revoke all on function public.kb_price_stream_sender() from public, anon;
grant execute on function public.kb_helper_whoami() to authenticated;
grant execute on function public.kb_helper_heartbeat(text, jsonb) to authenticated;
grant execute on function public.kb_helper_publish_notices(jsonb, date, date) to authenticated;
grant execute on function public.kb_helper_trades(bigint, text, bigint, int) to authenticated;
grant execute on function public.kb_price_stream_sender() to authenticated;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname = 'realtime' and tablename = 'messages'
                 and policyname = 'kb helper sends prices') then
    create policy "kb helper sends prices" on realtime.messages
      for insert to authenticated
      with check ((select realtime.topic()) = 'kb:prices'
                  and realtime.messages.extension = 'broadcast'
                  and (select public.kb_price_stream_sender()));
  end if;
end $$;

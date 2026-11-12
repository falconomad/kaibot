-- 0019: the off-box dead-man for multi-day books (M7 S80, issue #120; decision 15 sections 4 and 6; audit B1 item 8).
--
-- NOT APPLIED. Production migrations are the stakeholder's: apply this one on "apply", after 0017 and 0018 (it reads
-- kaibot.market_calendar_exception and kaibot.ledger_position). It must be applied before the first multi-day
-- position is held overnight (the same release, #133): until then 0006/0017 keep flattening the whole paper account
-- at the close minus 4 minutes, which is right for an intraday-only book and wrong for a multi-day one.
--
-- What changes. 0006/0017 sent DELETE /v2/positions?cancel_orders=true at the close minus 4 minutes when the engine
-- heartbeat was stale: every position sold, every protective stop cancelled. From this migration on, whenever the
-- market is open and the heartbeat is older than 3 minutes, the dead-man follows the same plan as the on-laptop
-- watchdog (src/kaibot/engine/failsafe.py; tests/test_failsafe_drills.py runs both on the same accounts):
--   1. cancel every open, unfilled Kaibot ENTRY order (a buy whose client_order_id starts with "kb");
--   2. verify that every multi-day sleeve (the active run's last kaibot.ledger_position rows of a multi-day
--      strategy) has its protective GTC stop at the broker; re-create a missing one in whole shares, at the
--      sleeve's stop_px, else 10% below the last price (risk/gate.py GAP_SCENARIO_PCT), never for more shares than
--      are free; client id kbdm-<strategy>-<symbol>-<yyyymmddhhmmss>-dmstop;
--   3. raise the alarm: one kaibot.halt row 'deadman' per session, and one kaibot.deadman_pass row per pass with
--      everything it did (result);
--   4. from the close minus 4 minutes to the close (15:56-16:00 ET, 12:56-13:00 on early closes): sell only the
--      INTRADAY shares, i.e. the shares of a symbol that no multi-day sleeve and no multi-day stop explains. A
--      symbol held only intraday: its orders are cancelled, then (next pass) the position is closed; a symbol held
--      by both: its intraday legs are cancelled, then (next pass) the intraday shares are sold at market. With no
--      multi-day shares anywhere it is the 0006 call itself: DELETE /v2/positions?cancel_orders=true, once.
--
-- How (pg_net answers asynchronously): one pg_cron job runs kaibot.deadman_check() every minute from 13:00 to
-- 21:59 UTC on weekdays; the function decides. A pass first READS the account (GET /v2/positions and GET
-- /v2/orders?status=open&nested=true; the request ids go into kaibot.deadman_pass), and the next run ACTS on the
-- answers (net._http_response). Reads are every 5 minutes while the engine is silent, every minute in the flatten
-- window. A pass whose answers have not come within 3 minutes is dropped ('expired').
--
-- Strategies: kaibot.strategy_holding names the rules that hold across sessions (the strategy registry of 0016
-- has the intraday ones only). tests/test_failsafe_drills.py keeps it equal to strategies/registry.MULTI_DAY. A
-- neighbour of a rule (trend_momentum~reg60) counts as the rule.
--
-- Paper only: the URL stays https://paper-api.alpaca.markets. kaibot.deadman_flatten(force) keeps its name and
-- signature (docs/ops/failsafe.md) and now runs kaibot.deadman_check(force).
-- Check after applying:
--   select jobname, schedule from cron.job where jobname like 'kaibot_deadman%';   -- one row: kaibot_deadman
--   select kaibot.deadman_check();       -- outside market hours: 'market closed' (or 'weekend', 'market holiday')

create table if not exists kaibot.strategy_holding (
  name    text primary key,
  holding text not null check (holding in ('intraday', 'multi_day'))
);
alter table kaibot.strategy_holding enable row level security;  -- no policies: only service_role
insert into kaibot.strategy_holding (name, holding) values
  ('trend_momentum', 'multi_day'),
  ('mom_12_1_band', 'multi_day'),
  ('mom_12_1_band_nofilter', 'multi_day')
on conflict (name) do update set holding = excluded.holding;

create table if not exists kaibot.deadman_pass (
  id            bigserial primary key,
  ts            timestamptz not null,
  session       date not null,
  req_positions bigint not null,
  req_orders    bigint not null,
  state         text not null default 'reading' check (state in ('reading', 'done', 'expired')),
  result        jsonb
);
create index if not exists deadman_pass_ts on kaibot.deadman_pass (ts);
alter table kaibot.deadman_pass enable row level security;

-- the name as a client id carries it (engine/orders.client_order_id)
create or replace function kaibot.deadman_clean(x text) returns text
language sql immutable set search_path = '' as $$
  select regexp_replace(coalesce(x, ''), '[^A-Za-z0-9_.]', '', 'g')
$$;

-- a strategy (or the strategy part of a client id) that holds across sessions
create or replace function kaibot.deadman_md(strategy text) returns boolean
language sql stable set search_path = '' as $$
  select coalesce(strategy, '') <> '' and exists (
    select 1 from kaibot.strategy_holding h
    where h.holding = 'multi_day'
      and left(kaibot.deadman_clean(strategy), length(kaibot.deadman_clean(h.name))) = kaibot.deadman_clean(h.name))
$$;

-- {strategy, symbol} of a Kaibot client id: the order manager's kb<run>-<strategy>-<symbol>-<yyyymmdd>-<purpose>-<n>,
-- or a fail-safe stop kbwd-... / kbdm-...; null for anything else
create or replace function kaibot.deadman_coid(coid text) returns text[]
language sql immutable set search_path = '' as $$
  select coalesce(
    regexp_match(coalesce(coid, ''), '^kb[0-9]+-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-[0-9]{8}-[A-Za-z0-9_.]+-[0-9]+$'),
    regexp_match(coalesce(coid, ''), '^kb(?:wd|dm)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-[0-9]{8,14}-[A-Za-z0-9_.]+$'))
$$;

-- the working sell orders and legs on a symbol (each holds shares at the venue), as (id, coid, type, qty left)
create or replace function kaibot.deadman_sells(ord jsonb, sym text)
returns table (id text, coid text, type text, qty numeric)
language sql immutable set search_path = '' as $$
  select o->>'id', o->>'client_order_id', o->>'type',
         greatest(coalesce((o->>'qty')::numeric, 0) - coalesce((o->>'filled_qty')::numeric, 0), 0)
  from jsonb_array_elements(coalesce(ord, '[]'::jsonb)) o
  where o->>'symbol' = sym and o->>'side' = 'sell'
    and o->>'status' in ('new', 'accepted', 'pending_new', 'partially_filled', 'held', 'accepted_for_bidding',
                         'pending_replace')
  union all
  select l->>'id', l->>'client_order_id', l->>'type',
         coalesce((l->>'qty')::numeric, (o->>'filled_qty')::numeric, (o->>'qty')::numeric, 0)
  from jsonb_array_elements(coalesce(ord, '[]'::jsonb)) o, jsonb_array_elements(coalesce(o->'legs', '[]'::jsonb)) l
  where o->>'symbol' = sym and o->>'status' = 'filled' and coalesce(l->>'side', 'sell') = 'sell'
    and l->>'status' in ('new', 'held', 'accepted', 'pending_new')
$$;

create or replace function kaibot.deadman_act(pass_id bigint, hdr jsonb, flatten boolean, t timestamptz)
returns jsonb
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  base constant text := 'https://paper-api.alpaca.markets';
  ny timestamp := t at time zone 'America/New_York';
  p kaibot.deadman_pass%rowtype;
  pos jsonb;
  ord jsonb;
  rid bigint;
  sess date;
  o jsonb;
  x record;
  s record;
  sym text;
  have numeric;
  last numeric;
  book_md numeric;
  covered numeric;
  md numeric;
  free numeric;
  want integer;
  q integer;
  px numeric;
  why text;
  intraday integer;
  any_md boolean := false;
  others text[];
  res jsonb := jsonb_build_object('canceled', '[]'::jsonb, 'stops', '[]'::jsonb, 'cancel', '[]'::jsonb,
                                  'closed', '[]'::jsonb, 'sold', '[]'::jsonb, 'multi_day', '{}'::jsonb,
                                  'unprotected', '{}'::jsonb, 'alarms', '[]'::jsonb, 'flatten', flatten);
begin
  select * into p from kaibot.deadman_pass where id = pass_id;
  select r.content::jsonb into pos from net._http_response r where r.id = p.req_positions and r.status_code = 200;
  select r.content::jsonb into ord from net._http_response r where r.id = p.req_orders and r.status_code = 200;
  if pos is null or ord is null then
    update kaibot.deadman_pass set state = 'expired', result = jsonb_build_object('error', 'the broker did not answer')
      where id = pass_id;
    return null;
  end if;

  -- 1. entry orders
  for o in select * from jsonb_array_elements(ord) loop
    if o->>'side' = 'buy' and coalesce(o->>'client_order_id', '') like 'kb%'
       and o->>'status' in ('new', 'accepted', 'pending_new', 'partially_filled', 'held', 'accepted_for_bidding',
                            'pending_replace')
       and o->>'filled_at' is null and coalesce((o->>'filled_qty')::numeric, 0) = 0 then
      perform net.http_delete(url := base || '/v2/orders/' || (o->>'id'), headers := hdr,
                              timeout_milliseconds := 10000);
      res := jsonb_set(res, '{canceled}', (res->'canceled') || to_jsonb(o->>'symbol'));
    end if;
  end loop;

  -- 2. the book: the active run's multi-day sleeves at its last close
  select r.id into rid from kaibot.run r where r.status in ('running', 'paused') and coalesce(r.kind, 'paper') = 'paper'
    order by r.id desc limit 1;
  select max(lp.session) into sess from kaibot.ledger_position lp where lp.run_id = rid;
  if sess is null then
    res := jsonb_set(res, '{alarms}', (res->'alarms')
             || to_jsonb('book unknown: multi-day shares taken from the resting multi-day stops only'::text));
  end if;

  for x in select e.key as symbol, e.value as v from jsonb_each(
             coalesce((select jsonb_object_agg(a->>'symbol', a) from jsonb_array_elements(pos) a), '{}'::jsonb)) e
           order by e.key loop
    sym := x.symbol;
    have := coalesce((x.v->>'qty')::numeric, 0);
    last := nullif((x.v->>'current_price')::numeric, 0);
    continue when have <= 0;
    select coalesce(sum(lp.qty), 0) into book_md from kaibot.ledger_position lp
      where lp.run_id = rid and lp.session = sess and lp.symbol = sym and kaibot.deadman_md(lp.strategy);
    select coalesce(sum(z.qty), 0) into covered from kaibot.deadman_sells(ord, sym) z
      where z.type in ('stop', 'stop_limit') and kaibot.deadman_md((kaibot.deadman_coid(z.coid))[1]);
    md := least(greatest(book_md, covered), have);
    select greatest(have - coalesce(sum(z.qty), 0), 0) into free from kaibot.deadman_sells(ord, sym) z;
    for s in select lp.strategy, sum(lp.qty) as qty, max(lp.stop_px) as stop_px from kaibot.ledger_position lp
             where lp.run_id = rid and lp.session = sess and lp.symbol = sym and kaibot.deadman_md(lp.strategy)
             group by lp.strategy order by lp.strategy loop
      select floor(s.qty)::integer - floor(coalesce(sum(z.qty), 0))::integer into want
        from kaibot.deadman_sells(ord, sym) z
        where z.type in ('stop', 'stop_limit')
          and (kaibot.deadman_coid(z.coid))[1] = kaibot.deadman_clean(s.strategy);
      continue when want < 1;
      -- a stop the dead-man itself sent in the last 3 minutes may not be listed yet: never send it twice
      continue when exists (select 1 from kaibot.deadman_pass dp, jsonb_array_elements(dp.result->'stops') st
                            where dp.id <> pass_id and dp.ts > t - interval '3 minutes'
                              and st->>'symbol' = sym and st->>'strategy' = s.strategy
                              and (st->>'qty')::integer >= want);
      q := least(want, floor(free)::integer);
      if s.stop_px is not null and s.stop_px > 0 and (last is null or s.stop_px < last) then
        px := round(s.stop_px, 2); why := 'known stop';
      elsif last is null then
        px := case when s.stop_px > 0 then round(s.stop_px, 2) end; why := 'known stop (no last price)';
      else
        px := floor(last * 0.90 * 100) / 100;
        why := 'emergency: 10% below the last price ' || case when s.stop_px is null then '(no stop known)'
               else format('(stop %s not below the last price %s)', s.stop_px, last) end;
      end if;
      if q >= 1 and px is not null then
        perform net.http_post(
          url := base || '/v2/orders',
          body := jsonb_build_object('symbol', sym, 'qty', q::text, 'side', 'sell', 'type', 'stop',
                                     'time_in_force', 'gtc', 'stop_price', to_char(px, 'FM999999990.00'),
                                     'client_order_id', 'kbdm-' || left(kaibot.deadman_clean(s.strategy), 40) || '-'
                                       || left(kaibot.deadman_clean(sym), 12) || '-'
                                       || to_char(ny, 'YYYYMMDDHH24MISS') || '-dmstop'),
          headers := hdr || '{"Content-Type": "application/json"}'::jsonb, timeout_milliseconds := 10000);
        res := jsonb_set(res, '{stops}', (res->'stops') || jsonb_build_object('strategy', s.strategy, 'symbol', sym,
                                                                              'qty', q, 'stop', px, 'why', why));
        free := free - q;
      end if;
      if q < want or px is null then
        res := jsonb_set(res, '{unprotected}', (res->'unprotected') || jsonb_build_object(sym,
                 coalesce((res->'unprotected'->>sym)::numeric, 0) + want - case when px is null then 0 else q end));
        res := jsonb_set(res, '{alarms}', (res->'alarms') || to_jsonb(format(
                 '%s (%s): multi-day shares without a protective stop', sym, s.strategy)));
      end if;
    end loop;
    if md > 0 then
      any_md := true;
      res := jsonb_set(res, '{multi_day}', (res->'multi_day') || jsonb_build_object(sym, md));
    end if;
    intraday := floor(have - md)::integer;
    continue when not flatten or intraday < 1;
    if md <= 0 then
      res := jsonb_set(res, '{closed}', (res->'closed') || to_jsonb(sym));
    else
      res := jsonb_set(res, '{sold}', (res->'sold') || jsonb_build_object('symbol', sym, 'qty', intraday));
    end if;
  end loop;

  -- 4. the flatten window: intraday shares only
  if flatten and jsonb_array_length(res->'closed') + jsonb_array_length(res->'sold') > 0 then
    if not any_md then
      if not exists (select 1 from kaibot.deadman_pass dp where dp.session = p.session and dp.id <> pass_id
                     and coalesce((dp.result->>'flattened_all')::boolean, false)) then
        perform net.http_delete(url := base || '/v2/positions', params := '{"cancel_orders": "true"}'::jsonb,
                                headers := hdr, timeout_milliseconds := 10000);
        res := res || '{"flattened_all": true}'::jsonb;
      end if;
    else
      for sym in select jsonb_array_elements_text(res->'closed') loop
        -- every order on the symbol goes first (it holds the shares); the close is sent once none is left
        select array_agg(z.id) into others from kaibot.deadman_sells(ord, sym) z;
        if coalesce(array_length(others, 1), 0) > 0 then
          perform net.http_delete(url := base || '/v2/orders/' || i, headers := hdr, timeout_milliseconds := 10000)
            from unnest(others) i;
          res := jsonb_set(res, '{cancel}', (res->'cancel') || to_jsonb(others));
        else
          perform net.http_delete(url := base || '/v2/positions/' || sym, headers := hdr,
                                  timeout_milliseconds := 10000);
        end if;
      end loop;
      for o in select jsonb_array_elements(res->'sold') loop
        sym := o->>'symbol';
        select array_agg(z.id) into others from kaibot.deadman_sells(ord, sym) z
          where not ((z.type in ('stop', 'stop_limit')) and kaibot.deadman_md((kaibot.deadman_coid(z.coid))[1]));
        if coalesce(array_length(others, 1), 0) > 0 then
          perform net.http_delete(url := base || '/v2/orders/' || i, headers := hdr, timeout_milliseconds := 10000)
            from unnest(others) i;
          res := jsonb_set(res, '{cancel}', (res->'cancel') || to_jsonb(others));
        else
          perform net.http_post(
            url := base || '/v2/orders',
            body := jsonb_build_object('symbol', sym, 'qty', (o->>'qty'), 'side', 'sell', 'type', 'market',
                                       'time_in_force', 'day', 'client_order_id', 'kbdm-intraday-'
                                         || left(kaibot.deadman_clean(sym), 12) || '-'
                                         || to_char(ny, 'YYYYMMDDHH24MISS') || '-x'),
            headers := hdr || '{"Content-Type": "application/json"}'::jsonb, timeout_milliseconds := 10000);
        end if;
      end loop;
    end if;
  end if;
  update kaibot.deadman_pass set state = 'done', result = res where id = pass_id;
  return res;
end;
$$;

create or replace function kaibot.deadman_check(force boolean default false, p_flatten boolean default null,
                                                p_now timestamptz default null)
returns text
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  base constant text := 'https://paper-api.alpaca.markets';
  t timestamptz := coalesce(p_now, now());
  ny timestamp := t at time zone 'America/New_York';
  ex kaibot.market_calendar_exception%rowtype;
  close_ts timestamp;
  win boolean;
  hb timestamptz;
  key_id text;
  secret text;
  hdr jsonb;
  p kaibot.deadman_pass%rowtype;
  res jsonb;
  said text := null;
begin
  select * into ex from kaibot.market_calendar_exception where day = ny::date;
  close_ts := ny::date + coalesce(ex.close_et, time '16:00');
  if not force then
    if extract(isodow from ny) not between 1 and 5 then
      return 'weekend';
    end if;
    if ex.day is not null and ex.close_et is null then
      return 'market holiday';
    end if;
    if ny < ny::date + time '09:30' or ny >= close_ts then
      return 'market closed';
    end if;
  end if;
  -- the flatten window: from the close minus 4 minutes (watchdog.DEADMAN_BEFORE_CLOSE) to the close
  win := coalesce(p_flatten, ny >= close_ts - interval '4 minutes' and ny < close_ts);
  select h.ts into hb from kaibot.heartbeat h where h.process = 'engine';
  if hb is not null and hb > t - interval '3 minutes' then
    return 'engine alive';
  end if;
  select decrypted_secret into key_id from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_key_id';
  select decrypted_secret into secret from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_secret';
  if key_id is null or secret is null then
    if not exists (select 1 from kaibot.halt h where h.kind = 'deadman' and h.session = ny::date
                   and h.reason like '%no paper keys%') then
      insert into kaibot.halt (kind, reason, session)
      values ('deadman', 'engine heartbeat stale but no paper keys in Vault: nothing checked', ny::date);
    end if;
    return 'no keys';
  end if;
  hdr := jsonb_build_object('APCA-API-KEY-ID', key_id, 'APCA-API-SECRET-KEY', secret);

  -- act on the newest pass whose answers are in
  select * into p from kaibot.deadman_pass dp where dp.state = 'reading' order by dp.id desc limit 1;
  if p.id is not null then
    if exists (select 1 from net._http_response r where r.id = p.req_positions)
       and exists (select 1 from net._http_response r where r.id = p.req_orders) then
      res := kaibot.deadman_act(p.id, hdr, win, t);
      if res is not null and not exists (select 1 from kaibot.halt h where h.kind = 'deadman'
                                         and h.session = ny::date and h.reason like 'engine silent%') then
        insert into kaibot.halt (kind, reason, session)
        values ('deadman', format('engine silent (heartbeat %s): entry orders canceled, protective stops checked, '
                                  'multi-day positions kept (pass %s)',
                                  coalesce(to_char(hb, 'YYYY-MM-DD HH24:MI:SS TZ'), 'missing'), p.id), ny::date);
      end if;
      said := coalesce(format('acted: %s entries canceled, %s stops, %s closed, %s sold',
                              jsonb_array_length(res->'canceled'), jsonb_array_length(res->'stops'),
                              jsonb_array_length(res->'closed'), jsonb_array_length(res->'sold')),
                       'pass expired: the broker did not answer');
    elsif p.ts < t - interval '3 minutes' then
      update kaibot.deadman_pass set state = 'expired', result = '{"error": "no answer within 3 minutes"}'::jsonb
        where id = p.id;
    else
      return 'waiting for the broker''s answers';
    end if;
  end if;

  -- read the account again: every minute in the flatten window, else every 5 minutes
  if win or force or not exists (select 1 from kaibot.deadman_pass dp where dp.ts > t - interval '5 minutes'
                                 and dp.ts <= t) then
    insert into kaibot.deadman_pass (ts, session, req_positions, req_orders)
    values (t, ny::date,
            net.http_get(url := base || '/v2/positions', headers := hdr, timeout_milliseconds := 10000),
            net.http_get(url := base || '/v2/orders', params := '{"status": "open", "nested": "true", "limit": "500"}'::jsonb,
                         headers := hdr, timeout_milliseconds := 10000));
    return coalesce(said || '; ', '') || 'reading the account';
  end if;
  return coalesce(said, 'nothing due');
end;
$$;

-- 0006's name and signature (docs/ops/failsafe.md): now the plan above, never a blanket flatten of a multi-day book
create or replace function kaibot.deadman_flatten(force boolean default false)
returns text
language sql
security definer
set search_path = kaibot, pg_temp
as $$
  select kaibot.deadman_check(force)
$$;

revoke all on function kaibot.deadman_check(boolean, boolean, timestamptz) from public;
revoke all on function kaibot.deadman_check(boolean, boolean, timestamptz) from anon, authenticated;
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from public;
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from anon, authenticated;
revoke all on function kaibot.deadman_flatten(boolean) from public;
revoke all on function kaibot.deadman_flatten(boolean) from anon, authenticated;

-- one job instead of the four of 0006 and 0017: every minute 13:00-21:59 UTC on weekdays (09:00-17:59 ET in winter,
-- 09:00-16:59 in summer covers 09:30-16:00 either way); kaibot.deadman_check decides what is due
select cron.unschedule(jobid) from cron.job
 where jobname in ('kaibot_deadman_edt', 'kaibot_deadman_est', 'kaibot_deadman_early_edt', 'kaibot_deadman_early_est',
                   'kaibot_deadman');
select cron.schedule('kaibot_deadman', '* 13-21 * * 1-5', 'select kaibot.deadman_check()');

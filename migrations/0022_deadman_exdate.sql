-- 0022: the off-box dead-man on an ex-date with the engine down since before the open (#291; #266 gap 1).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
-- Production migrations are the stakeholder's (Product applies on his word): after 0019 (and 0020, 0021, which it does
-- not touch).
--
-- Why. kaibot.deadman_act (0019) reads the active run's last kaibot.ledger_position rows. #287 makes the engine write
-- today's rows as soon as a split, spin-off, symbol change or cash merger changes a share count, but an engine that
-- is down from before the open on an ex-date writes none: the rows are yesterday's, on the pre-action basis. Then:
--   a 2-for-1 split: half the broker's shares counted as intraday and sold in the flatten window (15:56 ET);
--   a reverse split: a false "unprotected" alarm (more shares wanted than are held);
--   a symbol change: the renamed holding has no row, so it was intraday and closed.
-- Nothing on the database side says what happened, so the rule is conservative (no new data source):
--   1. the broker's count is the book's times a split's ratio (2, 3, 4, 5, 6, 8, 10, 15, 20 or 3-for-2, or the
--      inverse; whole shares, +-1 for cash in lieu): every share no intraday leg explains is multi-day, none sold;
--      after a reverse split a stop covers at most the broker's shares. Any other excess stays intraday, as 0019's
--      plan says (one stock held both ways);
--   2. a multi-day symbol gone at the broker while an unexplained position (no book row, no sell order) holds
--      exactly its shares: that position is the renamed holding, kept, not closed.
--   In both, the protective stop the dead-man re-creates (Alpaca cancels GTC stops on a reverse split and on a
--   symbol change) covers the sleeve's shares times the ratio, at most the shares kept, at the emergency price
--   (10% below the last), never at the book's stop price: that is on the old basis or another symbol's.
-- An alarm says each. Nothing ever sells more than the broker holds (as before). The on-laptop watchdog
-- (src/kaibot/engine/failsafe.plan) follows the same rules; tests/test_failsafe_drills.py runs both on one set of
-- cases.
--
-- Only kaibot.deadman_act is re-created (0019's body, these additions marked #291) and one helper is added. No
-- table, no delete, drop or truncate. pg_cron's job and kaibot.deadman_check are unchanged.
--
-- Check after applying (read-only):
--   select kaibot.deadman_split_like(80, 40), kaibot.deadman_split_like(4, 40), kaibot.deadman_split_like(50, 40);
--     -- 2 | 0.1 (or 0.1000...) | null
--   select prosrc like '%deadman_split_like%' from pg_proc where proname = 'deadman_act';        -- true

-- the broker's count over the book's when it is a split's ratio (failsafe.split_like, the same order of ratios)
create or replace function kaibot.deadman_split_like(have numeric, book numeric) returns numeric
language sql immutable set search_path = '' as $$
  select c.x from (
    select u.r as x, u.n * 2 as k
      from unnest(array[2, 3, 4, 5, 6, 8, 10, 15, 20, 1.5]::numeric[]) with ordinality u(r, n)
    union all
    select 1 / u.r, u.n * 2 + 1
      from unnest(array[2, 3, 4, 5, 6, 8, 10, 15, 20, 1.5]::numeric[]) with ordinality u(r, n)) c
  where coalesce(book, 0) > 0.000000001 and coalesce(have, 0) > 0.000000001
    and abs(have - floor(book * c.x + 0.000000001)) <= 1.000000001
    and abs(have - book) >= 0.999999999
  order by c.k
  limit 1
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
  other numeric;    -- #291: shares held by sell orders that are not multi-day stops (intraday legs, manual)
  ratio numeric;    -- #291: the broker's count over the book's, when it is a split's ratio
  cap integer;      -- #291: after a reverse split, the most shares a stop may cover
  renamed jsonb := '{}'::jsonb;  -- #291: unexplained position -> the gone multi-day symbol it holds the shares of
  sp numeric;       -- #291: the sleeve's stop price, null when the book is on yesterday's basis (split, renamed)
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
       and o->>'filled_at' is null then  -- a partly filled entry too: its rest is cancelled, what filled keeps its legs
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

  -- #291: a multi-day symbol gone at the broker while an unexplained position (no book row, no sell order) holds
  -- exactly its shares: taken as the renamed holding (a symbol change while the engine was down) and kept
  for x in select e.key as symbol, (e.value->>'qty')::numeric as qty from jsonb_each(
             coalesce((select jsonb_object_agg(a->>'symbol', a) from jsonb_array_elements(pos) a), '{}'::jsonb)) e
           order by e.key loop
    continue when coalesce(x.qty, 0) <= 0;
    continue when exists (select 1 from kaibot.ledger_position lp where lp.run_id = rid and lp.session = sess
                          and lp.symbol = x.symbol and kaibot.deadman_md(lp.strategy) and lp.qty > 0);
    continue when exists (select 1 from kaibot.deadman_sells(ord, x.symbol));
    sym := null;
    select g.symbol into sym from (
        select lp.symbol, sum(lp.qty) as q from kaibot.ledger_position lp
        where lp.run_id = rid and lp.session = sess and kaibot.deadman_md(lp.strategy) and lp.qty > 0
        group by lp.symbol) g
      where abs(g.q - x.qty) <= 0.000000001
        and not exists (select 1 from jsonb_array_elements(pos) a
                        where a->>'symbol' = g.symbol and coalesce((a->>'qty')::numeric, 0) > 0)
        and not exists (select 1 from jsonb_each_text(renamed) rr where rr.value = g.symbol)
      order by g.symbol limit 1;
    if sym is not null then
      renamed := renamed || jsonb_build_object(x.symbol, sym);
    end if;
  end loop;

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
    if renamed ? sym then
      md := have;
      res := jsonb_set(res, '{alarms}', (res->'alarms') || to_jsonb(format(
               '%s: %s shares, no book row, and %s with the same shares is gone at the broker (a symbol change?): '
               'kept as multi-day, not sold', sym, have, renamed->>sym)));
    end if;
    -- #291: the broker's count is the book's times a split's ratio (the book is yesterday's): the shares no
    -- intraday leg explains are kept as multi-day; after a reverse split stops cover at most the broker's shares
    select coalesce(sum(z.qty), 0) into other from kaibot.deadman_sells(ord, sym) z
      where not (z.type in ('stop', 'stop_limit') and kaibot.deadman_md((kaibot.deadman_coid(z.coid))[1]));
    ratio := kaibot.deadman_split_like(have, book_md);
    cap := 2147483647;
    if renamed ? sym then
      cap := floor(md)::integer;
    end if;
    if ratio is not null then
      md := case when ratio > 1 then have - other else least(md, have) end;
      cap := floor(md)::integer;  -- #291: a stop covers at most the shares kept
      res := jsonb_set(res, '{alarms}', (res->'alarms') || to_jsonb(format(
               '%s: the broker holds %s shares, the book %s (a split today?): %s kept as multi-day, nothing of them sold',
               sym, have, book_md, md)));
    end if;
    select greatest(have - coalesce(sum(z.qty), 0), 0) into free from kaibot.deadman_sells(ord, sym) z;
    for s in select lp.strategy, sum(lp.qty) as qty, max(lp.stop_px) as stop_px from kaibot.ledger_position lp
             where lp.run_id = rid and lp.session = sess and lp.symbol = coalesce(renamed->>sym, sym)  -- #291
               and kaibot.deadman_md(lp.strategy)
             group by lp.strategy order by lp.strategy loop
      -- #291: on a split the sleeve's shares times the ratio; the book's stop price is on the old basis (or, for a
      -- renamed holding, another symbol's): not used, the emergency stop below the last price is
      sp := case when ratio is not null or renamed ? sym then null else s.stop_px end;
      select floor(s.qty * coalesce(ratio, 1) + 0.000000001)::integer - floor(coalesce(sum(z.qty), 0))::integer into want
        from kaibot.deadman_sells(ord, sym) z
        where z.type in ('stop', 'stop_limit')
          and (kaibot.deadman_coid(z.coid))[1] = kaibot.deadman_clean(s.strategy);
      want := least(want, cap - floor(covered)::integer);  -- #291
      continue when want < 1;
      -- a stop the dead-man itself sent in the last 3 minutes may not be listed yet: never send it twice
      continue when exists (select 1 from kaibot.deadman_pass dp, jsonb_array_elements(dp.result->'stops') st
                            where dp.id <> pass_id and dp.ts > t - interval '3 minutes'
                              and st->>'symbol' = sym and st->>'strategy' = s.strategy
                              and (st->>'qty')::integer >= want);
      q := least(want, floor(free)::integer);
      if sp is not null and sp > 0 and (last is null or sp < last) then
        px := round(sp, 2); why := 'known stop';
      elsif last is null then
        px := case when sp > 0 then round(sp, 2) end; why := 'known stop (no last price)';
      else
        px := floor(last * 0.90 * 100) / 100;
        why := 'emergency: 10% below the last price ' || case when sp is null then '(no stop known)'
               else format('(stop %s not below the last price %s)', sp, last) end;
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
        cap := cap - q;
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

-- as 0019: only the dead-man itself (pg_cron, as the owner) runs these
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from public;
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from anon, authenticated;
revoke all on function kaibot.deadman_split_like(numeric, numeric) from public;
revoke all on function kaibot.deadman_split_like(numeric, numeric) from anon, authenticated;

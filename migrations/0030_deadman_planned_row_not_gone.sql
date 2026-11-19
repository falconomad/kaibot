-- 0030: the dead-man's #291 rename rule ignores rows opened in their own session (#344; Backend-quant). After 0022.
--
-- Why. Since #342 the engine writes today's kaibot.ledger_position row for each planned buy just before the 15:50 ET
-- send (opened_session = today, at its target shares). If that buy never fills and the engine dies, the planned stock
-- is a multi-day row with no position at the broker. 0022's rename rule read it as "a multi-day symbol gone at the
-- broker" and took ANY unexplained position (no book row, no sell order) with exactly the same shares as the renamed
-- holding: kept as multi-day, an emergency stop, a "symbol change?" alarm, not sold in the flatten window. Fail closed
-- (docs/ops/failsafe.md) says such a position is intraday and is sold.
--
-- The fix. A sleeve opened in its own row's session (a planned or just-bought buy) can never be a holding renamed
-- overnight, so it leaves the "gone" set: one line in the rename query's `g` subquery (marked #344). Its book row
-- still explains its own symbol (the "has a book row" skip is unchanged). The on-laptop watchdog
-- (src/kaibot/engine/failsafe.plan, SleeveView.opened_today) follows the same rule; tests/test_failsafe_planned_row.py
-- and tests/test_failsafe_drills.py run both.
--
-- Only kaibot.deadman_act is re-created: 0022's body with that one line, then 0022's revokes for it. No table, no
-- delete, drop or truncate; pg_cron's job, kaibot.deadman_check and kaibot.deadman_split_like are unchanged.
-- Outside 09:30-16:00 ET (the dead-man acts in market hours).
--
-- Check after applying (read-only):
--   select prosrc like '%opened_session < lp.session%' and prosrc like '%deadman_split_like%'
--     from pg_proc where proname = 'deadman_act';                                                     -- true
--   select count(*) from cron.job where jobname = 'kaibot_deadman';                                    -- 1

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
          and (lp.opened_session is null or lp.opened_session < lp.session)  -- #344: held over a close only
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

-- as 0019/0022: only the dead-man itself (pg_cron, as the owner) runs it
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from public;
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from anon, authenticated;

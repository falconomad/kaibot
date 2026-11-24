-- 0044: the dead-man's orders get a deterministic idempotency key (#585, M13 4.3b; the rest of #477). After 0030.
--
-- Why. 0019-0030's dead-man names each order after the second it is sent (kbdm-<strategy>-<symbol>-<yyyymmddHHMMSS>
-- -dmstop, kbdm-intraday-<symbol>-<yyyymmddHHMMSS>-x). A POST whose answer is lost, followed by a pass whose
-- open-orders read is late, sends the same stop again under a new id, and the venue's duplicate check cannot catch it.
--
-- The change (kaibot.deadman_act only):
-- - One client id per decision: kbdm-<strategy>-<symbol>-<yyyymmdd>-dmstop.g<n> for the stop and
--   kbdm-intraday-<symbol>-<yyyymmdd>-x.g<n> for the intraday sale; <yyyymmdd> is the pass's NY session and <n> a
--   generation from 0. Both parsers already accept the form (kaibot.deadman_coid, failsafe.FAILSAFE_COID): the
--   engine still adopts the stop (protect.py) and the watchdog still recognises it.
-- - A re-send uses the same id, so the venue answers 422 "client_order_id must be unique" instead of placing a
--   second order. <n> rises by one only when GET /v2/orders:by_client_order_id (sent with every re-send, read by the
--   next pass) answered 200 for exactly that id with a status that has ended it (a sale: canceled, expired,
--   rejected, done_for_day, never filled; a stop: any status that is not live, `replaced` included, QA on #615),
--   and this pass's open orders list neither it nor the engine's replacement of it (`<id>.r<k>`, `replaced_by`). A stop whose id this pass's open orders DO list
--   is already counted (the shares still wanted are more than it covers: a resized sleeve), so the stop for them
--   takes the next <n> too. No answer, an unreadable answer or anything else keeps <n>: fail closed towards one
--   order.
-- - The pass result keeps, per stop and sale, root, gen, coid and req (the POST's pg_net id); `lookups` (coid, req);
--   `already_sent`: today's ids the venue refused as duplicates (present, not an alarm); `epoch`: the lease epoch
--   (#584) it saw, for the record (not a gate: the dead-man acts only while the engine is silent).
-- - The intraday sale of ours that is still working is left alone (before: cancelled with the symbol's other
--   sells, then sent again under a new id).
-- Unchanged: the plan (what is cancelled, kept, stopped and sold), the 3-minute look-back, deadman_check,
-- deadman_flatten, the helpers and pg_cron's job.
--
-- Guard. The file refuses (raises, one transaction, nothing changed) unless kaibot.deadman_act's body is exactly
-- 0030's (md5 below). Outside 09:30-16:00 ET (the dead-man acts in market hours). The stakeholder's to apply.
--
-- Check after applying (read-only):
--   select prosrc like '%.g%' and prosrc like '%by_client_order_id%' and prosrc like '%opened_session < lp.session%'
--     from pg_proc where proname = 'deadman_act';                                                     -- true
--   select has_function_privilege('authenticated', 'kaibot.deadman_act(bigint, jsonb, boolean, timestamptz)',
--                                 'execute');                                                          -- false
--   select count(*) from cron.job where jobname = 'kaibot_deadman';                                    -- 1

begin;

-- refuse unless the database has exactly 0030's body (applied twice, or over a hand edit: nothing changes)
do $guard$
declare
  h text;
begin
  select md5(prosrc) into h from pg_proc
    where oid = to_regprocedure('kaibot.deadman_act(bigint, jsonb, boolean, timestamptz)');
  if h is distinct from '23e84f55be53bbf7a0286648907b71b8' then
    raise exception '0044: kaibot.deadman_act is not 0030''s (md5 %, expected 23e84f55be53bbf7a0286648907b71b8): nothing changed', h;
  end if;
end
$guard$;

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
  -- #585: a deterministic client id per decision: <root>.g<n>, root = kbdm-...-<session yyyymmdd>-<purpose>
  root text;
  gen integer;
  cid text;
  again boolean;    -- this id was sent before today: a re-send (the venue refuses it as a duplicate if it exists)
  req bigint;
  lk record;
  lkb jsonb;
  epoch bigint;
  res jsonb := jsonb_build_object('canceled', '[]'::jsonb, 'stops', '[]'::jsonb, 'cancel', '[]'::jsonb,
                                  'closed', '[]'::jsonb, 'sold', '[]'::jsonb, 'multi_day', '{}'::jsonb,
                                  'unprotected', '{}'::jsonb, 'alarms', '[]'::jsonb, 'flatten', flatten,
                                  'sales', '[]'::jsonb, 'lookups', '[]'::jsonb, 'already_sent', '[]'::jsonb);
begin
  select * into p from kaibot.deadman_pass where id = pass_id;
  select r.content::jsonb into pos from net._http_response r where r.id = p.req_positions and r.status_code = 200;
  select r.content::jsonb into ord from net._http_response r where r.id = p.req_orders and r.status_code = 200;
  if pos is null or ord is null then
    update kaibot.deadman_pass set state = 'expired', result = jsonb_build_object('error', 'the broker did not answer')
      where id = pass_id;
    return null;
  end if;

  -- #585: the lease epoch this pass saw (#584), for the record only: the dead-man acts only while the engine is silent
  begin
    execute 'select epoch from kaibot.engine_lease limit 1' into epoch;
    res := res || jsonb_build_object('epoch', epoch);
  exception when others then
    null;  -- no lease table or no epoch column: nothing to record
  end;
  -- #585: an earlier order of today the venue refused because its client id is in use is present, not an alarm
  res := jsonb_set(res, '{already_sent}', coalesce((
    select jsonb_agg(distinct st->>'coid') from kaibot.deadman_pass dp,
      jsonb_array_elements(coalesce(dp.result->'stops', '[]'::jsonb) || coalesce(dp.result->'sales', '[]'::jsonb)) st,
      net._http_response r
    where dp.session = p.session and dp.id <> pass_id and st ? 'req' and r.id = (st->>'req')::bigint
      and r.status_code = 422 and coalesce(r.content, '') like '%client_order_id%'), '[]'::jsonb));

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
        -- #585: one id per sleeve and session; its generation rises only on the venue's word that the order ended
        root := 'kbdm-' || left(kaibot.deadman_clean(s.strategy), 40) || '-' || left(kaibot.deadman_clean(sym), 12)
                || '-' || to_char(p.session, 'YYYYMMDD') || '-dmstop';
        select max((st->>'gen')::integer) into gen from kaibot.deadman_pass dp,
          jsonb_array_elements(coalesce(dp.result->'stops', '[]'::jsonb)) st
          where dp.session = p.session and dp.id <> pass_id and st->>'root' = root;
        again := gen is not null;
        gen := coalesce(gen, 0);
        if again and exists (select 1 from jsonb_array_elements(ord) oo
                             where oo->>'client_order_id' = root || '.g' || gen
                                or left(oo->>'client_order_id', length(root || '.g' || gen) + 2) = root || '.g' || gen || '.r') then
          -- listed in this read (itself, or the engine's replacement of it, `<id>.r<k>`, #197), so already counted
          -- in `want`: the shares left need a new stop
          gen := gen + 1;
          again := false;
        end if;
        if again then
          for lk in select r.content from kaibot.deadman_pass dp,
                      jsonb_array_elements(coalesce(dp.result->'lookups', '[]'::jsonb)) l, net._http_response r
                    where dp.session = p.session and dp.id <> pass_id and l->>'coid' = root || '.g' || gen
                      and r.id = (l->>'req')::bigint and r.status_code = 200 loop
            begin
              lkb := lk.content::jsonb;
            exception when others then
              lkb := null;  -- an unreadable answer is no evidence
            end;
            -- QA on #615: any status that is not live counts as ended, `replaced` included: the engine's
            -- replacement (`replaced_by`, `<id>.r<k>`) is followed in this read, and a stop that is not listed
            -- here under the id, a replacement's id or the replacement's order id needs a new one (a missing stop
            -- is the worse failure; a second one on a late read is bounded by the venue's share reservation)
            if lkb->>'client_order_id' = root || '.g' || gen
               and coalesce(lkb->>'status', '') not in ('new', 'accepted', 'pending_new', 'partially_filled', 'held',
                                                        'accepted_for_bidding', 'pending_replace', 'pending_cancel',
                                                        'calculated', 'stopped', 'suspended')
               and not exists (select 1 from jsonb_array_elements(ord) oo
                               where oo->>'client_order_id' = root || '.g' || gen
                                  or left(oo->>'client_order_id', length(root || '.g' || gen) + 2)
                                     = root || '.g' || gen || '.r'
                                  or oo->>'id' = lkb->>'replaced_by') then
              gen := gen + 1;  -- the id was used by an order that has ended: a new stop is needed
              again := false;
              exit;
            end if;
          end loop;
        end if;
        cid := root || '.g' || gen;
        req := net.http_post(
          url := base || '/v2/orders',
          body := jsonb_build_object('symbol', sym, 'qty', q::text, 'side', 'sell', 'type', 'stop',
                                     'time_in_force', 'gtc', 'stop_price', to_char(px, 'FM999999990.00'),
                                     'client_order_id', cid),
          headers := hdr || '{"Content-Type": "application/json"}'::jsonb, timeout_milliseconds := 10000);
        if again then  -- the next pass asks the venue what became of it (GET /v2/orders:by_client_order_id)
          res := jsonb_set(res, '{lookups}', (res->'lookups') || jsonb_build_object('coid', cid, 'req',
                   net.http_get(url := base || '/v2/orders:by_client_order_id',
                                params := jsonb_build_object('client_order_id', cid), headers := hdr,
                                timeout_milliseconds := 10000)));
        end if;
        res := jsonb_set(res, '{stops}', (res->'stops') || jsonb_build_object('strategy', s.strategy, 'symbol', sym,
                                                                              'qty', q, 'stop', px, 'why', why,
                                                                              'root', root, 'gen', gen,
                                                                              'coid', cid, 'req', req));
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
        -- #585: one id per symbol and session for the sale, as for the stop; a filled sale is never evidence (a
        -- second sale on a late positions read would sell short)
        root := 'kbdm-intraday-' || left(kaibot.deadman_clean(sym), 12) || '-' || to_char(p.session, 'YYYYMMDD')
                || '-x';
        select max((st->>'gen')::integer) into gen from kaibot.deadman_pass dp,
          jsonb_array_elements(coalesce(dp.result->'sales', '[]'::jsonb)) st
          where dp.session = p.session and dp.id <> pass_id and st->>'root' = root;
        again := gen is not null;
        gen := coalesce(gen, 0);
        if again then
          for lk in select r.content from kaibot.deadman_pass dp,
                      jsonb_array_elements(coalesce(dp.result->'lookups', '[]'::jsonb)) l, net._http_response r
                    where dp.session = p.session and dp.id <> pass_id and l->>'coid' = root || '.g' || gen
                      and r.id = (l->>'req')::bigint and r.status_code = 200 loop
            begin
              lkb := lk.content::jsonb;
            exception when others then
              lkb := null;
            end;
            if lkb->>'client_order_id' = root || '.g' || gen
               and lkb->>'status' in ('canceled', 'expired', 'rejected', 'done_for_day')
               and not exists (select 1 from jsonb_array_elements(ord) oo
                               where oo->>'client_order_id' = root || '.g' || gen) then
              gen := gen + 1;
              again := false;
              exit;
            end if;
          end loop;
        end if;
        cid := root || '.g' || gen;
        -- our own sale still working is left to work: neither cancelled nor sent again
        continue when exists (select 1 from kaibot.deadman_sells(ord, sym) z where z.coid = cid);
        select array_agg(z.id) into others from kaibot.deadman_sells(ord, sym) z
          where not ((z.type in ('stop', 'stop_limit')) and kaibot.deadman_md((kaibot.deadman_coid(z.coid))[1]));
        if coalesce(array_length(others, 1), 0) > 0 then
          perform net.http_delete(url := base || '/v2/orders/' || i, headers := hdr, timeout_milliseconds := 10000)
            from unnest(others) i;
          res := jsonb_set(res, '{cancel}', (res->'cancel') || to_jsonb(others));
        else
          req := net.http_post(
            url := base || '/v2/orders',
            body := jsonb_build_object('symbol', sym, 'qty', (o->>'qty'), 'side', 'sell', 'type', 'market',
                                       'time_in_force', 'day', 'client_order_id', cid),
            headers := hdr || '{"Content-Type": "application/json"}'::jsonb, timeout_milliseconds := 10000);
          if again then
            res := jsonb_set(res, '{lookups}', (res->'lookups') || jsonb_build_object('coid', cid, 'req',
                     net.http_get(url := base || '/v2/orders:by_client_order_id',
                                  params := jsonb_build_object('client_order_id', cid), headers := hdr,
                                  timeout_milliseconds := 10000)));
          end if;
          res := jsonb_set(res, '{sales}', (res->'sales') || jsonb_build_object('symbol', sym, 'qty', (o->>'qty'),
                                                                                'root', root, 'gen', gen,
                                                                                'coid', cid, 'req', req));
        end if;
      end loop;
    end if;
  end if;
  update kaibot.deadman_pass set state = 'done', result = res where id = pass_id;
  return res;
end;
$$;

-- as 0019/0022/0030: only the dead-man itself (pg_cron, as the owner) runs it
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from public;
revoke all on function kaibot.deadman_act(bigint, jsonb, boolean, timestamptz) from anon, authenticated;

commit;

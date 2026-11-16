-- 0020: the multi-day dashboard's data (#229; contract in the issue, docs/ops/ledger.md). READ-ONLY RPCs only:
-- no table, no column, no row changes. Safe to apply while the engine runs, before or after the engine code of #229
-- (without it the new fields are simply empty).
--
-- 1. kb_book (replaces 0018's): each position row also carries `sell_when` (the rule's readings of its sell
--    conditions at the latest decision on or before the session) and `sector` (the sector the rule's daily view uses;
--    null when the universe file does not list the name). Everything else is 0018's, unchanged.
-- 2. kb_multiday_plan(p_run): per rule, the latest decision (what is sold at the next close, the buy candidates) and,
--    once sized at 09:00 ET of the fill session, its orders and their state.
-- 3. kb_book_closed(p_run, p_limit): the book's closed multi-day holdings, newest first (the "Sold" tab).
--
-- Sources: kaibot.ledger_day / ledger_position (0018) and the engine's events `multi_day_decision`,
-- `multi_day_plan`, `multi_day_orders`, `multi_day_fill`, `multi_day_sold` (kaibot.event, written by the engine).
-- Every function is security definer, owner-checked (kaibot.is_owner()), revoked from public and anon.

-- ---------- helpers ----------
-- The latest multi_day_decision event of a rule decided after the close of a session on or before p_session.
create or replace function kaibot.md_decision_at(p_run bigint, p_rule text, p_session date) returns jsonb
language sql stable security definer set search_path = '' as $$
  select e.payload from kaibot.event e
  where e.run_id = p_run and e.type = 'multi_day_decision' and e.payload->>'strategy' = p_rule
    and (e.payload->>'session')::date <= p_session and coalesce((e.payload->>'decides')::boolean, false)
  order by (e.payload->>'session')::date desc, e.id desc limit 1
$$;
revoke all on function kaibot.md_decision_at(bigint, text, date) from public, anon, authenticated;

-- ---------- 1. kb_book ----------
create or replace function public.kb_book(p_run bigint, p_session date default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare d date; l kaibot.ledger_day; div numeric;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  d := coalesce(p_session, (select max(x.session) from kaibot.ledger_day x where x.run_id = p_run));
  select * into l from kaibot.ledger_day x where x.run_id = p_run and x.session = d;
  if l.run_id is null then
    return jsonb_build_object('run_id', p_run, 'session', null, 'mtm', null, 'positions', '[]'::jsonb,
      'exposure', null, 'reconciled', null);
  end if;
  select coalesce(sum(x.dividends), 0) into div from kaibot.ledger_day x where x.run_id = p_run and x.session <= d;
  return jsonb_build_object(
    'run_id', p_run,
    'session', d,
    'mtm', jsonb_build_object(
      'ts', l.ts, 'at_close', true, 'cash', l.cash, 'invested', l.invested, 'equity', l.close_equity_venue,
      'equity_reported', l.close_equity,
      'start_equity', (select r.amount_usd from kaibot.run r where r.id = p_run),
      'prev_close_equity', l.start_equity, 'open_equity', null, 'dividends_to_date', div),
    'positions', coalesce((select jsonb_agg(jsonb_build_object(
        'rule', p.strategy, 'symbol', p.symbol, 'qty', p.qty, 'avg_cost', p.avg_cost,
        'last', case when p.priced then p.mark end, 'last_ts', case when p.priced then l.ts end,
        'mark', p.mark, 'opened_session', p.opened_session, 'dividends', p.dividends,
        'stop', case when p.stop_px is null then null
                     else jsonb_build_object('price', p.stop_px, 'state', p.stop_state) end,
        'sell_when', dec.payload->'sell_when'->p.symbol,
        'sector', dec.payload->'sectors'->>p.symbol)
      order by p.strategy, p.symbol)
      from kaibot.ledger_position p
      left join lateral (select kaibot.md_decision_at(p_run, p.strategy, d) as payload) dec on true
      where p.run_id = p_run and p.session = d), '[]'::jsonb),
    'exposure', jsonb_build_object(
      'gross', l.gross, 'overnight', l.overnight, 'cap', null,
      'by_rule', coalesce((select jsonb_agg(jsonb_build_object('rule', b.strategy, 'value', b.value,
          'share', case when l.close_equity_venue > 0 then b.value / l.close_equity_venue else 0 end)
          order by b.value desc)
        from (select p.strategy, sum(p.qty * p.mark) as value from kaibot.ledger_position p
              where p.run_id = p_run and p.session = d group by p.strategy) b), '[]'::jsonb),
      'market_filter', '[]'::jsonb),
    'reconciled', case when l.reconciled ? 'ok' and l.reconciled->>'ok' is not null
      then jsonb_build_object('ok', (l.reconciled->>'ok')::boolean, 'ts', l.reconciled->>'ts',
                              'positions', l.reconciled->'positions', 'cash_diff', l.reconciled->'cash_diff') end
  );
end $$;

-- ---------- 2. kb_multiday_plan ----------
-- orders[].state: filled (a multi_day_fill of the symbol on the fill session), else the venue state of the latest
-- multi_day_orders event (refused = rejected, canceled = canceled / expired, deferred = deferred by the target
-- loop, working = any other sent state), else planned.
create or replace function public.kb_multiday_plan(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object('rules', coalesce((
    select jsonb_agg(r.obj order by r.rule) from (
      select dec.rule, jsonb_build_object(
        'rule', dec.rule,
        'decided_after_close_of', dec.p->>'session',
        'decided_at', dec.p->>'decided_at',
        'fills_at_close_of', dec.p->>'fill_session',
        'decides', coalesce((dec.p->>'decides')::boolean, false),
        'sell', coalesce((select jsonb_agg(jsonb_build_object('symbol', s.key, 'why', s.value) order by s.key)
                          from jsonb_each_text(coalesce(dec.p->'sell', '{}'::jsonb)) s), '[]'::jsonb),
        'buy_candidates', coalesce(dec.p->'buy_candidates', dec.p->'buy', '[]'::jsonb),
        'orders', coalesce((
          select jsonb_agg(jsonb_build_object(
              'symbol', t->>0, 'shares_now', (t->>1)::numeric, 'shares_target', (t->>2)::numeric,
              'action', case when t->>3 in ('sell', 'resize', 'buy') then t->>3
                             when (t->>2)::numeric = 0 then 'sell'
                             when (t->>1)::numeric > 0 then 'resize' else 'buy' end,
              'why', t->4,
              'coid', sent.x->>'coid',
              'state', case
                when exists (select 1 from kaibot.event f where f.run_id = p_run and f.type = 'multi_day_fill'
                             and f.payload->>'strategy' = dec.rule and f.payload->>'symbol' = t->>0
                             and (f.ts at time zone 'America/New_York')::date = (dec.p->>'fill_session')::date)
                  then 'filled'
                when ord.p->'deferred' ? (t->>0) then 'deferred'
                when sent.x->>'state' = 'rejected' then 'refused'
                when sent.x->>'state' in ('canceled', 'expired') then 'canceled'
                when sent.x->>'state' = 'filled' then 'filled'
                when sent.x is not null then 'working'
                else 'planned' end)
            order by case when t->>3 = 'sell' then 0 else 1 end, t->>0)
          from jsonb_array_elements(coalesce(plan.p->'targets', '[]'::jsonb)) t
          left join lateral (select x from jsonb_array_elements(coalesce(ord.p->'sent', '[]'::jsonb)) x
                             where x->>'symbol' = t->>0 limit 1) sent on true
          where (t->>1)::numeric <> (t->>2)::numeric), '[]'::jsonb),
        'skipped', coalesce((plan.p->>'skipped')::int, 0),
        'note', case when plan.p is null then 'sized at 09:00 ET on the fill session' else plan.p->>'note' end
      ) as obj
      from (select distinct on (e.payload->>'strategy') e.payload->>'strategy' as rule, e.payload as p
            from kaibot.event e where e.run_id = p_run and e.type = 'multi_day_decision'
            order by e.payload->>'strategy', (e.payload->>'session')::date desc, e.id desc) dec
      left join lateral (select e.payload as p from kaibot.event e where e.run_id = p_run and e.type = 'multi_day_plan'
                         and e.payload->>'strategy' = dec.rule and e.payload->>'session' = dec.p->>'fill_session'
                         order by e.id desc limit 1) plan on true
      left join lateral (select e.payload as p from kaibot.event e where e.run_id = p_run
                         and e.type = 'multi_day_orders' and e.payload->>'strategy' = dec.rule
                         and e.payload->>'session' = dec.p->>'fill_session'
                         order by e.id desc limit 1) ord on true
    ) r), '[]'::jsonb));
end $$;

-- ---------- 3. kb_book_closed ----------
-- A holding (run, rule, symbol, opened_session) is closed when the ledger's next close after its last row does not
-- hold it. exit_px: the share-weighted price of the sales of that symbol on the closing session (multi_day_fill;
-- multi_day_sold for a flatten). reason: the decision's sell code for that fill session, else the flatten's.
create or replace function public.kb_book_closed(p_run bigint, p_limit int default 50) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(c.obj order by c.closed_session desc, c.rule, c.symbol) from (
    select h.rule, h.symbol, h.closed_session, jsonb_build_object(
        'rule', h.rule, 'symbol', h.symbol, 'opened_session', h.opened_session, 'closed_session', h.closed_session,
        'qty', h.qty, 'avg_cost', h.avg_cost, 'exit_px', x.px,
        'pnl_usd', case when x.px is null then null else round((x.px - h.avg_cost) * h.qty, 2) end,
        'dividends', h.dividends,
        'days_held', case when h.opened_session is null then null else h.closed_session - h.opened_session end,
        'reason', coalesce(
          (select e.payload->'sell'->>h.symbol from kaibot.event e where e.run_id = p_run
             and e.type = 'multi_day_decision' and e.payload->>'strategy' = h.rule
             and e.payload->>'fill_session' = h.closed_session::text order by e.id desc limit 1),
          x.flatten)) as obj
    from (
      select last.strategy as rule, last.symbol, last.opened_session, last.qty, last.avg_cost, last.dividends,
             (select min(d.session) from kaibot.ledger_day d where d.run_id = p_run and d.session > last.session)
               as closed_session
      from (select distinct on (p.strategy, p.symbol, p.opened_session) p.*
            from kaibot.ledger_position p where p.run_id = p_run
            order by p.strategy, p.symbol, p.opened_session, p.session desc) last
      where not exists (select 1 from kaibot.ledger_position n where n.run_id = p_run
                          and n.strategy = last.strategy and n.symbol = last.symbol
                          and n.opened_session is not distinct from last.opened_session
                          and n.session > last.session)
    ) h
    left join lateral (
      select sum(f.qty * f.px) / nullif(sum(f.qty), 0) as px, max(f.flatten) as flatten from (
        select (e.payload->>'qty')::numeric as qty, (e.payload->>'px')::numeric as px, null::text as flatten
        from kaibot.event e where e.run_id = p_run and e.type = 'multi_day_fill' and e.payload->>'side' = 'sell'
          and e.payload->>'strategy' = h.rule and e.payload->>'symbol' = h.symbol
          and (e.ts at time zone 'America/New_York')::date = h.closed_session
        union all
        select h.qty, (e.payload->>'px')::numeric, e.payload->>'reason'
        from kaibot.event e where e.run_id = p_run and e.type = 'multi_day_sold'
          and e.payload->'keys' ? (h.rule || '|' || h.symbol)
          and (e.ts at time zone 'America/New_York')::date = h.closed_session) f
    ) x on true
    where h.closed_session is not null
    order by h.closed_session desc, h.rule, h.symbol
    limit greatest(coalesce(p_limit, 50), 0)
  ) c), '[]'::jsonb);
end $$;

revoke all on function public.kb_book(bigint, date), public.kb_multiday_plan(bigint),
  public.kb_book_closed(bigint, int) from public, anon;
grant execute on function public.kb_book(bigint, date), public.kb_multiday_plan(bigint),
  public.kb_book_closed(bigint, int) to authenticated;

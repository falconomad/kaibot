-- 0028 (Product, 19 Nov 2026; Backend-quant): a position that has no broker stop BY DESIGN says so. After 0020.
-- The dashboard's "N stocks have no safety stop" counted every multi-day row whose stop is missing, including the
-- momentum rules', whose pre-registrations have no stop (they exit by their target list). The engine now writes
-- stop_state 'none_by_design' for a sleeve of such a rule (engine/live_daily.exit_kind = "target_list"; until this
-- migration is applied it keeps writing 'missing', see Engine._upsert_position), and kb_book gives each position its
-- `stop_state` and its `exit` kind ("target_list" | "broker_stop"), so the frontend's list of rules without stops
-- (apps/web/src/lib/exits.ts) can go.
--
-- Additive: one more allowed value of ledger_position.stop_state (the check constraint is replaced, no row changes)
-- and kb_book re-created with two more keys per position (0020's text otherwise unchanged). No delete, drop of data
-- or truncate. The dead-man (0019/0022) never reads stop_state. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select stop_state, count(*) from kaibot.ledger_position group by 1;
--   select public.kb_book(<run>)->'positions'->0->>'exit';            -- as the owner

alter table kaibot.ledger_position drop constraint if exists ledger_position_stop_state_check;
alter table kaibot.ledger_position add constraint ledger_position_stop_state_check
  check (stop_state in ('resting', 'missing', 're-arming', 'none_by_design'));

-- 0020's kb_book, plus `stop_state` and `exit` on each position.
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
        'stop_state', p.stop_state,
        'exit', case when p.stop_state = 'none_by_design' then 'target_list' else 'broker_stop' end,
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

revoke all on function public.kb_book(bigint, date) from public, anon;
grant execute on function public.kb_book(bigint, date) to authenticated;

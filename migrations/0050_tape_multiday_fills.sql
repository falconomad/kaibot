-- 0050: the Activity tape (kb_tape) also lists the multi-day rule's fills (stakeholder bug, 26 Nov 2026: "Buys & sells"
-- showed nothing). After 0009 (re-creates its kb_tape); independent of 0048 and 0049 (numbered
-- after them only). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. kb_tape (0009) built its fill items only from kaibot.fill joined to kaibot."order", kaibot.signal and
-- kaibot.trade: the retired intraday path's tables (kaibot.fill has no rows at all in production). The live
-- multi-day rule books its fills only as engine events: `multi_day_fill` in kaibot.event, written by
-- src/kaibot/engine/live_daily.py (`_on_fill`) with the payload {strategy, symbol, side, qty, px, coid}.
--
-- What changes. One more branch in kb_tape's union: one 'fill' item per `multi_day_fill` event of the run, with
--   kind 'fill', source 'multi_day', ts (the event's), side, qty, venue_px (payload px), shadow_px and fees (null:
--   the payload has neither, unless a later engine adds `shadow_px` / `fees`), symbol, strategy, coid,
--   trade_id null, pnl_usd null, and for a sale exit_reason: the payload's `exit_reason` (or `reason`) if it has one
--   (today's engine sends neither), else 'stop' / 'flatten' when the client order id's purpose says so
--   (kb<run>-<rule>-<symbol>-<yyyymmdd>-<purpose>-<n>, engine/orders.py client_order_id), else the decision's sell
--   code for that symbol on the fill session (multi_day_decision `sell`, as 0020's kb_book_closed reads it).
-- An event is skipped when it is malformed (side not buy/sell, qty or px not a positive number, no symbol), and when
-- kaibot.fill already lists the same order (client_order_id = payload coid) for this run: no fill is shown twice.
-- The signal and intraday-fill branches are 0009's byte for byte (tests/test_tape_multiday_fills_sql.py diffs the
-- bodies). Owner-only, security definer, empty search_path, STABLE; paging by ts (p_before) and the limit as before.
--
-- Additive: one `create or replace function` with the same signature, then 0009's revoke and grant again. No table
-- changes; no insert, update, delete, drop or truncate. Any time, also while the engine runs. Until it is applied the
-- dashboard works as it does now (no multi-day rows on the tape).
--
-- Check after applying (read-only):
--   select prosrc like '%multi_day_fill%' from pg_proc where proname = 'kb_tape';                       -- true
--   select has_function_privilege('anon', 'public.kb_tape(bigint, int, timestamptz)', 'execute');        -- false
--   select has_function_privilege('authenticated', 'public.kb_tape(bigint, int, timestamptz)', 'execute'); -- true

create or replace function public.kb_tape(p_run bigint, p_limit int default 100, p_before timestamptz default null)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare lim int := least(greatest(coalesce(p_limit, 100), 1), 500);
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(x.item order by x.ts desc) from (
    select * from (
      select s.ts, jsonb_build_object('kind', 'signal', 'ts', s.ts, 'signal_id', s.id, 'mode', s.mode, 'strategy', s.strategy,
          'symbol', s.symbol, 'entry', s.entry, 'stop', s.stop, 'target', s.target, 'reasons', to_jsonb(s.reasons),
          'veto', s.rules_veto, 'approved', g.approved, 'qty', g.qty, 'binding', g.binding,
          'trade_id', (select t.id from kaibot.trade t where t.signal_id = s.id limit 1),
          'counterfactual', s.features->'counterfactual',
          'room', (select jsonb_build_object('status', rs.status, 'verdict', rs.verdict, 'cause', rs.judge->>'cause',
              'confidence', rs.judge->>'confidence', 'counterfactual', rs.counterfactual)
            from kaibot.room_session rs where rs.signal_id = s.id)) as item
      from kaibot.signal s left join kaibot.gate_run g on g.signal_id = s.id
      where s.run_id = p_run and (p_before is null or s.ts < p_before)
      union all
      select f.ts, jsonb_build_object('kind', 'fill', 'ts', f.ts, 'side', f.side, 'qty', f.qty,
          'venue_px', f.venue_px, 'shadow_px', f.shadow_px, 'fees', f.fees, 'tag', f.tag,
          'symbol', s.symbol, 'strategy', s.strategy, 'trade_id', t.id,
          'exit_reason', case when f.side = 'sell' then t.exit_reason end,
          'pnl_usd', case when f.side = 'sell' then t.pnl_usd end) as item
      from kaibot.fill f join kaibot."order" o on o.id = f.order_id
        join kaibot.signal s on s.id = o.signal_id
        left join kaibot.trade t on t.signal_id = s.id
      where s.run_id = p_run and (p_before is null or f.ts < p_before)
      union all
      -- 0050: the multi-day rule's fills (engine events; payload {strategy, symbol, side, qty, px, coid})
      select m.ts, jsonb_build_object('kind', 'fill', 'source', 'multi_day', 'ts', m.ts, 'side', m.side, 'qty', m.qty,
          'venue_px', m.px, 'shadow_px', m.shadow_px, 'fees', m.fees, 'tag', null,
          'symbol', m.symbol, 'strategy', m.strategy, 'coid', m.coid, 'trade_id', null,
          'exit_reason', case when m.side = 'sell' then coalesce(m.reason,
              case when m.purpose in ('stop', 'flatten') then m.purpose end,
              (select d.payload->'sell'->>m.symbol from kaibot.event d where d.run_id = p_run
                 and d.type = 'multi_day_decision' and d.payload->>'strategy' = m.strategy
                 and d.payload->>'fill_session' = (m.ts at time zone 'America/New_York')::date::text
               order by d.id desc limit 1)) end,
          'pnl_usd', null) as item
      from (
        select e.ts, e.payload->>'side' as side, e.payload->>'symbol' as symbol, e.payload->>'strategy' as strategy,
          e.payload->>'coid' as coid,
          case when jsonb_typeof(e.payload->'qty') = 'number' then (e.payload->>'qty')::numeric end as qty,
          case when jsonb_typeof(e.payload->'px') = 'number' then (e.payload->>'px')::numeric end as px,
          case when jsonb_typeof(e.payload->'shadow_px') = 'number' then (e.payload->>'shadow_px')::numeric end as shadow_px,
          case when jsonb_typeof(e.payload->'fees') = 'number' then (e.payload->>'fees')::numeric end as fees,
          nullif(coalesce(e.payload->>'exit_reason', e.payload->>'reason'), '') as reason,
          (string_to_array(e.payload->>'coid', '-'))[cardinality(string_to_array(e.payload->>'coid', '-')) - 1] as purpose
        from kaibot.event e
        where e.run_id = p_run and e.type = 'multi_day_fill' and (p_before is null or e.ts < p_before)
      ) m
      where m.side in ('buy', 'sell') and m.qty > 0 and m.px > 0 and coalesce(m.symbol, '') <> ''
        and not exists (select 1 from kaibot.fill f2 join kaibot."order" o2 on o2.id = f2.order_id
                          join kaibot.signal s2 on s2.id = o2.signal_id
                        where s2.run_id = p_run and o2.client_order_id = m.coid)
    ) u order by u.ts desc limit lim
  ) x), '[]'::jsonb);
end $$;

revoke all on function public.kb_tape(bigint, int, timestamptz) from public, anon;
grant execute on function public.kb_tape(bigint, int, timestamptz) to authenticated;

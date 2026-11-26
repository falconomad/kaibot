-- 0053: the price relay reads the held stocks through the helper, not with the trading key (#712, M18 ticket 7.2 of
-- the second engine audit). After 0018 (kaibot.ledger_position) and 0038 (kaibot.is_helper); independent of
-- 0048-0052 (numbered after them only). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. scripts/price_stream.py streams the held stocks' IEX prices to the dashboard. It found the held stocks with
-- GET /v2/positions on the paper TRADING account, so the helpers' env file held the full Alpaca trading pair (Alpaca
-- paper keys cannot be scoped: a key that can read positions can also place orders). After #712 the relay holds only
-- a market-data pair of a SEPARATE Alpaca paper account (data.env), which cannot see or trade the real paper account,
-- and reads which stocks are held here instead.
--
-- What it adds.
-- 1) public.kb_helper_held_symbols(): for the helper only (kaibot.is_helper(), 0038), the active paper run's held
--    stocks at its newest ledger session (the rows the engine writes after each session's fills, the same rows
--    engine/failsafe.py's book_from_store reads): [{symbol, market_value}], market_value = |qty x mark| summed over
--    the strategies' sleeves, largest first. Symbols and sizes only: no run id, cost, stop, strategy or P&L.
--
-- Additive: create or replace function, revoke and grant. No row is written; no delete, drop or truncate. Any time,
-- also while the engine runs (the engine never calls it). Changes no trading behaviour.
--
-- Check after applying (read-only):
--   select has_function_privilege('anon', 'public.kb_helper_held_symbols()', 'execute');            -- false
--   select has_function_privilege('authenticated', 'public.kb_helper_held_symbols()', 'execute');   -- true
--   select prosecdef from pg_proc where proname = 'kb_helper_held_symbols';                         -- true

create or replace function public.kb_helper_held_symbols() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_run bigint;
  v_session date;
begin
  if not kaibot.is_helper() then raise exception 'not authorised' using errcode = '42501'; end if;
  select r.id into v_run from kaibot.run r
   where r.kind = 'paper' and r.status in ('running', 'paused') order by r.id desc limit 1;
  if v_run is null then return '[]'::jsonb; end if;
  select max(p.session) into v_session from kaibot.ledger_position p where p.run_id = v_run;
  if v_session is null then return '[]'::jsonb; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('symbol', x.symbol, 'market_value', x.mv)
                                    order by x.mv desc, x.symbol)
    from (select p.symbol, sum(abs(p.qty * p.mark)) as mv
            from kaibot.ledger_position p
           where p.run_id = v_run and p.session = v_session and p.qty <> 0
           group by p.symbol) x), '[]'::jsonb);
end $$;

revoke all on function public.kb_helper_held_symbols() from public, anon;
grant execute on function public.kb_helper_held_symbols() to authenticated;

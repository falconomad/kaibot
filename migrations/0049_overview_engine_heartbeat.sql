-- 0049: kb_overview returns only the engine's heartbeat row (#723, M19 ticket 8.5 of the second engine audit).
-- After 0007 (re-creates its kb_overview); independent of 0048 (numbered after it only).
-- Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. kb_overview (0007) returned every row of kaibot.heartbeat: the engine's, and the watchdog's, the engine check's,
-- the research runner's and any other process that reports. The dashboard polls kb_overview every few seconds and
-- reads only the engine's row from it (`heartbeat.find((h) => h.process === "engine")` in lib/derive, poll,
-- startstate, tradingstate, Shell, Home and the pages); the Engine page reads every process from its own health RPC, not
-- from here. So each poll carried the other rows' detail for nothing.
--
-- What changes. One line of public.kb_overview: `where h.process = 'engine'`. `heartbeat` stays a list (of zero
-- or one row: process is the table's primary key), with the same row shape, so the dashboard reads it as before.
-- Every other key (run, daily, pending_commands, ledger, today, segments, halts_today) is 0007's byte for byte
-- (tests/test_overview_engine_heartbeat_sql.py diffs the bodies). Owner-only as before.
--
-- Additive: one `create or replace function` with the same signature; 0002's grants stand (create or replace keeps
-- them), revoked and granted again here as 0002 did. No table changes; no insert, update, delete, drop or truncate.
-- Any time, also while the engine runs. Until it is applied the dashboard works as it does now.
--
-- Check after applying (read-only):
--   select prosrc like '%h.process = ''engine''%' from pg_proc where proname = 'kb_overview';         -- true
--   select has_function_privilege('anon', 'public.kb_overview(bigint)', 'execute');                 -- false
--   select has_function_privilege('authenticated', 'public.kb_overview(bigint)', 'execute');        -- true
--   -- what each poll no longer carries: the other processes' rows, in bytes
--   select coalesce(sum(octet_length(to_jsonb(h)::text)), 0) from kaibot.heartbeat h where h.process <> 'engine';

create or replace function public.kb_overview(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select jsonb_build_object(
    'run', (select to_jsonb(r) from kaibot.run r where r.id = p_run),
    'daily', coalesce((select jsonb_agg(jsonb_build_object(
        'session', d.session, 'pnl', d.pnl_reported, 'pnl_venue', d.pnl_venue, 'pnl_shadow', d.pnl_shadow,
        'spy_ret', d.spy_ret, 'cash_ret', d.cash_ret, 'trades', d.trades) order by d.session)
      from kaibot.daily_result d where d.run_id = p_run), '[]'::jsonb),
    -- 0049 (#723): the engine's row only; the dashboard reads no other process here (the Engine page has its own health RPC)
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h where h.process = 'engine'), '[]'::jsonb),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts, 'payload', c.payload))
      from kaibot.command c where c.applied_at is null), '[]'::jsonb),
    'ledger', (select to_jsonb(l) from kaibot.ledger l where l.run_id = p_run order by l.ts desc limit 1),
    'today', (select jsonb_build_object('trades', count(*), 'pnl', coalesce(sum(t.pnl_usd), 0))
      from kaibot.trade t where t.run_id = p_run
        and t.entry_ts >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York'),
    'segments', coalesce((select jsonb_agg(jsonb_build_object('level', s.level, 'from_session', s.from_session)
      order by s.from_session, s.id) from kaibot.segment s where s.run_id = p_run), '[]'::jsonb),
    'halts_today', coalesce((select jsonb_agg(jsonb_build_object('kind', h.kind, 'reason', h.reason, 'ts', h.ts)
      order by h.ts desc) from kaibot.halt h
      where (h.run_id = p_run or h.run_id is null)
        and h.ts >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York'), '[]'::jsonb)
  ) into out;
  return out;
end $$;

revoke all on function public.kb_overview(bigint) from public, anon;
grant execute on function public.kb_overview(bigint) to authenticated;

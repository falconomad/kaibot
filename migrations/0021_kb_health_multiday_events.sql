-- 0021: kb_health lists the live daily path's events (#266; Product, 16 Nov 2026).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
-- Production migrations are the stakeholder's (Product applies on his word): apply after 0019 (0020 and 0021 are
-- independent and may be applied in either order).
--
-- What changes. public.kb_health() (0007) returns the last 80 events of a FIXED list of types. The dashboard's Health
-- and Activity now label the corporate-action skips of the multi-day path (#273), but the RPC never returned them:
--   multi_day_ca_skip      a buy skipped: "skipped: corporate action on <date>" (#271, trend_momentum.md Amendment 6)
--   multi_day_ca_unread    the corporate-actions read failed: no buys that session
-- The same is true of the multi-day events Health.tsx already labels (#207: multi_day_book_ended, _retired,
-- _reenabled, _reenable_refused, _no_decision, _waiting, _error, _overnight_off) and of #266's adoption events
-- (spin_off_adopted, symbol_change_adopted, corporate_action_not_adopted; emitted once #266's full PR merges), so
-- they are added too. Nothing else in the function changes: the same body as 0007, with the list longer.
--
-- Read-only: one `create or replace function` of a `stable` function, owner-only (kaibot.is_owner(), as before),
-- execute for `authenticated` only. No table, no delete, drop or truncate.
--
-- Check after applying (read-only):
--   select prosrc like '%multi_day_ca_skip%' and prosrc like '%multi_day_ca_unread%' from pg_proc
--     where proname = 'kb_health';                                                         -- true

create or replace function public.kb_health() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object(
    'now', now(),
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h) order by h.process) from kaibot.heartbeat h), '[]'::jsonb),
    'halts', coalesce((select jsonb_agg(jsonb_build_object('run_id', h.run_id, 'kind', h.kind, 'reason', h.reason,
        'ts', h.ts, 'session', h.session) order by h.ts desc)
      from (select * from kaibot.halt order by ts desc limit 50) h), '[]'::jsonb),
    'events', coalesce((select jsonb_agg(jsonb_build_object('type', e.type, 'ts', e.ts, 'run_id', e.run_id,
        'payload', e.payload) order by e.ts desc)
      from (select * from kaibot.event where type in (
          'engine_start', 'engine_stop', 'halt', 'halt_cleared', 'account_not_clean', 'calendar_stale', 'run_paused',
          'moc_refused', 'watchdog_flatten', 'watchdog_recovered', 'engine_error', 'shadow_error', 'session_start',
          'session_end', 'shadow_session_end', 'command_pause', 'command_resume', 'command_flatten_and_stop',
          'command_set_level', 'command_new_run',
          -- 0021: the live daily path (#206, #207) and its corporate-action steps (#266)
          'multi_day_book_ended', 'multi_day_retired', 'multi_day_reenabled', 'multi_day_reenable_refused',
          'multi_day_no_decision', 'multi_day_waiting', 'multi_day_error', 'multi_day_overnight_off',
          'multi_day_ca_skip', 'multi_day_ca_unread',
          'spin_off_adopted', 'symbol_change_adopted', 'corporate_action_not_adopted')
        order by ts desc limit 80) e), '[]'::jsonb),
    'deadman', jsonb_build_object(
      'armed', (select count(*) = 2 from vault.secrets
                where name in ('kaibot_alpaca_paper_key_id', 'kaibot_alpaca_paper_secret')),
      'jobs', coalesce((select jsonb_agg(jsonb_build_object('job', j.jobname, 'schedule', j.schedule, 'active', j.active))
        from cron.job j where j.jobname like 'kaibot_deadman%'), '[]'::jsonb),
      'runs', coalesce((select jsonb_agg(jsonb_build_object('status', d.status, 'message', left(d.return_message, 200),
          'start', d.start_time) order by d.start_time desc)
        from (select r.* from cron.job_run_details r join cron.job j on j.jobid = r.jobid
              where j.jobname like 'kaibot_deadman%' order by r.start_time desc limit 5) d), '[]'::jsonb)),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts) order by c.ts)
      from kaibot.command c where c.applied_at is null), '[]'::jsonb)
  );
end $$;

revoke all on function public.kb_health() from public, anon;
grant execute on function public.kb_health() to authenticated;

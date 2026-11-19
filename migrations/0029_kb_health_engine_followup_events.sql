-- 0029: kb_health lists the engine's follow-up events (Product, 19 Nov 2026; Backend-quant). After 0021.
--
-- What changes. public.kb_health() returns the last 80 events of a FIXED list of types (0007, longer in 0021). The
-- dashboard (#349, apps/web/src/lib/engineevents.ts and Health.tsx) now reads three types the engine sends that the
-- list leaves out, so until now only their heartbeat copies showed:
--   multi_day_unfilled        an order of the daily path the venue let expire at the close with shares left (#333/#337)
--   daily_note                the day's plain-words report (#311/#318, KAIBOT_DAILY_NOTE=on)
--   multi_day_review_shadow   the pre-open reviewer's verdicts, observer only: nothing reads them back (#310/#317)
-- Nothing else changes: 0021's text with these three types added at the end of the list
-- (tests/test_kb_health_events_sql.py checks the files against each other).
--
-- Dashboard only, additive and read-only: one `create or replace function` of a `stable` function, owner-only
-- (kaibot.is_owner(), as before), then 0021's revoke and grant. No table, no delete, drop or truncate. The engine
-- does not call kb_health. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select prosrc like '%multi_day_unfilled%' and prosrc like '%''daily_note''%'
--      and prosrc like '%multi_day_review_shadow%' from pg_proc where proname = 'kb_health';           -- true

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
          'spin_off_adopted', 'symbol_change_adopted', 'corporate_action_not_adopted',
          -- 0029: unfilled orders (#333/#337), the daily note (#311/#318), the pre-open shadow verdicts (#310/#317)
          'multi_day_unfilled', 'daily_note', 'multi_day_review_shadow')
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

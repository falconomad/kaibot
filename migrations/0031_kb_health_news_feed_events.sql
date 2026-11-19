-- 0031: kb_health lists the news poller's health events (Product, 19 Nov 2026; Data/Backend-quant). After 0029.
--
-- What changes. public.kb_health() returns the last 80 events of a FIXED list of types (0007, 0021, 0029). The
-- engine's news poller (src/kaibot/ingest/news_poll.py, #354, PR #355) sends two types the list leaves out, and the
-- dashboard's Health page labels them:
--   news_feed_unhealthy   every news request of a poll failed, the store refused the poll's writes, or no story for
--                         2 h on a weekday 09:30-16:00 ET {state, error, symbols, last_ok, last_item}
--   news_feed_ok          the feed recovered {state, error, symbols, last_ok, last_item}
-- Nothing else changes: 0029's text with these two types added at the end of the list
-- (tests/test_kb_health_news_events_sql.py checks the files against each other).
--
-- Dashboard only, additive and read-only: one `create or replace function` of a `stable` function, owner-only
-- (kaibot.is_owner(), as before), then 0021's revoke and grant. No table, no delete, drop or truncate. The engine
-- does not call kb_health. Any time, also while the engine runs; independent of whether #355 is merged.
--
-- Check after applying (read-only):
--   select prosrc like '%news_feed_unhealthy%' and prosrc like '%news_feed_ok%'
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
          'multi_day_unfilled', 'daily_note', 'multi_day_review_shadow',
          -- 0031: the engine's news poller turned unhealthy / recovered (#354, PR #355)
          'news_feed_unhealthy', 'news_feed_ok')
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

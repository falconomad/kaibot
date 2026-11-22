-- 0039: kb_health lists the health events of M9 tickets 1.12, 1.10 and 1.17 (Backend-quant, 22 Nov 2026).
-- After 0036.
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
--
-- What changes. public.kb_health() returns the last 80 events of a FIXED list of types. Three M9 changes send
-- health events the list does not have, so the Health page cannot show them:
--   replica_poison     a row Supabase refuses for good was skipped by the replicator; the journal keeps it and
--                      Supabase lacks it until the cause is fixed and the journal is sent again (1.12, #437)
--   replica_backlog    rows have waited for the replica for ten minutes without getting fewer: the dashboard and
--                      the dead-man's book are stale (1.12, #437)
--   clock_skew         the engine's clock and the broker's differ by more than five seconds: no new orders until
--                      they agree; a second event with state ok when they do (1.10, #435)
--   calendar_expiring  the macro calendar's covers_until, or the last row of kaibot.market_calendar_exception, is
--                      within 30 days (1.17, #442)
-- Nothing else changes: 0036's text with these types added at the end of the list
-- (tests/test_kb_health_m9_events_sql.py checks the files against each other).
--
-- Dashboard only, additive and read-only: one `create or replace function` of a `stable` function, owner-only
-- (kaibot.is_owner(), as before), then 0021's revoke and grant. No table, no delete, drop or truncate. The engine
-- does not call kb_health. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select prosrc like '%replica_poison%' and prosrc like '%clock_skew%' and prosrc like '%calendar_expiring%'
--      and prosrc like '%multi_day_column_missing%' from pg_proc where proname = 'kb_health';            -- true

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
          'news_feed_unhealthy', 'news_feed_ok',
          -- 0033: engine events the list left out (#392 and a grep of the engine's event names, 20 Nov 2026)
          'multi_day_carry', 'corporate_actions_error', 'ledger_mismatch', 'reconcile_blocked',
          'dividend_difference', 'venue_fee_unread', 'replica_conflict', 'stop_state_fallback',
          'engine_lease_takeover', 'journal_adopted', 'order_adopted', 'session_finalized_late',
          'watchdog_protect', 'multi_day_exit_recovered', 'multi_day_shorting_on',
          -- 0036: names sent under a dynamic name (#410's fallback dict, the pre-open review's EVENT, command_{kind})
          'multi_day_column_missing', 'multi_day_review', 'command_accept_reconcile',
          -- 0039: M9 engine safety (tickets 1.12, 1.10, 1.17): the replica, the clock, the hand-kept calendars
          'replica_poison', 'replica_backlog', 'clock_skew', 'calendar_expiring')
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

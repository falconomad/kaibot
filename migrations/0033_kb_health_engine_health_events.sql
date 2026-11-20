-- 0033: kb_health lists the engine's health events it left out (Backend-quant, 20 Nov 2026). After 0031.
--
-- What changes. public.kb_health() returns the last 80 events of a FIXED list of types (0007, 0021, 0029, 0031).
-- The engine sends health events the list leaves out, so the Health page never showed them (event 105, the
-- refused corporate-actions read of #392, was found only in the table). Added, from a grep of every event name in
-- src/ and scripts/ (tests/test_kb_health_engine_events_sql.py checks each is still sent):
--   multi_day_carry           unfilled buys carried to the next session, and why some were not (live_daily, #366)
--   corporate_actions_error   the session-start dividends/splits read failed or was refused in part (#392)
--   ledger_mismatch           ledger and broker differ (the drift check)
--   reconcile_blocked         the reconciliation blocks entries
--   dividend_difference       a dividend paid differs from the one accrued
--   venue_fee_unread          a venue fee could not be read (a fill's, #241; Alpaca's FEE activities, #238)
--   replica_conflict          Supabase holds another row for a journal key (replication)
--   stop_state_fallback       ledger_position rows written with a fallback stop_state (0028 not applied)
--   engine_lease_takeover     the engine took over an expired lease from another host
--   journal_adopted           the local journal was adopted with --adopt-journal despite problems
--   order_adopted             an order left by a crash was adopted at start-up
--   session_finalized_late    a session was finalised after a restart past the close
--   watchdog_protect          the watchdog re-created protective stops while the engine was silent
--   multi_day_exit_recovered  a flatten sale sent just before a crash was found at the broker and booked
--   multi_day_shorting_on     shorting is on at the paper account: the daily path's buys are held back
-- Routine flow (fills, plans, orders, decisions, booked dividends, config) stays out, so the 80 rows stay useful.
-- Nothing else changes: 0031's text with these types added at the end of the list
-- (tests/test_kb_health_engine_events_sql.py checks the files against each other).
--
-- Dashboard only, additive and read-only: one `create or replace function` of a `stable` function, owner-only
-- (kaibot.is_owner(), as before), then 0021's revoke and grant. No table, no delete, drop or truncate. The engine
-- does not call kb_health. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select prosrc like '%multi_day_carry%' and prosrc like '%corporate_actions_error%'
--      and prosrc like '%news_feed_ok%' from pg_proc where proname = 'kb_health';                     -- true

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
          'watchdog_protect', 'multi_day_exit_recovered', 'multi_day_shorting_on')
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

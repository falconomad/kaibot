-- 0036: kb_health lists the health events the engine sends under a dynamic name (Backend-quant, 21 Nov 2026).
-- After 0033.
--
-- What changes. public.kb_health() returns the last 80 events of a FIXED list of types. 0033 built its list from a
-- grep of literal `event("...")` calls, so events whose name is not written at the call missed it:
--   multi_day_column_missing  Supabase has no ledger.multi_day column (0034 not applied): the minute rows go
--                             without it and the column is retried hourly (#384, PR #410; the name is the
--                             replicator's fallback dict's "event", sent by engine/__main__.report_conflicts)
--   multi_day_review          the pre-open review's verdicts on the live daily path when it is ON (#131; the name is
--                             agents/preopen_daily.EVENT): a failed review skips that session's buys (fail closed)
--   command_accept_reconcile  the owner accepted the open ledger/broker differences (f"command_{kind}" in
--                             engine/core.py; reconcile.ACCEPT_COMMAND). Not sent from the dashboard yet (0001's
--                             command kind check), listed so it shows once it is
-- Tonight's other merges (#403, #405, #408, #410, #412) add no other health event: #403's market_alarm and
-- market_alarm_error and #405's filing_flag_shadow are research/notice rows (left out on purpose), #408 sends
-- venue_fee_unread (0033 lists it), #412 sends none.
-- tests/test_kb_health_engine_events_sql.py now also reads the dynamic names (the replicator's "event" keys, the
-- agents' EVENT constants, the news poller's queued events, the command kinds) and fails on a new dynamic call site.
-- Nothing else changes: 0033's text with these types added at the end of the list
-- (tests/test_kb_health_dynamic_events_sql.py checks the files against each other).
--
-- Dashboard only, additive and read-only: one `create or replace function` of a `stable` function, owner-only
-- (kaibot.is_owner(), as before), then 0021's revoke and grant. No table, no delete, drop or truncate. The engine
-- does not call kb_health. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select prosrc like '%multi_day_column_missing%' and prosrc like '%''multi_day_review''%'
--      and prosrc like '%multi_day_shorting_on%' from pg_proc where proname = 'kb_health';                -- true

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
          'multi_day_column_missing', 'multi_day_review', 'command_accept_reconcile')
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

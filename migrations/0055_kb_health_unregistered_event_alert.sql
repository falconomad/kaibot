-- 0055: kb_health lists the health events that waited for a migration, among them audit 2 5.13's
-- `unregistered_event`; the dead-man's tick alerts on that event through 0051's Healthchecks.io check
-- (Backend-quant, 26 Nov 2026; #693, #529). After 0039 (kb_health) and 0051 (kaibot.deadman_tick).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
--
-- 1. public.kb_health() returns the last 80 events of a FIXED list of types. The list is generated from the engine's
--    registry (`PYTHONPATH=src python -m kaibot.engine.event_names --sql`, src/kaibot/engine/event_names.py): 0039's
--    types, then every health event written since that waited in the registry's PENDING, then one new one:
--      unregistered_event        the engine wrote an event name its registry lacks (a code fault; audit 2 5.13)
--      journal_snapshot_*        the journal's start-up snapshot not written or not used (M10 1.24, #449)
--      engine_degraded_start, engine_lease_confirmed   start with the database unreachable (M10 1.25, #450)
--      daily_fetch_failed        the engine's own daily-bars fetch failed (audit 2 5.12, #692)
--      multi_day_data_late, data_entry_block, data_entry_unblocked   the daily-data checks (M12 3.11, #473)
--      engine_gap                the engine was not running for over an hour (QA-Ops, 24-25 Nov 2026)
--      multi_day_close_unknown, multi_day_sale_refused, multi_day_decision_lost, multi_day_no_daily_bar,
--      calendar_local            audit 2 5.9/5.11, #780, #746
--      watchdog_close_booked     a sale the watchdog made while the engine was down, booked to the sleeves it sold
--                                (#529's watchdog half, audit 2 5.13)
--    Nothing else changes: 0039's text with these types added at the end of the list.
--
-- 2. Alerts. kaibot.deadman_tick() (0051's body) also looks for `unregistered_event` rows newer than the last one it
--    alerted on (kaibot.deadman_mark, one row) and sends ONE /fail ping to the dead-man's Healthchecks check naming
--    them ("kaibot: the engine wrote an event its registry does not know: <names>"). The next run's success ping
--    turns the check green again, so it is one alert, as for a dead-man halt row. A run that pings fail for its own
--    reason anyway (a halt, missing keys, acting) adds the names to that message. The mark starts at the newest such
--    row at apply time (rows already on the Health page are not alerted again). A failure of this lookup never stops
--    the dead-man (it is skipped for that minute). Without the Healthchecks URL in Vault nothing is sent, as in 0051.
--
-- Additive: `create or replace function` (kb_health, deadman_tick), one new table `kaibot.deadman_mark` (RLS on, no
-- policy: the definer functions only), revoke/grant. kaibot.deadman_check and deadman_act are not changed by a byte.
-- Any time, best outside 09:30-16:00 ET (it replaces the dead-man's tick). Changes no trading behaviour.
--
-- Check after applying (read-only):
--   select prosrc like '%unregistered_event%' and prosrc like '%watchdog_close_booked%'
--     from pg_proc where proname = 'kb_health';                                                    -- true
--   select prosrc like '%deadman_mark%' from pg_proc where proname = 'deadman_tick';               -- true
--   select name, last_id from kaibot.deadman_mark;                                                 -- one row

-- 1. kb_health
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
          'command_set_level', 'command_new_run', 'multi_day_book_ended', 'multi_day_retired', 'multi_day_reenabled',
          'multi_day_reenable_refused', 'multi_day_no_decision', 'multi_day_waiting', 'multi_day_error',
          'multi_day_overnight_off', 'multi_day_ca_skip', 'multi_day_ca_unread', 'spin_off_adopted',
          'symbol_change_adopted', 'corporate_action_not_adopted', 'multi_day_unfilled', 'daily_note',
          'multi_day_review_shadow', 'news_feed_unhealthy', 'news_feed_ok', 'multi_day_carry',
          'corporate_actions_error', 'ledger_mismatch', 'reconcile_blocked', 'dividend_difference',
          'venue_fee_unread', 'replica_conflict', 'stop_state_fallback', 'engine_lease_takeover', 'journal_adopted',
          'order_adopted', 'session_finalized_late', 'watchdog_protect', 'multi_day_exit_recovered',
          'multi_day_shorting_on', 'multi_day_column_missing', 'multi_day_review', 'command_accept_reconcile',
          'replica_poison', 'replica_backlog', 'clock_skew', 'calendar_expiring', 'command_waiting',
          'command_rejected', 'multi_day_book_unchecked', 'multi_day_book_unconfirmed', 'multi_day_unbooked_aged',
          'unregistered_event', 'journal_snapshot_mismatch', 'journal_snapshot_unused', 'engine_degraded_start',
          'engine_lease_confirmed', 'daily_fetch_failed', 'multi_day_data_late', 'data_entry_block',
          'data_entry_unblocked', 'engine_gap', 'multi_day_close_unknown', 'multi_day_sale_refused',
          'multi_day_decision_lost', 'multi_day_no_daily_bar', 'calendar_local', 'watchdog_close_booked')
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

-- 2. the dead-man's alert marks: the newest row of an event type it has alerted on
create table if not exists kaibot.deadman_mark (
  name text primary key,
  last_id bigint not null
);
alter table kaibot.deadman_mark enable row level security;
revoke all on table kaibot.deadman_mark from public, anon, authenticated;
insert into kaibot.deadman_mark (name, last_id)
  select 'unregistered_event', coalesce(max(id), 0) from kaibot.event where type = 'unregistered_event'
  on conflict (name) do nothing;

create or replace function kaibot.deadman_tick(p_now timestamptz default null)
returns text
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  before_id bigint;
  said text;
  err text;
  h record;
  halts text := null;
  keys int;
  unreg text := null;  -- 0055: unregistered event names written since the last alert
  mark bigint;
  top bigint;
begin
  begin  -- 0055: never stops the dead-man; skipped for this minute on any error
    select last_id into mark from kaibot.deadman_mark where name = 'unregistered_event' for update;
    select max(e.id), string_agg(distinct coalesce(e.payload->>'name', '?'), ', ') into top, unreg
      from kaibot.event e where e.type = 'unregistered_event' and e.id > coalesce(mark, 0);
    if top is not null then
      insert into kaibot.deadman_mark (name, last_id) values ('unregistered_event', top)
        on conflict (name) do update set last_id = excluded.last_id;
      unreg := 'kaibot: the engine wrote an event its registry does not know: ' || left(unreg, 500)
               || ' (unregistered_event; add it to src/kaibot/engine/event_names.py)';
    else
      unreg := null;
    end if;
  exception when others then
    unreg := null;
  end;
  select coalesce(max(id), 0) into before_id from kaibot.halt where kind = 'deadman';
  begin
    said := kaibot.deadman_check(false, null, p_now);
  exception when others then
    get stacked diagnostics err = message_text;
    perform kaibot.deadman_ping('fail', format('kaibot dead-man FAILED: kaibot.deadman_check raised "%s"; it did '
                                               'nothing this minute (docs/ops/failsafe.md)', err)
                                        || coalesce('; ' || unreg, ''));
    return 'failed: ' || err;
  end;
  for h in select reason from kaibot.halt where kind = 'deadman' and id > before_id order by id loop
    halts := coalesce(halts || '; ', '') || h.reason;
  end loop;
  select count(*) into keys from vault.decrypted_secrets
   where name in ('kaibot_alpaca_paper_key_id', 'kaibot_alpaca_paper_secret')
     and coalesce(btrim(decrypted_secret), '') <> '';
  if halts is not null then
    perform kaibot.deadman_ping('fail', 'kaibot dead-man halt: ' || halts || coalesce('; ' || unreg, ''));
  elsif keys < 2 then
    perform kaibot.deadman_ping('fail', 'kaibot dead-man: no Alpaca paper keys in Vault (kaibot_alpaca_paper_key_id, '
                                        'kaibot_alpaca_paper_secret): it cannot act if the engine goes silent'
                                        || coalesce('; ' || unreg, ''));
  elsif said in ('engine alive', 'market closed', 'weekend', 'market holiday') and unreg is not null then
    perform kaibot.deadman_ping('fail', unreg);  -- 0055: one alert; the next run's success ping clears it
  elsif said in ('engine alive', 'market closed', 'weekend', 'market holiday') then
    perform kaibot.deadman_ping('success', 'kaibot dead-man: ' || said);
  else
    perform kaibot.deadman_ping('fail', 'kaibot dead-man acting, the engine heartbeat is stale: ' || said
                                        || coalesce('; ' || unreg, ''));
  end if;
  return said || case when keys < 2 and said <> 'no keys' then '; no paper keys in Vault' else '' end;
end;
$$;

revoke all on function kaibot.deadman_tick(timestamptz) from public;
revoke all on function kaibot.deadman_tick(timestamptz) from anon, authenticated;

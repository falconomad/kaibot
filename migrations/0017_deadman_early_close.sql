-- 0017: the off-box dead-man on early-close days (M7 S74d, issue #154; follow-up of the QA-Ops review of #151).
--
-- NOT APPLIED. Production migrations are the stakeholder's: apply this one on "apply". Until then the dead-man of
-- 0006 runs as before (15:56 ET on weekdays only) and early-close days have no off-box dead-man.
--
-- 0006 flattens at 15:56 ET, 4 minutes before a regular close, and does not know the calendar. So on early-close
-- days (13:00 ET) nothing off-box acts at 12:56, while the on-laptop watchdog retries its flatten only until
-- then. And at 15:56 on such a day, or on a weekday holiday, the market is already shut: a stale heartbeat there
-- (the engine is idle) would send DELETE /v2/positions, which Alpaca queues as market sells for the next open.
--
-- This migration:
-- 1) kaibot.market_calendar_exception: the sessions that are NOT 09:30-16:00 ET on a weekday. close_et null
--    = closed all day; otherwise the early close. Seeded from the NYSE Group calendar for 2026-2028 (announced
--    23 Dec 2025: every early close is at 13:00 ET). Extend it every year, next to config/macro_calendar.yaml:
--      insert into kaibot.market_calendar_exception (day, close_et, note) values ('2029-..', '13:00', '...');
-- 2) kaibot.deadman_flatten(force): the same function and signature, now due at the day's close minus 4 minutes
--    (watchdog.DEADMAN_BEFORE_CLOSE): 15:56 on a regular day, 12:56 on an early close, never on a holiday.
--    The rest (heartbeat older than 3 minutes, paper keys from Vault, paper URL hard-coded, halt row) is 0006.
-- 3) Two more pg_cron jobs at 16:56 and 17:56 UTC on weekdays (12:56 ET in summer and in winter). The function
--    ignores every run that is not due, so the four jobs together act at most once a day.
--
-- Paper only: the URL stays https://paper-api.alpaca.markets. Nothing here changes a regular full trading day.
-- Decision 15 (#120, multi-day books) will change what the dead-man closes; this keeps the 0006 behaviour.

create table if not exists kaibot.market_calendar_exception (
  day      date primary key,
  close_et time check (close_et is null or close_et between time '09:31' and time '16:00'),  -- null: closed
  note     text not null default ''
);
alter table kaibot.market_calendar_exception enable row level security;  -- no policies: only service_role

insert into kaibot.market_calendar_exception (day, close_et, note) values
  ('2026-01-01', null, 'New Year''s Day'),
  ('2026-01-19', null, 'Martin Luther King, Jr. Day'),
  ('2026-02-16', null, 'Washington''s Birthday'),
  ('2026-04-03', null, 'Good Friday'),
  ('2026-05-25', null, 'Memorial Day'),
  ('2026-06-19', null, 'Juneteenth'),
  ('2026-07-03', null, 'Independence Day (observed)'),
  ('2026-09-07', null, 'Labor Day'),
  ('2026-11-26', null, 'Thanksgiving Day'),
  ('2026-11-27', '13:00', 'day after Thanksgiving'),
  ('2026-12-24', '13:00', 'Christmas Eve'),
  ('2026-12-25', null, 'Christmas Day'),
  ('2027-01-01', null, 'New Year''s Day'),
  ('2027-01-18', null, 'Martin Luther King, Jr. Day'),
  ('2027-02-15', null, 'Washington''s Birthday'),
  ('2027-03-26', null, 'Good Friday'),
  ('2027-05-31', null, 'Memorial Day'),
  ('2027-06-18', null, 'Juneteenth (observed)'),
  ('2027-07-05', null, 'Independence Day (observed)'),
  ('2027-09-06', null, 'Labor Day'),
  ('2027-11-25', null, 'Thanksgiving Day'),
  ('2027-11-26', '13:00', 'day after Thanksgiving'),
  ('2027-12-24', null, 'Christmas Day (observed)'),
  ('2028-01-17', null, 'Martin Luther King, Jr. Day'),
  ('2028-02-21', null, 'Washington''s Birthday'),
  ('2028-04-14', null, 'Good Friday'),
  ('2028-05-29', null, 'Memorial Day'),
  ('2028-06-19', null, 'Juneteenth'),
  ('2028-07-03', '13:00', 'day before Independence Day'),
  ('2028-07-04', null, 'Independence Day'),
  ('2028-09-04', null, 'Labor Day'),
  ('2028-11-23', null, 'Thanksgiving Day'),
  ('2028-11-24', '13:00', 'day after Thanksgiving'),
  ('2028-12-25', null, 'Christmas Day')
on conflict (day) do nothing;

create or replace function kaibot.deadman_flatten(force boolean default false)
returns text
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  ny timestamp := now() at time zone 'America/New_York';
  ex kaibot.market_calendar_exception%rowtype;
  due timestamp;
  hb timestamptz;
  key_id text;
  secret text;
  req bigint;
begin
  select * into ex from kaibot.market_calendar_exception where day = ny::date;
  -- the day's close minus 4 minutes, New York time (DST-proof); runs up to 3 minutes late still act
  due := ny::date + coalesce(ex.close_et, time '16:00') - interval '4 minutes';
  if not force then
    if extract(isodow from ny) not between 1 and 5 then
      return 'weekend';
    end if;
    if ex.day is not null and ex.close_et is null then
      return 'market holiday';
    end if;
    if not (ny >= due and ny < due + interval '3 minutes') then
      return format('not %s ET', to_char(due, 'HH24:MI'));
    end if;
  end if;
  select ts into hb from kaibot.heartbeat where process = 'engine';
  if hb is not null and hb > now() - interval '3 minutes' then
    return 'engine alive';
  end if;
  select decrypted_secret into key_id from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_key_id';
  select decrypted_secret into secret from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_secret';
  if key_id is null or secret is null then
    insert into kaibot.halt (kind, reason)
    values ('deadman', format('engine heartbeat stale at %s ET but no paper keys in Vault: nothing closed',
                              to_char(due, 'HH24:MI')));
    return 'no keys';
  end if;
  select net.http_delete(
           url := 'https://paper-api.alpaca.markets/v2/positions',
           params := '{"cancel_orders": "true"}'::jsonb,
           headers := jsonb_build_object('APCA-API-KEY-ID', key_id, 'APCA-API-SECRET-KEY', secret),
           timeout_milliseconds := 10000)
    into req;
  insert into kaibot.halt (kind, reason)
  values ('deadman', format('engine heartbeat %s at %s ET: closed all paper positions (pg_net request %s)',
                            coalesce(to_char(hb, 'YYYY-MM-DD HH24:MI:SS TZ'), 'missing'), to_char(due, 'HH24:MI'),
                            req));
  return 'flatten sent';
end;
$$;

revoke all on function kaibot.deadman_flatten(boolean) from public;
revoke all on function kaibot.deadman_flatten(boolean) from anon, authenticated;

-- 0006's two jobs (15:56 ET) stay; these two cover 12:56 ET. The dashboard lists every 'kaibot_deadman%' job.
select cron.unschedule(jobid) from cron.job where jobname in ('kaibot_deadman_early_edt', 'kaibot_deadman_early_est');
select cron.schedule('kaibot_deadman_early_edt', '56 16 * * 1-5', 'select kaibot.deadman_flatten()');
select cron.schedule('kaibot_deadman_early_est', '56 17 * * 1-5', 'select kaibot.deadman_flatten()');

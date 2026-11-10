-- 0006: fail-closed support (stories S16, S18).
-- 1) halt.session: which trading session a halt belongs to (the engine counts daily stops per session to pause
--    a run after 3 in ~10 sessions). The engine writes this column from this release on: apply BEFORE deploying.
-- 2) Off-box dead-man (tech design §3): if the engine heartbeat is stale at 15:56 ET, Postgres itself closes
--    every position in the Alpaca PAPER account and cancels its orders. It covers what the on-laptop watchdog
--    cannot: the Mac asleep, off or offline. It is inert until the owner stores the paper keys in Vault:
--      select vault.create_secret('<paper key id>',  'kaibot_alpaca_paper_key_id');
--      select vault.create_secret('<paper secret>',  'kaibot_alpaca_paper_secret');
--    (run those in the Supabase SQL editor; never paste keys into chat or the repo).
--    Paper only: the URL is hard-coded to paper-api.alpaca.markets. Early-close days (12:55 flatten) are not
--    covered; the engine and the watchdog still flatten then, and positions keep their GTC stops.

alter table kaibot.halt add column if not exists session date;
create index if not exists halt_run_kind_session on kaibot.halt (run_id, kind, session);

create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function kaibot.deadman_flatten(force boolean default false)
returns text
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  ny timestamp := now() at time zone 'America/New_York';
  hb timestamptz;
  key_id text;
  secret text;
  req bigint;
begin
  -- cron fires at 19:56 and 20:56 UTC on weekdays; only the one that is 15:56 New York time acts (DST-proof)
  if not force and not (extract(isodow from ny) between 1 and 5 and extract(hour from ny) = 15
                        and extract(minute from ny) between 56 and 58) then
    return 'not 15:56 ET';
  end if;
  select ts into hb from kaibot.heartbeat where process = 'engine';
  if hb is not null and hb > now() - interval '3 minutes' then
    return 'engine alive';
  end if;
  select decrypted_secret into key_id from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_key_id';
  select decrypted_secret into secret from vault.decrypted_secrets where name = 'kaibot_alpaca_paper_secret';
  if key_id is null or secret is null then
    insert into kaibot.halt (kind, reason) values ('deadman', 'engine heartbeat stale at 15:56 ET but no paper keys in Vault: nothing closed');
    return 'no keys';
  end if;
  select net.http_delete(
           url := 'https://paper-api.alpaca.markets/v2/positions',
           params := '{"cancel_orders": "true"}'::jsonb,
           headers := jsonb_build_object('APCA-API-KEY-ID', key_id, 'APCA-API-SECRET-KEY', secret),
           timeout_milliseconds := 10000)
    into req;
  insert into kaibot.halt (kind, reason)
  values ('deadman', format('engine heartbeat %s at 15:56 ET: closed all paper positions (pg_net request %s)',
                            coalesce(to_char(hb, 'YYYY-MM-DD HH24:MI:SS TZ'), 'missing'), req));
  return 'flatten sent';
end;
$$;

revoke all on function kaibot.deadman_flatten(boolean) from public;
revoke all on function kaibot.deadman_flatten(boolean) from anon, authenticated;

select cron.unschedule(jobid) from cron.job where jobname in ('kaibot_deadman_edt', 'kaibot_deadman_est');
select cron.schedule('kaibot_deadman_edt', '56 19 * * 1-5', 'select kaibot.deadman_flatten()');
select cron.schedule('kaibot_deadman_est', '56 20 * * 1-5', 'select kaibot.deadman_flatten()');

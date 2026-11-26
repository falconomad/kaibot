-- 0051: the dead-man tells someone (audit 2 ticket 7.7, #717).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
-- Production migrations are the stakeholder's. After 0019 (kaibot.deadman_check). Additive: kaibot.deadman_check
-- and kaibot.deadman_act are not changed by a byte.
--
-- Before this, the dead-man wrote a kaibot.halt row and sent nothing off the box. A dead-man that had stopped (the
-- cron job gone or failing, pg_net down, the function raising, the paper keys missing from Vault) alerted nobody.
-- From this migration on, the one pg_cron job runs kaibot.deadman_tick(), which runs kaibot.deadman_check() as before
-- and then pings a Healthchecks.io check of the dead-man's own (the engine already uses Healthchecks.io; free plan):
--   - success (the URL itself): the check ran, the paper keys are in Vault, and nothing is wrong (engine alive,
--     market closed, weekend, holiday). Once a minute, 13:00-21:59 UTC on weekdays, as the job runs;
--   - /fail with the reason, every minute while it lasts (Healthchecks alerts once, when the check turns red):
--       * a dead-man halt row written this minute ("kaibot dead-man halt: engine silent ...");
--       * the dead-man acting on a silent engine (reading the account, waiting for the broker, acting);
--       * the Alpaca paper keys missing from Vault, checked on EVERY run, also while the engine is alive (before,
--         a missing key was found only when the engine had already gone silent);
--       * kaibot.deadman_check raising (its work of that minute is rolled back, as a failed cron run was before).
--   - no ping at all (the cron job gone, inactive or failing before it starts, pg_net's worker down, Supabase
--     down): the Healthchecks check turns red by itself after its grace time. That is the dead-man's own failure.
-- The check's schedule is cron "* 13-21 * * 1-5", time zone UTC, grace 10 minutes (docs/ops/failsafe.md, "Dead-man
-- alerts"). Healthchecks then sends the alert to e-mail and, with its phone integration, to the phone.
--
-- The ping URL lives in Vault as 'kaibot_deadman_healthchecks_url' (never in the repo). Without it nothing is sent
-- and kaibot.deadman_tick() is kaibot.deadman_check() and nothing more: applying this file changes no behaviour
-- until the secret is stored. A ping that pg_net cannot queue is dropped; it never stops the dead-man.
--
-- Check after applying (read-only):
--   select jobname, schedule, command from cron.job where jobname = 'kaibot_deadman';
--     -- one row: kaibot_deadman | * 13-21 * * 1-5 | select kaibot.deadman_tick()
--   select kaibot.deadman_tick();   -- outside market hours: 'market closed' (or 'weekend', 'market holiday')

create or replace function kaibot.deadman_ping(p_kind text, p_msg text)
returns bigint
language plpgsql
security definer
set search_path = kaibot, pg_temp
as $$
declare
  url text;
begin
  select decrypted_secret into url from vault.decrypted_secrets where name = 'kaibot_deadman_healthchecks_url';
  url := btrim(coalesce(url, ''));
  if url !~ '^https://[^[:space:]]+$' then
    return null;  -- not set up: nothing is sent
  end if;
  url := rtrim(url, '/') || case p_kind when 'fail' then '/fail' when 'log' then '/log' else '' end;
  return net.http_post(url := url, body := jsonb_build_object('msg', left(coalesce(p_msg, ''), 2000)),
                       timeout_milliseconds := 10000);
exception when others then
  return null;  -- an alert that cannot be queued never stops the dead-man
end;
$$;

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
begin
  select coalesce(max(id), 0) into before_id from kaibot.halt where kind = 'deadman';
  begin
    said := kaibot.deadman_check(false, null, p_now);
  exception when others then
    get stacked diagnostics err = message_text;
    perform kaibot.deadman_ping('fail', format('kaibot dead-man FAILED: kaibot.deadman_check raised "%s"; it did '
                                               'nothing this minute (docs/ops/failsafe.md)', err));
    return 'failed: ' || err;
  end;
  for h in select reason from kaibot.halt where kind = 'deadman' and id > before_id order by id loop
    halts := coalesce(halts || '; ', '') || h.reason;
  end loop;
  select count(*) into keys from vault.decrypted_secrets
   where name in ('kaibot_alpaca_paper_key_id', 'kaibot_alpaca_paper_secret')
     and coalesce(btrim(decrypted_secret), '') <> '';
  if halts is not null then
    perform kaibot.deadman_ping('fail', 'kaibot dead-man halt: ' || halts);
  elsif keys < 2 then
    perform kaibot.deadman_ping('fail', 'kaibot dead-man: no Alpaca paper keys in Vault (kaibot_alpaca_paper_key_id, '
                                        'kaibot_alpaca_paper_secret): it cannot act if the engine goes silent');
  elsif said in ('engine alive', 'market closed', 'weekend', 'market holiday') then
    perform kaibot.deadman_ping('success', 'kaibot dead-man: ' || said);
  else
    perform kaibot.deadman_ping('fail', 'kaibot dead-man acting, the engine heartbeat is stale: ' || said);
  end if;
  return said || case when keys < 2 and said <> 'no keys' then '; no paper keys in Vault' else '' end;
end;
$$;

revoke all on function kaibot.deadman_ping(text, text) from public;
revoke all on function kaibot.deadman_ping(text, text) from anon, authenticated;
revoke all on function kaibot.deadman_tick(timestamptz) from public;
revoke all on function kaibot.deadman_tick(timestamptz) from anon, authenticated;

-- the same one job (0019's name and schedule), now through the tick
select cron.schedule('kaibot_deadman', '* 13-21 * * 1-5', 'select kaibot.deadman_tick()');

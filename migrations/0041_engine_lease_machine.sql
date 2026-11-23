-- 0041 (Backend-quant, 22 Nov 2026; issue #562): the engine lease is bound to the holder AND the machine. After 0013.
--
-- Why. 0013 matches the lease on `holder` only. The holder id lives in the data folder (data/live/engine_holder_id),
-- so a data folder copied to a second machine made that machine the SAME holder: with Supabase up its start was
-- "already ours" (acquired, not refused) and both engines renewed the one lease. Two engines, one paper account.
--
-- What changes.
--   kaibot.engine_lease  a column `machine` (text, not null, default ''): a hash the engine computes at start from
--                        the host name and the operating system's own machine id (engine/lease.py `machine_key`),
--                        never from the data folder, so a copied folder does not carry it. Not a secret.
--   engine_lease_acquire / _renew / _release  take `p_machine` (default '') and match on holder AND machine:
--     acquire  "already ours" only when holder and machine are both this engine's; the same holder on another
--              machine with a live lease is REFUSED (`acquired: false`, `reason: 'holder_on_other_machine'`),
--              as for any other live holder; an expired lease is taken over as before (`taken_over: true`).
--     renew    extends only a row with this holder and machine; false otherwise (the engine stops: LeaseLost).
--     release  deletes only a row with this holder and machine.
--   Same single-row compare-and-set as 0013 (atomic under the row lock), database clock only, service role only.
--
-- Rollout. Apply BEFORE deploying the engine release that sends `p_machine`: an engine of an older release calls the
-- same functions without it, so it acts as machine '' and keeps its lease (its row has machine '' after this file).
-- The first start of the new release while an older one still holds the lease on the same machine is refused until
-- that lease is given back (a clean stop releases it) or expires (90 s): fail closed. The protection holds once
-- every engine runs the new release; the engine never sends ''.
--
-- Changes live trading behaviour only by refusing a second engine. No data is deleted; the three functions are
-- replaced in one transaction (no moment without them). Outside 09:30-16:00 ET.
--
-- Check after applying (read-only):
--   select column_name from information_schema.columns
--    where table_schema = 'kaibot' and table_name = 'engine_lease' and column_name = 'machine';      -- one row
--   select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--    where n.nspname = 'kaibot' and p.proname like 'engine_lease_%' order by 1;                     -- three rows,
--      each with a trailing `text` argument (p_machine)
--   select has_function_privilege('authenticated',
--          'kaibot.engine_lease_renew(text, integer, text)', 'execute');                             -- false

begin;

alter table kaibot.engine_lease add column if not exists machine text not null default '';

drop function if exists kaibot.engine_lease_acquire(text, text, integer);
drop function if exists kaibot.engine_lease_renew(text, integer);
drop function if exists kaibot.engine_lease_release(text);

-- Take the lease if it is free, expired or already ours (holder AND machine). Always returns the row as it is after
-- the call, plus `acquired`; when another holder's (or this holder's on another machine) expired lease was taken,
-- `taken_over` and the previous holder and host.
create or replace function kaibot.engine_lease_acquire(p_holder text, p_host text, p_ttl_s integer default 90,
                                                       p_machine text default '')
returns jsonb
language plpgsql security definer set search_path = kaibot, pg_temp as $$
declare
  prev kaibot.engine_lease;
  cur  kaibot.engine_lease;
  m    text := coalesce(p_machine, '');
begin
  if coalesce(p_holder, '') = '' or p_ttl_s is null or p_ttl_s < 30 or p_ttl_s > 600 then
    raise exception 'engine_lease_acquire: holder required, ttl must be 30..600 s';
  end if;
  -- Lock the row if it exists, so `prev` is exactly what the upsert below replaces.
  select * into prev from kaibot.engine_lease where id for update;
  insert into kaibot.engine_lease as l (id, holder, host, machine, started_at, renewed_at, expires_at)
  values (true, p_holder, coalesce(p_host, ''), m, now(), now(), now() + make_interval(secs => p_ttl_s))
  on conflict (id) do update
    set holder = excluded.holder, host = excluded.host, machine = excluded.machine,
        renewed_at = excluded.renewed_at, expires_at = excluded.expires_at,
        started_at = case when l.holder = excluded.holder and l.machine = excluded.machine and l.expires_at > now()
                          then l.started_at else excluded.started_at end
    where (l.holder = excluded.holder and l.machine = excluded.machine) or l.expires_at <= now()
  returning l.* into cur;
  if cur.id is null then                                     -- refused: someone else's lease is live
    select * into cur from kaibot.engine_lease where id;
    return jsonb_build_object('acquired', false, 'holder', cur.holder, 'host', cur.host,
                              'started_at', cur.started_at, 'expires_at', cur.expires_at,
                              'expires_in_s', greatest(0, ceil(extract(epoch from cur.expires_at - now()))),
                              'reason', case when cur.holder = p_holder then 'holder_on_other_machine'
                                             else 'held_by_other' end);
  end if;
  return jsonb_build_object('acquired', true, 'holder', cur.holder, 'host', cur.host,
                            'started_at', cur.started_at, 'expires_at', cur.expires_at,
                            'taken_over', prev.id is not null and (prev.holder <> p_holder or prev.machine <> m),
                            'previous_holder', case when prev.holder <> p_holder or prev.machine <> m
                                                    then prev.holder end,
                            'previous_host', case when prev.holder <> p_holder or prev.machine <> m
                                                  then prev.host end);
end $$;

-- Extend our own lease (holder AND machine). False means it is no longer ours (another engine took it over, it was
-- released by hand, or the row is this holder's on another machine): the caller must stop trading. A lease that
-- expired but was not taken by anyone is still ours and is extended.
create or replace function kaibot.engine_lease_renew(p_holder text, p_ttl_s integer default 90,
                                                     p_machine text default '')
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  if p_ttl_s is null or p_ttl_s < 30 or p_ttl_s > 600 then
    raise exception 'engine_lease_renew: ttl must be 30..600 s';
  end if;
  update kaibot.engine_lease
     set renewed_at = now(), expires_at = now() + make_interval(secs => p_ttl_s)
   where id and holder = p_holder and machine = coalesce(p_machine, '');
  return found;
end $$;

-- Give the lease back on a clean stop. Only the holder on its own machine can; false means it was not ours.
create or replace function kaibot.engine_lease_release(p_holder text, p_machine text default '')
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  delete from kaibot.engine_lease where id and holder = p_holder and machine = coalesce(p_machine, '');
  return found;
end $$;

revoke all on function kaibot.engine_lease_acquire(text, text, integer, text),
  kaibot.engine_lease_renew(text, integer, text), kaibot.engine_lease_release(text, text)
  from public, anon, authenticated;
grant execute on function kaibot.engine_lease_acquire(text, text, integer, text),
  kaibot.engine_lease_renew(text, integer, text), kaibot.engine_lease_release(text, text) to service_role;

commit;

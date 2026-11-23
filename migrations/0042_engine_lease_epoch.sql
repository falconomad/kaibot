-- 0042 (Backend-quant, 22 Nov 2026; M13 ticket 4.3, issue #477): a fencing token on the engine lease. After 0041.
--
-- Why. 0013/0041 say WHO holds the lease (holder and machine) but not WHICH tenure. A second engine process of the
-- same install on the same machine (scripts/engine_guard.sh failed, or a paused process that resumes after a quick
-- restart) matched the row as "already ours" and both renewed it. A number that only rises tells the tenures apart.
--
-- What changes.
--   kaibot.engine_lease_epoch_seq  a sequence: every successful acquire takes the next number (never reused, also
--                                  not after a release deletes the row).
--   kaibot.engine_lease.epoch      the tenure's number (bigint, default 0 for a row written before this file).
--   engine_lease_acquire  takes `p_fenced` (default false; the engine of this release sends true). A fenced success
--                         is a NEW tenure with a new epoch from the sequence, returned as `epoch`: also for the same
--                         holder and machine (a restart). The older process of that install then fails its next
--                         renewal (LeaseLost) and stops. An unfenced success (an engine of a release before #477)
--                         sets epoch 0. `taken_over` and `started_at` keep 0041's meaning. The acquires are
--                         serialised by a transaction-scoped advisory lock taken before the sequence is read, so the
--                         epochs rise in the order the tenures begin, also when no row exists yet.
--   engine_lease_renew / _release  take `p_epoch` (default null) and match `epoch = coalesce(p_epoch, 0)`. An engine
--                         of a release before #477 sends none: it keeps its lease while the row is an unfenced
--                         tenure (epoch 0, which the running engine's row is after this file), and loses it at its
--                         next renewal once an engine of this release has started (QA-Ops on #584: never two
--                         engines, also not during the rollout).
--   The engine renews with its epoch before every order it sends (engine/lease.py `fence`, engine/fence.py), so a
--   paused or older process whose tenure has ended sends nothing.
--   Same single-row compare-and-set as 0013 (atomic under the row lock), database clock only, service role only.
--
-- Rollout. Apply BEFORE deploying the engine release that sends `p_epoch`; after 0041. The running engine keeps its
-- lease (it sends no epoch and its row gets epoch 0); an engine of an older release can also start again after this
-- file (rollback-safe: its unfenced tenure has epoch 0). Outside 09:30-16:00 ET. No data is deleted; the functions are replaced in one
-- transaction.
--
-- Check after applying (read-only):
--   select count(*) from information_schema.columns
--    where table_schema = 'kaibot' and table_name = 'engine_lease' and column_name = 'epoch';         -- 1
--   select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
--    where n.nspname = 'kaibot' and p.proname like 'engine_lease_%' order by 1;                       -- three rows
--   select has_function_privilege('authenticated',
--          'kaibot.engine_lease_renew(text, integer, text, bigint)', 'execute');                      -- false
--   select has_function_privilege('authenticated',
--          'kaibot.engine_lease_acquire(text, text, integer, text, boolean)', 'execute');             -- false

begin;

create sequence if not exists kaibot.engine_lease_epoch_seq as bigint start 1;
revoke all on sequence kaibot.engine_lease_epoch_seq from public, anon, authenticated;
alter table kaibot.engine_lease add column if not exists epoch bigint not null default 0;

drop function if exists kaibot.engine_lease_acquire(text, text, integer, text);
drop function if exists kaibot.engine_lease_renew(text, integer, text);
drop function if exists kaibot.engine_lease_release(text, text);

-- Take the lease if it is free, expired or this holder's on this machine. Every success is a new tenure (a new
-- epoch). Always returns the row as it is after the call, plus `acquired`.
create or replace function kaibot.engine_lease_acquire(p_holder text, p_host text, p_ttl_s integer default 90,
                                                       p_machine text default '', p_fenced boolean default false)
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
  -- One acquire at a time, also when there is no row to lock yet: the epoch is drawn under this lock, so a later
  -- tenure always has the higher epoch (QA-Ops on #584). Released at the end of the transaction.
  perform pg_advisory_xact_lock(hashtext('kaibot.engine_lease_acquire'));
  -- Lock the row if it exists, so `prev` is exactly what the upsert below replaces.
  select * into prev from kaibot.engine_lease where id for update;
  insert into kaibot.engine_lease as l (id, holder, host, machine, epoch, started_at, renewed_at, expires_at)
  values (true, p_holder, coalesce(p_host, ''), m,
          case when coalesce(p_fenced, false) then nextval('kaibot.engine_lease_epoch_seq') else 0 end, now(), now(),
          now() + make_interval(secs => p_ttl_s))
  on conflict (id) do update
    set holder = excluded.holder, host = excluded.host, machine = excluded.machine, epoch = excluded.epoch,
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
  return jsonb_build_object('acquired', true, 'holder', cur.holder, 'host', cur.host, 'epoch', cur.epoch,
                            'started_at', cur.started_at, 'expires_at', cur.expires_at,
                            'taken_over', prev.id is not null and (prev.holder <> p_holder or prev.machine <> m),
                            'previous_holder', case when prev.holder <> p_holder or prev.machine <> m
                                                    then prev.holder end,
                            'previous_host', case when prev.holder <> p_holder or prev.machine <> m
                                                  then prev.host end);
end $$;

-- Extend our own tenure. False: it is no longer ours (another engine or another process of this install took it,
-- or it was released by hand): the caller must stop. A tenure that expired but was not taken is still ours.
create or replace function kaibot.engine_lease_renew(p_holder text, p_ttl_s integer default 90,
                                                     p_machine text default '', p_epoch bigint default null)
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  if p_ttl_s is null or p_ttl_s < 30 or p_ttl_s > 600 then
    raise exception 'engine_lease_renew: ttl must be 30..600 s';
  end if;
  update kaibot.engine_lease
     set renewed_at = now(), expires_at = now() + make_interval(secs => p_ttl_s)
   where id and holder = p_holder and machine = coalesce(p_machine, '')
     and epoch = coalesce(p_epoch, 0);
  return found;
end $$;

create or replace function kaibot.engine_lease_release(p_holder text, p_machine text default '',
                                                       p_epoch bigint default null)
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  delete from kaibot.engine_lease
   where id and holder = p_holder and machine = coalesce(p_machine, '') and epoch = coalesce(p_epoch, 0);
  return found;
end $$;

revoke all on function kaibot.engine_lease_acquire(text, text, integer, text, boolean),
  kaibot.engine_lease_renew(text, integer, text, bigint), kaibot.engine_lease_release(text, text, bigint)
  from public, anon, authenticated;
grant execute on function kaibot.engine_lease_acquire(text, text, integer, text, boolean),
  kaibot.engine_lease_renew(text, integer, text, bigint), kaibot.engine_lease_release(text, text, bigint) to service_role;

commit;

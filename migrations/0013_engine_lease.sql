-- 0013: cross-machine engine lease (issue #81). Engine-side only (service role).
--
-- One row says which engine may trade the paper account. An engine takes it at start-up, renews it with every
-- heartbeat and deletes it on a clean stop. While the row is live (expires_at in the future) an engine with a
-- different holder id is refused. A holder that stops renewing (crash, power loss, network gone) loses the lease
-- when it expires, and the next engine to start takes it over.
--
-- All three functions are single statements on the one row, so Postgres' row lock makes them atomic: two engines
-- that start in the same instant cannot both get `acquired = true`. Times are the database's clock (now()), never
-- an engine's, so clock differences between machines do not matter.
--
-- APPLY BEFORE deploying the engine code that calls these: an engine that cannot reach the functions refuses to
-- start (fail closed).

create table if not exists kaibot.engine_lease (
  id         boolean primary key default true check (id),   -- a single row, ever
  holder     text        not null,                          -- stable id of one engine install (not a secret)
  host       text        not null,                          -- hostname, for the operator
  started_at timestamptz not null default now(),            -- when this holder took the lease
  renewed_at timestamptz not null default now(),
  expires_at timestamptz not null
);
alter table kaibot.engine_lease enable row level security;   -- no policies: only service_role (bypasses RLS)
revoke all on kaibot.engine_lease from public, anon, authenticated;
grant select on kaibot.engine_lease to service_role;         -- writes go through the functions below only
revoke insert, update, delete on kaibot.engine_lease from service_role;

-- Take the lease if it is free, expired or already ours (compare-and-set). Always returns the row as it is after
-- the call, plus `acquired`; when another holder's expired lease was taken, `taken_over` and the previous holder.
create or replace function kaibot.engine_lease_acquire(p_holder text, p_host text, p_ttl_s integer default 90)
returns jsonb
language plpgsql security definer set search_path = kaibot, pg_temp as $$
declare
  prev kaibot.engine_lease;
  cur  kaibot.engine_lease;
begin
  if coalesce(p_holder, '') = '' or p_ttl_s is null or p_ttl_s < 30 or p_ttl_s > 600 then
    raise exception 'engine_lease_acquire: holder required, ttl must be 30..600 s';
  end if;
  -- Lock the row if it exists, so `prev` is exactly what the upsert below replaces.
  select * into prev from kaibot.engine_lease where id for update;
  insert into kaibot.engine_lease as l (id, holder, host, started_at, renewed_at, expires_at)
  values (true, p_holder, coalesce(p_host, ''), now(), now(), now() + make_interval(secs => p_ttl_s))
  on conflict (id) do update
    set holder = excluded.holder, host = excluded.host, renewed_at = excluded.renewed_at,
        expires_at = excluded.expires_at,
        started_at = case when l.holder = excluded.holder and l.expires_at > now()
                          then l.started_at else excluded.started_at end
    where l.holder = excluded.holder or l.expires_at <= now()
  returning l.* into cur;
  if cur.id is null then                                     -- refused: someone else's lease is live
    select * into cur from kaibot.engine_lease where id;
    return jsonb_build_object('acquired', false, 'holder', cur.holder, 'host', cur.host,
                              'started_at', cur.started_at, 'expires_at', cur.expires_at,
                              'expires_in_s', greatest(0, ceil(extract(epoch from cur.expires_at - now()))));
  end if;
  return jsonb_build_object('acquired', true, 'holder', cur.holder, 'host', cur.host,
                            'started_at', cur.started_at, 'expires_at', cur.expires_at,
                            'taken_over', prev.id is not null and prev.holder <> p_holder,
                            'previous_holder', case when prev.holder <> p_holder then prev.holder end,
                            'previous_host', case when prev.holder <> p_holder then prev.host end);
end $$;

-- Extend our own lease. False means it is no longer ours (another engine took it over, or it was released by
-- hand): the caller must stop trading. A lease that expired but was not taken by anyone is still ours and is
-- extended.
create or replace function kaibot.engine_lease_renew(p_holder text, p_ttl_s integer default 90)
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  if p_ttl_s is null or p_ttl_s < 30 or p_ttl_s > 600 then
    raise exception 'engine_lease_renew: ttl must be 30..600 s';
  end if;
  update kaibot.engine_lease
     set renewed_at = now(), expires_at = now() + make_interval(secs => p_ttl_s)
   where id and holder = p_holder;
  return found;
end $$;

-- Give the lease back on a clean stop. Only the holder can; false means it was not ours.
create or replace function kaibot.engine_lease_release(p_holder text)
returns boolean
language plpgsql security definer set search_path = kaibot, pg_temp as $$
begin
  delete from kaibot.engine_lease where id and holder = p_holder;
  return found;
end $$;

revoke all on function kaibot.engine_lease_acquire(text, text, integer),
  kaibot.engine_lease_renew(text, integer), kaibot.engine_lease_release(text) from public, anon, authenticated;
grant execute on function kaibot.engine_lease_acquire(text, text, integer),
  kaibot.engine_lease_renew(text, integer), kaibot.engine_lease_release(text) to service_role;

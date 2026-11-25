-- Audit 2 ticket 5.6 (#686): record migrations 0041-0047 in Supabase's migration history.
--
-- Production has every migration 0001-0047 (docs/ops/migrations_pending.md), but Supabase's history table
-- supabase_migrations.schema_migrations ends at 0040: 0041-0047 were applied by hand with psql, which writes no
-- history row. This file adds the seven rows, and nothing else. It changes no kaibot object, no function and no
-- data the engine or the dashboard reads; it runs no migration.
--
-- The owner runs it (the stakeholder applies everything on the production database), any time, also while the
-- engine runs. From the repo root, one transaction, stops at the first error:
--
--   psql "$DATABASE_URL" -X -1 -v ON_ERROR_STOP=1 -P pager=off -f scripts/backfill_migration_history.sql
--
-- (DATABASE_URL: Supabase, Project settings > Database > URI; psql: brew install libpq.) Then `list_migrations`
-- (or the last query below) ends at 0047.
--
-- Idempotent: a migration that already has a row (same number as version, or its name with or without the number)
-- is skipped, so a second run adds nothing. It copies the form of the existing 0040 row, whatever tool wrote it:
--   - version: the number itself ('0040') or a 14-digit UTC timestamp ('20261123210500', the Supabase CLI and MCP
--     form). For a timestamp, each row gets the time it was applied (below, from migrations_pending.md), kept
--     strictly after the row before it. 0043 and 0044: the time was not recorded (Product's word on 25 Nov 2026: 0001-0045
--     applied), so they get versions just before 0045's. In that form the version is an ordering key, not a record
--     of when;
--   - name: with the number ('0040_accept_unbooked') or without ('accept_unbooked').
-- It refuses, and writes nothing, when the table is missing, when no 0040 row is there, or when the version form is
-- neither of the two.
-- statements: empty. The SQL is in migrations/; Supabase reads the column only to show it.

do $$
declare
  last_v text;
  last_name text;
  ts_form boolean;
  numbered boolean;
  prev_v text;
  v text;
  m record;
  added int := 0;
begin
  if to_regclass('supabase_migrations.schema_migrations') is null then
    raise exception 'supabase_migrations.schema_migrations does not exist: nothing written';
  end if;

  select s.version, s.name into last_v, last_name
    from supabase_migrations.schema_migrations s
   where s.version = '0040' or s.name in ('0040_accept_unbooked', 'accept_unbooked')
   order by s.version desc limit 1;
  if last_v is null then
    raise exception 'no history row for 0040 (accept_unbooked): read the table first '
                    '(select version, name from supabase_migrations.schema_migrations order by version); nothing written';
  end if;
  if last_v ~ '^[0-9]{14}$' then
    ts_form := true;
  elsif last_v ~ '^[0-9]{4}$' then
    ts_form := false;
  else
    raise exception 'the 0040 row has version %, neither NNNN nor a 14-digit timestamp: nothing written', last_v;
  end if;
  numbered := last_name like '0040\_%';
  prev_v := last_v;

  for m in
    select * from (values
      ('0041', 'engine_lease_machine',     timestamptz '2026-11-23 21:10:00+00'),  -- stakeholder, ~22:10 CET
      ('0042', 'engine_lease_epoch',       timestamptz '2026-11-23 21:10:30+00'),  -- the same sitting
      ('0043', 'headline_feed',            timestamptz '2026-11-24 21:29:00+00'),  -- time not recorded
      ('0044', 'deadman_idempotency_key',  timestamptz '2026-11-24 21:29:30+00'),  -- time not recorded
      ('0045', 'invites',                  timestamptz '2026-11-24 21:30:00+00'),  -- owner, 22:30 CET
      ('0046', 'auth_followups',           timestamptz '2026-11-25 19:42:00+00'),  -- owner, about 20:42 CET
      ('0047', 'helper_shadow_results',    timestamptz '2026-11-25 19:42:30+00')   -- the same psql sitting
    ) as t(num, slug, applied_at)
    order by num
  loop
    if exists (select 1 from supabase_migrations.schema_migrations s
                where s.version = m.num or s.name in (m.num || '_' || m.slug, m.slug)) then
      select s.version into prev_v from supabase_migrations.schema_migrations s
       where s.version = m.num or s.name in (m.num || '_' || m.slug, m.slug) order by s.version desc limit 1;
      raise notice '% already recorded (version %): skipped', m.num, prev_v;
      continue;
    end if;
    if ts_form then
      v := to_char(m.applied_at at time zone 'UTC', 'YYYYMMDDHH24MISS');
      if v <= prev_v then  -- strictly after the row before it, whatever clock wrote that one
        v := to_char(to_timestamp(prev_v, 'YYYYMMDDHH24MISS') + interval '1 second', 'YYYYMMDDHH24MISS');
      end if;
    else
      v := m.num;
    end if;
    insert into supabase_migrations.schema_migrations (version, name, statements)
    values (v, case when numbered then m.num || '_' || m.slug else m.slug end, array[]::text[]);
    raise notice '% recorded as version %', m.num, v;
    prev_v := v;
    added := added + 1;
  end loop;
  raise notice 'backfill_migration_history: % row(s) added', added;
end $$;

-- the end of the history: the last row should be 0047
select version, name from supabase_migrations.schema_migrations order by version desc limit 9;

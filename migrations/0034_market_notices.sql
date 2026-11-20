-- 0034: the dashboard's market notices, "New on the market" and "Big day" (#378, M8 S104). Design:
-- docs/design/m8_market_notices.md. After 0033 (number only: no dependency on it).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
--
-- What it adds.
-- 1) kaibot.market_notice: a small display table, one row per notice. Written only by the Mac's notice publisher
--    (scripts/publish_notices.py, service role, off unless KAIBOT_NOTICES=1) from the new-listing watch (S102) and,
--    once #377 is merged, the market-event alarm for the next sessions. RLS on, no policy: anon and authenticated
--    get nothing; the dashboard reads it only through kb_market_notices(). The engine neither writes nor reads it.
-- 2) public.kb_market_notices(p_today date default null): owner-only, read-only (stable). The notices of the window
--    "four sessions back to five sessions ahead" of p_today (default: today in New York), from
--      a. kaibot.market_notice rows (not withdrawn, available_ts <= now()), and
--      b. the engine's `market_alarm` events (S103, PR #403; written only with KAIBOT_MARKET_ALARM=on): the latest
--         event per session, one big_day per flag that is true.
--    When both give the same notice (kind, flag, session, symbol), the engine's event wins.
--    Each notice: {kind, flag, tier, session, symbol, title, starts, first_eligible, detail, source}.
--    Sessions are weekdays that kaibot.market_calendar_exception (0017) does not close.
--
-- Read-only for the dashboard, additive. `create table if not exists`, `create index if not exists`,
-- `create or replace function`, revoke and grant. No delete, drop, truncate or update. Any time, also while the
-- engine runs.
--
-- Check after applying (read-only):
--   select has_function_privilege('anon', 'public.kb_market_notices(date)', 'execute');            -- false
--   select has_table_privilege('authenticated', 'kaibot.market_notice', 'select');                 -- false
--   select relrowsecurity from pg_class where oid = 'kaibot.market_notice'::regclass;               -- true

create table if not exists kaibot.market_notice (
  id             text primary key check (length(id) between 3 and 120),
  kind           text not null check (kind in ('new_listing', 'big_day')),
  flag           text not null check (flag in ('ipo', 'direct_listing', 'fed_day', 'cpi_day', 'megacap_report',
                                               'index_reshuffle', 'mega_listing')),
  tier           text not null check (tier in ('major', 'notice')),
  session        date not null,
  symbol         text check (symbol is null or symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
  title          text not null check (length(title) between 1 and 200),
  starts         timestamptz,
  first_eligible date,
  detail         jsonb not null default '{}'::jsonb
                 check (jsonb_typeof(detail) = 'object' and length(detail::text) <= 8000),
  available_ts   timestamptz not null,
  withdrawn      boolean not null default false,
  updated_at     timestamptz not null default now()
);
alter table kaibot.market_notice enable row level security;  -- no policies: only service_role
create index if not exists market_notice_session on kaibot.market_notice (session);
revoke all on kaibot.market_notice from public, anon, authenticated;
grant select, insert, update on kaibot.market_notice to service_role;

create or replace function public.kb_market_notices(p_today date default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_today date := coalesce(p_today, (now() at time zone 'America/New_York')::date);
  v_ahead date[];
  v_back date[];
  v_from date;
  v_to date;
  v_out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  -- today (when it is a session) and the next sessions, six in all; and the four sessions before today
  select array_agg(d order by d) into v_ahead from (
    select g::date as d from generate_series(v_today::timestamp, (v_today + 21)::timestamp, interval '1 day') g
    where extract(isodow from g) < 6 and not exists (
      select 1 from kaibot.market_calendar_exception x where x.day = g::date and x.close_et is null)
    order by 1 limit 6) s;
  select array_agg(d order by d) into v_back from (
    select g::date as d from generate_series((v_today - 1)::timestamp, (v_today - 21)::timestamp, interval '-1 day') g
    where extract(isodow from g) < 6 and not exists (
      select 1 from kaibot.market_calendar_exception x where x.day = g::date and x.close_et is null)
    order by 1 desc limit 4) s;
  v_from := coalesce(v_back[1], v_today);
  v_to := v_ahead[array_length(v_ahead, 1)];

  with stored as (
    select n.kind, n.flag, n.tier, n.session, n.symbol, n.title, n.starts, n.first_eligible, n.detail,
           'publisher'::text as source, 2 as pref
    from kaibot.market_notice n
    where not n.withdrawn and n.available_ts <= now() and n.session between v_from and v_to
  ),
  alarm as (  -- the engine's latest market_alarm event of each session in the window
    select distinct on (e.payload->>'session') e.payload as p, (e.payload->>'session')::date as session
    from kaibot.event e
    where e.type = 'market_alarm' and e.ts >= v_from::timestamptz - interval '10 days'
      and coalesce(e.payload->>'session', '') ~ '^\d{4}-\d{2}-\d{2}$'
      and (e.payload->>'session')::date between v_from and v_to
    order by e.payload->>'session', e.ts desc
  ),
  flags as (
    select a.session, f.key as flag, a.p
    from alarm a
    cross join lateral jsonb_each(case when jsonb_typeof(a.p->'flags') = 'object' then a.p->'flags'
                                       else '{}'::jsonb end) f
    where f.value = 'true'::jsonb
      and f.key in ('fed_day', 'cpi_day', 'megacap_report', 'index_reshuffle', 'mega_listing')
  ),
  engine as (
    select 'big_day'::text as kind, f.flag,
      case when f.flag in ('fed_day', 'mega_listing') then 'major'
           when f.flag = 'megacap_report' and jsonb_typeof(f.p->'n_megacaps') = 'number'
                and (f.p->>'n_megacaps')::numeric >= coalesce(case when jsonb_typeof(f.p->'megacap_major') = 'number'
                                                                   then (f.p->>'megacap_major')::numeric end, 3)
             then 'major'
           else 'notice' end as tier,
      f.session,
      case when f.flag = 'mega_listing' and jsonb_typeof(f.p->'details'->'mega_listing') = 'array'
           then nullif(split_part(coalesce(f.p->'details'->'mega_listing'->>0, ''), ' ', 1), '') end as symbol,
      case f.flag when 'fed_day' then 'The Fed announces its interest-rate decision'
                  when 'cpi_day' then 'The US inflation report comes out'
                  when 'megacap_report' then 'Some of the largest companies report their results'
                  when 'index_reshuffle' then 'Index funds rebalance at the close'
                  else 'A very large company starts trading' end as title,
      case f.flag when 'fed_day' then (f.session + time '14:00') at time zone 'America/New_York'
                  when 'cpi_day' then (f.session + time '08:30') at time zone 'America/New_York'
                  when 'index_reshuffle' then (f.session + coalesce(
                    (select x.close_et from kaibot.market_calendar_exception x where x.day = f.session),
                    time '16:00')) at time zone 'America/New_York'
                  end as starts,
      null::date as first_eligible,
      jsonb_strip_nulls(jsonb_build_object(
        'index', case when f.flag = 'index_reshuffle' then f.p->'details'->'index' end,
        'megacaps', case when f.flag = 'megacap_report' then f.p->'details'->'megacaps_reporting' end,
        'what', case when f.flag = 'mega_listing' then f.p->'details'->'mega_listing' end)) as detail,
      'engine'::text as source, 1 as pref
    from flags f
  ),
  merged as (
    select distinct on (kind, flag, session, coalesce(symbol, '')) *
    from (select * from engine union all select * from stored) u
    order by kind, flag, session, coalesce(symbol, ''), pref
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'kind', m.kind, 'flag', m.flag, 'tier', m.tier, 'session', m.session, 'symbol', m.symbol, 'title', m.title,
      'starts', m.starts, 'first_eligible', m.first_eligible, 'detail', m.detail, 'source', m.source)
      order by m.session, (m.tier = 'major') desc, m.starts nulls last, m.kind, m.flag), '[]'::jsonb)
    into v_out
  from (select * from merged order by session limit 40) m;

  return jsonb_build_object('today', v_today, 'from', v_from, 'to', v_to, 'sessions', to_jsonb(v_ahead),
                            'notices', v_out);
end $$;

revoke all on function public.kb_market_notices(date) from public, anon;
grant execute on function public.kb_market_notices(date) to authenticated;

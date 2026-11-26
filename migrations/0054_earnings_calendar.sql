-- 0054: upcoming earnings of the held stocks, for the dashboard (#797 follow-up; display only). After 0018
-- (kaibot.ledger_position) and 0002 (kaibot.is_owner); independent of 0053 (numbered after it only). Status (applied
-- or pending): docs/ops/migrations_pending.md.
--
-- Why. The stakeholder asked for an "Upcoming earnings" card on Home for the stocks the paper run holds. Alpha
-- Vantage's EARNINGS_CALENDAR (free tier, one request a day for every US symbol, scripts/fetch_av_earnings.py) gives
-- the next report dates; the script keeps only the engine universe's symbols and writes them here.
--
-- DISPLAY ONLY. The engine never reads this table or calls these functions: a pre-earnings filter may use a
-- calendar only after its pre-registered test (decision 20, section 3) passes gate v3.1 and the stakeholder approves.
-- Licence: Alpha Vantage's free key is personal and non-commercial, so the read is owner-only (as the Benzinga rows of
-- 0043): flag before any multi-user exposure.
--
-- What it adds.
-- 1) kaibot.earnings_calendar (symbol, fiscal_date_ending) primary key; report_date, report_time (pre-market,
--    post-market or null: the calendar may not say), estimate (EPS, may be null), currency, source, fetched_at.
--    RLS on: select for the owner only; all for service_role. No table privilege for anon or authenticated.
-- 2) kaibot.earnings_calendar_refresh(p_rows jsonb, p_fetched_at timestamptz): service_role only. Upserts the rows by
--    (symbol, fiscal_date_ending), then deletes every row whose report_date is before today (New York). Returns
--    {upserted, pruned, rows}. Rows that fail the checks raise (nothing is half-written: one statement each, one call).
-- 3) public.kb_upcoming_earnings(p_days int default 30): the owner only. The reports within the next p_days days
--    (1..90) of the stocks the active paper run holds at its newest ledger session (the rows 0053 reads), soonest
--    first: {as_of, today, fetched_at (newest), source, items: [{symbol, report_date, days_until, report_time,
--    fiscal_date_ending, estimate, currency}]}.
--
-- Additive: create table / index / function / policy only when missing, revoke and grant. The refresh function
-- deletes only rows of its own table whose report date has passed. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select has_function_privilege('anon', 'public.kb_upcoming_earnings(integer)', 'execute');                  -- false
--   select has_function_privilege('authenticated', 'kaibot.earnings_calendar_refresh(jsonb, timestamptz)', 'execute');  -- false
--   select public.kb_upcoming_earnings(30)->'items';   -- as the owner: [] until the first refresh

create table if not exists kaibot.earnings_calendar (
  symbol             text not null check (symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
  fiscal_date_ending date not null,
  report_date        date not null,
  report_time        text check (report_time in ('pre-market', 'post-market')),
  estimate           numeric(14,4),
  currency           text check (currency is null or currency ~ '^[A-Z]{3}$'),
  source             text not null default 'alphavantage' check (source = 'alphavantage'),
  fetched_at         timestamptz not null,
  primary key (symbol, fiscal_date_ending)
);
create index if not exists earnings_calendar_report_date on kaibot.earnings_calendar (report_date);
alter table kaibot.earnings_calendar enable row level security;
revoke all on kaibot.earnings_calendar from public, anon, authenticated;
grant select, insert, update, delete on kaibot.earnings_calendar to service_role;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname = 'kaibot' and tablename = 'earnings_calendar'
                 and policyname = 'owner reads earnings calendar') then
    create policy "owner reads earnings calendar" on kaibot.earnings_calendar for select to authenticated
      using ((select kaibot.is_owner()));
  end if;
  if not exists (select 1 from pg_policies where schemaname = 'kaibot' and tablename = 'earnings_calendar'
                 and policyname = 'runner writes earnings calendar') then
    create policy "runner writes earnings calendar" on kaibot.earnings_calendar for all to service_role
      using (true) with check (true);
  end if;
end $$;

create or replace function kaibot.earnings_calendar_refresh(p_rows jsonb, p_fetched_at timestamptz default now())
returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  today date := (now() at time zone 'America/New_York')::date;
  v_up int := 0;
  v_pruned int := 0;
begin
  if jsonb_typeof(coalesce(p_rows, '[]'::jsonb)) <> 'array' then
    raise exception 'p_rows must be a JSON array' using errcode = '22023';
  end if;
  insert into kaibot.earnings_calendar as c
         (symbol, fiscal_date_ending, report_date, report_time, estimate, currency, fetched_at)
  select upper(btrim(r->>'symbol')), (r->>'fiscal_date_ending')::date, (r->>'report_date')::date,
         nullif(r->>'report_time', ''), nullif(r->>'estimate', '')::numeric, nullif(r->>'currency', ''),
         coalesce(p_fetched_at, now())
    from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) r
  on conflict (symbol, fiscal_date_ending) do update
     set report_date = excluded.report_date, report_time = excluded.report_time, estimate = excluded.estimate,
         currency = excluded.currency, fetched_at = excluded.fetched_at;
  get diagnostics v_up = row_count;
  delete from kaibot.earnings_calendar c where c.report_date < today;
  get diagnostics v_pruned = row_count;
  return jsonb_build_object('upserted', v_up, 'pruned', v_pruned,
                            'rows', (select count(*) from kaibot.earnings_calendar));
end $$;
revoke all on function kaibot.earnings_calendar_refresh(jsonb, timestamptz) from public, anon, authenticated;
grant execute on function kaibot.earnings_calendar_refresh(jsonb, timestamptz) to service_role;

create or replace function public.kb_upcoming_earnings(p_days int default 30) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  today date := (now() at time zone 'America/New_York')::date;
  v_days int := greatest(1, least(coalesce(p_days, 30), 90));
  v_run bigint;
  v_session date;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select r.id into v_run from kaibot.run r
   where r.kind = 'paper' and r.status in ('running', 'paused') order by r.id desc limit 1;
  if v_run is not null then
    select max(p.session) into v_session from kaibot.ledger_position p where p.run_id = v_run;
  end if;
  return jsonb_build_object(
    'as_of', now(), 'today', today, 'source', 'alphavantage',
    'fetched_at', (select max(c.fetched_at) from kaibot.earnings_calendar c),
    'items', coalesce((
      select jsonb_agg(jsonb_build_object('symbol', c.symbol, 'report_date', c.report_date,
                                          'days_until', c.report_date - today, 'report_time', c.report_time,
                                          'fiscal_date_ending', c.fiscal_date_ending, 'estimate', c.estimate,
                                          'currency', c.currency)
                       order by c.report_date, c.symbol)
        from kaibot.earnings_calendar c
       where c.report_date >= today and c.report_date <= today + v_days
         and c.symbol in (select p.symbol from kaibot.ledger_position p
                           where p.run_id = v_run and p.session = v_session and p.qty <> 0)), '[]'::jsonb));
end $$;
revoke all on function public.kb_upcoming_earnings(int) from public, anon;
grant execute on function public.kb_upcoming_earnings(int) to authenticated;

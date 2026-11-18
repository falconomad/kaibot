-- 0026 (#319; Product, 18 Nov 2026): ONE $150 lifetime cap across every AI dollar on the Anthropic credit: the
-- engine's LLM calls (kaibot.budget_day, plus open kaibot.llm_call reservations) and the dashboard's stock summaries
-- (kaibot.summary_call, 0023, settled calls at their cost and open ones at their reservation). After 0023, 0024 and 0025.
--
-- Additive: three new functions (kaibot.ai_lifetime_spent, kaibot.ai_spend, kaibot.llm_reserve_lifetime), and
-- public.kb_summary_reserve, kb_summary_spend and kb_llm_budget re-created with the combined total. No table,
-- column or row changes; no delete, drop or truncate. Any time, also while the engine runs: an engine without the
-- #319 code keeps calling kaibot.llm_reserve (unchanged); with it, the engine reads kaibot.ai_spend and reserves
-- through kaibot.llm_reserve_lifetime (and falls back to the two tables while this migration is not applied).
--
-- One lock for both sides: today's kaibot.budget_day row, which kaibot.llm_reserve already locks (0008). Both
-- reservation paths take it before they read the total, so an engine call and a summary call cannot both fit the
-- last dollars. Lock order: kb_summary_reserve takes its advisory lock, then the row; the engine takes only the row.
--
-- Check after applying (read-only):
--   select kaibot.ai_spend();                    -- service role: {"llm": .., "summaries": .., "total": ..}
--   select public.kb_summary_spend();            -- as the owner: also "lifetime_usd" and "lifetime_cap": 150
--   select has_function_privilege('authenticated', 'kaibot.llm_reserve_lifetime(text, text, numeric, numeric, numeric, numeric, integer)', 'execute');  -- false

-- Every AI dollar so far: the LLM ledger, the summaries, and the engine's reservations still open (TTL p_ttl_s).
create or replace function kaibot.ai_lifetime_spent(p_ttl_s integer default 300) returns numeric
language sql stable security definer set search_path = '' as $$
  select coalesce((select sum(spent_usd) from kaibot.budget_day), 0)
       + coalesce((select sum(coalesce(actual_usd, reserved_usd)) from kaibot.summary_call), 0)
       + coalesce((select sum(reserved_usd) from kaibot.llm_call
                   where settled_at is null and ts > now() - make_interval(secs => p_ttl_s)), 0);
$$;

-- The engine's read (agents/gateway.Gateway.lifetime): the parts, so it can also cut this month's cap.
create or replace function kaibot.ai_spend() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'llm', coalesce((select sum(spent_usd) from kaibot.budget_day), 0),
    'llm_before_month', coalesce((select sum(spent_usd) from kaibot.budget_day
                                  where day < date_trunc('month', now() at time zone 'America/New_York')::date), 0),
    'summaries', coalesce((select sum(coalesce(actual_usd, reserved_usd)) from kaibot.summary_call), 0),
    'llm_open', coalesce((select sum(reserved_usd) from kaibot.llm_call
                          where settled_at is null and ts > now() - interval '300 seconds'), 0),
    'total', kaibot.ai_lifetime_spent(300),
    'cap', 150.0);
$$;

-- kaibot.llm_reserve (0008) with the lifetime cap checked inside the same row lock. The cap is the smaller of the
-- engine's (config/llm.yaml, itself at most $150) and the $150 written here: tighten-only. NULL: refused.
create or replace function kaibot.llm_reserve_lifetime(p_model text, p_purpose text, p_reserved numeric,
    p_daily_soft numeric, p_monthly_hard numeric, p_lifetime_hard numeric, p_ttl_s integer default 300)
returns bigint
language plpgsql security definer set search_path = kaibot, pg_temp as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  cap numeric := least(greatest(coalesce(p_lifetime_hard, 0), 0), 150.0);
begin
  insert into kaibot.budget_day (day) values (d) on conflict (day) do nothing;
  perform 1 from kaibot.budget_day where day = d for update;           -- the lock llm_reserve and the summaries take
  if kaibot.ai_lifetime_spent(p_ttl_s) + coalesce(p_reserved, 0) > cap then
    update kaibot.budget_day set state = 'no_entries' where day = d;
    return null;
  end if;
  return kaibot.llm_reserve(p_model, p_purpose, p_reserved, p_daily_soft, p_monthly_hard, p_ttl_s);
end $$;

-- 0023's kb_summary_reserve with the combined lifetime cap ("lifetime_cap"); every other check as before.
create or replace function public.kb_summary_reserve(p_symbol text, p_model text, p_reserved numeric) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
  day_cap constant numeric := 0.30;
  month_cap constant numeric := 5.00;
  lifetime_cap constant numeric := 150.0;
  new_id bigint;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then raise exception 'not a symbol' using errcode = '22023'; end if;
  if p_reserved is null or p_reserved <= 0 or p_reserved > 0.02 then
    return jsonb_build_object('ok', false, 'reason', 'too_large'); end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.summary_budget'));   -- serialises every summary reservation
  insert into kaibot.budget_day (day) values (d) on conflict (day) do nothing;
  perform 1 from kaibot.budget_day where day = d for update;          -- and against the engine's (#319)
  if exists (select 1 from kaibot.stock_summary where symbol = s and day = d) then
    return jsonb_build_object('ok', false, 'reason', 'cached'); end if;
  if exists (select 1 from kaibot.summary_call where symbol = s and day = d and settled_at is null
             and ts > now() - interval '2 minutes') then
    return jsonb_build_object('ok', false, 'reason', 'busy'); end if;
  if (select count(*) from kaibot.summary_call where symbol = s and day = d) >= 2 then
    return jsonb_build_object('ok', false, 'reason', 'symbol_limit'); end if;
  if kaibot.ai_lifetime_spent(300) + p_reserved > lifetime_cap then
    return jsonb_build_object('ok', false, 'reason', 'lifetime_cap'); end if;
  if kaibot.summary_spent(m0) + p_reserved > month_cap then
    return jsonb_build_object('ok', false, 'reason', 'month_cap'); end if;
  if kaibot.summary_spent(d) + p_reserved > day_cap then
    return jsonb_build_object('ok', false, 'reason', 'day_cap'); end if;
  insert into kaibot.summary_call (day, symbol, model, reserved_usd)
    values (d, s, left(coalesce(p_model, ''), 60), p_reserved) returning id into new_id;
  return jsonb_build_object('ok', true, 'call_id', new_id);
end $$;

-- 0023's kb_summary_spend, plus the combined lifetime total and its cap.
create or replace function public.kb_summary_spend() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object('day', d, 'day_usd', kaibot.summary_spent(d), 'month_usd', kaibot.summary_spent(m0),
    'day_cap', 0.30, 'month_cap', 5.00,
    'calls_today', (select count(*) from kaibot.summary_call where day = d),
    'cards_today', (select count(*) from kaibot.stock_summary where day = d),
    'lifetime_usd', round(kaibot.ai_lifetime_spent(300), 6), 'lifetime_cap', 150.0);
end $$;

-- 0025's kb_llm_budget, with the lifetime total across both ledgers (and its two parts).
create or replace function public.kb_llm_budget() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
  before numeric;
  summ numeric;
  life numeric;
  monthly numeric;
  b jsonb;
  stored text;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select coalesce(sum(x.spent_usd), 0) into before from kaibot.budget_day x where x.day < m0;
  select coalesce(sum(coalesce(x.actual_usd, x.reserved_usd)), 0) into summ from kaibot.summary_call x;
  life := kaibot.ai_lifetime_spent(300);
  monthly := greatest(0, least(25.0, 150.0 - before - summ));
  b := kaibot.llm_budget_state(1.0, monthly, 20.0);
  select x.state into stored from kaibot.budget_day x where x.day = d;
  return jsonb_build_object(
    'day', d,
    'day_usd', round((b->>'day')::numeric, 4),
    'month_usd', round((b->>'month')::numeric, 4),
    'lifetime_usd', round(life, 4),
    'lifetime_summaries_usd', round(summ, 4),
    'state', case when 'no_entries' in (b->>'state', stored) or life >= 150.0 then 'no_entries'
                  when 'rules_only' in (b->>'state', stored) then 'rules_only' else 'ok' end,
    'alert', (b->>'alert')::boolean,
    'caps', jsonb_build_object('daily_soft', 1.0, 'monthly_alert', 20.0, 'monthly_hard', 25.0, 'lifetime_hard', 150.0),
    'calls_today', (select count(*) from kaibot.llm_call c
      where c.ts >= d::timestamp at time zone 'America/New_York'),
    'open_reserved_usd', round((select coalesce(sum(c.reserved_usd), 0) from kaibot.llm_call c
      where c.settled_at is null and c.ts > now() - interval '5 minutes')::numeric, 4),
    'by_purpose', coalesce((select jsonb_object_agg(p.purpose, p.usd) from (
        select c.purpose, round(sum(coalesce(c.actual_usd, 0))::numeric, 4) as usd from kaibot.llm_call c
        where c.ts >= d::timestamp at time zone 'America/New_York' group by c.purpose) p), '{}'::jsonb)
  );
end $$;

revoke all on function kaibot.ai_lifetime_spent(integer), kaibot.ai_spend(),
  kaibot.llm_reserve_lifetime(text, text, numeric, numeric, numeric, numeric, integer)
  from public, anon, authenticated;
grant execute on function kaibot.ai_lifetime_spent(integer), kaibot.ai_spend(),
  kaibot.llm_reserve_lifetime(text, text, numeric, numeric, numeric, numeric, integer) to service_role;
revoke all on function public.kb_summary_reserve(text, text, numeric), public.kb_summary_spend(),
  public.kb_llm_budget() from public, anon;
grant execute on function public.kb_summary_reserve(text, text, numeric), public.kb_summary_spend(),
  public.kb_llm_budget() to authenticated;

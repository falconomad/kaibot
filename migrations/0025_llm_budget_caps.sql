-- 0025 (#309, Stage 1: the stakeholder's $200 of API credit): the dashboard's LLM budget shows the new caps.
-- Dashboard-only: the engine does not need this migration. agents/gateway.Gateway already holds the caps itself
-- ($1/day, $20/month alert, $25/month hard, $150 in all) through the existing kaibot.llm_reserve: it hands the
-- function the monthly cap min($25, $150 - the spend of the months before this one). kaibot.budget_day keeps one
-- row per ET day and is never pruned, so its sum is the lifetime total: no new table or column.
-- This replaces public.kb_llm_budget (0009), whose caps were written in ($3 / $150 / $190), and adds the lifetime
-- total and its cap. The fields the dashboard reads today keep their names and types.

create or replace function public.kb_llm_budget() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
  before numeric;
  life numeric;
  monthly numeric;
  b jsonb;
  stored text;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select coalesce(sum(x.spent_usd), 0) into before from kaibot.budget_day x where x.day < m0;
  select coalesce(sum(x.spent_usd), 0) into life from kaibot.budget_day x;
  monthly := greatest(0, least(25.0, 150.0 - before));
  b := kaibot.llm_budget_state(1.0, monthly, 20.0);
  select x.state into stored from kaibot.budget_day x where x.day = d;
  return jsonb_build_object(
    'day', d,
    'day_usd', round((b->>'day')::numeric, 4),
    'month_usd', round((b->>'month')::numeric, 4),
    'lifetime_usd', round(life, 4),
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

revoke all on function public.kb_llm_budget() from public, anon;
grant execute on function public.kb_llm_budget() to authenticated;

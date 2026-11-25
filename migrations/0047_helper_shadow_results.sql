-- 0047: the shadow rules' results for the helper, read-only (issue #371; the helpers' key path of 0038, #451).
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. The monthly shadow-results report (scripts/shadow_results.sh) needs DATABASE_URL, the database password,
-- which can write anything. The research runner on the Mac must run that report without any write-capable key
-- (Product's decision on #371). scripts/shadow_results.py signs in as the helper of 0038 (publishable key + the
-- helper user, docs/ops/helper_role.md) and calls this one function instead.
--
-- What changes. One function, public.kb_helper_shadow_results(), security definer with an empty search_path, STABLE
-- (so the server refuses any INSERT/UPDATE/DELETE inside it: "not allowed in a non-volatile function"). It runs the
-- four SELECTs of scripts/shadow_results.sql, copied byte for byte between the `>>> Qn` / `<<< Qn` markers
-- (tests/test_helper_shadow_results_sql.py checks the copy against the file), and returns
--   {"q1": [row, ...], "q2": [...], "q3": [...], "q4": [...]}
-- where each row is a json object whose keys are the query's columns in their order and whose values are the
-- columns' text (null stays null), i.e. what psql prints. It checks kaibot.is_helper() first: only the helper's
-- sign-in gets an answer. EXECUTE is revoked from public, anon and service_role and granted to authenticated only
-- (the helper is the authenticated role; any other signed-in user is refused by the is_helper() check).
--
-- In schema public like 0038's kb_helper_* functions (PostgREST's RPC schema; the `authenticated` role has no USAGE
-- on schema kaibot, and this migration does not give it one). No table, schema or policy grant changes.
--
-- Additive: one `create or replace function`, one revoke, one grant. No insert, update, delete, drop or truncate.
-- No engine object changes. Any time, also while the engine runs. Until it is applied, scripts/shadow_results.py
-- says that the function is missing and exits; scripts/shadow_results.sh works as before.
--
-- Check after applying (read-only):
--   select has_function_privilege('anon', 'public.kb_helper_shadow_results()', 'execute');           -- false
--   select has_function_privilege('service_role', 'public.kb_helper_shadow_results()', 'execute');   -- false
--   select has_function_privilege('authenticated', 'public.kb_helper_shadow_results()', 'execute');  -- true
--   select provolatile, prosecdef from pg_proc where proname = 'kb_helper_shadow_results';          -- s | t
--   select has_schema_privilege('authenticated', 'kaibot', 'usage');                               -- false

create or replace function public.kb_helper_shadow_results() returns json
language plpgsql stable security definer set search_path = '' as $$
declare q1 json; q2 json; q3 json; q4 json;
begin
  if not kaibot.is_helper() then raise exception 'not authorised' using errcode = '42501'; end if;
  select coalesce(json_agg((select json_object_agg(c.key, c.value order by c.n)
                            from json_each_text(to_json(x)) with ordinality c(key, value, n))), '[]'::json)
    into q1 from (
-- >>> Q1 (scripts/shadow_results.sql, byte for byte; the trailing semicolon dropped)
with ends as (            -- one summary per session; if the engine restarted, the latest one wins
  select distinct on ((payload->>'day')::date)
         (payload->>'day')::date as day, id, run_id, payload
  from kaibot.event
  where type = 'shadow_session_end'
  order by (payload->>'day')::date, id desc
),
per_rule as (
  select e.day, e.run_id, r.amount_usd, s.rule,
         coalesce((e.payload->s.rule->>'signals')::int, 0)     as signals,
         coalesce((e.payload->s.rule->>'sized')::int, 0)       as sized,
         coalesce((e.payload->s.rule->>'pnl_usd')::numeric, 0) as pnl_usd,
         (e.payload->s.rule->>'avg_r')::numeric                as avg_r
  from ends e
  left join kaibot.run r on r.id = e.run_id
  cross join (values ('news_continuation'), ('orb_stocks_in_play')) as s(rule)
  where e.payload ? s.rule
)
select rule,
       count(*)                                             as sessions,
       min(day)                                             as first_day,
       max(day)                                             as last_day,
       sum(signals)                                         as signals,
       sum(sized)                                           as sized_trades,
       count(*) filter (where sized > 0)                    as sessions_with_a_trade,
       round(sum(sized)::numeric / count(*), 2)             as trades_per_session,
       round(sum(avg_r * sized) / nullif(sum(sized), 0), 3) as mean_r_per_trade,
       count(*) filter (where pnl_usd > 0)                  as up_sessions,
       count(*) filter (where pnl_usd < 0)                  as down_sessions,
       round(avg(10000 * pnl_usd / nullif(amount_usd, 0)), 2)         as mean_daily_bp_of_pot,
       round(stddev_samp(10000 * pnl_usd / nullif(amount_usd, 0)), 2) as sd_daily_bp_of_pot,
       round(avg(pnl_usd / nullif(amount_usd, 0))
             / nullif(stddev_samp(pnl_usd / nullif(amount_usd, 0)), 0)
             * sqrt(count(*))::numeric, 2)                  as t_daily,
       case when sum(sized) > 0
            then ceil(900.0 * count(*) / sum(sized)) end    as sessions_for_900_trades_at_this_rate
from per_rule
group by rule
order by rule
-- <<< Q1
  ) x;
  select coalesce(json_agg((select json_object_agg(c.key, c.value order by c.n)
                            from json_each_text(to_json(x)) with ordinality c(key, value, n))), '[]'::json)
    into q2 from (
-- >>> Q2 (scripts/shadow_results.sql, byte for byte; the trailing semicolon dropped)
with ends as (
  select distinct on ((payload->>'day')::date)
         (payload->>'day')::date as day, id, ts, run_id, payload
  from kaibot.event
  where type = 'shadow_session_end'
  order by (payload->>'day')::date, id desc
)
select e.day, e.run_id, r.amount_usd as pot_usd, s.rule,
       (e.payload->s.rule->>'signals')::int     as signals,
       (e.payload->s.rule->>'sized')::int       as sized,
       (e.payload->s.rule->>'pnl_usd')::numeric as pnl_usd,
       (e.payload->s.rule->>'avg_r')::numeric   as avg_r,
       (e.payload->'cost_ms'->>'symbols')::int  as symbols_watched
from ends e
left join kaibot.run r on r.id = e.run_id
cross join (values ('news_continuation'), ('orb_stocks_in_play')) as s(rule)
where e.payload ? s.rule
order by e.day, s.rule
-- <<< Q2
  ) x;
  select coalesce(json_agg((select json_object_agg(c.key, c.value order by c.n)
                            from json_each_text(to_json(x)) with ordinality c(key, value, n))), '[]'::json)
    into q3 from (
-- >>> Q3 (scripts/shadow_results.sql, byte for byte; the trailing semicolon dropped)
with sig as (             -- one signal per rule, symbol and session; after a restart the latest row wins
  select distinct on (strategy, symbol, (ts at time zone 'America/New_York')::date)
         strategy as rule, symbol, (ts at time zone 'America/New_York')::date as day, features
  from kaibot.signal
  where mode = 'shadow'
    and strategy in ('news_continuation', 'orb_stocks_in_play')
  order by strategy, symbol, (ts at time zone 'America/New_York')::date, id desc
),
t as (
  select rule, symbol, day,
         (features->'counterfactual'->>'r_multiple')::numeric as r,
         features->'counterfactual'->>'reason'               as exit_reason
  from sig
  where jsonb_typeof(features->'counterfactual') = 'object'
    and features->'counterfactual'->>'r_multiple' is not null
),
ranked as (
  select t.*, row_number() over (partition by rule order by r desc) as best_rank
  from t
)
select rule,
       count(*)                                             as trades,
       count(distinct day)                                  as sessions_with_a_trade,
       count(distinct symbol)                               as symbols,
       round(avg(r), 3)                                     as mean_r,
       round(stddev_samp(r), 3)                             as sd_r,
       round(avg(r) / nullif(stddev_samp(r), 0) * sqrt(count(*))::numeric, 2) as t_per_trade,
       round(100.0 * count(*) filter (where r > 0) / count(*), 1)             as win_pct,
       count(*) filter (where exit_reason = 'stop')         as stopped_out,
       count(*) filter (where exit_reason = 'time')         as held_to_1555,
       round(max(r), 2)                                     as best_r,
       round(min(r), 2)                                     as worst_r,
       round(sum(r), 2)                                     as total_r,
       round(coalesce(sum(r) filter (where best_rank > 5), 0), 2) as total_r_without_best_5,
       case when avg(r) <> 0 and count(*) > 1
            then ceil(power(3 * stddev_samp(r) / abs(avg(r)), 2)) end as trades_for_t3_if_this_mean_were_true
from ranked
group by rule
order by rule
-- <<< Q3
  ) x;
  select coalesce(json_agg((select json_object_agg(c.key, c.value order by c.n)
                            from json_each_text(to_json(x)) with ordinality c(key, value, n))), '[]'::json)
    into q4 from (
-- >>> Q4 (scripts/shadow_results.sql, byte for byte; the trailing semicolon dropped)
with started as (
  select distinct (ts at time zone 'America/New_York')::date as day
  from kaibot.event where type = 'session_start'
),
ended as (
  select distinct (payload->>'day')::date as day
  from kaibot.event where type = 'shadow_session_end'
)
select 'session without a shadow summary' as finding, s.day, null::text as detail
from started s left join ended e using (day)
where e.day is null
union all
select 'shadow error', (ts at time zone 'America/New_York')::date,
       (payload->>'where') || ': ' || (payload->>'error')
from kaibot.event
where type = 'shadow_error'
order by 2, 1
-- <<< Q4
  ) x;
  return json_build_object('q1', q1, 'q2', q2, 'q3', q3, 'q4', q4);
end $$;

revoke all on function public.kb_helper_shadow_results() from public, anon, authenticated, service_role;
grant execute on function public.kb_helper_shadow_results() to authenticated;

-- S97 (#371): the shadow rules' results to date. Four plain SELECTs: nothing is written.
-- Source of truth: docs/research/event_driven/design.md, section 5 (tests/test_shadow_results_sql.py keeps this
-- file and that section the same, and runs every query on a scratch Postgres with rows shaped like engine/shadow.py's).
--
-- Run (read-only on the server side as well: the session refuses any write):
--   ./scripts/shadow_results.sh                      # DATABASE_URL = the Supabase Postgres connection string
-- or paste one query at a time into the Supabase SQL editor.
--
-- How to read the output: design.md section 5, "How many sessions before the numbers mean anything", and the
-- table in issue #371. Below 60 sessions or 50 sized trades a rule, the numbers show the plumbing works, nothing more.

-- Q1. One row per rule: sessions, would-be trades, mean R, daily result
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
order by rule;

-- Q2. The same data day by day (gaps, restarts, the change of pot between runs)
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
order by e.day, s.rule;

-- Q3. Per would-be trade (the exact R of each trade, not the daily average)
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
order by rule;

-- Q4. Health of the record: sessions without a shadow summary, and shadow errors
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
order by 2, 1;

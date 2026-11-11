-- 0008: M3 agent room (stories S32-S36). Engine-side only (service role); the dashboard reads via kb_* later.

-- S32: atomic budget reservations. kaibot.llm_reserve locks today's budget_day row, so concurrent calls from
-- any process can never overspend: reserved + spent must stay under the caps or the call is refused (NULL).
alter table kaibot.llm_call add column if not exists settled_at timestamptz;
alter table kaibot.llm_call add column if not exists error text;
alter table kaibot.llm_call add column if not exists cache_write_tokens integer;
create index if not exists llm_call_ts on kaibot.llm_call (ts desc);

create or replace function kaibot.llm_reserve(p_model text, p_purpose text, p_reserved numeric,
    p_daily_soft numeric, p_monthly_hard numeric, p_ttl_s integer default 300)
returns bigint
language plpgsql security definer set search_path = kaibot, pg_temp as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
  day_spent numeric; month_spent numeric; open_res numeric; new_id bigint;
begin
  if p_reserved is null or p_reserved <= 0 or p_reserved > 1 then
    raise exception 'reservation must be between 0 and $1';
  end if;
  insert into kaibot.budget_day (day) values (d) on conflict (day) do nothing;
  perform 1 from kaibot.budget_day where day = d for update;           -- serialises every reservation
  select coalesce(spent_usd, 0) into day_spent from kaibot.budget_day where day = d;
  select coalesce(sum(spent_usd), 0) into month_spent from kaibot.budget_day where day >= m0;
  select coalesce(sum(reserved_usd), 0) into open_res from kaibot.llm_call
    where settled_at is null and ts > now() - make_interval(secs => p_ttl_s);
  if day_spent + open_res + p_reserved > p_daily_soft or month_spent + open_res + p_reserved > p_monthly_hard then
    update kaibot.budget_day set state = case when month_spent + open_res + p_reserved > p_monthly_hard
                                              then 'no_entries' else 'rules_only' end where day = d;
    return null;
  end if;
  insert into kaibot.llm_call (model, purpose, reserved_usd) values (p_model, p_purpose, p_reserved) returning llm_call.id into new_id;
  return new_id;
end $$;

create or replace function kaibot.llm_settle(p_id bigint, p_actual numeric, p_in integer, p_out integer,
    p_cache_read integer default 0, p_cache_write integer default 0, p_error text default null)
returns void
language plpgsql security definer set search_path = kaibot, pg_temp as $$
declare d date := (now() at time zone 'America/New_York')::date;
begin
  update kaibot.llm_call set actual_usd = coalesce(p_actual, 0), input_tokens = p_in, output_tokens = p_out,
    cache_read_tokens = p_cache_read, cache_write_tokens = p_cache_write, error = p_error, settled_at = now()
    where id = p_id and settled_at is null;
  if found then
    insert into kaibot.budget_day (day, spent_usd) values (d, coalesce(p_actual, 0))
      on conflict (day) do update set spent_usd = kaibot.budget_day.spent_usd + excluded.spent_usd;
  end if;
end $$;

create or replace function kaibot.llm_budget_state(p_daily_soft numeric, p_monthly_hard numeric, p_monthly_alert numeric)
returns jsonb
language sql stable security definer set search_path = kaibot, pg_temp as $$
  select jsonb_build_object(
    'day', coalesce((select spent_usd from kaibot.budget_day where day = (now() at time zone 'America/New_York')::date), 0),
    'month', coalesce((select sum(spent_usd) from kaibot.budget_day
                       where day >= date_trunc('month', now() at time zone 'America/New_York')::date), 0),
    'state', case
      when coalesce((select sum(spent_usd) from kaibot.budget_day
                     where day >= date_trunc('month', now() at time zone 'America/New_York')::date), 0) >= p_monthly_hard then 'no_entries'
      when coalesce((select spent_usd from kaibot.budget_day
                     where day = (now() at time zone 'America/New_York')::date), 0) >= p_daily_soft then 'rules_only'
      else 'ok' end,
    'alert', coalesce((select sum(spent_usd) from kaibot.budget_day
                       where day >= date_trunc('month', now() at time zone 'America/New_York')::date), 0) >= p_monthly_alert)
$$;

revoke all on function kaibot.llm_reserve(text, text, numeric, numeric, numeric, integer),
  kaibot.llm_settle(bigint, numeric, integer, integer, integer, integer, text),
  kaibot.llm_budget_state(numeric, numeric, numeric) from public, anon, authenticated;
grant execute on function kaibot.llm_reserve(text, text, numeric, numeric, numeric, integer),
  kaibot.llm_settle(bigint, numeric, integer, integer, integer, integer, text),
  kaibot.llm_budget_state(numeric, numeric, numeric) to service_role;

-- S34/S36: one room session per dip candidate, plus the rules-only arm and the counterfactual for the A/B.
alter table kaibot.room_session add column if not exists ts timestamptz not null default now();
alter table kaibot.room_session add column if not exists status text not null default 'done'
  check (status in ('pending', 'done', 'timeout', 'invalid', 'skipped', 'error'));
alter table kaibot.room_session add column if not exists rules_only text check (rules_only in ('allow', 'veto'));
alter table kaibot.room_session add column if not exists evidence jsonb;       -- the pack: ids, sources, times
alter table kaibot.room_session add column if not exists error text;
alter table kaibot.room_session add column if not exists counterfactual jsonb; -- rules-only outcome at day end
create unique index if not exists room_session_signal on kaibot.room_session (signal_id);

-- S33: one triage per deduplicated cluster.
alter table kaibot.news_cluster add column if not exists triage_ts timestamptz;
alter table kaibot.news_cluster add column if not exists minhash bigint[];
alter table kaibot.news_cluster add column if not exists last_seen timestamptz;
create index if not exists news_cluster_first_seen on kaibot.news_cluster (first_seen desc);
create index if not exists news_cluster_tickers on kaibot.news_cluster using gin (tickers);
create unique index if not exists news_item_sha_source on kaibot.news_item (headline_sha1, source);

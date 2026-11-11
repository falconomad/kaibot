-- 0007: M2 dashboard reads (stories S26-S31). All read-only, owner-checked, security definer, no direct table access.
-- Sessions are US/Eastern trading dates: (ts at time zone 'America/New_York')::date.

-- Overview: + level segments (S23: what applies from which session), today's halts (S28 risk strip).
create or replace function public.kb_overview(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select jsonb_build_object(
    'run', (select to_jsonb(r) from kaibot.run r where r.id = p_run),
    'daily', coalesce((select jsonb_agg(jsonb_build_object(
        'session', d.session, 'pnl', d.pnl_reported, 'pnl_venue', d.pnl_venue, 'pnl_shadow', d.pnl_shadow,
        'spy_ret', d.spy_ret, 'cash_ret', d.cash_ret, 'trades', d.trades) order by d.session)
      from kaibot.daily_result d where d.run_id = p_run), '[]'::jsonb),
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h), '[]'::jsonb),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts, 'payload', c.payload))
      from kaibot.command c where c.applied_at is null), '[]'::jsonb),
    'ledger', (select to_jsonb(l) from kaibot.ledger l where l.run_id = p_run order by l.ts desc limit 1),
    'today', (select jsonb_build_object('trades', count(*), 'pnl', coalesce(sum(t.pnl_usd), 0))
      from kaibot.trade t where t.run_id = p_run
        and t.entry_ts >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York'),
    'segments', coalesce((select jsonb_agg(jsonb_build_object('level', s.level, 'from_session', s.from_session)
      order by s.from_session, s.id) from kaibot.segment s where s.run_id = p_run), '[]'::jsonb),
    'halts_today', coalesce((select jsonb_agg(jsonb_build_object('kind', h.kind, 'reason', h.reason, 'ts', h.ts)
      order by h.ts desc) from kaibot.halt h
      where (h.run_id = p_run or h.run_id is null)
        and h.ts >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York'), '[]'::jsonb)
  ) into out;
  return out;
end $$;

-- Evidence counter (S29): trades toward 900 and a 95% confidence interval for the mean R per trade.
create or replace function public.kb_evidence(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  with t as (
    select t.strategy, t.pnl_usd, t.pnl_usd / nullif(t.risk_usd, 0) as r
    from kaibot.trade t where t.run_id = p_run and t.exit_ts is not null and t.pnl_usd is not null
  ), agg as (
    select count(*) as n, coalesce(sum(pnl_usd), 0) as pnl, avg(r) as mean_r, stddev_samp(r) as sd_r from t
  )
  select jsonb_build_object(
    'n', a.n, 'pnl', round(a.pnl, 2), 'mean_r', round(a.mean_r, 4), 'sd_r', round(a.sd_r, 4),
    't', case when a.n > 1 and a.sd_r > 0 then round(a.mean_r / a.sd_r * sqrt(a.n::numeric), 3) end,
    'ci_lo', case when a.n > 1 then round(a.mean_r - 1.96 * a.sd_r / sqrt(a.n::numeric), 4) end,
    'ci_hi', case when a.n > 1 then round(a.mean_r + 1.96 * a.sd_r / sqrt(a.n::numeric), 4) end,
    'sessions', (select count(*) from kaibot.daily_result d where d.run_id = p_run),
    'first_session', (select min(d.session) from kaibot.daily_result d where d.run_id = p_run),
    'by_strategy', coalesce((select jsonb_agg(jsonb_build_object('strategy', s.strategy, 'n', s.n,
        'pnl', round(s.pnl, 2), 'mean_r', round(s.mean_r, 4)) order by s.n desc)
      from (select strategy, count(*) n, sum(pnl_usd) pnl, avg(r) mean_r from t group by strategy) s), '[]'::jsonb)
  ) into out from agg a;
  return out;
end $$;

-- Daily report card (S30): ready as soon as the engine finalises the session (~16:00 ET).
create or replace function public.kb_report(p_run bigint, p_session date default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare d date; lo timestamptz; hi timestamptz;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  d := coalesce(p_session, (select max(x.session) from kaibot.daily_result x where x.run_id = p_run));
  if d is null then
    return jsonb_build_object('session', null, 'sessions', '[]'::jsonb);
  end if;
  lo := d::timestamp at time zone 'America/New_York';
  hi := (d + 1)::timestamp at time zone 'America/New_York';
  return jsonb_build_object(
    'session', d,
    'sessions', coalesce((select jsonb_agg(x.session order by x.session desc)
      from kaibot.daily_result x where x.run_id = p_run), '[]'::jsonb),
    'day', (select jsonb_build_object('pnl', x.pnl_reported, 'pnl_venue', x.pnl_venue, 'pnl_shadow', x.pnl_shadow,
        'spy_ret', x.spy_ret, 'cash_ret', x.cash_ret, 'trades', x.trades)
      from kaibot.daily_result x where x.run_id = p_run and x.session = d),
    'start_equity', (select r.amount_usd + coalesce(sum(x.pnl_reported), 0) from kaibot.run r
      left join kaibot.daily_result x on x.run_id = r.id and x.session < d where r.id = p_run group by r.amount_usd),
    'trades', coalesce((select jsonb_agg(jsonb_build_object('id', t.id, 'strategy', t.strategy, 'symbol', t.symbol,
        'entry_ts', t.entry_ts, 'exit_ts', t.exit_ts, 'qty', t.qty, 'entry_px', t.entry_px, 'exit_px', t.exit_px,
        'pnl_usd', t.pnl_usd, 'risk_usd', t.risk_usd, 'exit_reason', t.exit_reason) order by t.entry_ts)
      from kaibot.trade t where t.run_id = p_run and t.entry_ts >= lo and t.entry_ts < hi), '[]'::jsonb),
    'signals', (select jsonb_build_object(
        'live', count(*) filter (where s.mode = 'live'),
        'approved', count(*) filter (where s.mode = 'live' and g.approved),
        'shadow', count(*) filter (where s.mode = 'shadow'))
      from kaibot.signal s left join kaibot.gate_run g on g.signal_id = s.id
      where s.run_id = p_run and s.ts >= lo and s.ts < hi),
    'vetoes', coalesce((select jsonb_agg(jsonb_build_object('reason', v.reason, 'n', v.n) order by v.n desc) from (
        select coalesce(s.rules_veto, 'gate: ' || coalesce(g.binding, 'qty 0')) as reason, count(*) as n
        from kaibot.signal s left join kaibot.gate_run g on g.signal_id = s.id
        where s.run_id = p_run and s.mode = 'live' and s.ts >= lo and s.ts < hi
          and (s.rules_veto is not null or not coalesce(g.approved, false))
        group by 1) v), '[]'::jsonb),
    'shadow', coalesce((select jsonb_agg(jsonb_build_object('strategy', s.strategy, 'symbol', s.symbol, 'ts', s.ts,
        'veto', s.rules_veto, 'pnl_usd', (s.features->'counterfactual'->>'pnl_usd')::numeric,
        'r', (s.features->'counterfactual'->>'r_multiple')::numeric) order by s.ts)
      from kaibot.signal s where s.run_id = p_run and s.mode = 'shadow' and s.ts >= lo and s.ts < hi), '[]'::jsonb),
    'halts', coalesce((select jsonb_agg(jsonb_build_object('kind', h.kind, 'reason', h.reason, 'ts', h.ts) order by h.ts)
      from kaibot.halt h where (h.run_id = p_run or h.run_id is null) and h.ts >= lo and h.ts < hi), '[]'::jsonb)
  );
end $$;

-- Weekly attribution (S31): where the P&L comes from, with automatic flags for losers.
-- flag 'kill' = >= 20 trades and t <= -2; 'watch' = >= 10 trades and mean R < 0. Flags inform; they never act.
create or replace function public.kb_attribution(p_run bigint, p_weeks int default 4) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare since timestamptz := now() - make_interval(days => 7 * least(greatest(coalesce(p_weeks, 4), 1), 520));
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return (
    with t as (
      select t.strategy, coalesce(t.exit_reason, 'open') as exit_reason, t.symbol, t.pnl_usd,
             t.pnl_usd / nullif(t.risk_usd, 0) as r,
             to_char(t.entry_ts at time zone 'America/New_York', 'HH24') || ':00' as hour_et,
             to_char(t.entry_ts at time zone 'America/New_York', 'Dy') as weekday,
             date_trunc('week', t.entry_ts at time zone 'America/New_York')::date as week
      from kaibot.trade t
      where t.run_id = p_run and t.exit_ts is not null and t.pnl_usd is not null and t.entry_ts >= since
    ), dims as (
      select 'strategy' as dim, strategy as key, pnl_usd, r from t
      union all select 'exit_reason', exit_reason, pnl_usd, r from t
      union all select 'hour_et', hour_et, pnl_usd, r from t
      union all select 'weekday', weekday, pnl_usd, r from t
      union all select 'symbol', symbol, pnl_usd, r from t
    ), g as (
      select dim, key, count(*) as n, sum(pnl_usd) as pnl, avg(r) as mean_r, stddev_samp(r) as sd_r,
             avg((pnl_usd > 0)::int) as win
      from dims group by dim, key
    )
    select jsonb_build_object(
      'since', since, 'weeks', least(greatest(coalesce(p_weeks, 4), 1), 520),
      'n', (select count(*) from t),
      'groups', coalesce((select jsonb_agg(jsonb_build_object('dim', g.dim, 'key', g.key, 'n', g.n,
          'pnl', round(g.pnl, 2), 'mean_r', round(g.mean_r, 3), 'win', round(g.win, 3),
          't', case when g.n > 1 and g.sd_r > 0 then round(g.mean_r / g.sd_r * sqrt(g.n::numeric), 2) end,
          'flag', case when g.n >= 20 and g.sd_r > 0 and g.mean_r / g.sd_r * sqrt(g.n::numeric) <= -2 then 'kill'
                       when g.n >= 10 and g.mean_r < 0 then 'watch' end)
        order by g.dim, g.pnl) from g where g.dim <> 'symbol' or g.n >= 3), '[]'::jsonb),
      'weekly', coalesce((select jsonb_agg(jsonb_build_object('week', w.week, 'strategy', w.strategy, 'n', w.n,
          'pnl', round(w.pnl, 2)) order by w.week, w.strategy)
        from (select week, strategy, count(*) n, sum(pnl_usd) pnl from t group by week, strategy) w), '[]'::jsonb)
    )
  );
end $$;

-- Tape (S27): every decision and fill, newest first, with reasons and costs.
create or replace function public.kb_tape(p_run bigint, p_limit int default 100, p_before timestamptz default null)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare lim int := least(greatest(coalesce(p_limit, 100), 1), 500);
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(x.item order by x.ts desc) from (
    select * from (
      select s.ts, jsonb_build_object('kind', 'signal', 'ts', s.ts, 'mode', s.mode, 'strategy', s.strategy,
          'symbol', s.symbol, 'entry', s.entry, 'stop', s.stop, 'target', s.target, 'reasons', to_jsonb(s.reasons),
          'veto', s.rules_veto, 'approved', g.approved, 'qty', g.qty, 'binding', g.binding,
          'trade_id', (select t.id from kaibot.trade t where t.signal_id = s.id limit 1),
          'counterfactual', s.features->'counterfactual') as item
      from kaibot.signal s left join kaibot.gate_run g on g.signal_id = s.id
      where s.run_id = p_run and (p_before is null or s.ts < p_before)
      union all
      select f.ts, jsonb_build_object('kind', 'fill', 'ts', f.ts, 'side', f.side, 'qty', f.qty,
          'venue_px', f.venue_px, 'shadow_px', f.shadow_px, 'fees', f.fees, 'tag', f.tag,
          'symbol', s.symbol, 'strategy', s.strategy, 'trade_id', t.id,
          'exit_reason', case when f.side = 'sell' then t.exit_reason end,
          'pnl_usd', case when f.side = 'sell' then t.pnl_usd end) as item
      from kaibot.fill f join kaibot."order" o on o.id = f.order_id
        join kaibot.signal s on s.id = o.signal_id
        left join kaibot.trade t on t.signal_id = s.id
      where s.run_id = p_run and (p_before is null or f.ts < p_before)
    ) u order by u.ts desc limit lim
  ) x), '[]'::jsonb);
end $$;

-- Engine health (S28): processes, halts, operational events, the dead-man's last runs.
create or replace function public.kb_health() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object(
    'now', now(),
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h) order by h.process) from kaibot.heartbeat h), '[]'::jsonb),
    'halts', coalesce((select jsonb_agg(jsonb_build_object('run_id', h.run_id, 'kind', h.kind, 'reason', h.reason,
        'ts', h.ts, 'session', h.session) order by h.ts desc)
      from (select * from kaibot.halt order by ts desc limit 50) h), '[]'::jsonb),
    'events', coalesce((select jsonb_agg(jsonb_build_object('type', e.type, 'ts', e.ts, 'run_id', e.run_id,
        'payload', e.payload) order by e.ts desc)
      from (select * from kaibot.event where type in (
          'engine_start', 'engine_stop', 'halt', 'halt_cleared', 'account_not_clean', 'calendar_stale', 'run_paused',
          'moc_refused', 'watchdog_flatten', 'watchdog_recovered', 'engine_error', 'shadow_error', 'session_start',
          'session_end', 'shadow_session_end', 'command_pause', 'command_resume', 'command_flatten_and_stop',
          'command_set_level', 'command_new_run')
        order by ts desc limit 80) e), '[]'::jsonb),
    'deadman', jsonb_build_object(
      'armed', (select count(*) = 2 from vault.secrets
                where name in ('kaibot_alpaca_paper_key_id', 'kaibot_alpaca_paper_secret')),
      'jobs', coalesce((select jsonb_agg(jsonb_build_object('job', j.jobname, 'schedule', j.schedule, 'active', j.active))
        from cron.job j where j.jobname like 'kaibot_deadman%'), '[]'::jsonb),
      'runs', coalesce((select jsonb_agg(jsonb_build_object('status', d.status, 'message', left(d.return_message, 200),
          'start', d.start_time) order by d.start_time desc)
        from (select r.* from cron.job_run_details r join cron.job j on j.jobid = r.jobid
              where j.jobname like 'kaibot_deadman%' order by r.start_time desc limit 5) d), '[]'::jsonb)),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts) order by c.ts)
      from kaibot.command c where c.applied_at is null), '[]'::jsonb)
  );
end $$;

revoke all on function public.kb_evidence(bigint), public.kb_report(bigint, date), public.kb_attribution(bigint, int),
  public.kb_tape(bigint, int, timestamptz), public.kb_health() from public, anon;
grant execute on function public.kb_evidence(bigint), public.kb_report(bigint, date), public.kb_attribution(bigint, int),
  public.kb_tape(bigint, int, timestamptz), public.kb_health() to authenticated;

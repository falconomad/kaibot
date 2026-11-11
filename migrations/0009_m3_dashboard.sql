-- 0009: M3 dashboard reads (stories S35, S36). Read-only, owner-checked, security definer, no direct table access.
-- Model text in room_session (thesis, counter, red flags, invalidation) is untrusted: the dashboard renders it as plain text.

-- S35: one room session with its evidence pack, for the transcript (traded or not).
create or replace function public.kb_room(p_signal bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare s kaibot.room_session;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select * into s from kaibot.room_session where signal_id = p_signal;
  if s.id is null then return null; end if;
  return jsonb_build_object(
    'session', jsonb_build_object('id', s.id, 'signal_id', s.signal_id, 'ts', s.ts, 'status', s.status,
        'verdict', s.verdict, 'rules_only', s.rules_only, 'valid', s.valid, 'proposer', s.proposer, 'critic', s.critic,
        'judge', s.judge, 'evidence', s.evidence, 'latency_ms', s.latency_ms, 'cost_usd', s.cost_usd, 'error', s.error,
        'counterfactual', s.counterfactual),
    'signal', (select jsonb_build_object('id', g.id, 'run_id', g.run_id, 'ts', g.ts, 'mode', g.mode, 'strategy', g.strategy,
        'symbol', g.symbol, 'entry', g.entry, 'stop', g.stop, 'target', g.target, 'reasons', to_jsonb(g.reasons),
        'veto', g.rules_veto) from kaibot.signal g where g.id = s.signal_id),
    'trade', (select jsonb_build_object('id', t.id, 'qty', t.qty, 'pnl_usd', t.pnl_usd, 'risk_usd', t.risk_usd,
        'exit_reason', t.exit_reason, 'exit_ts', t.exit_ts)
      from kaibot.trade t where t.signal_id = s.signal_id order by t.id limit 1)
  );
end $$;

-- S35: the tape (0007) plus the room's verdict on each signal, so vetoed candidates show their transcript too.
create or replace function public.kb_tape(p_run bigint, p_limit int default 100, p_before timestamptz default null)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare lim int := least(greatest(coalesce(p_limit, 100), 1), 500);
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return coalesce((select jsonb_agg(x.item order by x.ts desc) from (
    select * from (
      select s.ts, jsonb_build_object('kind', 'signal', 'ts', s.ts, 'signal_id', s.id, 'mode', s.mode, 'strategy', s.strategy,
          'symbol', s.symbol, 'entry', s.entry, 'stop', s.stop, 'target', s.target, 'reasons', to_jsonb(s.reasons),
          'veto', s.rules_veto, 'approved', g.approved, 'qty', g.qty, 'binding', g.binding,
          'trade_id', (select t.id from kaibot.trade t where t.signal_id = s.id limit 1),
          'counterfactual', s.features->'counterfactual',
          'room', (select jsonb_build_object('status', rs.status, 'verdict', rs.verdict, 'cause', rs.judge->>'cause',
              'confidence', rs.judge->>'confidence', 'counterfactual', rs.counterfactual)
            from kaibot.room_session rs where rs.signal_id = s.id)) as item
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

-- S36: room vs rules-only A/B. The rules-only arm is every session's counterfactual (full size, filled in at day end).
-- veto lift = mean R of allowed trades - mean counterfactual R of vetoed candidates (> 0: the room kept the better ones),
-- with a Welch-style 95% interval. Spend is all LLM calls in the period (triage + room): global, not per run.
create or replace function public.kb_room_ab(p_run bigint, p_weeks int default 13) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  w int := least(greatest(coalesce(p_weeks, 13), 1), 520);
  since timestamptz := now() - make_interval(days => 7 * least(greatest(coalesce(p_weeks, 13), 1), 520));
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return (
    with s as (
      select rs.status, rs.verdict, rs.cost_usd,
             (rs.counterfactual->>'pnl_usd')::numeric as cf_pnl, (rs.counterfactual->>'r_multiple')::numeric as cf_r,
             t.id as trade_id, t.pnl_usd as pnl, t.pnl_usd / nullif(t.risk_usd, 0) as r, t.exit_ts is not null as closed
      from kaibot.room_session rs join kaibot.signal g on g.id = rs.signal_id
        left join lateral (select * from kaibot.trade x where x.signal_id = rs.signal_id order by x.id limit 1) t on true
      where g.run_id = p_run and rs.ts >= since
    ), v as (
      select count(*) as n, count(cf_r) as n_cf, avg(cf_r) as mean_r, stddev_samp(cf_r) as sd_r,
             coalesce(sum(cf_pnl), 0) as cf_pnl, count(*) filter (where cf_pnl < 0) as n_loss_avoided,
             coalesce(-sum(cf_pnl) filter (where cf_pnl < 0), 0) as loss_avoided,
             count(*) filter (where cf_pnl > 0) as n_win_missed, coalesce(sum(cf_pnl) filter (where cf_pnl > 0), 0) as win_missed
      from s where status = 'done' and verdict = 'veto'
    ), a as (
      select count(*) as n, count(r) filter (where closed) as n_closed, avg(r) filter (where closed) as mean_r,
             stddev_samp(r) filter (where closed) as sd_r, coalesce(sum(pnl) filter (where closed), 0) as pnl
      from s where status = 'done' and verdict = 'allow'
    ), d as (
      select count(*) as n, count(*) filter (where closed and cf_pnl is not null) as n_paired,
             coalesce(sum(pnl) filter (where closed and cf_pnl is not null), 0) as pnl,
             coalesce(sum(cf_pnl) filter (where closed and cf_pnl is not null), 0) as cf_pnl,
             avg(r) filter (where closed) as mean_r
      from s where status = 'done' and verdict = 'downgrade'
    ), na as (   -- the room did not answer: no trade (fail closed)
      select count(*) as n, count(cf_pnl) as n_cf, coalesce(sum(cf_pnl), 0) as cf_pnl
      from s where status in ('timeout', 'invalid', 'error')
    ), arms as (  -- paired sessions: the rules-only result is known and the room arm's trade (if any) is closed
      select count(*) as n, coalesce(sum(cf_pnl), 0) as rules_pnl,
             coalesce(sum(case when trade_id is not null then pnl else 0 end), 0) as room_pnl
      from s where status <> 'pending' and cf_pnl is not null and (trade_id is null or closed)
    ), spend as (
      select coalesce(sum(c.usd), 0) as total,
             coalesce(jsonb_object_agg(c.purpose, round(c.usd, 4)), '{}'::jsonb) as by_purpose
      from (select x.purpose, sum(coalesce(x.actual_usd, 0)) as usd
            from kaibot.llm_call x where x.ts >= since group by x.purpose) c
    )
    select jsonb_build_object(
      'since', since, 'weeks', w,
      'n', (select count(*) from s),
      'by_verdict', coalesce((select jsonb_object_agg(coalesce(k.verdict, 'none'), k.n)
        from (select verdict, count(*) as n from s where status = 'done' group by verdict) k), '{}'::jsonb),
      'by_status', coalesce((select jsonb_object_agg(k.status, k.n)
        from (select status, count(*) as n from s group by status) k), '{}'::jsonb),
      'veto', (select jsonb_build_object('n', v.n, 'n_cf', v.n_cf, 'mean_r', round(v.mean_r, 4), 'sd_r', round(v.sd_r, 4),
          'cf_pnl', round(v.cf_pnl, 2), 'n_loss_avoided', v.n_loss_avoided, 'loss_avoided', round(v.loss_avoided, 2),
          'n_win_missed', v.n_win_missed, 'win_missed', round(v.win_missed, 2)) from v),
      'allow', (select jsonb_build_object('n', a.n, 'n_closed', a.n_closed, 'mean_r', round(a.mean_r, 4),
          'sd_r', round(a.sd_r, 4), 'pnl', round(a.pnl, 2)) from a),
      'downgrade', (select jsonb_build_object('n', d.n, 'n_paired', d.n_paired, 'pnl', round(d.pnl, 2),
          'cf_pnl', round(d.cf_pnl, 2), 'mean_r', round(d.mean_r, 4)) from d),
      'no_answer', (select jsonb_build_object('n', na.n, 'n_cf', na.n_cf, 'cf_pnl', round(na.cf_pnl, 2)) from na),
      'arms', (select jsonb_build_object('n', x.n, 'rules_pnl', round(x.rules_pnl, 2), 'room_pnl', round(x.room_pnl, 2)) from arms x),
      'lift', (select jsonb_build_object('n_veto', v.n_cf, 'n_allow', a.n_closed,
          'diff', round(a.mean_r - v.mean_r, 4),
          'ci_lo', case when v.n_cf > 1 and a.n_closed > 1 then round(a.mean_r - v.mean_r
              - 1.96 * sqrt(v.sd_r ^ 2 / v.n_cf + a.sd_r ^ 2 / a.n_closed), 4) end,
          'ci_hi', case when v.n_cf > 1 and a.n_closed > 1 then round(a.mean_r - v.mean_r
              + 1.96 * sqrt(v.sd_r ^ 2 / v.n_cf + a.sd_r ^ 2 / a.n_closed), 4) end) from v, a),
      'spend', (select jsonb_build_object('llm_usd', round(sp.total, 4), 'by_purpose', sp.by_purpose,
          'room_usd', round((select coalesce(sum(cost_usd), 0) from s)::numeric, 4),
          'per_loss_avoided', case when v.n_loss_avoided > 0 then round(sp.total / v.n_loss_avoided, 4) end)
        from spend sp, v)
    )
  );
end $$;

-- S36 / S32: today's LLM spend and budget state for Engine health. Caps mirror config/llm.yaml
-- ($3/day soft -> rules-only, $150/month alert, $190/month hard -> no entries). The engine may also have set the day's
-- state when a reservation was refused (reserved + spent over a cap); the worse of the two is shown.
create or replace function public.kb_llm_budget() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  b jsonb;
  stored text;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  b := kaibot.llm_budget_state(3.0, 190.0, 150.0);
  select x.state into stored from kaibot.budget_day x where x.day = d;
  return jsonb_build_object(
    'day', d,
    'day_usd', round((b->>'day')::numeric, 4),
    'month_usd', round((b->>'month')::numeric, 4),
    'state', case when 'no_entries' in (b->>'state', stored) then 'no_entries'
                  when 'rules_only' in (b->>'state', stored) then 'rules_only' else 'ok' end,
    'alert', (b->>'alert')::boolean,
    'caps', jsonb_build_object('daily_soft', 3.0, 'monthly_alert', 150.0, 'monthly_hard', 190.0),
    'calls_today', (select count(*) from kaibot.llm_call c
      where c.ts >= d::timestamp at time zone 'America/New_York'),
    'open_reserved_usd', round((select coalesce(sum(c.reserved_usd), 0) from kaibot.llm_call c
      where c.settled_at is null and c.ts > now() - interval '5 minutes')::numeric, 4),
    'by_purpose', coalesce((select jsonb_object_agg(p.purpose, p.usd) from (
        select c.purpose, round(sum(coalesce(c.actual_usd, 0))::numeric, 4) as usd from kaibot.llm_call c
        where c.ts >= d::timestamp at time zone 'America/New_York' group by c.purpose) p), '{}'::jsonb)
  );
end $$;

revoke all on function public.kb_room(bigint), public.kb_tape(bigint, int, timestamptz), public.kb_room_ab(bigint, int),
  public.kb_llm_budget() from public, anon;
grant execute on function public.kb_room(bigint), public.kb_tape(bigint, int, timestamptz), public.kb_room_ab(bigint, int),
  public.kb_llm_budget() to authenticated;

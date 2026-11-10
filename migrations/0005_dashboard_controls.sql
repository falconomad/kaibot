-- Dashboard controls + live positions (decision 10). The browser queues commands; the engine applies them.
create or replace function public.kb_command(p_kind text, p_idem text, p_payload jsonb default '{}'::jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if p_kind not in ('pause', 'resume', 'flatten_and_stop', 'set_level') then raise exception 'unknown command'; end if;
  if p_kind = 'set_level' and coalesce(p_payload->>'level', '') not in ('conservative', 'balanced', 'aggressive') then
    raise exception 'unknown level';
  end if;
  if p_idem is null or length(p_idem) < 8 then raise exception 'idempotency key required'; end if;
  insert into kaibot.command (idempotency_key, kind, payload)
    values (p_idem, p_kind, case when p_kind = 'set_level' then jsonb_build_object('level', p_payload->>'level') else '{}'::jsonb end)
    on conflict (idempotency_key) do nothing;
  return jsonb_build_object('queued', true);
end $$;
revoke all on function public.kb_command(text, text, jsonb) from public, anon;
grant execute on function public.kb_command(text, text, jsonb) to authenticated;

-- Overview now also returns the latest ledger snapshot (open positions, cash) for the run.
create or replace function public.kb_overview(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare out jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select jsonb_build_object(
    'run', (select to_jsonb(r) from kaibot.run r where r.id = p_run),
    'daily', coalesce((select jsonb_agg(jsonb_build_object(
        'session', d.session, 'pnl', d.pnl_reported, 'pnl_venue', d.pnl_venue, 'pnl_shadow', d.pnl_shadow,
        'spy_ret', d.spy_ret, 'trades', d.trades) order by d.session)
      from kaibot.daily_result d where d.run_id = p_run), '[]'::jsonb),
    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h), '[]'::jsonb),
    'pending_commands', coalesce((select jsonb_agg(jsonb_build_object('kind', c.kind, 'ts', c.ts, 'payload', c.payload))
      from kaibot.command c where c.applied_at is null), '[]'::jsonb),
    'ledger', (select to_jsonb(l) from kaibot.ledger l where l.run_id = p_run order by l.ts desc limit 1),
    'today', (select jsonb_build_object('trades', count(*), 'pnl', coalesce(sum(t.pnl_usd), 0))
      from kaibot.trade t where t.run_id = p_run and t.entry_ts >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York')
  ) into out;
  return out;
end $$;

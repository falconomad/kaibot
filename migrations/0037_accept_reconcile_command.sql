-- 0037 (Frontend, 21 Nov 2026; M9 ticket 1.14, #439): the owner can accept a ledger/broker difference from the
-- dashboard. After 0005.
--
-- What changes.
--   kaibot.command   the kind check also allows 'accept_reconcile' (0001 allowed pause, resume, flatten_and_stop,
--                    new_run, set_level). The check constraint is replaced; no row changes.
--   public.kb_command  0005's function plus the kind 'accept_reconcile'. Its payload must carry
--                    reason  3 to 200 characters, in the owner's words (the engine journals it), and
--                    shown   the differences the owner's screen displayed when they confirmed: 1 to 50 objects
--                            {"kind": "quantity" | "cash", "symbol": text (quantity only), "offset": number}
--                            (offset = broker minus ledger: shares, or dollars for cash).
--                    Only those keys are stored. The engine (engine/core.py `_accept_reconcile`,
--                    engine/reconcile.py `Reconciler.resolve(shown=...)`) reads the venue again and accepts only
--                    when `shown` is exactly what it finds; otherwise it refuses and buys stay stopped.
--                    Everything else is 0005's text: owner-only (kaibot.is_owner()), the idempotency key (8+
--                    characters, unique: a second call with the same key queues nothing), the other four kinds and
--                    their payloads unchanged. `new_run` stays with kb_new_run (0002).
--
-- Changes live trading behaviour once applied AND used: an accepted difference lifts the stop on new buys that the
-- reconciliation holds (sales, stops and flattens are never held). Applying the file alone changes nothing: no
-- command is queued by it, and an engine without the #439 code already applies the kind (S78), ignoring `shown`.
-- No delete, truncate or drop of data. Outside 09:30-16:00 ET (the engine reads kaibot.command every loop).
--
-- Check after applying (read-only):
--   select pg_get_constraintdef(oid) like '%accept_reconcile%' from pg_constraint
--    where conrelid = 'kaibot.command'::regclass and contype = 'c';                       -- one row, true
--   select prosrc like '%accept_reconcile%' from pg_proc where proname = 'kb_command';     -- true
--   select has_function_privilege('anon', 'public.kb_command(text, text, jsonb)', 'execute');  -- false

alter table kaibot.command drop constraint if exists command_kind_check;
alter table kaibot.command add constraint command_kind_check
  check (kind in ('pause', 'resume', 'flatten_and_stop', 'new_run', 'set_level', 'accept_reconcile'));

create or replace function public.kb_command(p_kind text, p_idem text, p_payload jsonb default '{}'::jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  pl jsonb := '{}'::jsonb;
  reason text;
  shown jsonb;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if p_kind not in ('pause', 'resume', 'flatten_and_stop', 'set_level', 'accept_reconcile') then raise exception 'unknown command'; end if;
  if p_kind = 'set_level' and coalesce(p_payload->>'level', '') not in ('conservative', 'balanced', 'aggressive') then
    raise exception 'unknown level';
  end if;
  if p_idem is null or length(p_idem) < 8 then raise exception 'idempotency key required'; end if;
  if p_kind = 'set_level' then
    pl := jsonb_build_object('level', p_payload->>'level');
  elsif p_kind = 'accept_reconcile' then
    reason := btrim(coalesce(p_payload->>'reason', ''));
    shown := p_payload->'shown';
    if length(reason) < 3 or length(reason) > 200 then raise exception 'a reason of 3 to 200 characters is required'; end if;
    if shown is null or jsonb_typeof(shown) <> 'array' or jsonb_array_length(shown) < 1 or jsonb_array_length(shown) > 50 then
      raise exception 'the differences shown are required';
    end if;
    if exists (
      select 1 from jsonb_array_elements(shown) x
       where jsonb_typeof(x) is distinct from 'object'
          or coalesce(x->>'kind', '') not in ('quantity', 'cash')
          or jsonb_typeof(x->'offset') is distinct from 'number'
          or (x->>'kind' = 'quantity' and coalesce(x->>'symbol', '') !~ '^[A-Z][A-Z0-9.]{0,9}$')
          or (x->>'kind' = 'cash' and jsonb_typeof(x->'symbol') is not null and jsonb_typeof(x->'symbol') <> 'null')
    ) then
      raise exception 'a difference shown is not one that can be accepted';
    end if;
    pl := jsonb_build_object('reason', reason, 'shown', (
      select jsonb_agg(jsonb_build_object('kind', x->>'kind', 'symbol', x->>'symbol', 'offset', x->'offset'))
        from jsonb_array_elements(shown) x));
  end if;
  insert into kaibot.command (idempotency_key, kind, payload)
    values (p_idem, p_kind, pl)
    on conflict (idempotency_key) do nothing;
  return jsonb_build_object('queued', true);
end $$;
revoke all on function public.kb_command(text, text, jsonb) from public, anon;
grant execute on function public.kb_command(text, text, jsonb) to authenticated;

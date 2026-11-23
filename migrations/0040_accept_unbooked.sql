-- 0040 (backend-quant, 22 Nov 2026; #529): the owner books or accepts a fill whose activity never came, from the
-- dashboard. After 0037.
--
-- What changes.
--   public.kb_command  0037's function, and `accept_reconcile`'s `shown` may also hold rows of kind 'unbooked':
--                      {"kind": "unbooked", "symbol": ticker, "coid": the engine's client order id (kb...),
--                       "venue": number > 0 (shares the broker filled), "booked": number >= 0 (shares the books
--                       hold of it), "action": "book" | "accept"}.
--                      Only those keys are stored for such a row (0037's three for the others). The engine
--                      (engine/core.py `_accept_rows`, engine/reconcile.py `unbooked_plan`) acts only when the rows
--                      are exactly the day-old unbooked fills it last found, reads the order at the broker again,
--                      and refuses otherwise. "book" books the broker's filled shares at its average fill price;
--                      "accept" stops counting them as unbooked. Everything else is 0037's text, unchanged.
--   No table, constraint, row, trigger or grant changes besides the function's own revoke/grant.
--
-- Changes live trading behaviour once applied AND used: a booked fill puts the shares in the books (the rule can
-- then sell them, and buys in that stock resume); an accepted one lifts the wait for it. Applying the file alone
-- changes nothing, and an engine without the #529 code refuses such a row (it is not a difference it can accept).
-- No delete, truncate or drop of data. Outside 09:30-16:00 ET (the engine reads kaibot.command every loop).
--
-- Check after applying (read-only):
--   select prosrc like '%unbooked%' and prosrc like '%accept_reconcile%' from pg_proc where proname = 'kb_command'; -- true
--   select has_function_privilege('anon', 'public.kb_command(text, text, jsonb)', 'execute');                  -- false

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
          or coalesce(x->>'kind', '') not in ('quantity', 'cash', 'unbooked')
          or (x->>'kind' in ('quantity', 'cash') and jsonb_typeof(x->'offset') is distinct from 'number')
          or (x->>'kind' in ('quantity', 'unbooked') and coalesce(x->>'symbol', '') !~ '^[A-Z][A-Z0-9.]{0,9}$')
          or (x->>'kind' = 'cash' and jsonb_typeof(x->'symbol') is not null and jsonb_typeof(x->'symbol') <> 'null')
          or (x->>'kind' = 'unbooked' and (
                coalesce(x->>'coid', '') !~ '^kb[0-9]+-[A-Za-z0-9_.~-]{1,120}$'
             or jsonb_typeof(x->'venue') is distinct from 'number' or (x->>'venue')::numeric <= 0
             or jsonb_typeof(x->'booked') is distinct from 'number' or (x->>'booked')::numeric < 0
             or coalesce(x->>'action', '') not in ('book', 'accept')))
    ) then
      raise exception 'a difference shown is not one that can be accepted';
    end if;
    pl := jsonb_build_object('reason', reason, 'shown', (
      select jsonb_agg(case when x->>'kind' = 'unbooked'
               then jsonb_build_object('kind', 'unbooked', 'symbol', x->>'symbol', 'coid', x->>'coid',
                                       'venue', x->'venue', 'booked', x->'booked', 'action', x->>'action')
               else jsonb_build_object('kind', x->>'kind', 'symbol', x->>'symbol', 'offset', x->'offset') end)
        from jsonb_array_elements(shown) x));
  end if;
  insert into kaibot.command (idempotency_key, kind, payload)
    values (p_idem, p_kind, pl)
    on conflict (idempotency_key) do nothing;
  return jsonb_build_object('queued', true);
end $$;
revoke all on function public.kb_command(text, text, jsonb) from public, anon;
grant execute on function public.kb_command(text, text, jsonb) to authenticated;

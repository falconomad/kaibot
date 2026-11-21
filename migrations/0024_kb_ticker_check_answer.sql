-- 0024: kb_ticker_check_answer: the dashboard's Vercel function answers 'My lists' ticker checks (stakeholder,
-- 18 Nov 2026: "move the check onto Vercel so it works even when the Mac is off").
--
-- Status (applied or pending): docs/ops/migrations_pending.md.
-- Production migrations are the stakeholder's (Product applies on his word). Dashboard-only, like 0020 and 0021: any
-- time, also while the engine runs. Independent of 0023 (stock summaries).
--
-- Until now only scripts/run_ticker_checks.py on the Mac (service role) answered the rows kb_check_ticker leaves
-- `pending`. apps/web/api/check-ticker.ts runs the same check (engine/tickercheck.py's verdict; a shared JSON and two
-- tests keep them equal) with the caller's own session and the publishable key. There is no service-role key on
-- Vercel, so it writes the answer through this owner-only SECURITY DEFINER function.
--
-- What it does: one new function. No table, column, index, trigger or engine function is created or changed.
--   - owner only (kaibot.is_owner(), as every kb_* RPC); execute for `authenticated` only;
--   - updates ONLY today's (ET) row of that symbol, and only while it is `pending` or `error`: an answer that is
--     already there (the Mac worker's, or an earlier one) is never overwritten; it is returned instead;
--   - validates its input: a ticker symbol that is not a reference fund; status accepted / refused / failed;
--     accepted needs a reference sector fund and a detail that meets the level floors (>= 60 sessions, last close
--     >= $5, 20-day $ volume > $50M); refused needs a plain reason; sizes are bounded;
--   - `failed` (the broker or the bars could not be read) counts an attempt; the third makes the row `error` with the
--     same words as tickercheck.process, which the owner can retry (kb_check_ticker puts it back to `pending`).
-- The engine checks every custom ticker again at each session open on the history it loaded (Engine._vet_custom),
-- so even a wrong answer here cannot make a failing ticker trade. Nothing here places, changes or cancels an order.
--
-- Check after applying (read-only):
--   select p.prosecdef, has_function_privilege('anon', p.oid, 'execute') as anon,
--          has_function_privilege('authenticated', p.oid, 'execute') as auth
--   from pg_proc p where p.proname = 'kb_ticker_check_answer';                 -- true, false, true

create or replace function public.kb_ticker_check_answer(
  p_symbol text, p_status text, p_reason text, p_etf text, p_name text, p_industry text, p_detail jsonb
) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  st text := coalesce(p_status, '');
  d jsonb := coalesce(p_detail, '{}'::jsonb);
  today date := (now() at time zone 'America/New_York')::date;
  refs text[] := array['SPY','XLK','XLC','XLY','XLP','XLV','XLF','XLI','XLE','XLU','XLRE','XLB'];
  c kaibot.ticker_check;
  n int;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' or s = any(refs) then
    raise exception 'not a custom ticker' using errcode = '22023'; end if;
  if st not in ('accepted', 'refused', 'failed') then raise exception 'bad status' using errcode = '22023'; end if;
  if jsonb_typeof(d) <> 'object' or length(d::text) > 4000 then raise exception 'bad detail' using errcode = '22023'; end if;
  if length(coalesce(p_name, '')) > 200 or length(coalesce(p_industry, '')) > 200 then
    raise exception 'name or industry too long' using errcode = '22023'; end if;
  if st = 'accepted' and not coalesce((   -- a missing field is null: coalesce makes it a refusal, never a pass
       p_reason is null and p_etf = any(refs)
       and jsonb_typeof(d->'sessions') = 'number' and (d->>'sessions')::numeric >= 60
       and jsonb_typeof(d->'last_close') = 'number' and (d->>'last_close')::numeric >= 5
       and jsonb_typeof(d->'adv20_usd') = 'number' and (d->>'adv20_usd')::numeric > 50000000), false) then
    raise exception 'an accepted answer must meet the level floors and name a sector fund' using errcode = '22023';
  end if;
  if st = 'refused' and (p_etf is not null or length(btrim(coalesce(p_reason, ''))) not between 1 and 300) then
    raise exception 'a refusal needs a plain reason and no sector fund' using errcode = '22023';
  end if;

  select * into c from kaibot.ticker_check where symbol = s and checked_on = today for update;
  if c.symbol is null or c.status not in ('pending', 'error') then
    return jsonb_build_object('ok', false, 'reason', case when c.symbol is null then 'no check today' else 'already answered' end,
      'check', case when c.symbol is null then null else kaibot.ticker_view(c) end);
  end if;

  if st = 'failed' then
    n := c.attempts + 1;
    update kaibot.ticker_check set attempts = n,
        detail = jsonb_build_object('error', left(coalesce(d->>'error', 'unknown'), 80), 'checked_by', 'vercel'),
        status = case when n >= 3 then 'error' else status end,
        reason = case when n >= 3 then 'We could not check ' || s || ' right now. Try again later.' else reason end,
        updated_at = now()
      where symbol = s and checked_on = today returning * into c;
  else
    update kaibot.ticker_check set status = st, reason = case when st = 'refused' then btrim(p_reason) end,
        etf = case when st = 'accepted' then p_etf end, name = left(p_name, 200), industry = left(p_industry, 200),
        detail = d, updated_at = now()
      where symbol = s and checked_on = today returning * into c;
  end if;
  return jsonb_build_object('ok', true, 'check', kaibot.ticker_view(c));
end $$;

revoke all on function public.kb_ticker_check_answer(text, text, text, text, text, text, jsonb) from public, anon;
grant execute on function public.kb_ticker_check_answer(text, text, text, text, text, text, jsonb) to authenticated;

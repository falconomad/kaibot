-- 0052: a licence tag on the stock summaries (#722, M19 ticket 8.4 of the second engine audit). After 0023 (adds a
-- column to its kaibot.stock_summary and re-creates its kb_stock_summary and kb_summary_settle) and 0027; independent of
-- 0048-0051 (numbered after them only). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- Why. A summary card holds the model's one-sentence gist of each news item (and the headline itself when the model's
-- draft was refused). Those items are Benzinga's, through Alpaca: either Alpaca's news API, read by
-- apps/web/api/summary.ts, or kaibot.news_item, which the engine's news desk fills from the same Alpaca feed.
-- Alpaca's individual data licence lets the owner see them and does not let anyone else (the headline line's 0043
-- reads it the same way: source alpaca_benzinga, licence owner_only). The cards were stored with no tag saying so,
-- so a future member path could not tell a card with Benzinga text from one with only the plain facts.
--
-- What it adds.
-- 1) kaibot.stock_summary.licence: 'owner_only' (the card holds news text: Benzinga through Alpaca) or 'public' (no
--    news text: the company profile, the earnings date and corporate actions only). Default 'owner_only', so every
--    existing card is owner_only. A check constraint ties the tag to the content: a card whose body lists any news
--    item, or that has no news list at all, can only be owner_only.
-- 2) kaibot.stock_summary_card(p_symbol text, p_owner boolean): THE licence guard. owner_only cards only when p_owner
--    is true; otherwise public cards only. Not callable by anon or authenticated.
-- 3) public.kb_stock_summary: owner only as before (0023's body, read through the guard with kaibot.is_owner()), and
--    the answer says its `licence`. When multi-user comes, a member path can only ever receive public cards.
-- 4) public.kb_summary_settle: 0023's body; the stored card's licence is the one the function asks for
--    (`p_body.licence`, set from its sources by api/summary.ts) only if that is 'public' AND the body's news list is
--    empty; anything else stores owner_only. Same signature, so the dashboard's function works before and after this.
--
-- Additive: add column if not exists, add constraint when missing, create or replace function, revoke and grant. No
-- row is written at apply time; no delete, drop or truncate. Any time, also while the engine runs (the engine never
-- reads or writes these tables). Changes no trading behaviour.
--
-- Check after applying (read-only):
--   select count(*) filter (where licence = 'owner_only'), count(*) from kaibot.stock_summary;        -- all, all
--   select has_function_privilege('authenticated', 'kaibot.stock_summary_card(text, boolean)', 'execute');  -- false
--   select has_function_privilege('anon', 'public.kb_stock_summary(text)', 'execute');                -- false
--   select public.kb_stock_summary('AAPL')->>'licence';             -- as the owner, once a card exists: owner_only

alter table kaibot.stock_summary add column if not exists licence text not null default 'owner_only';

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'stock_summary_licence_by_content'
                 and conrelid = 'kaibot.stock_summary'::regclass) then
    alter table kaibot.stock_summary add constraint stock_summary_licence_by_content
      check (licence = 'owner_only' or (licence = 'public' and coalesce(body->'news' = '[]'::jsonb, false)));
  end if;
end $$;

-- The licence guard: the latest card of the last 14 days for one symbol that the caller may read, or null.
-- owner_only cards reach only a caller that passes p_owner = true.
create or replace function kaibot.stock_summary_card(p_symbol text, p_owner boolean) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  today date := (now() at time zone 'America/New_York')::date;
  r kaibot.stock_summary;
begin
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then return null; end if;
  select * into r from kaibot.stock_summary x where x.symbol = s and x.day <= today and x.day > today - 14
    and (p_owner is true or x.licence = 'public')
    order by x.day desc limit 1;
  if r.symbol is null then return null; end if;
  return jsonb_build_object('symbol', r.symbol, 'day', r.day, 'today', r.day = today, 'text', r.text, 'body', r.body,
                            'sources', r.sources, 'ai', r.model is not null, 'model', r.model, 'written_at', r.created_at,
                            'licence', r.licence);
end $$;
revoke all on function kaibot.stock_summary_card(text, boolean) from public, anon, authenticated;

-- 0023's kb_stock_summary, read through the guard.
create or replace function public.kb_stock_summary(p_symbol text) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_owner boolean := kaibot.is_owner();
begin
  if not v_owner then raise exception 'not authorised' using errcode = '42501'; end if;
  return kaibot.stock_summary_card(p_symbol, v_owner);
end $$;

-- 0023's kb_summary_settle; the card's licence: public only when asked for and the body lists no news.
create or replace function public.kb_summary_settle(p_call bigint, p_actual numeric, p_in integer, p_out integer,
    p_error text, p_text text, p_body jsonb, p_sources jsonb, p_ai boolean) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare c kaibot.summary_call;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  update kaibot.summary_call
     set actual_usd = least(greatest(coalesce(p_actual, reserved_usd), 0), 0.999999), input_tokens = p_in,
         output_tokens = p_out, error = left(p_error, 300), settled_at = now()
   where id = p_call and settled_at is null returning * into c;
  if c.id is null then raise exception 'unknown or settled call' using errcode = '22023'; end if;
  if p_text is null then return null; end if;
  insert into kaibot.stock_summary (symbol, day, text, body, sources, model, cost_usd, call_id, licence)
    values (c.symbol, c.day, p_text, coalesce(p_body, '{}'), coalesce(p_sources, '[]'),
            case when coalesce(p_ai, false) then c.model end, c.actual_usd, c.id,
            case when p_body->>'licence' = 'public' and coalesce(p_body->'news' = '[]'::jsonb, false)
                 then 'public' else 'owner_only' end)
    on conflict (symbol, day) do nothing;
  return public.kb_stock_summary(c.symbol);
end $$;

revoke all on function public.kb_stock_summary(text),
  public.kb_summary_settle(bigint, numeric, integer, integer, text, text, jsonb, jsonb, boolean) from public, anon;
grant execute on function public.kb_stock_summary(text),
  public.kb_summary_settle(bigint, numeric, integer, integer, text, text, jsonb, jsonb, boolean) to authenticated;

-- 0043: the dashboard's headline line (stakeholder and Product, 23 Nov 2026). After 0042 (number only: no dependency
-- on it; 0042 is #477's engine lease epoch). Status (applied or pending): docs/ops/migrations_pending.md.
--
-- What it adds.
-- 1) kaibot.headline: a small display table, one row per headline. Written only by the engine (service role,
--    src/kaibot/news/feed.py, KAIBOT_HEADLINE_FEED), never read by the engine. Two sources:
--      alpaca_benzinga  the headlines the engine already fetches (licence owner_only: Alpaca's individual data
--                       licence allows the owner to see them and forbids showing them to anyone else);
--      sec_edgar        8-K filings of the held and decided stocks, one line from the item codes (licence public;
--                       off unless KAIBOT_HEADLINE_EDGAR=on).
--    A check constraint ties the licence to the source, so an Alpaca row can never be stored as public.
--    RLS on: select for the owner only (kaibot.is_owner()); all for service_role (the engine). No table privilege for anon or authenticated
--    (decision 10: the browser reads through owner-checked functions), so the policy is a second lock.
--    Retention: a statement trigger after every insert keeps 7 days and at most 500 rows.
-- 2) kaibot.headline_list(p_owner boolean, p_limit int): THE licence guard. owner_only rows only when p_owner is
--    true; otherwise public rows only. Not callable by anon or authenticated.
-- 3) public.kb_headlines(p_limit int default 20): the dashboard's read. Owner only today (single owner, decision 10);
--    it passes kaibot.is_owner() to headline_list, so when multi-user comes a non-owner path can only ever receive
--    public rows. Held/decided stocks first, then newest first; at most 50.
--
-- Additive: create table / index / function / trigger / policy only when missing, revoke and grant. The trigger's
-- function deletes old rows of THIS table only (retention). Any time, also while the engine runs; the engine's
-- writes fail softly (logged once) until it is applied.
--
-- Check after applying (read-only):
--   select relrowsecurity from pg_class where oid = 'kaibot.headline'::regclass;                    -- true
--   select has_table_privilege('authenticated', 'kaibot.headline', 'select');                       -- false
--   select has_function_privilege('anon', 'public.kb_headlines(integer)', 'execute');              -- false
--   select has_function_privilege('authenticated', 'kaibot.headline_list(boolean, integer)', 'execute');  -- false

create table if not exists kaibot.headline (
  id          bigint generated always as identity primary key,
  dedup_key   text not null unique check (length(dedup_key) between 5 and 80),
  symbols     text[] not null check (cardinality(symbols) between 1 and 10
                                     and array_to_string(symbols, ',') ~ '^[A-Z][A-Z0-9.-]{0,9}(,[A-Z][A-Z0-9.-]{0,9})*$'),
  headline    text not null check (length(headline) between 1 and 140 and headline !~ '[[:cntrl:]]'),
  source      text not null check (source in ('alpaca_benzinga', 'sec_edgar')),
  licence     text not null check (licence in ('owner_only', 'public')),
  url         text check (url is null or (url ~ '^https://[^[:space:]"''<>]+$' and length(url) <= 500)),
  tags        text[] not null default '{}'
              check (tags <@ array['analyst_up', 'analyst_down', 'management_change', 'listing']::text[]),
  focus       boolean not null default false,  -- a held or decided stock
  created_at  timestamptz not null,            -- the provider's time (Alpaca created_at, SEC acceptance time)
  written_at  timestamptz not null default now(),
  constraint headline_licence_by_source
    check (licence = case source when 'sec_edgar' then 'public' else 'owner_only' end)
);
create index if not exists headline_recent on kaibot.headline (created_at desc);
alter table kaibot.headline enable row level security;
revoke all on kaibot.headline from public, anon, authenticated;
grant select, insert, update on kaibot.headline to service_role;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname = 'kaibot' and tablename = 'headline'
                 and policyname = 'owner reads headlines') then
    create policy "owner reads headlines" on kaibot.headline for select to authenticated
      using ((select kaibot.is_owner()));
  end if;
  -- the engine's writes (Supabase's service_role bypasses RLS anyway; said here so the intent is in the file)
  if not exists (select 1 from pg_policies where schemaname = 'kaibot' and tablename = 'headline'
                 and policyname = 'engine writes headlines') then
    create policy "engine writes headlines" on kaibot.headline for all to service_role using (true) with check (true);
  end if;
end $$;

create or replace function kaibot.headline_trim() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  delete from kaibot.headline where created_at < now() - interval '7 days';
  delete from kaibot.headline where id in (
    select h.id from kaibot.headline h order by h.created_at desc, h.id desc offset 500);
  return null;
end $$;
revoke all on function kaibot.headline_trim() from public, anon, authenticated;

do $$ begin
  if not exists (select 1 from pg_trigger where tgname = 'headline_trim'
                 and tgrelid = 'kaibot.headline'::regclass) then
    create trigger headline_trim after insert on kaibot.headline
      for each statement execute function kaibot.headline_trim();
  end if;
end $$;

-- The licence guard: owner_only rows reach only a caller that passes p_owner = true.
create or replace function kaibot.headline_list(p_owner boolean, p_limit int default 20) returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'id', h.id, 'symbols', to_jsonb(h.symbols), 'headline', h.headline, 'source', h.source,
      'licence', h.licence, 'url', h.url, 'tags', to_jsonb(h.tags), 'focus', h.focus, 'created_at', h.created_at)
      order by h.focus desc, h.created_at desc, h.id desc), '[]'::jsonb)
  from (select * from kaibot.headline x
        where (p_owner is true or x.licence = 'public') and x.created_at >= now() - interval '7 days'
        order by x.focus desc, x.created_at desc, x.id desc
        limit greatest(1, least(coalesce(p_limit, 20), 50))) h;
$$;
revoke all on function kaibot.headline_list(boolean, int) from public, anon, authenticated;

create or replace function public.kb_headlines(p_limit int default 20) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_owner boolean := kaibot.is_owner();
begin
  if not v_owner then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object('as_of', now(), 'items', kaibot.headline_list(v_owner, p_limit));
end $$;
revoke all on function public.kb_headlines(int) from public, anon;
grant execute on function public.kb_headlines(int) to authenticated;

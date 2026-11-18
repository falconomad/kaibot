-- 0023: stock summaries (display-only AI; stakeholder, 18 Nov 2026). A plain-English card per stock and ET day, written
-- on demand by the dashboard's serverless function (apps/web/api/summary.ts) when the owner opens a stock, and read
-- back by the stock sheets, the trade page and the My lists chips. The engine never reads any of it.
--
-- NOT APPLIED. Production migrations are the stakeholder's (Product applies on his word). Additive only, like 0020 and
-- 0021: two new tables and five new owner-only functions; no engine table or function is touched. Any time, also while
-- the engine runs.
--
-- The function runs with the publishable key and the owner's own session (no service-role key): every write goes
-- through the definer functions below, which hold the summaries' own hard caps for every instance at once:
--   $0.30 a day and $5.00 a month (ET), at most $0.02 reserved per call, at most 2 calls per symbol and day, and none
--   once the day's card exists. A reservation that is never settled keeps counting (a crashed call is not free).
-- The model's text never reaches a trading decision: nothing outside these functions reads these tables.
--
-- No delete, drop or truncate. Check after applying (read-only):
--   select public.kb_summary_spend();     -- as the owner: {"day_usd": 0, "month_usd": 0, "day_cap": 0.30, ...}
--   select has_function_privilege('anon', 'public.kb_summary_reserve(text, text, numeric)', 'execute');  -- false

create table if not exists kaibot.summary_call (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  day           date not null,                                  -- ET day the call counts on
  symbol        text not null check (symbol ~ '^[A-Z]{1,5}([.][A-Z])?$'),
  model         text not null check (length(model) between 1 and 60),
  reserved_usd  numeric(10, 6) not null check (reserved_usd > 0 and reserved_usd <= 0.02),
  actual_usd    numeric(10, 6) check (actual_usd is null or (actual_usd >= 0 and actual_usd < 1)),
  input_tokens  integer, output_tokens integer,
  error         text check (error is null or length(error) <= 300),
  settled_at    timestamptz
);
create index if not exists summary_call_day on kaibot.summary_call (day, symbol);

create table if not exists kaibot.stock_summary (
  symbol     text not null check (symbol ~ '^[A-Z]{1,5}([.][A-Z])?$'),
  day        date not null,                                     -- ET day it was written for
  text       text not null check (length(text) between 1 and 1200),
  body       jsonb not null default '{}' check (jsonb_typeof(body) = 'object' and length(body::text) <= 8000),
  sources    jsonb not null default '[]' check (jsonb_typeof(sources) = 'array' and length(sources::text) <= 2000),
  model      text,                                              -- null: the plain facts (the model's draft was refused)
  cost_usd   numeric(10, 6) not null default 0,
  call_id    bigint references kaibot.summary_call(id),
  created_at timestamptz not null default now(),
  primary key (symbol, day)
);

alter table kaibot.summary_call enable row level security;
alter table kaibot.stock_summary enable row level security;
revoke all on kaibot.summary_call, kaibot.stock_summary from public, anon, authenticated;
grant select on kaibot.summary_call, kaibot.stock_summary to service_role;   -- reads for ops; writes via the functions

-- What the day and the month have spent: settled calls at their cost, unsettled ones at their reservation.
create or replace function kaibot.summary_spent(p_from date) returns numeric
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(coalesce(actual_usd, reserved_usd)), 0) from kaibot.summary_call where day >= p_from;
$$;
revoke all on function kaibot.summary_spent(date) from public, anon, authenticated;

-- The latest card of the last 14 days for one symbol, or null. `today` says whether it is today's.
create or replace function public.kb_stock_summary(p_symbol text) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  today date := (now() at time zone 'America/New_York')::date;
  r kaibot.stock_summary;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then return null; end if;
  select * into r from kaibot.stock_summary where symbol = s and day <= today and day > today - 14
    order by day desc limit 1;
  if r.symbol is null then return null; end if;
  return jsonb_build_object('symbol', r.symbol, 'day', r.day, 'today', r.day = today, 'text', r.text, 'body', r.body,
                            'sources', r.sources, 'ai', r.model is not null, 'model', r.model, 'written_at', r.created_at);
end $$;

-- The facts Kaibot already holds for one symbol: news headlines of the last 7 days (kaibot.news_item, written by the
-- engine's news desk) and the latest ticker check (Finnhub's name and industry, the exchange).
create or replace function public.kb_summary_facts(p_symbol text) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  c kaibot.ticker_check;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then raise exception 'not a symbol' using errcode = '22023'; end if;
  select * into c from kaibot.ticker_check where symbol = s and status = 'accepted' order by checked_on desc limit 1;
  return jsonb_build_object(
    'symbol', s,
    'today', (now() at time zone 'America/New_York')::date,
    'profile', case when c.symbol is null then null else jsonb_build_object('name', c.name, 'industry', c.industry,
                    'exchange', c.detail->>'exchange', 'as_of', c.checked_on) end,
    'news', coalesce((select jsonb_agg(jsonb_build_object('headline', left(n.headline, 300), 'source', n.source,
                        'published_at', n.published_at) order by n.published_at desc)
                      from (select * from kaibot.news_item where s = any(tickers)
                              and published_at > now() - interval '7 days' and available_ts <= now()
                            order by published_at desc limit 10) n), '[]'::jsonb));
end $$;

-- Reserve one call's worst case under the caps. {"ok": true, "call_id": n} or {"ok": false, "reason": ...}:
-- cached (today's card exists) | busy (another call for it is running) | symbol_limit | day_cap | month_cap | too_large.
create or replace function public.kb_summary_reserve(p_symbol text, p_model text, p_reserved numeric) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
  day_cap constant numeric := 0.30;
  month_cap constant numeric := 5.00;
  new_id bigint;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then raise exception 'not a symbol' using errcode = '22023'; end if;
  if p_reserved is null or p_reserved <= 0 or p_reserved > 0.02 then
    return jsonb_build_object('ok', false, 'reason', 'too_large'); end if;
  perform pg_advisory_xact_lock(hashtext('kaibot.summary_budget'));   -- serialises every reservation
  if exists (select 1 from kaibot.stock_summary where symbol = s and day = d) then
    return jsonb_build_object('ok', false, 'reason', 'cached'); end if;
  if exists (select 1 from kaibot.summary_call where symbol = s and day = d and settled_at is null
             and ts > now() - interval '2 minutes') then
    return jsonb_build_object('ok', false, 'reason', 'busy'); end if;
  if (select count(*) from kaibot.summary_call where symbol = s and day = d) >= 2 then
    return jsonb_build_object('ok', false, 'reason', 'symbol_limit'); end if;
  if kaibot.summary_spent(m0) + p_reserved > month_cap then
    return jsonb_build_object('ok', false, 'reason', 'month_cap'); end if;
  if kaibot.summary_spent(d) + p_reserved > day_cap then
    return jsonb_build_object('ok', false, 'reason', 'day_cap'); end if;
  insert into kaibot.summary_call (day, symbol, model, reserved_usd)
    values (d, s, left(coalesce(p_model, ''), 60), p_reserved) returning id into new_id;
  return jsonb_build_object('ok', true, 'call_id', new_id);
end $$;

-- Settle a call (its real cost, even when it failed) and, with a text, store the day's card. Returns the card or null.
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
  insert into kaibot.stock_summary (symbol, day, text, body, sources, model, cost_usd, call_id)
    values (c.symbol, c.day, p_text, coalesce(p_body, '{}'), coalesce(p_sources, '[]'),
            case when coalesce(p_ai, false) then c.model end, c.actual_usd, c.id)
    on conflict (symbol, day) do nothing;
  return public.kb_stock_summary(c.symbol);
end $$;

-- What the summaries spent, for the dashboard.
create or replace function public.kb_summary_spend() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  d date := (now() at time zone 'America/New_York')::date;
  m0 date := date_trunc('month', now() at time zone 'America/New_York')::date;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object('day', d, 'day_usd', kaibot.summary_spent(d), 'month_usd', kaibot.summary_spent(m0),
    'day_cap', 0.30, 'month_cap', 5.00,
    'calls_today', (select count(*) from kaibot.summary_call where day = d),
    'cards_today', (select count(*) from kaibot.stock_summary where day = d));
end $$;

revoke all on function public.kb_stock_summary(text), public.kb_summary_facts(text),
  public.kb_summary_reserve(text, text, numeric),
  public.kb_summary_settle(bigint, numeric, integer, integer, text, text, jsonb, jsonb, boolean),
  public.kb_summary_spend() from public, anon;
grant execute on function public.kb_stock_summary(text), public.kb_summary_facts(text),
  public.kb_summary_reserve(text, text, numeric),
  public.kb_summary_settle(bigint, numeric, integer, integer, text, text, jsonb, jsonb, boolean),
  public.kb_summary_spend() to authenticated;

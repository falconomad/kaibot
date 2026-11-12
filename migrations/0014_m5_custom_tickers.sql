-- 0014: M5 S68 (#96): custom ticker lists. Contract: docs/m5/contract.md ("Custom ticker lists").
-- Approved by the stakeholder on 11 Nov 2026 (decision 12, "Custom ticker lists"). Apply BEFORE merging PR #103,
-- and after 0013 (the engine lease, PR #105). It changes the tradable universe only for an owner who applies a
-- config in 'Only these' mode with a custom list.
--
-- 1. kaibot.ticker_check: one row per ticker and ET day (the daily cache). The dashboard asks with kb_check_ticker;
--    a worker with the service role (scripts/run_ticker_checks.py -> engine/tickercheck.py) fills in the answer
--    from the broker's asset record, completed daily SIP bars and Finnhub's industry. SQL cannot call the vendors.
-- 2. kaibot.ticker_list: named lists per owner (plain symbols; a list is a saved selection, not a permission).
-- 3. config_check accepts focus.custom = {name, tickers: [{symbol, etf}]}: at most 40 tickers, stored BY VALUE in
--    the config so an applied version never changes. A custom list is accepted ONLY with focus.mode = 'only'
--    (in All and Prefer mode Kaibot trades the catalogue and nothing else). 'Only' still needs >= 5 stocks.
-- 4. kb_save_config / kb_apply_config also require every custom ticker to be ACCEPTED (config_custom_check).
--    The engine checks again, point-in-time, at every session open; the owner cannot override either check.

-- ---------- the daily check cache ----------
create table if not exists kaibot.ticker_check (
  symbol     text not null check (symbol ~ '^[A-Z]{1,5}([.][A-Z])?$'),
  checked_on date not null,                       -- ET calendar day: the result is cached for that day
  status     text not null default 'pending' check (status in ('pending', 'accepted', 'refused', 'error')),
  reason     text,                                -- plain words when refused or error
  etf        text check (etf is null or etf in ('SPY','XLK','XLC','XLY','XLP','XLV','XLF','XLI','XLE','XLU','XLRE','XLB')),
  name       text,
  industry   text,
  detail     jsonb not null default '{}',         -- sessions, adv20_usd, last_close, exchange, as_of
  attempts   int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (symbol, checked_on),
  check (status <> 'accepted' or etf is not null)
);
create index if not exists ticker_check_pending on kaibot.ticker_check (updated_at) where status = 'pending';

-- ---------- named lists per owner ----------
create table if not exists kaibot.ticker_list (
  id         bigint generated always as identity primary key,
  owner      uuid not null,
  name       text not null check (length(name) between 1 and 40),
  symbols    text[] not null check (cardinality(symbols) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner, name)
);

alter table kaibot.ticker_check enable row level security;
alter table kaibot.ticker_list enable row level security;
revoke all on kaibot.ticker_check, kaibot.ticker_list from public, anon, authenticated;
grant select, update on kaibot.ticker_check to service_role;   -- the check worker; the browser uses the RPCs only

-- ---------- validation ----------
create or replace function kaibot.config_check(p_level text, p_body jsonb) returns text
language plpgsql stable set search_path = '' as $$
declare
  lim kaibot.level_limit;
  k text; v jsonb; n numeric; sec jsonb; sel text[]; mode text; first_m int; last_m int; on_count int;
  cust jsonb; tk jsonb;
  refs text[] := array['SPY','XLK','XLC','XLY','XLP','XLV','XLF','XLI','XLE','XLU','XLRE','XLB'];
  floors jsonb := '{"risk_per_trade_pct":0.10,"max_positions":1,"max_position_pct":5,"max_entries_per_day":1,
                    "daily_stop_pct":0.25,"run_drawdown_end_pct":2}';
  ints text[] := array['max_positions', 'max_entries_per_day'];
begin
  select * into lim from kaibot.level_limit where level = p_level;
  if lim.level is null then return 'unknown level'; end if;
  if p_body is null or jsonb_typeof(p_body) <> 'object' then return 'config must be an object'; end if;
  if length(p_body::text) > 8192 then return 'config too large'; end if;
  if coalesce(p_body->>'schema', '1') <> '1' then return 'unknown config schema'; end if;
  for k in select jsonb_object_keys(p_body) loop
    if k not in ('schema', 'preset', 'boldness', 'focus', 'risk', 'strategies', 'timing', 'advanced') then
      return 'unknown config setting: ' || k; end if;
  end loop;
  foreach k in array array['focus', 'risk', 'strategies', 'timing', 'advanced'] loop
    if p_body ? k and jsonb_typeof(p_body->k) <> 'object' then return k || ' must be an object'; end if;
  end loop;

  -- risk: floor <= value <= level
  sec := coalesce(p_body->'risk', '{}');
  for k, v in select * from jsonb_each(sec) loop
    if not floors ? k then return 'unknown risk setting: ' || k; end if;
    if jsonb_typeof(v) <> 'number' then return 'risk.' || k || ' must be a number'; end if;
    n := v::text::numeric;
    if k = any(ints) and n <> trunc(n) then return 'risk.' || k || ' must be a whole number'; end if;
    if n < (floors->>k)::numeric or n > (case k
        when 'risk_per_trade_pct' then lim.risk_per_trade_pct when 'max_positions' then lim.max_positions
        when 'max_position_pct' then lim.max_position_pct when 'max_entries_per_day' then lim.max_entries_per_day
        when 'daily_stop_pct' then lim.daily_stop_pct else lim.run_drawdown_end_pct end) then
      return 'risk.' || k || ' is outside the allowed range for this level';
    end if;
  end loop;

  -- strategies: only the level's; at least one on
  sec := coalesce(p_body->'strategies', '{}');
  for k, v in select * from jsonb_each(sec) loop
    if k not in ('dip_reversal', 'spy_late_momentum') then return 'unknown strategy: ' || k; end if;
    if jsonb_typeof(v) <> 'boolean' then return 'strategies.' || k || ' must be true or false'; end if;
    if v = 'true'::jsonb and not (k = any(lim.strategies)) then return k || ' is not part of this level'; end if;
  end loop;
  select count(*) into on_count from unnest(lim.strategies) s where coalesce((sec->>s)::boolean, true);
  if on_count = 0 then return 'at least one strategy must stay on'; end if;

  -- focus
  sec := coalesce(p_body->'focus', '{}');
  for k in select jsonb_object_keys(sec) loop
    if k not in ('themes', 'symbols', 'exclude', 'mode', 'custom') then return 'unknown focus setting: ' || k; end if;
  end loop;
  mode := coalesce(sec->>'mode', 'all');
  if mode not in ('all', 'prefer', 'only') then return 'focus.mode must be all, prefer or only'; end if;
  foreach k in array array['themes', 'symbols', 'exclude'] loop
    if sec ? k and (jsonb_typeof(sec->k) <> 'array' or jsonb_array_length(sec->k) > 100
                    or exists (select 1 from jsonb_array_elements(sec->k) e where jsonb_typeof(e) <> 'string')) then
      return 'focus.' || k || ' must be a list of names'; end if;
  end loop;
  if (select count(distinct e) from jsonb_array_elements_text(coalesce(sec->'exclude', '[]')) e) > 10 then
    return 'at most 10 stocks can be excluded'; end if;
  if exists (select 1 from jsonb_array_elements_text(coalesce(sec->'themes', '[]')) t
             where not exists (select 1 from kaibot.config_theme c where c.theme = t)) then return 'unknown theme'; end if;
  if exists (select 1 from jsonb_array_elements_text(coalesce(sec->'symbols', '[]') || coalesce(sec->'exclude', '[]')) s
             where not exists (select 1 from kaibot.config_universe u where u.symbol = s)) then
    return 'a focus symbol is not in the universe'; end if;
  -- S68: a custom ticker list, stored by value (symbol + sector ETF). Shape only here; kaibot.config_custom_check
  -- (called by kb_save_config / kb_apply_config) requires every ticker to be ACCEPTED by kb_check_ticker.
  if sec ? 'custom' then
    -- Approved 11 Nov 2026: custom tickers trade only in 'Only these' mode. All and Prefer keep the catalogue.
    if mode <> 'only' then
      return 'A custom list can only be used with ''Only these''. Choose ''Only these'' or remove the list.'; end if;
    cust := sec->'custom';
    if jsonb_typeof(cust) <> 'object' then return 'focus.custom must be an object'; end if;
    for k in select jsonb_object_keys(cust) loop
      if k not in ('name', 'tickers') then return 'unknown focus.custom setting: ' || k; end if;
    end loop;
    if cust ? 'name' and (jsonb_typeof(cust->'name') <> 'string' or length(cust->>'name') > 40) then
      return 'the list name must be text of at most 40 characters'; end if;
    if not cust ? 'tickers' or jsonb_typeof(cust->'tickers') <> 'array' or jsonb_array_length(cust->'tickers') = 0 then
      return 'a custom list needs at least one ticker'; end if;
    if jsonb_array_length(cust->'tickers') > 40 then return 'a custom list can hold at most 40 tickers'; end if;
    for tk in select * from jsonb_array_elements(cust->'tickers') loop
      if jsonb_typeof(tk) <> 'object' or (select count(*) from jsonb_object_keys(tk)) <> 2
         or not (tk ? 'symbol' and tk ? 'etf') then
        return 'each custom ticker needs a symbol and its sector fund'; end if;
      if jsonb_typeof(tk->'symbol') <> 'string' or (tk->>'symbol') !~ '^[A-Z]{1,5}([.][A-Z])?$' then
        return 'a custom ticker is not a valid US stock symbol'; end if;
      if (tk->>'symbol') = any(refs) then return (tk->>'symbol') || ' is a reference fund and cannot be traded'; end if;
      if exists (select 1 from kaibot.config_universe u where u.symbol = tk->>'symbol') then
        return (tk->>'symbol') || ' is already in the catalogue: pick it as a stock instead'; end if;
      if jsonb_typeof(tk->'etf') <> 'string' or not ((tk->>'etf') = any(refs)) then
        return (tk->>'symbol') || ' has an unknown sector fund'; end if;
    end loop;
    if (select count(distinct e->>'symbol') from jsonb_array_elements(cust->'tickers') e)
       <> jsonb_array_length(cust->'tickers') then return 'a custom ticker is listed twice'; end if;
  end if;
  select coalesce(array_agg(distinct x), '{}') into sel from (
    select c.symbol x from kaibot.config_theme c
      where c.theme in (select jsonb_array_elements_text(coalesce(sec->'themes', '[]')))
    union select jsonb_array_elements_text(coalesce(sec->'symbols', '[]'))
    union select e->>'symbol' from jsonb_array_elements(coalesce(sec->'custom'->'tickers', '[]')) e
  ) q where x not in (select jsonb_array_elements_text(coalesce(sec->'exclude', '[]')));
  if mode = 'only' and cardinality(sel) < 5 then return '''Only these'' needs at least 5 stocks'; end if;

  -- timing
  sec := coalesce(p_body->'timing', '{}');
  for k, v in select * from jsonb_each(sec) loop
    if k not in ('entry_start', 'entry_end', 'pause_losses', 'pause_minutes', 'vix_max', 'event_window_min') then
      return 'unknown timing setting: ' || k; end if;
    if k in ('entry_start', 'entry_end') then
      if jsonb_typeof(v) <> 'string' or (v #>> '{}') !~ '^[0-2][0-9]:[0-5][0-9]$' then return 'timing.' || k || ' must be HH:MM'; end if;
    elsif jsonb_typeof(v) <> 'number' then return 'timing.' || k || ' must be a number';
    end if;
  end loop;
  first_m := split_part(coalesce(sec->>'entry_start', '09:35'), ':', 1)::int * 60
             + split_part(coalesce(sec->>'entry_start', '09:35'), ':', 2)::int - 570;
  last_m := split_part(coalesce(sec->>'entry_end', '15:45'), ':', 1)::int * 60
            + split_part(coalesce(sec->>'entry_end', '15:45'), ':', 2)::int - 570;
  if not (5 <= first_m and first_m < last_m and last_m <= 375) or last_m - first_m < 30 then
    return 'entry window must sit inside 09:35-15:45 and be at least 30 minutes long'; end if;
  n := coalesce((sec->>'pause_losses')::numeric, 3);
  if n <> trunc(n) or n < 2 or n > 3 then return 'timing.pause_losses must be 2 or 3'; end if;
  n := coalesce((sec->>'pause_minutes')::numeric, 60);
  if n <> trunc(n) or n < 60 or n > 240 then return 'timing.pause_minutes must be 60..240'; end if;
  n := coalesce((sec->>'vix_max')::numeric, 35);
  if n < 20 or n > 35 then return 'timing.vix_max must be 20..35'; end if;
  n := coalesce((sec->>'event_window_min')::numeric, 30);
  if n <> trunc(n) or n < 30 or n > 90 then return 'timing.event_window_min must be 30..90'; end if;

  -- advanced: tighten only
  sec := coalesce(p_body->'advanced', '{}');
  for k, v in select * from jsonb_each(sec) loop
    if k not in ('min_price_usd', 'min_adv_usd', 'max_spread_bp') then return 'unknown advanced setting: ' || k; end if;
    if jsonb_typeof(v) <> 'number' then return 'advanced.' || k || ' must be a number'; end if;
  end loop;
  n := coalesce((sec->>'min_price_usd')::numeric, 5);
  if n < 5 or n > 1000 then return 'advanced.min_price_usd must be 5..1000'; end if;
  n := coalesce((sec->>'min_adv_usd')::numeric, 50000000);
  if n < 50000000 or n > 50000000000 then return 'advanced.min_adv_usd must be at least 50,000,000'; end if;
  n := coalesce((sec->>'max_spread_bp')::numeric, 5);
  if n < 1 or n > 5 then return 'advanced.max_spread_bp must be 1..5'; end if;
  return null;
exception when others then
  return 'config is malformed';  -- e.g. a number where text was expected: never an internal error to the client
end $$;
revoke all on function kaibot.config_check(text, jsonb) from public, anon, authenticated;

-- Latest check of a ticker within the last 7 ET days (a draft made on Friday can still be applied on Monday).
create or replace function kaibot.ticker_latest(p_symbol text) returns kaibot.ticker_check
language sql stable set search_path = '' as $$
  select c from kaibot.ticker_check c
  where c.symbol = p_symbol and c.checked_on >= (now() at time zone 'America/New_York')::date - 7
  order by c.checked_on desc limit 1
$$;
revoke all on function kaibot.ticker_latest(text) from public, anon, authenticated;

-- Every custom ticker of a config must be accepted, with the sector ETF the check gave it. Null = fine.
create or replace function kaibot.config_custom_check(p_body jsonb) returns text
language plpgsql stable set search_path = '' as $$
declare t jsonb; c kaibot.ticker_check; s text;
begin
  if jsonb_typeof(p_body->'focus'->'custom'->'tickers') is distinct from 'array' then return null; end if;
  for t in select * from jsonb_array_elements(p_body->'focus'->'custom'->'tickers') loop
    s := t->>'symbol';
    c := kaibot.ticker_latest(s);
    if c.symbol is null or c.status in ('pending', 'error') then
      return s || ' has not been checked yet. Check it before saving.'; end if;
    if c.status = 'refused' then return s || ' cannot be used: ' || coalesce(c.reason, 'it was refused'); end if;
    if c.etf is distinct from (t->>'etf') then return s || ' has a different sector fund than its check'; end if;
  end loop;
  return null;
end $$;
revoke all on function kaibot.config_custom_check(jsonb) from public, anon, authenticated;

-- ---------- RPCs ----------
-- kb_save_config / kb_apply_config: as in 0011, plus the accepted-ticker check.
create or replace function public.kb_save_config(p_run bigint, p_body jsonb) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare r kaibot.run; err text; d kaibot.config_draft;
begin
  r := kaibot.owner_run(p_run);
  if r.status = 'ended' then raise exception 'this run has ended' using errcode = '22023'; end if;
  err := coalesce(kaibot.config_check(r.level, p_body), kaibot.config_custom_check(p_body));
  if err is not null then raise exception '%', err using errcode = '22023'; end if;
  insert into kaibot.config_draft (run_id, body, updated_at) values (p_run, p_body, now())
    on conflict (run_id) do update set body = excluded.body, updated_at = now()
    returning * into d;
  return jsonb_build_object('ok', true, 'draft', jsonb_build_object('body', d.body, 'updated_at', d.updated_at));
end $$;

create or replace function public.kb_apply_config(p_run bigint, p_idem uuid) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare r kaibot.run; err text; body jsonb; v int; af timestamptz; c kaibot.config;
begin
  r := kaibot.owner_run(p_run);
  if r.status = 'ended' then raise exception 'this run has ended' using errcode = '22023'; end if;
  select * into c from kaibot.config where idem = p_idem;
  if c.version is not null then  -- idempotent retry
    return jsonb_build_object('version', c.version, 'applies_from', c.applies_from);
  end if;
  perform pg_advisory_xact_lock(hashtextextended('kb_apply_config:' || p_run, 0));
  select d.body into body from kaibot.config_draft d where d.run_id = p_run;
  if body is null then raise exception 'nothing to apply: no draft' using errcode = '22023'; end if;
  err := coalesce(kaibot.config_check(r.level, body), kaibot.config_custom_check(body));
  if err is not null then raise exception '%', err using errcode = '22023'; end if;
  af := kaibot.next_open_after(now());
  update kaibot.config set status = 'superseded'
    where run_id = p_run and status = 'applied' and applies_from > now();  -- one applied version per session
  select coalesce(max(version), 0) + 1 into v from kaibot.config where run_id = p_run;
  insert into kaibot.config (run_id, version, body, config_sha, applies_from, idem)
    values (p_run, v, body, encode(sha256(convert_to(body::text, 'UTF8')), 'hex'), af, p_idem);
  insert into kaibot.replay_job (run_id, config_version) values (p_run, v);
  delete from kaibot.config_draft where run_id = p_run;
  return jsonb_build_object('version', v, 'applies_from', af);
end $$;

create or replace function kaibot.ticker_view(c kaibot.ticker_check) returns jsonb
language sql immutable set search_path = '' as $$
  select jsonb_build_object('symbol', c.symbol, 'status', c.status, 'reason', c.reason, 'etf', c.etf, 'name', c.name,
    'industry', c.industry, 'in_catalogue', false, 'checked_on', c.checked_on, 'detail', c.detail)
$$;
revoke all on function kaibot.ticker_view(kaibot.ticker_check) from public, anon, authenticated;

-- May Kaibot trade this ticker? Answers from today's cache, or queues the check and returns `pending` (ask again).
create or replace function public.kb_check_ticker(p_symbol text) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  s text := upper(btrim(coalesce(p_symbol, '')));
  today date := (now() at time zone 'America/New_York')::date;
  c kaibot.ticker_check;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if s !~ '^[A-Z]{1,5}([.][A-Z])?$' then
    return jsonb_build_object('symbol', left(s, 12), 'status', 'refused', 'in_catalogue', false,
      'reason', 'That does not look like a US stock ticker (1 to 5 letters, for example PLTR).');
  end if;
  if s = any(array['SPY','XLK','XLC','XLY','XLP','XLV','XLF','XLI','XLE','XLU','XLRE','XLB']) then
    return jsonb_build_object('symbol', s, 'status', 'refused', 'in_catalogue', false,
      'reason', s || ' is a reference fund Kaibot compares stocks against, so it cannot be traded.');
  end if;
  if exists (select 1 from kaibot.config_universe u where u.symbol = s) then
    return jsonb_build_object('symbol', s, 'status', 'accepted', 'in_catalogue', true, 'reason', null);
  end if;
  select * into c from kaibot.ticker_check where symbol = s and checked_on = today;
  if c.symbol is not null and c.status <> 'error' then return kaibot.ticker_view(c); end if;
  if c.symbol is null and (select count(*) from kaibot.ticker_check where checked_on = today) >= 200 then
    raise exception 'Too many ticker checks today. Try again tomorrow.' using errcode = '22023';
  end if;
  insert into kaibot.ticker_check (symbol, checked_on) values (s, today)
    on conflict (symbol, checked_on) do update
      set status = 'pending', reason = null, attempts = 0, updated_at = now()   -- retry after an error
    returning * into c;
  return kaibot.ticker_view(c);
end $$;

-- The owner's named lists, each symbol with its latest check (or `unchecked`).
create or replace function public.kb_ticker_lists() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  return jsonb_build_object('max_tickers', 40, 'max_lists', 20, 'min_only', 5,
    'lists', coalesce((select jsonb_agg(jsonb_build_object('name', l.name, 'updated_at', l.updated_at,
        'symbols', (select jsonb_agg(
            case when exists (select 1 from kaibot.config_universe u where u.symbol = x.s)
                 then jsonb_build_object('symbol', x.s, 'status', 'accepted', 'in_catalogue', true)
                 when (kaibot.ticker_latest(x.s)).symbol is null
                 then jsonb_build_object('symbol', x.s, 'status', 'unchecked', 'in_catalogue', false)
                 else kaibot.ticker_view(kaibot.ticker_latest(x.s)) end order by x.i)
          from unnest(l.symbols) with ordinality x(s, i))) order by lower(l.name))
      from kaibot.ticker_list l where l.owner = (select auth.uid())), '[]'::jsonb));
end $$;

create or replace function public.kb_save_ticker_list(p_name text, p_symbols text[]) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare nm text := btrim(coalesce(p_name, '')); syms text[]; me uuid := (select auth.uid());
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  if length(nm) < 1 or length(nm) > 40 then
    raise exception 'Give the list a name of 1 to 40 characters.' using errcode = '22023'; end if;
  select coalesce(array_agg(s order by i), '{}') into syms from (
    select upper(btrim(x.s)) as s, min(x.i) as i from unnest(coalesce(p_symbols, '{}')) with ordinality x(s, i)
    group by 1) q;
  if cardinality(syms) < 1 then raise exception 'A list needs at least one ticker.' using errcode = '22023'; end if;
  if cardinality(syms) > 40 then
    raise exception 'A list can hold at most 40 tickers.' using errcode = '22023'; end if;
  if exists (select 1 from unnest(syms) s where s !~ '^[A-Z]{1,5}([.][A-Z])?$') then
    raise exception 'One of the tickers does not look like a US stock symbol.' using errcode = '22023'; end if;
  if syms && array['SPY','XLK','XLC','XLY','XLP','XLV','XLF','XLI','XLE','XLU','XLRE','XLB'] then
    raise exception 'Reference funds (SPY and the sector funds) cannot be in a list.' using errcode = '22023'; end if;
  if not exists (select 1 from kaibot.ticker_list where owner = me and name = nm)
     and (select count(*) from kaibot.ticker_list where owner = me) >= 20 then
    raise exception 'You can keep at most 20 lists. Delete one first.' using errcode = '22023'; end if;
  insert into kaibot.ticker_list (owner, name, symbols) values (me, nm, syms)
    on conflict (owner, name) do update set symbols = excluded.symbols, updated_at = now();
  return jsonb_build_object('ok', true, 'name', nm, 'symbols', to_jsonb(syms));
end $$;

create or replace function public.kb_delete_ticker_list(p_name text) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  delete from kaibot.ticker_list where owner = (select auth.uid()) and name = btrim(coalesce(p_name, ''));
  return jsonb_build_object('ok', true);
end $$;

revoke all on function public.kb_check_ticker(text), public.kb_ticker_lists(), public.kb_save_ticker_list(text, text[]),
  public.kb_delete_ticker_list(text) from public, anon;
grant execute on function public.kb_check_ticker(text), public.kb_ticker_lists(),
  public.kb_save_ticker_list(text, text[]), public.kb_delete_ticker_list(text) to authenticated;

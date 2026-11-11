-- 0011: M5 configuration panel (decision 12; stories S42, S43, S48). Contract: docs/m5/contract.md.
-- The owner's config can only make Kaibot pickier or smaller. SQL validates every save and apply (this file); the
-- engine validates again at the session open (engine/userconfig.py) and refuses the session on any failure.
-- The browser has no table access: owner-checked security-definer kb_* functions only.

-- ---------- reference data (mirrors config/profiles.yaml and config/universe_seed.yaml; a test checks they match) ----------
create table if not exists kaibot.level_limit (
  level text primary key,
  risk_per_trade_pct numeric not null, max_positions int not null, max_position_pct numeric not null,
  max_entries_per_day int not null, daily_stop_pct numeric not null, run_drawdown_end_pct numeric not null,
  strategies text[] not null
);
insert into kaibot.level_limit values
  ('conservative', 0.25, 3, 10, 10, 0.75, 4, array['dip_reversal']),
  ('balanced', 0.5, 5, 15, 20, 1.5, 8, array['dip_reversal', 'spy_late_momentum']),
  ('aggressive', 1.0, 8, 20, 30, 3.0, 12, array['dip_reversal', 'spy_late_momentum'])
on conflict (level) do update set risk_per_trade_pct = excluded.risk_per_trade_pct, max_positions = excluded.max_positions,
  max_position_pct = excluded.max_position_pct, max_entries_per_day = excluded.max_entries_per_day,
  daily_stop_pct = excluded.daily_stop_pct, run_drawdown_end_pct = excluded.run_drawdown_end_pct,
  strategies = excluded.strategies;

create table if not exists kaibot.config_theme (
  theme text not null, label text not null, symbol text not null, primary key (theme, symbol)
);
delete from kaibot.config_theme;
insert into kaibot.config_theme (theme, label, symbol)
select t.theme, t.label, unnest(t.syms) from (values
  ('hyperscalers', 'Hyperscalers', array['MSFT','AMZN','GOOGL','META','ORCL']),
  ('semiconductors', 'Semiconductors', array['NVDA','AVGO','AMD','INTC','QCOM','TXN']),
  ('big_tech', 'Big Tech', array['AAPL','MSFT','NVDA','GOOGL','AMZN','META','TSLA']),
  ('software', 'Software & IT', array['MSFT','ORCL','CRM','ADBE','CSCO']),
  ('media_telecom', 'Media & telecom', array['GOOGL','META','NFLX','DIS','TMUS']),
  ('consumer', 'Consumer', array['AMZN','TSLA','HD','MCD','NKE','LOW','SBUX','BKNG']),
  ('healthcare', 'Healthcare', array['UNH','JNJ','LLY','MRK','ABBV','PFE','TMO']),
  ('financials', 'Financials', array['JPM','BAC','WFC','GS','MS','V','MA']),
  ('industrials', 'Industrials', array['CAT','BA','GE','HON','UPS','RTX','DE','LIN']),
  ('energy', 'Energy', array['XOM','CVX','COP']),
  ('defensives', 'Defensives', array['PG','KO','PEP','COST','WMT','NEE','DUK','PLD','AMT'])
) as t(theme, label, syms);

create table if not exists kaibot.config_universe (symbol text primary key);
delete from kaibot.config_universe;
insert into kaibot.config_universe (symbol) select unnest(array[
  'AAPL','MSFT','NVDA','AVGO','ORCL','CRM','ADBE','AMD','CSCO','INTC','QCOM','TXN','GOOGL','META','NFLX','DIS','TMUS',
  'AMZN','TSLA','HD','MCD','NKE','LOW','SBUX','BKNG','PG','KO','PEP','COST','WMT','UNH','JNJ','LLY','MRK','ABBV','PFE',
  'TMO','JPM','BAC','WFC','GS','MS','V','MA','CAT','BA','GE','HON','UPS','RTX','DE','XOM','CVX','COP','NEE','DUK',
  'PLD','AMT','LIN']);

-- ---------- config versions, drafts, replay queue ----------
create table if not exists kaibot.config (
  run_id       bigint not null references kaibot.run(id),
  version      int not null check (version >= 1),
  body         jsonb not null,
  config_sha   text not null,
  status       text not null default 'applied' check (status in ('applied', 'superseded')),
  applies_from timestamptz not null,
  created_at   timestamptz not null default now(),
  idem         uuid not null unique,
  primary key (run_id, version)
);
create table if not exists kaibot.config_draft (
  run_id     bigint primary key references kaibot.run(id),
  body       jsonb not null,
  updated_at timestamptz not null default now()
);
create table if not exists kaibot.replay_job (
  id             bigint generated always as identity primary key,
  run_id         bigint not null references kaibot.run(id),
  config_version int not null,
  status         text not null default 'queued' check (status in ('queued', 'running', 'done', 'waiting_for_data', 'error')),
  result         jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (run_id, config_version)
);
alter table kaibot.trade add column if not exists config_version int not null default 0;
alter table kaibot.daily_result add column if not exists config_version int not null default 0;

-- An applied version is immutable: only applied -> superseded, and nothing is ever deleted.
create or replace function kaibot.config_guard() returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then raise exception 'config versions are never deleted' using errcode = '42501'; end if;
  if new.body is distinct from old.body or new.version <> old.version or new.run_id <> old.run_id
     or new.config_sha <> old.config_sha or new.created_at <> old.created_at or new.applies_from <> old.applies_from
     or not (old.status = 'applied' and new.status = 'superseded') then
    raise exception 'an applied config version is immutable' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists config_immutable on kaibot.config;
create trigger config_immutable before update or delete on kaibot.config
  for each row execute function kaibot.config_guard();

alter table kaibot.level_limit enable row level security;
alter table kaibot.config_theme enable row level security;
alter table kaibot.config_universe enable row level security;
alter table kaibot.config enable row level security;
alter table kaibot.config_draft enable row level security;
alter table kaibot.replay_job enable row level security;
revoke all on kaibot.level_limit, kaibot.config_theme, kaibot.config_universe, kaibot.config, kaibot.config_draft,
  kaibot.replay_job from public, anon, authenticated;
grant select on kaibot.level_limit, kaibot.config_theme, kaibot.config_universe, kaibot.config to service_role;
grant select, update on kaibot.replay_job to service_role;

-- ---------- validation (mirrors engine/userconfig.tighten; returns null when the body is a pure tightening) ----------
create or replace function kaibot.config_check(p_level text, p_body jsonb) returns text
language plpgsql stable set search_path = '' as $$
declare
  lim kaibot.level_limit;
  k text; v jsonb; n numeric; sec jsonb; sel text[]; mode text; first_m int; last_m int; on_count int;
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
    if k not in ('themes', 'symbols', 'exclude', 'mode') then return 'unknown focus setting: ' || k; end if;
  end loop;
  mode := coalesce(sec->>'mode', 'all');
  if mode not in ('all', 'prefer', 'only') then return 'focus.mode must be all, prefer or only'; end if;
  foreach k in array array['themes', 'symbols', 'exclude'] loop
    if sec ? k and (jsonb_typeof(sec->k) <> 'array' or jsonb_array_length(sec->k) > 100
                    or exists (select 1 from jsonb_array_elements(sec->k) e where jsonb_typeof(e) <> 'string')) then
      return 'focus.' || k || ' must be a list of names'; end if;
  end loop;
  if exists (select 1 from jsonb_array_elements_text(coalesce(sec->'themes', '[]')) t
             where not exists (select 1 from kaibot.config_theme c where c.theme = t)) then return 'unknown theme'; end if;
  if exists (select 1 from jsonb_array_elements_text(coalesce(sec->'symbols', '[]') || coalesce(sec->'exclude', '[]')) s
             where not exists (select 1 from kaibot.config_universe u where u.symbol = s)) then
    return 'a focus symbol is not in the universe'; end if;
  select coalesce(array_agg(distinct x), '{}') into sel from (
    select c.symbol x from kaibot.config_theme c
      where c.theme in (select jsonb_array_elements_text(coalesce(sec->'themes', '[]')))
    union select jsonb_array_elements_text(coalesce(sec->'symbols', '[]'))
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

-- next session open (09:30 ET) strictly after `t`; weekends skipped (holidays: the engine applies it at the real open)
create or replace function kaibot.next_open_after(t timestamptz) returns timestamptz
language sql immutable set search_path = '' as $$
  with base as (select (t at time zone 'America/New_York') as et),
  d as (select case when et::time < time '09:30' then et::date else et::date + 1 end as d0 from base),
  wd as (select case extract(isodow from d0) when 6 then d0 + 2 when 7 then d0 + 1 else d0 end as d1 from d)
  select (d1 + time '09:30') at time zone 'America/New_York' from wd
$$;

create or replace function kaibot.owner_run(p_run bigint) returns kaibot.run
language plpgsql stable security definer set search_path = '' as $$
declare r kaibot.run;
begin
  if not kaibot.is_owner() then raise exception 'not authorised' using errcode = '42501'; end if;
  select * into r from kaibot.run where id = p_run;
  if r.id is null then raise exception 'no such run' using errcode = '22023'; end if;
  return r;
end $$;
revoke all on function kaibot.owner_run(bigint) from public, anon, authenticated;

-- ---------- RPCs ----------
create or replace function public.kb_get_config(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare r kaibot.run; lim kaibot.level_limit;
begin
  r := kaibot.owner_run(p_run);
  select * into lim from kaibot.level_limit where level = r.level;
  return jsonb_build_object(
    'level', r.level,
    'bounds', jsonb_build_object(
      'risk_per_trade_pct', jsonb_build_object('min', 0.10, 'max', lim.risk_per_trade_pct),
      'max_positions', jsonb_build_object('min', 1, 'max', lim.max_positions),
      'max_position_pct', jsonb_build_object('min', 5, 'max', lim.max_position_pct),
      'max_entries_per_day', jsonb_build_object('min', 1, 'max', lim.max_entries_per_day),
      'daily_stop_pct', jsonb_build_object('min', 0.25, 'max', lim.daily_stop_pct),
      'run_drawdown_end_pct', jsonb_build_object('min', 2, 'max', lim.run_drawdown_end_pct),
      'pause_losses', jsonb_build_object('min', 2, 'max', 3),
      'pause_minutes', jsonb_build_object('min', 60, 'max', 240),
      'vix_max', jsonb_build_object('min', 20, 'max', 35),
      'event_window_min', jsonb_build_object('min', 30, 'max', 90),
      'min_price_usd', jsonb_build_object('min', 5, 'max', 1000),
      'min_adv_usd', jsonb_build_object('min', 50000000, 'max', 50000000000),
      'max_spread_bp', jsonb_build_object('min', 1, 'max', 5),
      'entry', jsonb_build_object('start', '09:35', 'end', '15:45'),
      'strategies', to_jsonb(lim.strategies)),
    'themes', (select jsonb_object_agg(theme, jsonb_build_object('label', label, 'symbols', syms)) from (
        select theme, min(label) as label, jsonb_agg(symbol order by symbol) as syms from kaibot.config_theme group by theme) t),
    'universe', (select jsonb_agg(symbol order by symbol) from kaibot.config_universe),
    'applied', (select jsonb_build_object('version', c.version, 'body', c.body, 'applies_from', c.applies_from,
                  'created_at', c.created_at) from kaibot.config c
                where c.run_id = p_run and c.status = 'applied' and c.applies_from <= now()
                order by c.version desc limit 1),
    'pending', (select jsonb_build_object('version', c.version, 'body', c.body, 'applies_from', c.applies_from,
                  'created_at', c.created_at) from kaibot.config c
                where c.run_id = p_run and c.status = 'applied' and c.applies_from > now()
                order by c.version desc limit 1),
    'draft', (select jsonb_build_object('body', d.body, 'updated_at', d.updated_at) from kaibot.config_draft d
              where d.run_id = p_run),
    'versions', coalesce((select jsonb_agg(jsonb_build_object('version', c.version, 'status', c.status,
                  'applies_from', c.applies_from, 'created_at', c.created_at) order by c.version desc)
                from kaibot.config c where c.run_id = p_run), '[]'::jsonb),
    'replays', coalesce((select jsonb_agg(jsonb_build_object('version', j.config_version, 'status', j.status,
                  'result', j.result, 'updated_at', j.updated_at) order by j.config_version desc)
                from kaibot.replay_job j where j.run_id = p_run), '[]'::jsonb)
  );
end $$;

create or replace function public.kb_save_config(p_run bigint, p_body jsonb) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare r kaibot.run; err text; d kaibot.config_draft;
begin
  r := kaibot.owner_run(p_run);
  if r.status = 'ended' then raise exception 'this run has ended' using errcode = '22023'; end if;
  err := kaibot.config_check(r.level, p_body);
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
  err := kaibot.config_check(r.level, body);
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

create or replace function public.kb_reset_config(p_run bigint) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare r kaibot.run;
begin
  r := kaibot.owner_run(p_run);
  delete from kaibot.config_draft where run_id = p_run;
  return jsonb_build_object('ok', true);
end $$;

-- S48: results per config chapter (version 0 = the level's defaults)
create or replace function public.kb_chapters(p_run bigint) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare r kaibot.run;
begin
  r := kaibot.owner_run(p_run);
  return coalesce((select jsonb_agg(x order by x.version) from (
    select v.version,
           coalesce((select c.applies_from from kaibot.config c where c.run_id = p_run and c.version = v.version),
                    r.created_at) as applies_from,
           (select min((t.entry_ts at time zone 'America/New_York')::date) from kaibot.trade t
             where t.run_id = p_run and t.config_version = v.version) as "from",
           (select max((t.entry_ts at time zone 'America/New_York')::date) from kaibot.trade t
             where t.run_id = p_run and t.config_version = v.version) as "to",
           s.n as n_trades, round(coalesce(s.pnl, 0), 2) as pnl_usd, round(s.mean_r, 4) as mean_r,
           case when s.n > 1 and s.sd_r > 0 then round(s.mean_r / (s.sd_r / sqrt(s.n::numeric)), 3) end as t_stat,
           s.n < 30 as too_early
    from (select 0 as version union select c.version from kaibot.config c
            where c.run_id = p_run and c.status = 'applied'
          union select distinct t.config_version from kaibot.trade t where t.run_id = p_run) v
    cross join lateral (
      select count(*)::int as n, sum(t.pnl_usd) as pnl, avg(t.pnl_usd / nullif(t.risk_usd, 0)) as mean_r,
             stddev_samp(t.pnl_usd / nullif(t.risk_usd, 0)) as sd_r
      from kaibot.trade t where t.run_id = p_run and t.config_version = v.version and t.exit_ts is not null) s
  ) x), '[]'::jsonb);
end $$;

revoke all on function public.kb_get_config(bigint), public.kb_save_config(bigint, jsonb),
  public.kb_apply_config(bigint, uuid), public.kb_reset_config(bigint), public.kb_chapters(bigint) from public, anon;
grant execute on function public.kb_get_config(bigint), public.kb_save_config(bigint, jsonb),
  public.kb_apply_config(bigint, uuid), public.kb_reset_config(bigint), public.kb_chapters(bigint) to authenticated;

-- The gate (0010) judges one frozen configuration: the random baseline records which config it was drawn under.
alter table kaibot.gate_random add column if not exists config_version int not null default 0;

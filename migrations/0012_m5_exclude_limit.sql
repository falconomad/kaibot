-- 0012: M5 S53 (#69): at most 10 excluded stocks (config/presets.yaml limits.max_excluded). Only config_check changes;
-- exclusions already apply in every focus mode (engine/userconfig.Rules.excluded).
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
  if (select count(distinct e) from jsonb_array_elements_text(coalesce(sec->'exclude', '[]')) e) > 10 then
    return 'at most 10 stocks can be excluded'; end if;
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

-- 0027 (#324; Product, 18 Nov 2026): a stock-summary call that cost more than it reserved is flagged. After 0026.
-- kb_summary_settle (0023) records the call's TRUE cost (up to $0.999999), and it still does: the lifetime cap
-- (0026) counts it as it is. The dashboard's function (apps/web/api/summary.ts, #324) now sizes max_tokens from the
-- reservation, so a settle above it means the input was denser than its estimate; that is now visible.
--
-- Additive: one generated column (computed from the row, no data written by anyone) and kb_summary_spend re-created
-- with a count of flagged calls. No delete, drop or truncate. Any time, also while the engine runs.
--
-- Check after applying (read-only):
--   select count(*) from kaibot.summary_call where over_reservation;     -- normally 0
--   select public.kb_summary_spend()->'over_reservation_calls';          -- as the owner

alter table kaibot.summary_call add column if not exists over_reservation boolean
  generated always as (actual_usd is not null and actual_usd > reserved_usd) stored;

-- 0026's kb_summary_spend, plus the number of calls settled above their reservation.
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
    'cards_today', (select count(*) from kaibot.stock_summary where day = d),
    'lifetime_usd', round(kaibot.ai_lifetime_spent(300), 6), 'lifetime_cap', 150.0,
    'over_reservation_calls', (select count(*) from kaibot.summary_call where over_reservation));
end $$;

revoke all on function public.kb_summary_spend() from public, anon;
grant execute on function public.kb_summary_spend() to authenticated;

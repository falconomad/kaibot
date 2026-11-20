-- 0034 (#384; Backend-quant, 20 Nov 2026): the minute ledger row says what the multi-day book holds.
--
-- NOT APPLIED (docs/ops/migrations_pending.md, step 20). Numbered 0034: 0033 is kept for the kb_health migration
-- another team is adding at the same time; apply 0033 first if it exists by then, else this one alone is fine (it
-- does not depend on it).
--
-- What changes. One nullable jsonb column on kaibot.ledger (the engine's one row a minute). The engine fills it with
-- the multi-day book: the ledger's cash, and per sleeve (rule, symbol) its shares and the price this row's equity
-- counts it at, with that price's time and source (contract: issue #384, docs/ops/ledger.md "The minute row").
-- `positions` keeps its meaning (day positions only), so the dashboard never sees a multi-day holding twice.
-- kb_overview (0007) already returns the whole row (`to_jsonb(l)`): no function changes.
--
-- The engine does not depend on it: until it is applied the replicator sends the minute rows without `multi_day`
-- (the local journal keeps the full row), reports it once (event `multi_day_column_missing`) and tries the column
-- again about once an hour, so it switches by itself after the apply, no restart.
--
-- Additive: `add column if not exists` only. No delete, drop or truncate; old rows get null. Any time, also while
-- the engine runs: dashboard only.
--
-- Check after applying (read-only):
--   select data_type, is_nullable from information_schema.columns
--    where table_schema = 'kaibot' and table_name = 'ledger' and column_name = 'multi_day';   -- jsonb | YES

alter table kaibot.ledger add column if not exists multi_day jsonb;

comment on column kaibot.ledger.multi_day is
  '#384: the multi-day book in this minute: {v, cash_ledger, invested, settling[], positions[{rule, symbol, qty, '
  'avg_cost, mark, mark_ts, mark_from, value}]}. Never part of `positions` (day positions only). Null on old rows '
  'and for an engine without the daily path that holds no multi-day sleeve.';

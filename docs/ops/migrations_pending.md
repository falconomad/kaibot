# Pending production migrations

Owner: backend-quant. Last checked against main on 17 Nov 2026 (0001-0022 all applied; nothing pending). Only the stakeholder applies production migrations
(or Product on his word).

## What production has now

- 0001-0013: applied.
- 0014-0018: **applied by Product on 13 Nov 2026** (steps 1-5 below: the rest of 0014, then 0015, 0016, 0017, 0018).
  Every read-only check below passed: `strategy_registry` has 4 rows, the four `kaibot_deadman_*` jobs are scheduled,
  the ledger tables exist, 6 `jseq` columns, 0 applied Balanced/Hunter configs with dip off.
- 0019: **applied by Product on 13 Nov 2026, about 18:55 CET** (step 6 below; the stakeholder delegated the decision).
  Its read-only checks passed: one pg_cron job `kaibot_deadman` (the four `kaibot_deadman_*` jobs are gone).
  Shorting off on the paper account is done by `./scripts/go_live_paper.sh` (`./scripts/alpaca_no_shorting.sh`).
- 0020: **applied by Product on 17 Nov 2026, about 13:49 CET** (step 7 below). The multi-day experiment of #133 itself
  needs no database change (its caps and label are in `config/profiles.yaml` and `strategies/registry.py`, and reach the
  dashboard in the heartbeat's `detail.experiments`).
- 0021: **applied by Product on 17 Nov 2026, about 13:53 CET** (step 8 below).
- 0022: **applied by Product on 17 Nov 2026, 21:16 UTC (16:16 ET, after the close)** (step 9 below; PR #295, keys from
  QA-Ops and Backend-quant on eaf0cd4). Checked: both function bodies in the database match the file byte for byte
  (md5), neither runs for anon or authenticated, one pg_cron job unchanged. `deadman_split_like(80, 40)` = 2,
  `(50, 40)` = null; `(4, 40)` = 0.125, not 0.1: 1/8 comes before 1/10 in the ratio order and 40 x 1/8 = 5 is within
  the one-share tolerance. Harmless (the stop is capped at the 4 shares held); the comment's expected value was off.

## Apply in this order

Apply them one after the other in the same sitting. Each step is idempotent, so if one fails you can fix it and
run it again. Do not reorder them.

| # | What to apply | What it does | Supabase asks to confirm? |
|---|---------------|--------------|---------------------------|
| 1 | `migrations/0014_m5_custom_tickers.sql`, **lines 244-268 and 351-362 only** (`kb_apply_config`, `kb_delete_ticker_list`, the final revoke/grant) | Apply checks custom tickers again; owners can delete a saved list; the ticker RPCs get their grants | **Yes**: `delete from kaibot.config_draft` (inside `kb_apply_config`) and `delete from kaibot.ticker_list` (inside `kb_delete_ticker_list`). Both run only when the RPC is called, never at apply time |
| 2 | `migrations/0015_m7_trial_registry.sql` (whole file) | Append-only `kaibot.trial_registry`: one row per research run, so every engine reads one trial count | **Yes**: `drop trigger if exists` (it re-creates the two append-only triggers) |
| 3 | `migrations/0016_m7_strategy_registry.sql` (whole file) | `kaibot.strategy_registry`; SPY late momentum leaves Balanced and Hunter; `config_check` reads strategy names from the registry and keeps 0014's custom-ticker check | No: no delete, drop or truncate (the registry seed is an upsert) |
| 4 | `migrations/0017_deadman_early_close.sql` (whole file) | Off-box dead-man knows early closes and holidays (12:56 ET on early-close days, never on a holiday) | **Yes**: `cron.unschedule(...)` and `net.http_delete(...)` (the paper flatten call, only when the dead-man fires) |
| 5 | `migrations/0018_m7_ledger.sql` (whole file; PR #160, once merged) | The ledger's tables (`ledger_day`, `ledger_position`), engine-assigned ids and `jseq` for idempotent replication from the local journal, `kb_report` start equity from the mark-to-market close, and `kb_book` / `kb_equity_series` for the multi-day dashboard | No: only `alter`, `add column if not exists`, `create ... if not exists` and `create or replace` |
| 6 | `migrations/0019_deadman_overnight.sql` (whole file; PR for #120, once merged). **Before the first multi-day position is held overnight**; it can wait until then | The off-box dead-man no longer flattens the whole account: while the engine is silent it cancels entry orders, verifies (re-creates) every multi-day protective stop and raises the alarm; in the last 4 minutes it sells intraday shares only. One pg_cron job `kaibot_deadman` replaces the four `kaibot_deadman_*` jobs (docs/ops/failsafe.md) | **Yes**: `cron.unschedule(...)`, `net.http_delete(...)` and `net.http_post(...)` (paper orders, only when the dead-man acts). **First** turn shorting off on the paper account: Alpaca dashboard, paper account, Account Configurations, Shorting off (or `PATCH /v2/account/configurations {"no_shorting": true}`); check `GET /v2/account/configurations` gives `"no_shorting": true` and `GET /v2/account` gives `shorting_enabled: false` |
| 7 | `migrations/0020_m7_multiday_dashboard.sql` (whole file; PR for #229, once merged). Any time, also while the engine runs | The multi-day dashboard's data: `kb_book` positions also carry `sell_when` and `sector`; new `kb_multiday_plan` (the next close's plan and its orders) and `kb_book_closed` (sold holdings). Read-only functions, owner-checked, no table or row changes | No: only `create or replace function`, `revoke` and `grant` |
| 8 | `migrations/0021_kb_health_multiday_events.sql` (whole file; PR for #266's events). Independent of step 7 (0020, #247): either order. Any time: dashboard read only | `kb_health` also returns the live daily path's events, among them the corporate-action skips the dashboard now labels (`multi_day_ca_skip`, `multi_day_ca_unread`, #271/#273), the #207 multi-day events and #266's adoption events. Same function as 0007 otherwise, owner-only | No: one `create or replace function`, then the revoke/grant of 0007 |
| 9 | `migrations/0022_deadman_exdate.sql` (whole file; PR for #291). Outside 09:30-16:00 ET (the dead-man acts in market hours). After 0019 | The off-box dead-man on an ex-date with the engine down since before the open: a broker count that is the book's times a split's ratio is kept as multi-day (nothing of it sold at 15:56), a reverse split's stops cover at most the broker's shares, a re-created stop on a split or renamed holding covers the kept shares at the emergency price (never the book's old-basis stop), and a renamed holding (a gone multi-day symbol's exact shares in an unexplained position) is kept. One helper `kaibot.deadman_split_like`; `deadman_act` re-created with 0019's body plus the #291 lines | No: `create or replace function` and `revoke` only (the function's `net.http_*` calls are 0019's, run only when the dead-man acts) |

0020 is #247's (multi-day dashboard data, open). 0020 and 0021 do not depend on each other: apply them in either order.

To get step 1 as one SQL text: `sed -n '244,268p;351,362p' migrations/0014_m5_custom_tickers.sql`.
Running the whole 0014 file again instead is also safe (every statement is `if not exists` or `create or replace`),
**but only before step 3**: 0014's `config_check` has the old hard-coded strategy list, and 0016 replaces it.

## Never

- Never apply 0014's `config_check` after 0016. 0016's `config_check` is 0014's with only the strategies block
  replaced; going back would restore the hard-coded list (`tests/test_migrations_config_check.py` guards the files,
  not the database).

## Checks after applying (read-only)

```sql
-- step 1: the new apply function runs the custom-ticker check
select prosrc like '%config_custom_check%' from pg_proc where proname = 'kb_apply_config';          -- true
select count(*) from pg_proc where proname = 'kb_delete_ticker_list';                              -- 1
-- step 3: config_check keeps custom tickers and reads the registry
select pg_get_functiondef('kaibot.config_check(text,jsonb)'::regprocedure) ~ 'config_check_strategies'
   and pg_get_functiondef('kaibot.config_check(text,jsonb)'::regprocedure) ~ 'focus.custom';         -- true
select name, retired from kaibot.strategy_registry order by position;                              -- 4 rows
-- step 5: the ledger tables exist and the replica can take the journal's ids
select count(*) from pg_tables where schemaname = 'kaibot' and tablename in ('ledger_day', 'ledger_position'); -- 2
select count(*) from information_schema.columns where table_schema = 'kaibot' and column_name = 'jseq';      -- 6
-- step 8: kb_health returns the corporate-action skips
select prosrc like '%multi_day_ca_skip%' and prosrc like '%multi_day_ca_unread%' from pg_proc where proname = 'kb_health'; -- true
-- step 6: one dead-man job, and it knows the multi-day rules
select jobname, schedule from cron.job where jobname like 'kaibot_deadman%';    -- kaibot_deadman | * 13-21 * * 1-5
select count(*) from kaibot.strategy_holding where holding = 'multi_day';                          -- 3
-- step 9 (0022): the dead-man knows split ratios
select kaibot.deadman_split_like(80, 40), kaibot.deadman_split_like(50, 40);                    -- 2 | (null)  (checked 17 Nov)
select prosrc like '%deadman_split_like%' from pg_proc where proname = 'deadman_act';           -- true
-- step 7: the dashboard RPCs exist, owner-only
select count(*) from pg_proc where proname in ('kb_multiday_plan', 'kb_book_closed', 'md_decision_at');   -- 3
select has_function_privilege('anon', 'public.kb_multiday_plan(bigint)', 'execute');              -- false
-- QA-Ops follow-up from #156: an applied Balanced/Hunter config with dip off must be re-applied with dip on
select run_id, version from kaibot.config c join kaibot.run r on r.id = c.run_id
 where c.status = 'applied' and r.level in ('balanced', 'aggressive')
   and c.body->'strategies'->>'dip_reversal' = 'false';                                             -- 0 rows
```

Step 5 can wait: an engine on the #160 code without 0018 keeps writing the tables the dashboard reads today
(database ids, no ledger tables; `python -m kaibot.engine --check` shows `"schema": "pre-0018"`). After applying
it, restart the engine once to switch (docs/ops/ledger.md, "Migrations to apply").

The whole sequence (simulated production state, then steps 1-5, then 0018 again) was run on a scratch
Postgres 16 (0001-0013 with Supabase stubs: roles, `auth.users`, vault, `cron`/`net` stand-ins); `tests/test_config_sql.py` (SQL and Python validators agree on 1500 random configs) passed against it.

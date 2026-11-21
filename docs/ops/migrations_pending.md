# Pending production migrations

Owner: backend-quant. Last checked against main on 20 Nov 2026 (0001-0032 applied; 0033 pending). Only the stakeholder applies production migrations
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
- 0023: **applied in production** (Product's note on #315, 18 Nov 2026; step 10 below).
- 0024: **applied in production** (Product's note on #315, 18 Nov 2026; step 11 below; #321).
- 0025: **applied by Product on 18 Nov 2026, 21:2x UTC (after the close)** (step 12 below; PR #315, #309).
- 0026: **applied by Product on 18 Nov 2026, after 0025** (step 13 below; #319, PR #322). Checked: `kaibot.ai_spend()` = {total 0, cap 150}; `llm_reserve_lifetime` not executable by authenticated; `kb_llm_budget` not by anon.
- 0027: **applied by Product on 18 Nov 2026, about 22:45 UTC** (step 14 below; PR #325).
- 0028: **applied by Product on 19 Nov 2026, 00:3x UTC (after the close; stakeholder: "merge everything now")** (step 15 below; PR #339). The constraint was named ledger_position_stop_state_check, as expected.
- 0029: **applied by Product on 19 Nov 2026, 09:1x UTC** (step 16 below). Check: kb_health lists the three types.
- 0030: **applied by Product on 19 Nov 2026, 09:2x UTC (04:2x ET)** (step 17 below). deadman_act md5 = 23e84f55be53bbf7a0286648907b71b8, the body in the file byte for byte (applied as 0022's body plus the one line, guarded by both md5s); security definer, search_path kaibot, pg_temp; not executable by authenticated; one cron job unchanged.
- 0031: **applied by Product on 19 Nov 2026, 21:1x UTC (after the close)** (step 18 below). Check: kb_health lists news_feed_unhealthy and news_feed_ok.
- 0032: **applied by Product on 20 Nov 2026, about 17:0x UTC** (step 19 below; PRs #394, #395). Check: one policy `kb owner receives prices` (SELECT, {authenticated}) on realtime.messages; `kb_price_stream_reader()` not executable by anon.
- 0033: **pending (NOT APPLIED)** (step 20 below; dashboard read only, any time; the PR for kb_health's engine health events, #392 item 4).

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
| 10 | `migrations/0023_stock_summary.sql` (whole file; the stock-summaries PR). Any time, also while the engine runs: dashboard only, additive | Stock summaries for the dashboard: tables `kaibot.summary_call` (each model call's reservation and real cost) and `kaibot.stock_summary` (one card per stock and ET day), and the owner-only definer functions `kb_stock_summary`, `kb_summary_facts`, `kb_summary_reserve`, `kb_summary_settle`, `kb_summary_spend` that `apps/web/api/summary.ts` calls with the owner's session. The caps ($0.30 a day, $5 a month, $0.02 a call, 2 calls per symbol and day) are in the SQL. No engine table or function changes | No: `create table if not exists`, `create or replace function`, `revoke` and `grant` only |
| 11 | `migrations/0024_kb_ticker_check_answer.sql` (whole file; the ticker check on Vercel). Any time, also while the engine runs: dashboard only, independent of 0023 | One owner-only SECURITY DEFINER function `kb_ticker_check_answer`: `apps/web/api/check-ticker.ts` writes a 'My lists' check's answer into today's `pending` or `error` row of `kaibot.ticker_check` (validated; never overwrites an answer). Until it is applied the function answers "database update not applied" and the row waits for the Mac worker | No: one `create or replace function`, then revoke/grant |
| 12 | `migrations/0025_llm_budget_caps.sql` (whole file; PR #315 for #309). Any time, also while the engine runs: dashboard read only | `public.kb_llm_budget` (0009) shows the Stage 1 LLM caps: $1/day, $20/month alert, $25/month hard (were written in as $3 / $150 / $190), and adds `lifetime_usd` and `caps.lifetime_hard` ($150, the sum of `kaibot.budget_day`). The engine does not need it: `agents/gateway.Gateway` holds the caps itself through the existing `kaibot.llm_reserve`. Fields the dashboard reads keep their names | No: one `create or replace function`, then `revoke` and `grant` |
| 13 | `migrations/0026_ai_lifetime_cap.sql` (whole file; PR for #319). After 0023 and 0025. Any time, also while the engine runs | One $150 lifetime cap across every AI dollar: the engine's LLM ledger (`kaibot.budget_day` + open `llm_call` reservations) and the stock summaries (`kaibot.summary_call`). New `kaibot.ai_lifetime_spent`, `kaibot.ai_spend` (the engine's read) and `kaibot.llm_reserve_lifetime` (the engine's reservation with the cap checked inside today's `budget_day` row lock), service role only. `kb_summary_reserve` takes the same row lock and refuses `lifetime_cap`; `kb_summary_spend` and `kb_llm_budget` show the combined `lifetime_usd`. An engine without the #319 code keeps working (it calls `llm_reserve`, unchanged) | No: `create or replace function`, `revoke` and `grant` only. Its functions write only inside a reservation (`insert into kaibot.budget_day ... on conflict do nothing`, the `state` update 0008 already makes) |
| 14 | `migrations/0027_summary_over_reservation.sql` (whole file; PR for #324). After 0026. Any time, also while the engine runs: dashboard only, additive | A stock-summary call settled above its reservation is flagged: generated column `kaibot.summary_call.over_reservation` (`actual_usd > reserved_usd`), counted in `kb_summary_spend().over_reservation_calls`. The true cost is still recorded as it is | No: `alter table ... add column if not exists` (a generated column, computed from each row), `create or replace function`, `revoke` and `grant` |
| 15 | `migrations/0028_stop_none_by_design.sql` (whole file; PR #339). After 0020. Any time, also while the engine runs: additive | `ledger_position.stop_state` also allows `none_by_design` (a sleeve of a rule whose pre-registration has no stop: the momentum rules exit by their target list), and `kb_book` gives each position `stop_state` and `exit` ("target_list" or "broker_stop"), so the dashboard's stop alarm counts only sleeves that should have a broker stop. The engine writes `none_by_design` only once the database takes it (until then it writes `missing`, as now). The dead-man never reads `stop_state` | **Yes**: `alter table ... drop constraint if exists` (the check constraint, re-added at once with one more value; no row changes), then `create or replace function`, `revoke` and `grant` |
| 16 | `migrations/0029_kb_health_engine_followup_events.sql` (whole file; the PR for kb_health's follow-up events). After 0021. Any time, also while the engine runs: dashboard read only | `kb_health` also returns `multi_day_unfilled` (an order the venue let expire at the close with shares left, #333/#337), `daily_note` (#311/#318) and `multi_day_review_shadow` (the pre-open reviewer's observer verdicts, #310/#317), which the dashboard reads since #349. 0021's function otherwise, owner-only; the engine does not call it | No: one `create or replace function`, then the revoke/grant of 0021 |
| 17 | `migrations/0030_deadman_planned_row_not_gone.sql` (whole file; the PR for #344). After 0022. **Outside 09:30-16:00 ET** (the dead-man acts in market hours); independent of 0029 | The dead-man's #291 rename rule leaves out rows opened in their own session (a planned buy written before the 15:50 send that never filled), so an unexplained position with exactly the planned shares is sold in the flatten window instead of kept with an emergency stop. `deadman_act` is 0022's body plus that one line; the watchdog (`failsafe.plan`) follows the same rule | No: one `create or replace function`, then 0022's revokes for it (the function's `net.http_*` calls are 0019's, run only when the dead-man acts) |
| 18 | `migrations/0031_kb_health_news_feed_events.sql` (whole file; the PR for kb_health's news events). After 0029. Any time, also while the engine runs: dashboard read only | `kb_health` also returns `news_feed_unhealthy` and `news_feed_ok`, the engine's news poller's health events (#354, PR #355), which Health.tsx labels. 0029's function otherwise, owner-only; the engine does not call it | No: one `create or replace function`, then the revoke/grant of 0021 |
| 19 | `migrations/0032_price_stream_channel.sql` (whole file; the price-stream PR). Any time, also while the engine runs: dashboard only, additive. Then, in the Supabase dashboard: Realtime > Settings > "Allow public access" **off** (the dashboard uses no public channel) | Only the owner may receive the streamed prices: one SELECT policy on `realtime.messages` for `authenticated`, topic `kb:prices`, broadcasts only, `kaibot.is_owner()` through the definer wrapper `public.kb_price_stream_reader()` (0002 revokes `is_owner` from authenticated). No INSERT policy (browsers cannot send), no table or row changes; `scripts/price_stream.py` sends with the service key and stores nothing | No: `create or replace function`, `revoke`/`grant`, and `create policy` inside an "if not exists" block (no drop) |
| 20 | `migrations/0033_kb_health_engine_health_events.sql` (whole file; the PR for kb_health's engine health events). After 0031. Any time, also while the engine runs: dashboard read only | `kb_health` also returns 15 health events the engine sends that the list left out, among them `multi_day_carry` (#366) and `corporate_actions_error` (#392), plus `ledger_mismatch`, `reconcile_blocked`, `replica_conflict`, `watchdog_protect` and the others named in the file's header. 0031's function otherwise, owner-only; the engine does not call it. Health.tsx shows an unlabelled type by its name until Design labels it | No: one `create or replace function`, then the revoke/grant of 0021 |

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
-- step 11 (0024): the answer function is owner-only and not for anon
select p.prosecdef, has_function_privilege('anon', p.oid, 'execute'), has_function_privilege('authenticated', p.oid, 'execute')
  from pg_proc p where p.proname = 'kb_ticker_check_answer';                                   -- true | false | true
-- step 12 (0025): the dashboard shows the Stage 1 caps and the lifetime total
select prosrc like '%lifetime_hard%' and prosrc like '%llm_budget_state(1.0,%' from pg_proc where proname = 'kb_llm_budget'; -- true
-- step 13 (0026): one lifetime total for both ledgers, engine functions service role only
select (kaibot.ai_spend()->>'total')::numeric = kaibot.ai_lifetime_spent(300);                       -- true
select has_function_privilege('authenticated', 'kaibot.llm_reserve_lifetime(text, text, numeric, numeric, numeric, numeric, integer)', 'execute'); -- false
-- step 14 (0027): calls settled above their reservation (normally 0)
select count(*) from kaibot.summary_call where over_reservation;                                   -- 0
-- step 15 (0028): the new stop state is allowed and kb_book names each position's exit kind
select pg_get_constraintdef(oid) like '%none_by_design%' from pg_constraint where conname = 'ledger_position_stop_state_check'; -- true
select prosrc like '%''exit''%' from pg_proc where proname = 'kb_book';                                -- true
-- step 16 (0029): kb_health returns the unfilled orders, the daily note and the shadow verdicts
select prosrc like '%multi_day_unfilled%' and prosrc like '%''daily_note''%' and prosrc like '%multi_day_review_shadow%'
  from pg_proc where proname = 'kb_health';                                                          -- true
-- step 17 (0030): the rename rule skips rows opened in their own session; one dead-man job, unchanged
select prosrc like '%opened_session < lp.session%' and prosrc like '%deadman_split_like%'
  from pg_proc where proname = 'deadman_act';                                                        -- true
select jobname, schedule from cron.job where jobname like 'kaibot_deadman%';    -- kaibot_deadman | * 13-21 * * 1-5
-- step 18 (0031): kb_health returns the news poller's health events
select prosrc like '%news_feed_unhealthy%' and prosrc like '%news_feed_ok%' and prosrc like '%multi_day_review_shadow%'
  from pg_proc where proname = 'kb_health';                                                          -- true
-- step 20 (0033): kb_health returns the engine's health events (multi_day_carry, corporate_actions_error, ...)
select prosrc like '%multi_day_carry%' and prosrc like '%corporate_actions_error%' and prosrc like '%news_feed_ok%'
  from pg_proc where proname = 'kb_health';                                                          -- true
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

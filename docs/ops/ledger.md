# Ledger and local journal (story S77, issue #117)

Decision 15 section 3 (`Ledger`), audit B1 items 4, 5, 13, 14 and D5, D8, H2. Code: `src/kaibot/engine/journal.py`,
`src/kaibot/engine/ledger.py`, `src/kaibot/engine/core.py`. Migration: `migrations/0018_m7_ledger.sql`.

## What changed

| Before | Now |
|---|---|
| Positions in a dict keyed by symbol: two rules could not hold one stock | `Engine.pos` is keyed by (strategy, symbol); drift compares the broker's shares with the sum over the rules; closing one rule's position sells only its shares |
| Equity = start + realised today + unrealised since the fill | the same intraday, but unrealised P&L of a position held over a close counts from that close's mark, and dividends count |
| Start equity = pot + sum of realised daily results | yesterday's mark-to-market close from the ledger (identical to the old number when no position is held over the close, so intraday sizing does not move) |
| No equity series; daily result is realised P&L only | `ledger_day` per session: start and close equity, cash, invested, dividends, exposure, reconciliation; `ledger_position` per rule and symbol |
| Every write to Supabase on the order path (4 tries x 20 s) and the order row written after the broker call | a local append-only journal written first; Supabase replicated in the background |
| A restart after the close never finalised the day | the next closed tick finalises the last session if nobody did |
| No dividends or splits | booked to every rule that holds the symbol |

**Live trading behaviour:** accounting changes (start equity, the day's equity the daily stop reads when a
position is held over a close, dividends). Intraday decisions are unchanged: the engine golden
(`tests/test_spine_golden.py`) is identical except one count (the new `ledger_day` rows). No rule holds overnight
yet; a position is only held over a close when the engine was off at 15:55.

## The journal

- File: `$KAIBOT_DATA_DIR/journal.sqlite` (default `data/live/journal.sqlite`), SQLite in WAL mode with
  `synchronous=FULL`. One table `entry(seq, ts, kind, body)`; triggers refuse every update and delete.
- **Durability** (M9 1.18, #443). Every time the file is opened the settings are read back: `journal_mode` must be
  `wal` and `synchronous` must be FULL; on macOS `fullfsync` and `checkpoint_fullfsync` are switched on and must
  read 1 (without them macOS's fsync stops at the drive's cache). SQLite does not raise when a setting is not
  taken (a network or FAT volume answers `delete` to write-ahead mode), so a file without them is refused: the
  engine exits with **code 4** before it takes the lease or reads the broker, and the log line names the setting.
  Fix: keep `$KAIBOT_DATA_DIR` on a local disk (APFS on the Mac, ext4 or xfs on a server, a Docker volume and not
  a network share). Under the service (`scripts/service_run.sh`) launchd starts the engine again every minute and
  it is refused each time until the cause is fixed: the service log and the alarm say "refused its journal (exit
  code 4)", nothing is sent to the broker meanwhile, and the broker-held stops, the watchdog and the dead-man keep
  working. In Docker on a Mac the engine is a Linux process on a bind mount of `data/live`: write-ahead mode is
  asserted there too, but `fullfsync` is a macOS setting and does not apply (the Python mode gets it).
- Entries: `row` (an insert, update or upsert of an engine table), the ledger's `run`, `position`, `fill`,
  `dividend`, `split`, `mark`, the broker trail `intent` (written before every order call: entry, cancel,
  replace, sell, MOC, close) and `ack` (its answer), and `import` / `bootstrap` (rows copied from Supabase once).
- **Checksums** (#496). Every entry carries a checksum (column `sum`: SHA-256 over the checksum of the entry
  before it, its number, time, kind and text). It is checked when the file is opened and on every read, by the
  engine and by the watchdog's read-only reader. An entry that fails, or one that has none where it must, is never
  folded: the error names the entry (`journal entry 4711 fails its checksum`).
  - At start: the engine logs the line and exits with **code 4** before it takes the lease or reads the broker.
  - While running: the engine writes an `engine_error` event with the line, stops (broker-held stops stay; the
    watchdog takes over as for any stop) and exits with code 4; every later start refuses the file the same way.
    The watchdog, on a journal it cannot trust, reads the ledger rows in Supabase instead.
  - What to do: the file is damaged (disk, or a bad copy). Move it aside and restore last night's backup, or start
    a fresh journal from Supabase ("Taking over" below). Do not edit the file.
  - **Older journals stay readable.** A file written before checksums gets the column when it is first opened
    and keeps its entries as they are; `meta.checksum_from` records the first entry number that must carry a
    checksum (the cut-over). Entries below it are accepted without one and are not protected; a new file's
    cut-over is 1.
  - **After a roll-back to a release from before checksums**, that release appends entries without a checksum.
    The next start of a newer release refuses them (`journal entry N has no checksum`). If the roll-back explains
    them, accept them once, with the engine stopped: `python -m kaibot.engine.journal --reseal` prints the range
    it accepted and moves the cut-over past it. It never accepts an entry whose checksum is wrong.
  - Not covered: the last entries cut off the end of the file (the start check below catches that against
    Supabase: "restored backup"), and a change to an entry from before the cut-over.
- State is a fold: `JournalStore` rebuilds the engine's tables from the `row` and `import` entries when it opens;
  the engine folds the ledger from the ledger entries. Kill the engine at any point and the next start is where
  the last durable entry left it.
- The first start of a new journal copies the active run from Supabase (run, segments, trades, daily results,
  halts, the random baseline, the last 200 live signals with their gate runs and orders). If Supabase cannot be
  read then, the engine exits with **code 4** and trades nothing: it would not know its own open positions.
- **Identity.** Each journal file gets a uuid and an epoch (1 .. 2^21-1, from the uuid) when it is created, stored
  in its `meta` table. Its ids (run, signal, order, trade) and its `jseq` values are `epoch * 2^32 + n`: far above
  the database sequences, never shared by two journals (a standby, a new machine, a recreated volume), and below
  2^53 so the dashboard reads them exactly. The heartbeat carries `journal: {id, seq}`.
- **Every start checks the journal is the newest one Supabase has seen** (after the lease and the bootstrap,
  before anything else). It refuses, exit **code 4** with the reason, when:
  - *stale standby*: the engine's last heartbeat, or (with 0018) the newest row of a `jseq` table, comes from
    another journal this one never saw (it ran the engine after this file last did);
  - *restored backup*: the heartbeat, or (with 0018) rows of this journal's own range, are beyond the end of the
    file (an older copy, e.g. from the #121 backup).

  Starting anyway would trade on old positions and reuse keys that Supabase then rejects.

### Taking over (standby, new machine, restore)

1. Stop the old engine (or make sure it is dead) and let its replica drain: on the old machine
   `.venv/bin/python -m kaibot.engine.journal --status` shows `pending 0`. If the old machine is gone, its
   unsent rows are lost to Supabase (its file still holds them).
2. On the new machine, start with **no** `journal.sqlite` (move an old one aside, never delete it): the new
   journal copies the active run from Supabase, records the journal it takes over from, and starts.
3. Never bring the old file back into service: it refuses (stale standby). To return to the old machine, move its
   file aside too and let it start a fresh journal.
4. `--adopt-journal` (`python -m kaibot.engine --adopt-journal`, once) is only for when *this file* is the true
   state and Supabase is the one that is wrong (e.g. rows written by a test against the production project, or a
   restore you have checked against the broker). The journal then records what it saw as known, skips its
   numbering past every key of its own found in Supabase, writes a `journal_adopted` event, and starts. Run
   `--check` and compare the broker's positions first.
- Tables read from Supabase (other writers own them): `command`, `config`, the trial registry, news, heartbeat,
  the lease. Reads use one try with a 5 s timeout and a 30 s breaker; with Supabase down, commands wait there and
  a session open refuses entries (the config cannot be read), while stops, management and the flatten carry on.
  Commands applied while Supabase is unreachable are remembered locally and never applied twice.

## Replication

- A background thread (`Replicator`) sends `row` entries in journal order from a cursor every 2 s, and once more
  at a clean stop. A failure pauses it until the next pass; the heartbeat shows `replication: {cursor, last_seq,
  pending, error}`.
- Idempotent: the four referenced tables carry the engine's id; `segment`, `gate_run`, `fill`, `halt`, `event`
  and `gate_report` carry `jseq` (unique); `ledger`, `daily_result`, `gate_random`, `room_session`, `ledger_day`,
  `ledger_position` and `order.client_order_id` are natural keys.
- **A "duplicate key" answer** (a retry after a lost answer) reads the stored row by the key Postgres names and
  compares it with the row sent (numbers to the cent, times as instants, JSON by value). The same row: done (for
  `order` before 0018, its database id is mapped, so later fills point at it). A different row, or none found:
  a **conflict**: recorded in the journal (`replica_conflict`), counted in the heartbeat's
  `replication.conflicts`, turned by the engine into a `replica_conflict` event and halt row (shown on the
  dashboard), and replication goes on with the next entry. It never stops for good on one row. Trading is not
  affected: the engine's state is the journal. Before and after 0018 alike.
- Status: `.venv/bin/python -m kaibot.engine.journal --status`.
- **Rebuild Supabase from the journal** (after a restore, or into a fresh project with the migrations applied):
  stop the engine, then `.venv/bin/python -m kaibot.engine.journal --rebuild`. Sends every row again; rows already
  there are skipped.

## Restart and finalisation

- A restart mid-session restores the session as before (S40) and the ledger from the journal.
- A restart with the market closed finalises the last session this run traded if it has no `daily_result` and no
  ledger close: order sync, the daily result, the ledger's close, the day-end jobs, and the event
  `session_finalized_late`. Done once.
- At every close the ledger compares itself with the broker: shares per symbol (all rules), and cash as the change
  since the run began (the paper account holds more than the pot). Tolerance: $1 or 0.05% of the pot, whichever is
  more. A difference is the event `ledger_mismatch` and is kept in `ledger_day.reconciled`; the in-session drift
  check still stops entries on a share difference.

## Drill: Supabase unreachable for 10 minutes

`tests/test_ledger_engine.py::test_drill_supabase_unreachable_for_10_minutes_keeps_stops_and_the_flatten`: Supabase
refuses every call from 15:48 to 15:58 while a position is open. Every minute's tick runs (no loop error), the stop
is managed every minute, the 15:55 flatten goes out and fills, rows queue in the journal, and after the outage the
trade, its fills, the daily result, the ledger close and the applied command all arrive.

Not covered here (QA-Ops / #118): the watchdog and the database dead-man read the heartbeat in Supabase, so a long
enough outage still lets them act on their own rules.

## Dividends and splits

- Research: `python -m kaibot.engine.research --strategy <rule> --corporate-actions data/daily` books the daily
  store's splits and cash dividends on their ex-date (`kaibot.replay.corporate_actions`).
- Live: at the open, when the run holds a position, the engine reads Alpaca's non-trade activities (`DIV*` with
  `net_amount`; `SPLIT` splits). Alpaca does not document the fields of a split row, so the ratio is the broker's
  share count over the ledger's; without both the split is not booked and the drift check stops entries.
  The codes asked for must be in the API reference's `ActivityType` enum (a test pins them): one unknown code makes
  Alpaca refuse the whole request (#392: `SSP`, the older guide's name for a split, gave 422 "invalid activity type"
  from 18 Nov 2026 until the fix). If Alpaca refuses the combined request, dividends and splits are asked one group
  at a time; a refused group is a `corporate_actions_error` event naming it (`refused`), and the other group is
  still booked. A failed read does not by itself block entries: a missed split shows as a share-count difference
  and the drift check stops entries; a missed payment leaves its accrual open until a later read matches it.
  A live dividend is booked when Alpaca pays it (the activity date), not on the ex-date: the mark-to-market close
  shows the ex-date drop first and the cash later. That is exact for intraday rules (nothing is held over an
  ex-date, so nothing is ever paid). It is **not** right for a rule held across sessions: equity is understated
  between ex-date and pay date, and a dividend paid after the sale finds no holder and is dropped (ledger cash
  drifts from broker cash). So the engine refuses a multi-day live rule on live dividends
  (`MultiDayDividendsError`, test `test_multi_day_holding_on_pay_date_dividends_is_refused_until_118`): #118 must
  replace this guard with ex-date accrual (Alpaca corporate actions: ex-date, rate, pay date; accrue a receivable
  at the ex-date for the holders at the previous close, settle it against the `DIV` activity on the pay date).
- A split rescales the engine's position (shares, fill, stop, target) and says `split_booked`; re-creating the
  protective orders around the split is the order manager's job (S78).

## Reconciliation with the broker (S78, #118)

At start-up, at every session start, and every 15 minutes while a difference is open, the engine compares the ledger
with the broker (`engine/reconcile.py`):

- **What is checked:** shares per symbol, and the cash change since the run began. The cash change is net of
  dividends accrued at the ex-date that are not paid yet.
- **Tolerance (cash):** the larger of **$1 and 0.05% of the run's pot** ($50 at $100k). It covers what the ledger
  does not model to the cent:
  - a regulatory fee the venue has taken but not posted yet. Alpaca puts no fee on a fill; it posts its fees
    (SEC, FINRA TAF, CAT) as `FEE` activities the evening after the trades, and the engine books each one once, as
    a `fee` ledger entry, at the next session start (#238). So these fees no longer use up the tolerance over
    time;
  - cash in lieu and dividend amounts rounded by the broker;
  - dividend withholding.
- **Tolerance (shares):** none.
- **While a difference is open:**
  - entries (buys) are blocked; on the daily path, buys in the affected symbol, and every buy on a cash difference;
  - a sale that only reduces exposure goes out: at most min(ledger, broker) shares in a symbol whose shares differ;
  - exits, flattens, protective stops and cancels are never blocked;
  - a failed read counts as a difference (fail closed).
- **Clearing a real difference.** The owner accepts it with a reason:
  - command line: `python -m kaibot.engine.reconcile --status`, then `--accept "reason"`;
  - engine command: `accept_reconcile` with `{"reason": ...}`;
  - dashboard (#439, M9 1.14; needs migration 0037): Engine page, safety check "Books and broker". The sheet shows
    each difference (the stock, the engine's shares against the broker's, the cash difference) from the heartbeat's
    `reconcile` block, asks for a reason and a tick that the numbers were compared with the broker, and queues
    `kb_command('accept_reconcile', {"reason": ..., "shown": [{"kind", "symbol", "offset"}]})`. The engine reads the
    broker again and accepts only when `shown` is exactly what it finds (all or nothing). A refusal is the event
    `reconcile_blocked` with `when: accept_refused` and stays in the heartbeat (`reconcile.refused`) until an
    acceptance or a clean check; buys stay stopped. Differences without an `offset` (a failed read, a fill on an
    unknown order, a fill not booked yet) cannot be accepted: they clear only when the books agree.

  The accepted difference is journalled as an offset (`reconcile_resolved`) and survives restarts. The same
  difference stays accepted; a difference that changes by more than the tolerance blocks again.

## Migrations to apply

`0018_m7_ledger.sql`. Additive and re-runnable. Apply it before or after this code starts on paper:

- Before (or while today's engine runs): the replicator sees `ledger_day` and `segment.jseq` and replicates with
  engine ids and `jseq` (idempotent), including the ledger tables.
- Not applied yet: at its first contact with Supabase the replicator finds them missing and writes the pre-0018
  tables as the engine always did (database ids, mapped in the journal's `idmap`; no `jseq`; `ledger_day` and
  `ledger_position` stay in the journal). The dashboard keeps updating with today's RPCs; `kb_book` and
  `kb_equity_series` do not exist yet. A lost answer may duplicate a row on retry (as before S77), and
  `python -m kaibot.engine.journal --rebuild` is refused. `--check` shows `"schema": "pre-0018"` on the
  journal line.
- Applied while the engine runs: nothing changes until the engine restarts (the check is done once); then it
  switches to the new tables. Rows sent before the switch keep their database ids; references to them go through
  the map. The ledger rows of the sessions before the switch are not sent (they stay in the journal).

## RPC contract for the multi-day dashboard (S90)

All owner-checked, `security definer`, granted to `authenticated`, like the other `kb_*` functions. Money in US
dollars, fractions 0..1, sessions `YYYY-MM-DD` (New York), times ISO 8601.

### `kb_book(p_run bigint, p_session date default null) -> jsonb`

The book at a session's close (default: the latest session the ledger closed). The shape of `MultidayBook` in
`apps/web/src/lib/multiday.ts`:

```
{ run_id, session,
  mtm: { ts, at_close: true, cash, invested, equity, equity_reported, start_equity, prev_close_equity,
         open_equity: null, dividends_to_date },
  positions: [ { rule, symbol, qty, avg_cost, last, last_ts, mark, opened_session, dividends,
                 stop: { price, state: "resting" | "missing" | "re-arming" } | null } ],
  exposure: { gross, overnight, cap: null, by_rule: [ { rule, value, share } ], market_filter: [] },
  reconciled: { ok, ts, positions, cash_diff } | null }
```

With no close yet: `{run_id, session: null, mtm: null, positions: [], exposure: null, reconciled: null}`.

### `kb_equity_series(p_run bigint, p_from date default null, p_to date default null) -> jsonb`

The pot marked to market, one point per session, oldest first:
`[{session, start_equity, close_equity, close_equity_venue, cash, invested, dividends, pnl, pnl_venue, pnl_shadow,
gross, positions, marked}]`. Sessions closed before the ledger existed have `marked: false`, the old realised
equity and nulls for the rest.

### `kb_report` (changed) and the compatibility view

`kb_report.start_equity` now reads `kaibot.daily_result_mtm` (daily_result plus the ledger's start and close
equity, falling back to the old "pot + realised results" for unmarked sessions). Every other dashboard RPC reads
the same tables as before and is unchanged. The minute `ledger` row keeps its shape; if two rules hold one stock,
the second appears as `"SYMBOL|strategy"` in `positions`.

### Against the frontend draft (`apps/web/src/lib/multiday.ts`)

| Draft | Contract | Note |
|---|---|---|
| `MultidayBook` fields | same names | |
| `mtm.equity` = cash + invested | same (`close_equity_venue`) | `bookTotals` agrees with it |
| - | `mtm.equity_reported` (added) | what the engine sizes and stops on ("report the worse": reported results, not venue fills) |
| `mtm.start_equity` "the run's starting pot" | the pot (`run.amount_usd`) | |
| `mtm.prev_close_equity` | `ledger_day.start_equity` | the base of today's daily stop, on the reported basis |
| `mtm.open_equity` | always `null` | not kept |
| `mtm.at_close` | always `true` | intraday values: a later story (the minute `ledger` row has them by symbol) |
| `positions[].last` | the close mark, `null` when no price was found | `mark` (added) always holds the price used |
| `positions[].stop.state` | `resting` or `missing` | `re-arming` arrives with the order manager (S78) |
| - | `positions[].dividends` (added) | |
| `exposure.cap: number` | `null` until the stakeholder confirms the level limits | type should be `number \| null` |
| `exposure.market_filter` | `[]` | the portfolio builder's (S79) |
| `reconciled: {ok, ts}` | plus `positions`, `cash_diff` | |
| - | `kb_equity_series` | the "MTM pot series" for the chart |

Frontend (not edited here): replace the draft types with these, make `cap` nullable, drop the mock.

## The minute row (`kaibot.ledger`, #384)

One row a minute while the engine trades. Contract between the engine (`Engine._ledger`, `_md_minute`) and the
dashboard (`apps/web/src/lib/marks.ts`, `engineMarks`); `kb_overview` returns the whole newest row as `ledger`.

| Field | Meaning | Authoritative for |
|---|---|---|
| `equity_venue`, `equity_shadow` | the run marked now (unchanged) | the pot (`potNow`: the lower of the two) |
| `positions` | **day positions only** (unchanged). A multi-day sleeve never goes here | the dashboard's day-position rows |
| `cash` | the row's equity less everything the row lists, in whole cents (was: equity less day positions, so a multi-day book showed as cash) | a "cash" figure |
| `multi_day` (jsonb, migration 0034) | `{v: 1, cash_ledger, invested, settling: [symbol], positions: [{rule, symbol, qty, avg_cost, mark, mark_ts, mark_from, value}]}` | the price of each multi-day holding in that minute |

- Identity, exact to the cent (`tests/test_ledger_minute_multiday.py`): `cash + multi_day.invested + Σ positions[s].qty × last = equity`.
  `cash_ledger` is the ledger's own cash (`Book.cash`); `cash` equals it within one cent per holding (each holding's
  `value` is rounded to the cent).
  "equity" here is the engine's reported equity (`equity_now`, realised P&L counted as min(venue, shadow) per
  trade). It equals `equity_venue` and `equity_shadow` while every closed trade of the day has the same venue and
  shadow P&L, which is always the case with multi-day rules only (a multi-day sale counts one P&L for all three).
  An intraday trade whose venue and shadow fills differ moves `equity_venue` away by `realized_venue -
  realized_reported`; `cash` then follows the reported equity, not the venue's (QA-Ops on #384).
- If building the block fails, the row is written without it (logged) and the minute goes on: it is a report.
- `mark` is the price `equity_now` counts the sleeve at, in this order: the last minute bar (`mark_from` `minute_bar`,
  `mark_ts` the bar's end), the broker's `current_price` (`broker`, the time of the positions read), the last close
  mark (`last_close`, 16:00 ET of that close), the average cost (`cost`, `mark_ts` null). Nothing new is fetched.
- `settling`: symbols with a daily-path order open (15:50-15:57 ET near-close orders) or a multi-day sale not yet
  booked. A fill is booked at the start of the tick that sees it; until then it is in neither `cash` nor
  `multi_day.positions`. A reader does not compare shares while `settling` is not empty.
- `multi_day` is absent (no key) for an engine without the daily path that holds no multi-day sleeve.
- No double counting: the dashboard takes day positions from `positions` only and prices the **booked** book
  (`kb_book`) with `multi_day.positions` only when the row is today's, `settling` is empty and the row lists exactly
  the book's (rule, symbol, shares); the account's live prices (`/api/marks`) come first. Otherwise the close marks.
- Before 0034 is applied the replicator sends the rows without `multi_day` (the journal keeps it), says so once
  (event `multi_day_column_missing`) and tries the column again hourly; no restart is needed after the apply.

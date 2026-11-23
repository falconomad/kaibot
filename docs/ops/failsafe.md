# Fail-safes (M1: stories S11, S15, S16, S18; overnight-safe since M7 S80, #120)

What stops Kaibot from adding risk when something breaks, from the inside out. Since S80 (decision 15 sections 4
and 6) none of them sells a **multi-day** sleeve: they cancel entries, keep (or re-create) its protective stop and
raise the alarm. Intraday sleeves are flattened at the close as before. Only run drawdown, a new run and "Flatten
and stop" sell everything.

| Layer | Runs where | Protects against | What it does |
|---|---|---|---|
| Broker-held **GTC** stops | Alpaca | engine or laptop dead at any time | intraday: bracket legs (stop and 2R target); multi-day: one standalone GTC stop per sleeve, whole shares (S78, `engine/protect.py`). They stay at the broker until filled or replaced |
| Engine halts | engine | bad state | no new entries for the session (exits keep being managed); `Engine.entry_block()` says why, for the multi-day target path too |
| Daily stop | engine | a bad day, overnight gaps included | the day's change in mark-to-market equity (start = yesterday's close mark) at the level's stop: entries blocked for the day, intraday sleeves flattened, multi-day sleeves keep their stops |
| Engine lease | Supabase row + engine | a second engine on another machine | start-up refused while another engine's lease is live; renewal failing: no new entries; lease taken by another engine: this one stops ([run_engine.md](run_engine.md#engine-lease-one-engine-whatever-the-machine)). Supabase unreachable at start-up: a start "reducing only" when the last lease recorded in the journal was this machine's own ([Degraded start](#degraded-start-supabase-down-when-the-engine-starts-m10-125-450)); every order is preceded by a renewal with the tenure's epoch (M13 4.3, #477, `engine/fence.py`), so a paused or older process whose tenure ended sends nothing |
| Watchdog | separate process, same machine | engine crashed or hung | heartbeat > 90 s (one that cannot be read counts only if this machine does not show the engine alive, see below): cancel open Kaibot buys (`kb...`) not yet completely filled, halt row + event; once a minute: every multi-day sleeve has its stop at the broker, a missing one is re-created; still stale at close minus 5 min (15:55 ET, 12:55 on early closes): sell the **intraday** shares only, retried until close minus 4 min |
| Dead-man | Supabase (pg_cron + pg_net) | laptop asleep, off or offline | with 0019: heartbeat > 3 min old while the market is open: the same plan as the watchdog (cancel entries, verify and re-create stops, alarm); from close minus 4 min to the close: sell the intraday shares only. Before 0019 (0006/0017): `DELETE /v2/positions?cancel_orders=true` at close minus 4 min, i.e. the whole account |

Restart and recovery (crash, 10-minute outage, API outage, half-written order, two engines): see
[outage_drill.md](outage_drill.md) and `scripts/outage_drill.sh`.

## Watchdog and dead-man on the last minutes of a session (S74c #147, S74d #154)
- **The close** is the earlier of the broker clock's `next_close` and the broker calendar's close for the day,
  so on early-close days (13:00 ET) the watchdog flattens at 12:55. The calendar is read once a day, only when
  the engine is already stale, and never inside the flatten window (from the clock's close minus 5 minutes the
  clock's `next_close`, already calendar-aware, decides alone); the happy path makes no extra calls.
- **The first flatten pass** of the day always tries every position, however long it takes.
- **Retries** run until close minus 4 minutes, the dead-man time. If the dead-man will act (the engine heartbeat
  will be older than 3 minutes at the dead-man time, `DEADMAN_STALE_S`), a retry starts no close within
  `CLOSE_BUDGET_S` (25 s) of the dead-man time; a symbol it does not start counts as failed, so the day is not
  marked flattened. 25 s is a slow close, not a worst case: one close can overrun it (several order-path calls,
  each with a 20 s deadline), but only that one close. If the dead-man will not act (the engine stopped less
  than 3 minutes before it), retries are not cut. A watchdog that first sees the stale engine after the dead-man
  time still makes one pass, as a backstop.
- **A failed open-orders read** (entry cancels) is logged and returned as `entry_read_failed`; the halt and the
  flatten of that poll still happen.
- **Order-path budget:** a trading call that gives up because its rate-limit slot comes after its deadline no
  longer books that slot, so it does not push other callers back.
- **Dead-man on early closes:** migration `0017_deadman_early_close.sql` (**not applied yet; the stakeholder
  applies it**) adds `kaibot.market_calendar_exception` (holidays and 13:00 closes, NYSE calendar 2026-2028) and
  two more pg_cron jobs at 12:56 ET. `deadman_flatten()` then acts at the day's close minus 4 minutes and never on
  a holiday. Before 0017, early-close days have no off-box dead-man (the GTC stops still protect positions).
  **Every autumn** add the next year's holidays and early closes to that table, next to `macro_calendar.yaml`.
  Check after applying: `select jobname, schedule from cron.job where jobname like 'kaibot_deadman%';` shows four
  jobs, and `select kaibot.deadman_flatten();` outside the due minutes returns `not 15:56 ET` (or `not 12:56 ET`).

## A heartbeat the watchdog cannot read (M9 1.6)
The heartbeat lives in Supabase. When the watchdog cannot **read** it (Supabase unreachable from the Mac), it asks
the Mac itself before acting, because three minutes without Supabase used to cancel a healthy engine's working buys:
- **The process.** The engine writes `engine.pid` (pid and host name) into its data folder at every start. A pid
  file of this host whose process is gone: the engine is dead, at once. A process that exists proves nothing (it may
  hang). In Docker the engine's pid is in another container and is not checked.
- **The journal.** `journal.sqlite` is on the same disk and every tick of a session writes its minute row. The engine
  counts as alive only while the last write is younger than 150 s (`LOCAL_ALIVE_S`: one tick period on top of the
  90 s). No journal, a silent journal, an engine with no run: stale, as before.
- **Alive:** nothing is cancelled, sold or halted on that poll (log line "heartbeat unreadable, but ... the engine
  is alive"). **Not alive:** exactly the old path. A dead engine is therefore acted on at most 150 s after its last
  journal write instead of 90 s after its last heartbeat, and only while Supabase is unreadable too.
- **The end of the outage (QA-Ops, #485).** The first good read after unreadable ones usually shows the engine's
  last beat from before the outage (the watchdog polls every 15 s, the engine beats about every 10 s). For 90 s
  (`STALE_S`) from that first good read a stale row is judged by the Mac in the same way; a fresh beat ends it.
- Otherwise a heartbeat that **was read** and is stale, or no heartbeat row at all, is acted on whatever the journal says. The
  dead-man in Supabase is unchanged: if the Mac is cut off for more than 3 minutes while Supabase is up, it still
  cancels the entries from its side.
- **Carried, not lost.** Before each entry cancel the watchdog appends the order's ids to `watchdog_cancels.jsonl`
  in the data folder. The carry-over (#363) reads it: a buy the watchdog cancelled is sent again at the next
  session like one that never reached the venue. A buy the dead-man cancelled is not recognised yet (its record is
  in Supabase) and stays "order canceled" until the next decision.

## Degraded start: Supabase down when the engine starts (M10 1.25, #450)

Before this, an engine that could not take its lease from Supabase at start-up exited (code 3), and one that could
not verify its journal there exited too (code 4). A Supabase outage at the moment of a restart therefore meant no
engine, while the dead-man (which also lives in Supabase) was down as well. Now the engine may come up **reducing
only**: it manages exits, stops and sales and books fills, and buys nothing, until Supabase answers again.

**When it is allowed** (`engine/__main__.py` `degraded_start`; otherwise exit code 3, as before):

1. The lease could not be *checked* (`LeaseUnavailable`). A lease that another engine holds is never a reason: that
   is still exit code 3.
2. The journal's last `lease` record is this install's own (the holder id in `data/live/engine_holder_id`) and says
   `acquired`, `released` or `degraded_start`. The engine writes a `lease` entry to its local journal whenever the
   lease changes (`acquired`, `released`, `lost`, `held_by_other`, `degraded_start`, `contradicted`); these entries
   are never sent to Supabase. No record (a new journal, or one that has not held the lease since this release), a
   record of another holder (the journal was copied), a record written on another host name (the whole data folder,
   holder file included, was copied to another machine; QA-Ops on #536), or `lost` / `held_by_other` /
   `contradicted` (another engine was seen after this one): no degraded start. A host name that changed (macOS can
   take one from the network) therefore refuses a degraded start too: fail closed, the start is as before (code 3).
3. The broker shows nothing against it (`other_engine_evidence`): no open order, and no fill since five minutes
   before the journal's last entry, that carries an engine's client order id (`kb...`, the fail-safes' `kbwd-` and
   `kbdm-` apart) which this journal does not know. An engine journals every order's client id before it sends it,
   so such an id was sent by another engine. If the broker cannot be read, that counts as evidence. A refusal here
   is journalled (`contradicted`) and holds for every later start until a lease is taken from Supabase.

**What "reducing only" is.** `EngineLease.degraded` is set and `lease.ok` is False. That is the state a *running*
engine is in when its renewals fail, which existed before: `Engine.entry_block()` answers "lease unverified", and
all three doors to a buy ask it (the multi-day send through `_Gate.allows`, the intraday entry, and the gate's
kill switch). Exits, protective stops, sales, cancels of its own orders and the booking of fills go on. In
addition, until the lease is confirmed the engine applies **no command** (one marked applied here would be taken
from the engine that holds the lease), writes **no heartbeat** (the row belongs to the lease holder, and the
journal check of other engines reads it) and sends **no row** to Supabase (the replicator and the news poller are
not started). The watchdog judges it by its journal, as for any engine whose heartbeat cannot be read (M9 1.6).
Ticket 1.21's trading state is not there yet: the mode uses the existing entry block, and
`EngineLease.reducing_only` is the hook that state should read ("reducing"). The order fence (M13 4.3, #477,
`engine/fence.py`) has the lease from the degraded start on and follows the same rule: a fenced call that only
reduces (a sale, a stop or its resize, a cancel, closing a position) goes out, any other is refused before the venue.

**Becoming active** (`confirm_lease`, once per loop pass, about every 10 s): the lease is taken with the same atomic
database call as at any start, then the journal's identity is verified (`verify_identity`). Only when both succeed
is `degraded` cleared; the replicator starts, commands are read and buys are allowed. From then on every order
call renews first with the tenure's epoch (#477; QA-Ops on #584 found the fence left without its tenure here). A session that *opened*
while Supabase was down could not read the owner's config and refused entries (`config unreadable`, fail closed).
Since #536 (test-eng's drill) the config is read again at once when the lease is confirmed, and then once a minute
while it is still unreadable and the lease is ours (`Engine.retry_rules`; the same holds for any Supabase blip at a
session's open). It is the same config the open would have read (the latest applied one created before the open:
point-in-time, never a newer one). When it is read, the refusal is lifted (`halt_cleared` event, kind `config`) and
the session goes on under every other check: the entry hours, the 15:50-15:57 send window (a buy whose window has
closed waits for the next session), the risk gate and the lease. Custom tickers ("Only these") were not loaded at the
open and are not traded that session (named in the event). A config that is read and invalid is refused as at the
open, for the session. While it stays unreadable entries stay off and the confirmation says so: the log line is
`engine lease confirmed and journal verified, but entries stay OFF: config refused: config unreadable: ...` and the
`engine_lease_confirmed` event has `entries: false` and `entries_off`. Only `entries: true` is logged as
`fully active (entries allowed)`.

### Why two engines can never both trade

A is the machine that starts degraded; B is any other engine.

| Interleaving | What happens |
|---|---|
| B took the lease while A was down, and has sent orders | A's start finds B's client order ids at the broker (point 3): A does not start. |
| B took the lease while A was down, and has sent nothing yet | A starts reducing only. A cannot buy: it holds no lease, and the only way to one is the atomic call that B's live lease refuses. When A reaches Supabase it gets `LeaseHeld` and stops (code 3). Until then B is the only buyer. |
| B starts while A runs degraded | B takes the lease normally (A holds none). Same as above from there. |
| B ran and stopped (or died and its lease expired) before A reaches Supabase | A's call takes the free lease, but `lease.ok` stays False until the journal is verified. B's heartbeat names another journal: A is a stale standby, gives the lease back and stops (code 4). A bought nothing at any point. |
| B's lease had only lapsed (B alive but silent for more than 90 s) when A's call takes it | B's next renewal says "not ours" and B stops; A then fails the journal check and stops. No engine, not two: the broker-held stops, the watchdog and the dead-man cover it. The same happens today when a stale engine is started normally. |
| The whole data folder (journal and holder file) copied to a second machine C | C's degraded start is refused: the journal's last lease record names A's host, not C's (point 2). This does not cover a *normal* start of C with Supabase up: C then has A's holder id, and the lease treats a holder as one engine. That was so before this change (run_engine.md: never copy the holder file to another machine) and is not loosened here. |
| A and B both degraded (both cut off) | Neither holds a lease, neither buys. |
| A and B both sell the same position on the same day, same run | The client order id is a pure function of run, rule, symbol, day, purpose and attempt, so both compute the same one. The venue accepts the first and refuses the second (a reused `client_order_id`); the second engine finds the order under its id and follows it. One sale. |
| A and B sell the same shares under different ids (another purpose, attempt or run) | The venue reserves shares for an open sell order and the paper account has shorting off (the engine refuses multi-day buys while it is on), so the second sale is refused for want of shares, or fills only what is left. The account is never short and never sells more than it held. |
| A's book is stale and its rule, stop or run-drawdown flatten sells something B meant to keep | Possible, and the one cost of this mode: exposure can only go *down*. B's next reconciliation shows the difference and stops B's buys in that stock until the owner accepts it. |
| A's command handling, heartbeat or rows interfere with B | They cannot: A reads no command, writes no heartbeat and sends no row until it is confirmed. If A turns out stale its journal is moved aside as for any stale standby (docs/ops/ledger.md, "Taking over"). |

The first sale case rests on Alpaca refusing a reused client order id, which is its documented behaviour and what
the simulated venue does; `scripts/probe_alpaca_idempotency.py` measures it on the paper account, and its result
is not in the repo yet. The second case (no shorting, shares reserved by open orders) does not depend on it.

Tested in `tests/test_degraded_start.py` with the simulated store and venue: every refusal above, the confirmation
paths, two engines with the same book selling the same position in both orders (one sale, never short, only the
lease holder buys), and the drill: Supabase down at start, the engine comes up reducing only and buys nothing;
Supabase back, it becomes active and the rule's buy goes out once; a restart inside the session with Supabase back
at 15:20 (the config read again, the 15:50 buy goes out); Supabase back inside the send window (the buy goes out)
and after it (nothing is bought that day); the config still unreadable at the confirmation (entries off, said in
the log and the event, lifted at the first good read); a config read late and invalid (refused for the session).

## Engine halts (fail closed)
Each writes a `halt` row and an event; all clear at the next session start, except `three_stops` (the run is paused until you press Resume)
and `engine_errors`, which also clears after 5 clean ticks in a row (M9 1.7, #432: the halt row stays, a `halt_cleared` event says why).
Another halt raised while `engine_errors` is set takes the kill over (its own row and event) and is not cleared by clean ticks.

| Kind | Trigger |
|---|---|
| `daily_stop` | day P&L at the level's daily stop: flatten and halt |
| `run_drawdown` | drawdown from the run's peak at the level's cap: flatten and end the run |
| `drift` | engine positions differ from the broker's on two consecutive minutes |
| `engine_errors` | 3 loop iterations in a row raised; cleared after 5 ticks in a row that did not raise (about 5 minutes) |
| `calendar_stale` | today is after `covers_until` in `config/macro_calendar.yaml` |
| `three_stops` | 3 daily stops within 14 calendar days (~10 sessions): run paused |
| lease unverified (no halt row) | the engine lease could not be renewed: entries blocked until a renewal succeeds; shown as `lease: unverified` in the heartbeat |
| degraded start (no halt row) | M10 1.25: the engine started while Supabase was down and is reducing only: entries blocked ("lease unverified"), no command applied, no heartbeat, until the lease is taken and the journal verified; events `engine_degraded_start`, then `engine_lease_confirmed` (`entries: false` and `entries_off` while something, such as an unreadable owner config, still blocks buys) |
| `config` | the owner's config was rejected at the session's open (entries off for that session), or could not be read (`config unreadable`: asked again once a minute while the lease is ours; lifted with a `halt_cleared` event when read) |
| account not clean | positions or open orders on symbols the engine doesn't hold (entries blocked until clean) |
| clock skew (no halt row) | M9 1.10 (#435): the broker clock's timestamp, read at every tick, is more than 5 s from this machine's clock (or the answer has no timestamp): no new order until they agree (the near-close send of the multi-day path, buys and sales alike, and intraday entries); exits, stops, cancels and the flatten go on. One `clock_skew` event when it starts and one (`state: ok`) when it ends; an ERROR log line `clock skew: ...`; `entry_block` says how far and which way. Fix: set the machine's clock (System Settings, Date & Time, "Set time and date automatically") |
| market data checks (no halt row) | M12 3.11 (#473), **off by default**: only with `KAIBOT_DATA_CHECKS_BLOCK=on` (or a comma list of checks: `stale`, `no_bars`, `bad_price`, `unmatched_move`, `bar_gap`; `on` = all but `bar_gap`, which IEX's quiet minutes would trip). Today's IEX minute bars are checked at every minute's read (`kaibot.ingest.data_checks`, the same checks `KAIBOT_DATA_CHECKS=1` only reports). A blocking finding on a stock (a zero or negative price anywhere today, a move of 5 % in 5 minutes the index does not share, no bar or none for more than 5 minutes) blocks **new positions in that stock**: intraday entries (signal row with `rules_veto` "market data: ..."), and the multi-day path's buys ("entries blocked: market data: ..."; the buy waits for a later tick in the window, or the carry-over on the next session). A blocking finding on the index (SPY stale, empty, bad or gappy) or a check that itself fails blocks **every intraday entry**; the multi-day path asks about the stock alone (its buys have the pre-send quote gate, M9 1.16). A stock the engine does not scan has no bars to judge and is not blocked. **Since the book loop (M10 1.19, #538) the live engine, with the intraday path off (the default), reads minute bars of its holdings only (`BookMarket`), which has no data checks: then the switch could block nothing, and the engine refuses to start with it on (`KAIBOT_DATA_CHECKS_BLOCK is on (...), but this engine's market has no data checks ... Not starting`; Product's ruling on #583, fail closed). With the intraday path on (`LiveMarket`) intraday entries and the multi-day buys of scanned stocks are blocked as described.** A follow-up is needed for the book loop itself to judge the send's buys Sales, stops, cancels and the flatten never ask. The heartbeat has `data_checks` (blocking set, count, first five) only while it is on; a WARNING `data check (blocks entries): ...` when a finding first appears. The limits are the report's first guesses (`Limits` in data_checks.py): turn it on only after the report-only run has shown them quiet on real sessions. An unknown check name stops the engine at start |

Also enforced per entry by `risk/gate.py`: quote older than 5 s, missing bars, buying power (the smaller of the pot
and the paper account's own figure), the ±30 min event blackout (FOMC 14:00; CPI 08:30 is pre-open), and VIX > 35.

## Setup
- The watchdog starts automatically with `./scripts/run_engine.sh` (Docker: container `kaibot-watchdog`;
  Python: `logs/watchdog.log`, pid in `data/live/watchdog.pid`). It keeps running when the engine stops: that is
  its job. After updating the code, restart it: `kill $(cat data/live/watchdog.pid)` then start the engine again.
- The dead-man (migration `0006_failsafe.sql`) is inert until you store the **paper** keys in Supabase Vault, in
  the SQL editor (never in chat or the repo):
  ```sql
  select vault.create_secret('<paper key id>', 'kaibot_alpaca_paper_key_id');
  select vault.create_secret('<paper secret>', 'kaibot_alpaca_paper_secret');
  ```
  Test it outside market hours: `select kaibot.deadman_flatten(force => true);` with the engine stopped closes all
  paper positions; with the engine running it returns `engine alive`.
- Every autumn, extend `config/macro_calendar.yaml` (Fed tentative schedule ~September, BLS CPI schedule) and
  bump `covers_until`, or entries stop on 1 January.
- **The engine says so 30 days ahead** (M9 1.17, #442). At every session start it checks both hand-kept calendars
  and, when one ends within 30 days (or has ended), writes a `calendar_expiring` event and an ERROR log line
  `calendar expiring: ...`, every session until it is extended:
  - `macro_calendar`: `covers_until` of `config/macro_calendar.yaml`;
  - `deadman_holidays`: the last row of `kaibot.market_calendar_exception` (read from Supabase; today's rows end
    on the last listed day of 2028).
  It is an alert only: nothing is halted before `covers_until` passes. If the holiday table cannot be read, the
  session starts as usual and the table is read again at the next session start.

## Overnight-safe fail-safes (S80, #120)

One plan, two implementations: `src/kaibot/engine/failsafe.py` (the watchdog) and
`migrations/0019_deadman_overnight.sql` (the dead-man). `tests/test_failsafe_drills.py` runs both on the same
accounts and checks that they agree.

- **Entries:** every open buy not yet completely filled (a partly filled one has its rest cancelled; the filled
  shares keep their legs or get their stop) whose client id starts with `kb` (intraday `kb-<run>-<signal>-<n>`, order
  manager `kb<run>-<strategy>-<symbol>-<yyyymmdd>-<purpose>-<n>`) is cancelled. Sells (stops, sales) are never touched.
- **Which shares are multi-day**, per symbol: the larger of what the book says and what the multi-day stops at the
  broker cover (their client ids name the strategy), never more than the broker holds. The book is, for the watchdog,
  the engine's local journal (`$KAIBOT_DATA_DIR/journal.sqlite`, opened read-only), else the ledger rows in Supabase
  (`ledger_position`, the last close); for the dead-man, the ledger rows. Multi-day strategies:
  `strategies/registry.MULTI_DAY` (in SQL: `kaibot.strategy_holding`, kept equal by a test).
- **Stops:** a multi-day sleeve whose shares no resting stop of its own covers gets one: GTC stop sell, whole shares
  (a fraction is never covered), at its last known stop (the order manager's journal, or `ledger_position.stop_px`);
  if none is known, or it is not below the last price, 10% below the last price (`risk/gate.GAP_SCENARIO_PCT`). Never
  for more shares than are free (other sell orders hold shares); what cannot be covered is an alarm (`unprotected`).
  Client ids: `kbwd-<strategy>-<symbol>-<time>-wdstop` (watchdog), `kbdm-...-dmstop` (dead-man). The engine treats
  them as orders on a symbol it holds. The engine adopts a resting `kbwd-`/`kbdm-` stop of a known sleeve as that
  sleeve's own protective stop at start-up, at each session's stop reconciliation and before every sale (#188,
  `ProtectiveStops.adopt`; journal `stop_adopted`, unknown sleeve `stop_adopt_skipped`). It never adds a second stop
  and never cancels and re-arms the stop; it re-prices and resizes it through replace. A fail-safe stop that filled
  while the engine was down is booked to its sleeve. Before `KAIBOT_MULTIDAY=on`: #197, a replaced stop must keep a
  client id the fail-safes recognise (Alpaca gives a replaced order a new one). #197 sends one on every replace: the
  next attempt of the engine's own `kb<run>-...-stop-<n>`, or `<root>.r<n>` for an adopted `kbwd-`/`kbdm-` stop.
- **Never a stop on a flat position (#179's lesson):** the fail-safes only ADD a stop where none covers the shares;
  they never cancel and re-arm one. The watchdog reads the position again right before each new stop and sends
  none if the shares are gone (a stop filled in between). The dead-man acts only on an account read made in the last 3 minutes (older reads expire, and every open read
  expires when the engine beats again; drilled), normally about a minute old,
  so a stop that filled in that minute could be followed by its new stop on a flat position; Alpaca then rejects it
  for want of shares only if shorting is off on the paper account. **Turn shorting off on the paper account**
  before applying 0019 (Alpaca dashboard, paper account, Account Configurations, Shorting off; API:
  `PATCH /v2/account/configurations {"no_shorting": true}`; check `GET /v2/account` shows `shorting_enabled: false`), and keep #179 (the order manager's re-arm) fixed
  before protective stops go live.
- **Fail closed:** a position that neither the book nor a multi-day stop explains counts as intraday and is sold at
  the close minus 5 minutes. So with Supabase down AND the journal unreadable, a multi-day position keeps its place
  only through its own resting stop; one with no stop at all is sold rather than carried unprotected.
- **Alarm:** watchdog: `halt` row `watchdog` and events `watchdog_protect`, `watchdog_flatten` (with `kept`). Dead-man:
  one `halt` row `deadman` per session ("engine silent ...") and a `kaibot.deadman_pass` row per pass with everything
  it did (`result`). The alarm channel of S81 reads these.
- **Engine side:** the close-minus-5 flatten and the daily stop take intraday sleeves only; only intraday positions
  are "carried" (sold at the next open); the account guard and the drift check count the ledger's multi-day sleeves as
  the engine's own; equity counts them from their last close mark (the overnight gap is in the day's change); run
  drawdown, a new run and "Flatten and stop" also sell them (`_flatten_md`), and the sale is booked when it fills. Since M9 ticket 1.8 (#433) that sale goes through the
  order manager, one per sleeve, under `kb<run>-<rule>-<symbol>-<day>-flatten-<n>`: every fill is booked at its own
  quantity (a sale cancelled after a partial fill too), and a sale without an answer is looked up by its id, never
  sent again blind. Without the daily path (KAIBOT_MULTIDAY off) the older direct sale (`kbfl-...`) is still used.
- **Turning multi-day rules on:** `KAIBOT_MULTIDAY=on` in the engine's environment (the `.env`); default off, and the
  engine refuses to load a multi-day rule without it. It is the explicit switch for the first paper start (#133,
  after Gate B) and needs #188 (done) and #197 first. It does not loosen anything else: `config/profiles.yaml` `all_levels.allow_overnight` (the level
  limits, the stakeholder's) still caps overnight exposure at 0 while false, and migration 0019 must be applied first.

### Drills (`tests/test_failsafe_drills.py`, every one with 40 shares of a multi-day sleeve held from the day before)

| Drill | Who acts | What must happen |
|---|---|---|
| Engine killed at 15:50 | watchdog | the closing-auction buy is cancelled; the resting stop is kept (a missing one re-created, whole shares, at the journal's stop price); 15:55 sells the intraday position only; the restarted engine knows the sleeve |
| Engine killed at 15:50, laptop gone | dead-man (SQL) | the same from Supabase: entry cancelled, stop re-created (`kbdm-`), intraday legs cancelled then the position closed in the 15:56-16:00 window, the multi-day position kept; one `deadman` halt |
| Network cut overnight | dead-man, then the engine | the engine and the watchdog cannot reach Alpaca (loop errors, kill switch); the dead-man reads the account at 09:35, finds the stop resting and sells nothing; when the network is back the engine counts the overnight gap, the daily stop blocks entries and the stop stays |
| Supabase down at the open | watchdog | the heartbeat is unreadable and the engine is dead (its journal is silent): stale, entries cancelled, the missing stop re-created from the local journal; at 15:55 nothing multi-day is sold. With the journal unreadable too: kept through its own stop, or sold if it has none (fail closed) |

The dead-man drills run the migration in a throwaway local Postgres (`tests/pg_harness.py`: stand-ins for pg_net,
Vault and pg_cron; never Supabase). They are skipped where no PostgreSQL binaries are installed.

### Applying 0019 (the stakeholder)

Apply `migrations/0019_deadman_overnight.sql` after 0017 and 0018, before the first multi-day position is held
overnight. It replaces the four `kaibot_deadman_*` jobs by one (`kaibot_deadman`, every minute 13:00-21:59 UTC on
weekdays; the function decides what is due). Check: `select jobname, schedule from cron.job where jobname like
'kaibot_deadman%';` gives one row, and `select kaibot.deadman_check();` outside market hours gives `market closed`.
A test outside market hours with the engine stopped: `select kaibot.deadman_check(force => true);` twice, a minute
apart (the first reads the account, the second acts); `select result from kaibot.deadman_pass order by id desc
limit 1;` shows what it did. It never sells a multi-day position.

### Audit B1: the twenty places that assumed "flat every night"

| # | Assumption (audit `docs/research/engine_v3/code_audit.md` B1) | Status | Where and why |
|---|---|---|---|
| 1 | Flatten at close minus 5; no entries after | changed | S78 (#118): `flatten_all("time")` takes intraday sleeves only (`execution.flatten_at_close`); drilled here. "No entries after 15:45" stays for intraday entries; the multi-day path sends near-close DAY market orders from 15:50 to 15:57 ET (#337; before it, closing-auction orders) and asks `Engine.entry_block()` |
| 2 | "Carried" positions sold at the next open | changed | `_finalize_day` and `_start_day` mark only intraday positions as carried; a multi-day sleeve is meant to be held |
| 3 | Daily stop sells everything | changed | `flatten_all("daily_stop")` takes intraday sleeves only; entries blocked for the day (`Day.halted`, `entry_block`); multi-day sleeves keep their stops; the halt reason names them |
| 4 | Equity = start + realised today + unrealised since the fill | changed | S77 counted held `Pos` from the last close mark; now the ledger's multi-day sleeves too, at the minute's price (bars, else the broker's `current_price`); a multi-day sale is realised against cost less the gain to the last mark (`_md_realized`). Overnight gaps are in the day's change |
| 5 | Start equity = pot + realised daily results | changed in S77 (#117) | the ledger's last mark-to-market close; unchanged here |
| 6 | Open risk must fit in the room left before the daily stop | unchanged | `risk/gate.py` is two-key code and its open-risk budget is about intraday proposals; multi-day buys are sized by the portfolio builder (#119) under book limits (gap scenario, overnight cap). What changed: the cash a multi-day book holds is no longer counted as intraday buying power (`_decide`) |
| 7 | Watchdog closes every position at 15:55 | changed | `engine/watchdog.py` + `engine/failsafe.py`: cancel entries, verify and re-create stops, alarm, sell intraday shares only |
| 8 | Database dead-man deletes all positions at 15:56 | changed (file only) | `migrations/0019_deadman_overnight.sql`, **not applied**: the same plan in SQL |
| 9 | One position per symbol, exits tied to the entry bracket | changed in S77/S78 | positions keyed by (strategy, symbol); one standalone protective stop per sleeve (`engine/protect.py`); unchanged here |
| 10 | Foreign-position guard | changed | `check_account` and the drift check count the ledger's multi-day sleeves (and orders on their symbols) as known; anything else still blocks entries, including positions left by an ended run (it should have been flat) |
| 11 | New run and run drawdown flatten everything | changed so they still do | decision 15 section 6 keeps them: `flatten_all` for `run_drawdown` and `manual` now also sells multi-day sleeves (`_flatten_md`), the fill is booked to the ledger, and a new run waits until they are sold. "Flatten and stop" likewise |
| 12 | Per-day state object (entries, pause, halts reset daily) | unchanged | these are per session by definition (the daily stop is the day's change; the pause after three losses is an intraday rule). What must cross sessions already does: the run's peak (`run.peak_equity`), three daily stops in 14 days (halt rows), the book (ledger and journal) |
| 13 | Daily result is realised P&L only | changed in S77 | `ledger_day` / `ledger_position` hold the mark-to-market close; multi-day sales now enter the day's result too (item 4) |
| 14 | Gate report books P&L on the exit day | unchanged here | multi-day rules are judged by the mark-to-market Gate B report (`engine/daily_report.py`, #161) and evidence gate v2 (#126); the intraday gate report stays for the dip soak test |
| 15 | Random baseline is intraday | unchanged here | the exposure-matched random control for multi-day rules is #126 / #143 |
| 16 | Walk-forward simulates each month from flat | unchanged here | multi-day rules run as one continuous account (`engine/daily.run_daily`, #161); `walkforward.py` stays for intraday research |
| 17 | Minute-bar arrays; live keeps 70 sessions | unchanged here | multi-day rules read the daily store (`docs/ops/daily_bars.md`, `DailyData`), not the live minute feed |
| 18 | Shadow and room counterfactuals end at the flatten | unchanged | they belong to intraday shadow rules; the room as a pre-open review of the candidate list is #131 |
| 19 | Owner config: flatten and "no overnight" locked | unchanged here | tighten-only multi-day settings are #132; until then the switches are `KAIBOT_MULTIDAY` (this story) and `all_levels.allow_overnight` (the stakeholder's level limits) |
| 20 | Dashboard functions by entry day | changed in S77 | 0018 added `kb_book`, `kb_equity_series` and the mark-to-market start equity of `kb_report`; the multi-day dashboard is #130 |

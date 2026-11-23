# The UI after this week's engine: what to show, what to fix, in what order

Design and product research, 23 Nov 2026. **Status: a proposal for Product. No code. Nothing in it is decided.**
Read at `main` 1cec2fd (PR #599). Brand: lowercase **kaibo** (`docs/design/brand/brand_system_v1.md`). Builds on
`mobile_first_v4.md` (its rules that do not bend still hold), `m13_push_notifications.md`,
`m13_large_transaction_approval.md` and `m8_market_notices.md`.

Sources read: `CLAUDE.md`; `docs/research/engine_audit/report.md` and `decisions.md`; `docs/design/*.md`;
`docs/ops/failsafe.md`, `run_engine.md`, `ledger.md`, `drills.md`, `news.md`, `filings_8k.md`;
`src/kaibot/engine/event_names.py`, `standing.py`, `core.py` (`_status`, `_daily_status`), `__main__.py`
(`make_beat`, the main loop), `live_daily.py` (`_report_refused`); `config/book_stop.yaml`, `config/index_core.yaml`;
`docs/research/results/README.md` and `gate_v3_rescore.md`; migration 0007 (`kb_overview`) and the `kb_*` names the
dashboard calls; `apps/web/src` (`Shell.tsx`, `Home.tsx`, `pages/Health.tsx`, `lib/startstate.ts`, `lib/multiday.ts`,
`lib/healthwords.ts`, `lib/stream.ts`, `lib/notices.ts`, `ReconcileAccept.tsx`, `routes.ts`); `apps/web/size-budget.json`;
the PRs merged on 21 to 23 Nov and issues #446, #476, #487, #600. Open PRs #601 (prices paused cue), #602 and #603
(book-stop band data, index cap 27%), #584 (lease fencing, migration 0042) and #509 (first-load headroom) were read
by title and treated as not merged.

## In five lines

1. The engine now has **far more states than the phone can name.** The top bar knows "Running", "Running · no new
   entries", "Engine off/asleep" and "Engine offline". Behind those sit a selling-only start, an unverified lease, a
   clock disagreement, books that differ from the broker, a damaged journal, a command that waits, and a decision
   that is due, sent, short or late. Most of that is already in the heartbeat that `kb_overview` hands the page whole.
2. **The most important gap is "why is kaibo not buying, and is that a problem?"** The heartbeat says `managing` but
   not why; the owner has to open Engine and read event rows to find out.
3. **Two states read as calm when they are not:** an engine stopped by a damaged journal looks like a sleeping engine
   at night, and a broker payload kaibo refused to book is labelled "no orders sent".
4. Build **one trading-state line** now from the signals that exist, and switch it to the engine's own state when
   ticket 1.21 (#446) lands. Add **one "next action" line** from the standing-targets block. Both are lazy chunks
   for now: the first load has no room for them (section 4).
5. News, the market-event alarm, shadow observers and gate v3 belong **one tap away, in lazy chunks**, never on
   Home's first screen. The first-load JavaScript has about 2.4 kB left of 204 kB (main `10b0004`, 23 Nov, after
   #609); before #609 it had about 120 B. New surfaces stay lazy until #509 lands.

---

## 1. What the engine now knows that the UI hides or shows badly

"Where it is" is where the dashboard can read it today. "No migration" means the data already reaches the page:
`kb_overview` returns every heartbeat row as `to_jsonb(h)` (migration 0007), so any new field in the heartbeat's
`detail` reaches the browser without a database change. `kb_health` is different: its event list is generated from
`event_names.HEALTH` and every addition needs a migration the stakeholder applies.

| # | Engine capability (this week) | Where it is | What the UI does today | Gap |
|---|---|---|---|---|
| 1.1 | **Trading state** (active / reducing only / halted). Ticket 1.21 (#446) is **still open**; the state is spread over `halted`, `lease`, `reconcile`, clock-skew events, halt rows, `command_waiting` and the heartbeat's `state` (`trading` or `managing`) | heartbeat `detail`; `halts_today`; `kb_health` events | `managing` reads "Running · no new entries" (`Shell.tsx` `STATE_TEXT`) with no reason. A halt shows as a red row; the other blocks don't show on Home at all | The owner can't tell "outside the buying window" (normal) from "buys blocked by a safety check" (worth knowing). The heartbeat does not carry `entry_block()`'s reason text |
| 1.2 | **Degraded start** (Supabase down at start-up: selling only, no heartbeat, no rows, no commands) and `engine_lease_confirmed` (`entries: true/false`) | events `engine_degraded_start`, `engine_lease_confirmed` are in `PENDING`: written to the journal, **not yet in `kb_health`** | Nothing. While degraded there is no heartbeat, so the chip says "Engine offline". After recovery nothing says "it ran selling only from 09:41 to 15:20" | The owner sees a scary state while it happens (unavoidable: the dashboard's database is the thing that is down) and no explanation after it (avoidable) |
| 1.3 | **Journal integrity stops** (chained checksums, structure check at every open; a damaged entry stops the tick, exit code 4, every later start refused) | while running: `engine_error` with `JournalCorrupt: ...`, then heartbeat `state: stopped`. At a refused start: **nothing reaches Supabase** | `stopped` plus a closed market reads **"Engine asleep · starts 15:00"** (`engineClock`). In market hours it reads "Engine off" | **False reassurance.** The one stop that needs the owner at the Mac looks like a normal night. Health shows the event row only to someone who opens it |
| 1.4 | **Lease bound to holder and machine** (#567, migration 0041): a copied holder file or another host is refused | log line only; refused start writes nothing | "Engine offline · last seen N min ago" | The heartbeat has `host`. The chip shows it only while live. "Last seen on <host>" would tell the owner which machine to look at |
| 1.5 | **"Flatten and stop" waits** for open orders and unbooked fills (#525, #533) | pending command in `kb_overview`; `command_waiting` {why, action} in `kb_health` | Done well: `flattenState` says sent / waiting / engine off (#532) | It does not say **what** it waits for (the `why`), and only the Engine page has the event |
| 1.6 | **Lost-order adoption** (#535: an order called lost that the venue later fills is adopted and booked) | a journal entry and a WARNING log line; **no event** on the multi-day path (`order_adopted` is registered and in `kb_health` but only the intraday path writes it) | Nothing | A fill that appears "from nowhere" in Activity has no story. Needs an engine event (the name already exists: no migration) |
| 1.7 | **Refused broker payloads** (#545: a fill, order view or dividend that fails its checks is never booked; buys in that stock wait for reconciliation) | `multi_day_error` with `step: broker_payload`, `error`, `refused`, `action` | Labelled from `EVENT_LABELS`: **"Multi-day step failed (no orders sent)"** | **Wrong words.** Orders may have gone out; what failed is booking one answer from the broker. The owner needs "a broker answer looked wrong and was not booked; buys in XOM wait for you" |
| 1.8 | **Owner books or accepts an unreported fill** (#566, migration 0040) | heartbeat `reconcile` (`unbooked`, `refused`); `kb_command('accept_reconcile', …)` | Done well on the Engine page (`ReconcileAccept.tsx`); Home shows a red "books and the broker disagree" row | The Home row should open that sheet directly; today the owner has to find Engine › Books and broker |
| 1.9 | **Standing targets**: per rule, the decision in force and whether it is `undecided`, `due`, `sent`, `met` or `replaced`, with `late_of` and the symbols still `short` (#538) | heartbeat `multi_day.rules.<rule>.standing` | **Not read anywhere** in `apps/web`. The plan panel uses `kb_multiday_plan` plus `planlate.ts` instead | The one sentence the owner wants ("decided Friday, goes out today at 15:50, 2 buys still short") is computed by the engine and dropped by the page |
| 1.10 | **Data checks blocking buys** (#583) | heartbeat `data_checks` only while `KAIBOT_DATA_CHECKS_BLOCK` is on; the live book loop **refuses to start** with it on (Product, #583) | Nothing | Nothing to show today. Design the row now (per stock, on the plan) so it is ready when the book loop gets its own checks |
| 1.11 | **Book stop**: 27% ceiling, `mode: fixed` (`config/book_stop.yaml`; #600 keeps 27%); the band mode (decision 17) is designed, not wired | heartbeat `multi_day.book_line` {drawdown_pct, end_pct, ended} | Home Details: "2.2% below its peak · retires at 21%" (`lineText`) | Fine for fixed mode. In band mode the line will move with the number of sessions: the copy must then say the line can move, and which mode is on. Today nothing says "fixed" |
| 1.12 | **Index core** (SPY within its own cap, 27% at every level per #600/#603), `enabled: false` | `config/index_core.yaml`; no live profile has `max_index_pct` | Nothing | Correct for now. When it is switched on, SPY must not look like a stock pick: it needs its own row ("Index core: SPY, 27% at most") and must not count toward "Holding N of 30" |
| 1.13 | **Gate v3 re-score** (`docs/research/results/gate_v3_rescore.md`): every rule `fail`, and the page says it is a re-score, not a verdict | repository files only; **no database table** | Go / no-go shows gate v2's ten-year verdicts (`Gate.tsx`, `kb_gate_report`) | The owner can't see that the yardstick changed (exposure-matched SPY, T-bill cash, Holm). Can ship as a static JSON in the Gate chunk: no migration |
| 1.14 | **Shadow strategies** and the **pre-open review shadow** (pinned model, logging only, #581) | events `shadow_session_end`, `multi_day_review_shadow` (in `kb_health`) | Health shows reviewer lines; Activity has a "Test only" chip | Mostly right. The risk is the opposite one: observer verdicts next to real trades can read as advice. Keep them labelled "observer, no effect on trades" and out of Home |
| 1.15 | **Headline tags** (S105, deterministic, error rate being measured) | research data (`#379`, `#597`) | Nothing | Not ready to show: a tag with an unmeasured error rate on the phone is a claim. Wait for the measurement |
| 1.16 | **Market-event alarm** (S103: Fed and CPI days, megacap reports, index reshuffles; gates nothing) | `market_alarm` is a ROUTINE event (off `kb_health`); `kb_market_notices` (0035) | "Big day" notices on Home, lazy chunk (#378) | Done. Keep it one row, informational, never red |
| 1.17 | **Live price stream** (IEX, about one message a second; #599 calmer tick; #601 "Prices paused" cue open) | Realtime channel `kb:prices` (0032) | Streamed prices replace polled ones; stale after 5 s | Two honesty points: IEX is one exchange, so the figure can differ from the engine's mark; and the book's stop line and P&L are measured on confirmed marks, not on the stream. Neither is said on the screen |
| 1.18 | **News strip** (being built: owner-only Alpaca/Benzinga headlines now; SEC EDGAR 8-K items for multi-user) | `news_item` via the engine's poller; heartbeat `news` {state, last_ok, …}; `news_feed_unhealthy` / `news_feed_ok` | Health shows the feed events; summaries use the news | The strip's design is open. The feed's own health (`news.state`) should decide whether the strip says "no news" or "news feed down" |
| 1.19 | **Replication health** (`replica_poison`, `replica_backlog`; heartbeat `replication.backlog_alarm`) | heartbeat and `kb_health` | Event rows on Health | When the dashboard's copy falls behind, **every number on the phone is older than it looks.** The "Updated 5 s ago" line under Home's details measures the poll, not the data |
| 1.20 | Stale copy | `pages/Health.tsx` | Health still explains the retired dip rule's entry conditions and says the engine "places bracket orders" | The live engine runs the book loop; the intraday path is off. Small copy fix |

## 2. The owner's top jobs on the phone, and where the UI fails them

The owner is one person, non-technical about the engine, checking a paper account from a phone, mostly in Berlin
evenings (the US session is 15:30 to 22:00 CET). Ordered by how often the job comes up.

| # | Job ("when I open kaibo, I want to…") | How often | Where the UI fails it today |
|---|---|---|---|
| J1 | **Know in one glance that nothing needs me.** | Several times a day | The calm state and the "needs you" states use the same chip. A journal stop at night says "asleep" (1.3). Books-differ, clock skew and unverified lease show only as "no new entries" (1.1) |
| J2 | **Know what kaibo will do next, and when.** | Daily, around 15:50 ET | The plan panel lists orders but not the one line "goes out today 15:50–15:57 · 6 buys, 2 sales". The standing block that says it is unused (1.9) |
| J3 | **Understand something kaibo did on its own** (a sale, an adopted order, a refused answer, a carried buy). | A few times a week | Activity rows exist for fills; adoptions have no event (1.6); refusals are mislabelled (1.7); carry-overs are on Health only |
| J4 | **Act when kaibo asks** (accept a books difference, book an unreported fill, later approve a large order). | Rare, but it blocks buys until done | The sheet is good, but it is two pages away from the red row that announces it (1.8). Nothing tells the owner at all unless they open the app (no push until the server move, #600 item 4) |
| J5 | **See how the pot is doing against SPY**, honestly. | Daily | Mostly well served (v4 Home). Live prices tick, but the stop line and P&L are on confirmed marks; nothing says so (1.17). Data behind it may be stale (1.19) |
| J6 | **Know whether the experiment is still worth running.** | Weekly | Go / no-go still speaks gate v2; the v3 re-score, the T-bill cash rule and the "none can pass yet" caveat are invisible (1.13) |
| J7 | **See the news on what I hold**, without being told what to do with it. | Daily | Not built yet (1.18) |
| J8 | **Stop everything** (Flatten and stop) and see that it really stopped. | Very rare | Well served since #532; missing only "waiting for: 2 open orders, 1 unbooked fill" (1.5) |

## 3. What to change, in order

Sizes: **S** up to a day of frontend work, **M** two to four days, **L** a week or more. Flags: **[M]** needs a
migration (the stakeholder applies it; the next free number at the time; 0042 is taken by #584), **[E]** needs an
engine change (outside 09:30–16:00 ET, the usual keys), **[none]** dashboard only. "Lazy" means a separate chunk, not
in the first load.

### Now (this week; dashboard only unless flagged)

| # | Change | Why (one line) | Size | Data / RPC / event | Flag |
|---|---|---|---|---|---|
| N1 | **One trading-state line** in the top bar and on Engine: **Buying** (in the window, nothing blocks), **Holding** (outside the window, nothing blocks: calm), **Selling only** (something blocks buys; the reason in words), **Stopped** (halt or run end), **Not running** (with "needs you" or "asleep") | Answers J1 in one place, and is the shape 1.21 will fill | M | heartbeat `state`, `halted`, `lease`, `reconcile`, `account_clean`, `multi_day`; `halts_today`; pending commands. A pure `tradingState()` in `lib/`, unit-tested on every combination | [none] |
| N2 | **Never "asleep" after a damaged journal**: if the latest engine event is `engine_error` with `JournalCorrupt` and no `engine_start` came after it, the chip is red "Stopped: its record is damaged · needs you at the Mac", with a link to ledger.md's steps in words | Removes the worst false reassurance (1.3) | S | `kb_health` events (Home must read the latest `engine_error`; see E1 for the cleaner source) | [none] |
| N3 | **Fix the refused-payload words**: `multi_day_error` with `step: broker_payload` reads "A broker answer looked wrong and was not booked · buys in {symbol} wait for you", and links to the books sheet | The current label says the opposite of what happened (1.7) | S | `multi_day_error` payload (`step`, `error`, `action`); a case in `healthwords.ts` | [none] |
| N4 | **Red row opens its sheet**: Home's "books and the broker disagree" row opens `ReconcileAccept` directly (same component, lazy) | J4: the action is one tap from the alarm | S | heartbeat `reconcile` (already read) | [none] |
| N5 | **"Next action" line** on Home under the pot: "Next: today 15:50–15:57 ET · 4 buys, 1 sale" / "Decided after Friday's close, sent late today" / "2 buys still short, carried to Monday" | J2 in one sentence, from the engine's own record | S | heartbeat `multi_day.rules.<rule>.standing` {state, decided, fill_session, sent_on, late_of, short, replaced_by}; `kb_multiday_plan` for counts | [none] |
| N6 | **"Last seen on <host>"** in the offline chip and Engine | Tells the owner which machine to check (1.4) | S | heartbeat `detail.host` (already sent) | [none] |
| N7 | **Data freshness that means it**: when `replication.backlog_alarm` is true, Home's "Updated" line becomes amber "kaibo's record is ahead of this screen · figures may be behind" | The poll's age is not the data's age (1.19) | S | heartbeat `replication` | [none] |
| N8 | **Copy clean-up on Engine**: drop the dip-rule paragraph and "places bracket orders"; say "runs the book: decides after the close, sends at 15:50 ET" | Stale words erode trust in the right ones (1.20) | S | none | [none] |

### Next (the next one to two weeks)

| # | Change | Why (one line) | Size | Data / RPC / event | Flag |
|---|---|---|---|---|---|
| E1 | **The engine puts its reason into the heartbeat**: `entry_block` (the list `Engine.entry_block()` already computes, as short codes plus words), and `stopped_because` (`journal_damaged` or `clean`; a lost lease writes nothing, by design) before it exits | N1 and N2 become exact instead of inferred; behaviour-neutral (logging only) | S | heartbeat `detail.entry_block`, `detail.stopped_because` | [E] |
| E2 | **An event for lost-order adoption on the multi-day path** (reuse `order_adopted`, payload `{coid, symbol, path: "lost", filled}`) and an Activity row "An order kaibo had given up on was filled after all; it is booked" | J3: no fill without a story (1.6) | S engine + S web | `order_adopted` (already in `kb_health`) | [E] |
| E3 | **Degraded-start history**: move `engine_degraded_start` and `engine_lease_confirmed` from `PENDING` into `kb_health`; Engine shows "Ran selling only from 09:41 to 15:20 because kaibo's database was unreachable; nothing was bought" | Explains after the fact what could not be shown live (1.2) | S web | events above | [M] |
| N9 | **"Can't reach kaibo's database" is its own state**: when the page's own RPCs fail, the screen says this is the connection, not the engine, and that stops stay at the broker | Today a database outage and a dead engine look the same; during a degraded start they are different things | S | the query error state (`ErrorState`) | [none] |
| N10 | **"Since you last looked"** card on Home (lazy): up to five lines of automatic actions since the last visit (sales, carried buys, cleared halts, adopted orders, refused answers), each opening its Activity row | J3 without push; the pattern Robinhood uses for its managed portfolios, but deterministic, not generated | M | `kb_health` events since a timestamp in `localStorage`; Activity rows | [none] |
| N11 | **Flatten-and-stop says what it waits for**: "Waiting for 2 open orders and 1 fill the broker hasn't reported" | J8; the engine already says why (1.5) | S | `command_waiting` {why, action} | [none] |
| N12 | **Price-source honesty**: one line in the position sheet and Home details: "Live prices are from one exchange (IEX). The stop line and P&L use confirmed closing marks." Pairs with #601's "Prices paused" | Prevents reading a tick as the engine's view (1.17) | S | `lib/stream.ts` state | [none] |
| N13 | **News on what kaibo holds**, owner only, in the position sheet and as one Activity chip ("News"), **not** a Home feed: headline, source, time (ET and Berlin), link out. Feed state decides "No news in 24 h" vs "News feed is down" | J7; news is context for a holding, not a prompt to trade | M (lazy) | `news_item` through a new owner-only `kb_news(p_symbols, p_since)`; heartbeat `news.state` | [M] |
| N14 | **Gate v3 page section** (lazy, in the Gate chunk): per rule, exposure, active return against exposure-matched SPY with its interval, the six marks as pass / fail / unknown, and the top line "a re-score, not a verdict: no current rule can pass v3" | J6; the yardstick changed and the page must say so | M | `docs/research/results/gate_v3_rescore.json` imported at build time into the Gate chunk | [none] |

### Later (with the server move, 1.21, the band, the index core, multi-user)

| # | Change | Why (one line) | Size | Data / RPC / event | Flag |
|---|---|---|---|---|---|
| L1 | **Switch N1 to the engine's own trading state** when #446 ships: one field `trading_state` {active, reducing, halted} plus reasons; the owner's "Selling only" switch on Engine (a command) if Product wants it | One source of truth; the composite stays only as a fallback | S web | heartbeat `trading_state`; `kb_command` kind (new) | [E] [M] |
| L2 | **Push notifications** (FCM candidate, #600 item 4) with four time-sensitive kinds only: approval needed, buys stopped by a safety check, books differ, engine silent in market hours; everything else in a daily digest | J4 off the app; see section 4 on fatigue | L | `m13_push_notifications.md`; a sender reading new events | [M] [server] |
| L3 | **Large-order approval** (#487) | The stakeholder's replacement for a second halt person | L | `m13_large_transaction_approval.md` | [E] [M] |
| L4 | **Book-stop band copy**: "Stop line: 23% below its peak today (moves with the book's age; never above 27%)" and the mode ("fixed" / "band") on Engine | The line will move; a moving line must say so (1.11) | S | `book_line` plus `mode` and `ceiling_pct` in the heartbeat | [E] |
| L5 | **Index-core row**: SPY shown apart from the picks, with its own cap, excluded from "Holding N of 30" and from per-name risk copy | Index exposure is not a stock pick (1.12) | S | `kb_book` positions with a `role` (index / sleeve) | [E] or [M] |
| L6 | **Data-check rows on the plan**: "Buy waits: today's price data for X looks wrong" | Ready for when the book loop has its own checks (1.10) | S | heartbeat `data_checks` | [E] |
| L7 | **8-K items** in the news sheet for every user: "Filed with the SEC · Item 5.02: officer change · accepted 16:31 ET" | Public-domain filings replace licensed headlines for other users (section 5) | M | an 8-K item table per user's symbols; `kb_filings` | [M] [E or collector] |
| L8 | **Headline tags** next to headlines, after S105's error rate is published, and only tags whose measured error rate Product accepts | A tag is a claim; show it once it is measured (1.15) | S | tag column on `news_item` | [M] |

### The ten to do first, in one line each

1. N1 one trading-state line: Buying / Holding / Selling only / Stopped / Not running, with the reason in words.
2. N2 a damaged-journal stop never reads "asleep": red, "needs you at the Mac".
3. E1 the engine writes `entry_block` and `stopped_because` into the heartbeat (logging only).
4. N3 refused broker payloads get true words and a link to the books sheet.
5. N5 a "Next action" line on Home from the standing-targets block.
6. N4 the red books row opens the accept/book sheet directly.
7. N7 and N9 freshness that means it: replication lag and "can't reach the database" are their own states.
8. E2 plus N10 an event for lost-order adoption and a "Since you last looked" card for automatic actions.
9. N13 news per holding in the sheet and an Activity chip, not a Home feed, with feed health.
10. N14 gate v3 on Go / no-go as "a re-score, not a verdict", from the committed results.

## 4. Risks

### Alert fatigue

The clinical alarm literature is the clearest warning: one month of ICU monitoring at UCSF logged about 2.5 million
alarms, and 88.8% of the arrhythmia alarms were false positives; staff became desensitised and silenced them
([UCSF](https://www.ucsf.edu/news/2014/10/119601/hospital-logs-staggering-25-million-alarms-just-month)). kaibo's
risk is smaller in scale but the same in kind: the engine's registry now lists 65 health event types (plus 4 pending), and the dashboard
already shows red for halts, missing stops and books that differ.

- **Three levels, fixed by kind, not by mood.** Red ("needs you"): only states that block buys until the owner acts,
  or an engine that is not running in market hours. Amber ("worth knowing"): a halt that clears itself, a carried buy,
  a late send, a feed down. Everything else is a row in Activity or Engine, never a banner.
- **Map them to the platform's own levels** when push arrives. Apple's guidance asks apps to represent urgency
  accurately and to keep Time Sensitive for events happening now or within an hour
  ([Apple HIG](https://developer.apple.com/design/human-interface-guidelines/managing-notifications.md)). Only L2's
  four kinds qualify. Fills, the daily note and news are passive or digest.
- **A cleared state clears the banner.** `halt_cleared`, `clock_skew` with `state: ok`, `engine_lease_confirmed` and
  `news_feed_ok` must remove their banner the moment they arrive (the Health page already pairs halts with clears).
- **One per cause.** Six fills of one rebalance are one line; a stuck replicator is one amber line, not one per row.
- **Count before shipping.** Before L2, replay a week of real `kb_health` rows through the classifier and publish how
  many reds and ambers the owner would have seen. Target: no more than one red a week in a normal week.

### False reassurance

The opposite failure is worse for a trading app: a calm screen over a problem.

- **"Asleep" vs "stopped".** N2 and E1. A green or grey chip must never stand for an engine that will refuse its next
  start.
- **The chip's freshness is not the data's freshness** (N7). A heartbeat 10 s old can carry a book whose ledger rows
  are an hour behind.
- **Live ticks are not the engine's marks** (N12). Showing a moving price next to the stop line invites the reading
  "the stop is watching this price". It is not: the book's run end is measured on confirmed prices since #527.
- **"Holding" must be earned.** N1's calm state needs positive evidence (fresh heartbeat, `lease: ok`,
  `reconcile` clean, no halt, no pending command), not the absence of red. Missing data reads as "Not sure",
  never as "Holding".
- **Observers and tags are not advice.** Shadow verdicts, the pre-open reviewer and headline tags are labelled
  "observer, no effect on trades" wherever they appear. Public labels its AI assistant experimental and says it may
  be inaccurate ([Public](https://help.public.com/en/articles/9354354-what-is-alpha)); Robinhood warns its
  generated portfolio digests may contain errors ([Robinhood](https://robinhood.com/us/en/support/articles/strategies-digests)).
  kaibo should go further and not generate "why it moved" text at all (CLAUDE.md: models may only veto or halve).
- **"No news" vs "feed down"** (N13): an empty strip during a feed outage would say "nothing happened".

### The first-load budget (204 kB of JavaScript, gzipped)

The budget in `apps/web/size-budget.json` is 204,000 bytes. It was set from 197,841 bytes measured on 18 Nov; that
headroom is gone. Measured on 23 Nov (CI placeholder env, the size check's own file list, gzip level 9): main
`1cec2fd` **203,877 bytes, about 120 B left**; after #609 (the summary and ticker parsers on demand, −2.1 kB) and
#608 merged, main `10b0004` is **201,634 bytes, about 2.4 kB left**. PR #509 (open) would free about 28.5 kB by
building the Supabase client from parts. Open PRs that also take first-load bytes have to be measured again after
each merge.

- **New surfaces are lazy until #509 lands.** That includes N1's line (#607 already loads `TradingState` as its own
  chunk) and N5's line. In the first load only the small pure checks that keep Home and the top bar honest even before
  a chunk arrives: N2's journal-damage rule (`lib/journalstop.ts`, #607), N6, N7. Each PR posts its measured bytes.
- **Lazy, like Notices:** the "Since you last looked" card (N10), the news sheet and chip (N13), gate v3 (N14 in the
  Gate chunk), ReconcileAccept when opened from Home (N4), any push settings (L2).
- **No new libraries** for any of this. Dates and numbers through the existing `lib/fmt.ts`.
- If #509 merges, keep at least 10 kB of the freed space as headroom rather than spending it.

## 5. What changes for multi-user (January)

The legal frame is unchanged (report, "Before a paid server…"): paper only for others first, no live automation for
other people's accounts without a German regulatory opinion. For the UI that means:

- **The engine's machinery becomes the operator's, not the user's.** "Your Mac", host names, lease, journal, degraded
  start, replication: a user sees one service state ("kaibo is running" / "kaibo is paused for maintenance; stops stay
  at the broker") and their own account's trading state. The operator gets an admin view (#92) with the detail of
  section 1. N6's host name must never reach another user.
- **Trading state per account.** N1's line is per user's run; one user's books difference must not show on another's
  screen. Every `kb_*` that returns heartbeat detail today (`kb_overview` returns **all** heartbeat rows) needs a
  tenant filter (#478) before a second user signs in. **[M]**
- **News.** Alpaca's Benzinga partnership speaks of displaying news in partner apps, but the page gives no terms or
  plans for redistribution ([Alpaca](https://alpaca.markets/blog/alpaca-partners-with-benzinga-to-deliver-real-time-embedded-financial-news)),
  so headlines stay owner-only until Alpaca confirms in writing. For everyone else, SEC 8-K items (L7): companies
  generally file within four business days of the event, and the item number says what kind of event it was, for
  example 5.02 for an officer departure or appointment
  ([Investor.gov](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/how-read-8)).
  The copy must show the filing time, not suggest it is the event time.
- **Live prices.** The IEX stream is licensed to the individual (report, unverified, from a third party) and the
  Realtime channel is owner-only (0032). Other users get polled prices unless their own Alpaca grant gives them a
  stream; the page must not assume one.
- **Approvals and push become per user and per device** (#476, #487). The approval screen is the only place a user
  decides anything about an order; a notification never carries the decision.
- **Words that do not advise.** With other people's money (even paper) the copy is reviewed for anything that reads
  as a recommendation: no "kaibo thinks", no ranking language on news, no "good time to".
- **Onboarding states.** A user without a connected paper account, a user whose grant was revoked, and a user whose
  run is waiting for its first session each need a state of N1 ("Not connected", "Connection ended", "Starts at the
  next close"). `startstate.ts` covers the third for the owner today.

## How good apps handle the same things

Paraphrased; no layouts or assets are copied. Where only press material or help pages were found, the row says so.

| Pattern | Seen in | What kaibo takes from it |
|---|---|---|
| Notification categories the user switches one by one (price moves, order status, account activity) | Robinhood's notification settings list separate push categories, including order status and account activity ([Robinhood](https://robinhood.com/us/en/support/articles/updating-your-notification-settings)) | L2: one switch per kind, all off until the owner turns one on (as `m13_push_notifications.md` proposes) |
| Price alerts set per asset from a bell on its page; volatility alerts as one toggle | Revolut's help pages ([Revolut](https://help.revolut.com/help/wealth/stocks/getting-started-with-trading/managing-your-trading-account/trading-alerts/managing-my-trading-watchlist-and-price-alerts/)) | Not for kaibo now: the owner does not trade by hand, so price alerts would invite overriding the engine |
| Automated trades in one fixed daily window, stated plainly | Composer trades automated strategies once a day between 15:50 and 16:00 ET ([Composer](https://help.composer.trade/article/60-after-hours-trading)) | N5: say the window (15:50–15:57 ET, and in Berlin time) every time the plan is shown. The same window as kaibo's makes the comparison easy |
| Past automated activity grouped and labelled by kind in a history | Composer lists internal swaps under a "Swaps" label in the strategy's history ([Composer](https://help.composer.trade/article/206-understanding-symphony-swaps)) | N10 and Activity: automatic actions named by what they were (carried, adopted, refused), not by engine step |
| A fixed run time and a plain choice of how automatic investing distributes money | Trading 212 runs AutoInvest at 08:00 CET and explains its two distribution modes on one page ([Trading 212](https://helpcentre.trading212.com/hc/en-us/articles/30661163244317-Pies-AutoInvest-Introduction)) | The rules of the automation, written once, one tap from the plan line |
| Weekly summaries of what drove a managed portfolio, with top drivers and detractors, flagged as possibly wrong | Robinhood Strategies Digests, AI-generated ([Robinhood](https://robinhood.com/us/en/support/articles/strategies-digests)) | N10 borrows the shape (what happened, biggest contributors) but **computes** it from kaibo's own records; nothing generated |
| A simpler "Focused" order flow beside an advanced one; AI news summaries on quote pages | IBKR's GlobalTrader redesign, from the press release only ([Nasdaq](https://www.nasdaq.com/press-release/interactive-brokers-redesigns-ibkr-globaltrader-app-simpler-smarter-mobile-trading)) | The two-depth idea matches v4 (Home simple, Engine detailed). The AI summaries are not for kaibo |
| An activities page with filters for trades, transfers and dividends; paper and live switched in settings | Alpaca's web dashboard update ([Alpaca](https://alpaca.markets/blog/web-dashboard-update)) | Activity's chips already do this; add "Automatic" (N10) and "News" (N13) |

## Open questions for Product

1. **N1's words.** "Buying / Holding / Selling only / Stopped / Not running": plain, but "Holding" could be read as
   "holding positions". Alternative: "Waiting for the close".
2. **E1** is an engine change with no behaviour change. Can it ride with the next engine PR outside market hours, or
   does it need its own ticket under M10?
3. **N13** needs `kb_news` (a migration). Product to confirm owner-only scope until Alpaca answers on redistribution.
4. **N14** imports committed result files into the web build. Agree that `docs/research/results/*.json` is a stable
   enough contract, or should Backend-quant publish a table?
5. **The red-a-week target** for alert fatigue (section 4): accept, or set another number?

---

_Generated by [Claude Code](https://claude.ai/code)_
